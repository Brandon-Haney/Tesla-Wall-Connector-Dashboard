"""Main entry point for the TWC Collector service."""

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

from .config import settings, ChargerConfig
from .twc_client import TWCClient
from .comed_client import ComEdClient
from .tessie_client import TessieClient
from .opower_client import OpowerClient, OpowerAuthError
from .influx_writer import InfluxWriter
from .models import TWCVitals, TessieVehicle, VehicleChargingSession, FleetWallConnector, FleetChargeSession

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("twc-collector")


class PriceStatistics:
    """Calculates rolling price statistics for smart charging thresholds."""

    def __init__(self, influx_writer: 'InfluxWriter'):
        self.influx_writer = influx_writer
        self._cached_stats: Optional[dict] = None
        self._last_calculation: Optional[datetime] = None
        # Recalculate at most every 6 hours
        self._cache_duration = timedelta(hours=6)

    def calculate_statistics(self, lookback_days: int = 30) -> Optional[dict]:
        """Calculate price statistics from historical data.

        Args:
            lookback_days: Number of days to include in the calculation

        Returns:
            Dictionary with statistical values or None if insufficient data
        """
        # Get all price values from InfluxDB
        values = self.influx_writer.get_price_values(lookback_days)

        if not values:
            logger.warning("No price data available for statistics calculation")
            return None

        if len(values) < 100:  # Minimum ~8 hours of 5-min data
            logger.warning(f"Insufficient price data ({len(values)} points) for reliable statistics")
            return None

        # Sort for percentile calculation
        sorted_values = sorted(values)
        n = len(sorted_values)

        def percentile(p: int) -> float:
            """Calculate percentile value."""
            idx = (n - 1) * p / 100
            lower = int(idx)
            upper = min(lower + 1, n - 1)
            weight = idx - lower
            return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight

        # Calculate statistics
        mean = sum(values) / n
        sorted_half = n // 2
        median = (sorted_values[sorted_half] + sorted_values[sorted_half - 1]) / 2 if n % 2 == 0 else sorted_values[sorted_half]

        # Standard deviation
        variance = sum((x - mean) ** 2 for x in values) / n
        std_dev = variance ** 0.5

        # Days of data available
        days_available = self.influx_writer.get_price_data_days_available(lookback_days)

        stats = {
            "mean": round(mean, 3),
            "median": round(median, 3),
            "std_dev": round(std_dev, 3),
            "min": round(min(values), 3),
            "max": round(max(values), 3),
            "p10": round(percentile(10), 3),
            "p25": round(percentile(25), 3),
            "p75": round(percentile(75), 3),
            "p90": round(percentile(90), 3),
            "p95": round(percentile(95), 3),
            "count": n,
            "days_available": days_available,
        }

        return stats

    def get_statistics(self, lookback_days: int = 30, force_recalculate: bool = False) -> Optional[dict]:
        """Get price statistics, using cache if available.

        Args:
            lookback_days: Number of days to include
            force_recalculate: Force recalculation even if cache is valid

        Returns:
            Dictionary with statistical values or None if unavailable
        """
        now = datetime.now(timezone.utc)

        # Check if we can use cached stats
        if not force_recalculate and self._cached_stats and self._last_calculation:
            age = now - self._last_calculation
            if age < self._cache_duration:
                return self._cached_stats

        # Calculate new statistics
        stats = self.calculate_statistics(lookback_days)

        if stats:
            self._cached_stats = stats
            self._last_calculation = now

            # Store in InfluxDB for dashboard access
            self.influx_writer.write_price_statistics(stats)

            logger.info(
                f"Price statistics updated: "
                f"mean={stats['mean']:.2f}¢, median={stats['median']:.2f}¢, "
                f"p75={stats['p75']:.2f}¢ (resume), p90={stats['p90']:.2f}¢ (stop)"
            )

        return stats

    def get_stop_threshold(self, percentile: int = 90) -> Optional[float]:
        """Get the price threshold for stopping charging.

        Args:
            percentile: The percentile to use (default 90th = top 10% expensive)

        Returns:
            Price threshold in cents/kWh or None if unavailable
        """
        stats = self.get_statistics()
        if stats:
            return stats.get(f"p{percentile}")
        return None

    def get_resume_threshold(self, percentile: int = 75) -> Optional[float]:
        """Get the price threshold for resuming charging.

        Args:
            percentile: The percentile to use (default 75th)

        Returns:
            Price threshold in cents/kWh or None if unavailable
        """
        stats = self.get_statistics()
        if stats:
            return stats.get(f"p{percentile}")
        return None

    def get_current_percentile(self, current_price: float) -> Optional[int]:
        """Calculate what percentile the current price falls into.

        Args:
            current_price: Current price in cents/kWh

        Returns:
            Percentile (0-100) or None if stats unavailable
        """
        stats = self.get_statistics()
        if not stats:
            return None

        # Estimate percentile based on stored values
        if current_price <= stats["p10"]:
            return 10
        elif current_price <= stats["p25"]:
            # Interpolate between 10 and 25
            ratio = (current_price - stats["p10"]) / (stats["p25"] - stats["p10"]) if stats["p25"] > stats["p10"] else 0
            return int(10 + ratio * 15)
        elif current_price <= stats["median"]:
            ratio = (current_price - stats["p25"]) / (stats["median"] - stats["p25"]) if stats["median"] > stats["p25"] else 0
            return int(25 + ratio * 25)
        elif current_price <= stats["p75"]:
            ratio = (current_price - stats["median"]) / (stats["p75"] - stats["median"]) if stats["p75"] > stats["median"] else 0
            return int(50 + ratio * 25)
        elif current_price <= stats["p90"]:
            ratio = (current_price - stats["p75"]) / (stats["p90"] - stats["p75"]) if stats["p90"] > stats["p75"] else 0
            return int(75 + ratio * 15)
        elif current_price <= stats["p95"]:
            ratio = (current_price - stats["p90"]) / (stats["p95"] - stats["p90"]) if stats["p95"] > stats["p90"] else 0
            return int(90 + ratio * 5)
        else:
            return 99


class SmartChargingController:
    """Controls vehicle charging based on adaptive price thresholds.

    Uses rolling price statistics to determine when to pause/resume charging
    during price spikes. Thresholds automatically adjust as electricity prices
    trend up or down over time.
    """

    def __init__(
        self,
        tessie_client: 'TessieClient',
        price_statistics: PriceStatistics,
        influx_writer: 'InfluxWriter'
    ):
        self.tessie_client = tessie_client
        self.price_statistics = price_statistics
        self.influx_writer = influx_writer

        # State tracking per vehicle: vin -> {status, last_action_time, paused_by_smart_charging}
        self.vehicle_states: Dict[str, dict] = {}

        # Configuration from settings
        self.enabled = settings.smart_charging_enabled
        self.control_enabled = settings.smart_charging_control_enabled
        self.stop_percentile = settings.smart_charging_stop_percentile
        self.resume_percentile = settings.smart_charging_resume_percentile
        self.min_interval = settings.smart_charging_min_interval  # seconds

    def _get_vehicle_state(self, vin: str) -> dict:
        """Get or create state tracking for a vehicle."""
        if vin not in self.vehicle_states:
            self.vehicle_states[vin] = {
                "status": "unknown",  # charging, paused_by_price, not_charging, would_pause
                "last_action_time": None,
                "paused_by_smart_charging": False,
                "simulated_pause": False,  # True when dry-run mode detected a price spike
                "last_action": None,
            }
        return self.vehicle_states[vin]

    def _can_take_action(self, state: dict) -> bool:
        """Check if enough time has passed since last action (hysteresis)."""
        if state["last_action_time"] is None:
            return True

        elapsed = (datetime.now(timezone.utc) - state["last_action_time"]).total_seconds()
        return elapsed >= self.min_interval

    async def evaluate_and_act(
        self,
        vin: str,
        display_name: str,
        is_charging: bool,
        current_price_cents: float
    ) -> Optional[str]:
        """Evaluate current price and take charging action if needed.

        Args:
            vin: Vehicle VIN
            display_name: Vehicle display name for logging
            is_charging: Whether vehicle is currently charging
            current_price_cents: Current electricity price in cents/kWh

        Returns:
            Action taken: "stopped", "started", or None if no action
        """
        if not self.enabled:
            return None

        state = self._get_vehicle_state(vin)

        # Get thresholds from statistics
        stats = self.price_statistics.get_statistics()
        if not stats:
            logger.debug(f"[{display_name}] Smart charging: No statistics available")
            return None

        stop_threshold = stats.get(f"p{self.stop_percentile}", 0)
        resume_threshold = stats.get(f"p{self.resume_percentile}", 0)
        current_percentile = self.price_statistics.get_current_percentile(current_price_cents)

        logger.debug(
            f"[{display_name}] Smart charging: "
            f"price={current_price_cents:.2f}¢ ({current_percentile}th %ile), "
            f"stop>{stop_threshold:.2f}¢, resume<{resume_threshold:.2f}¢"
        )

        action_taken = None

        # Decision logic
        if is_charging and not state["paused_by_smart_charging"]:
            # Vehicle is charging normally - check if we should stop
            if current_price_cents > stop_threshold:
                if self._can_take_action(state):
                    # Price spike detected
                    if self.control_enabled:
                        # Control is enabled - actually stop charging
                        logger.warning(
                            f"[{display_name}] PRICE SPIKE: {current_price_cents:.2f}¢/kWh "
                            f"({current_percentile}th %ile) > {stop_threshold:.2f}¢ ({self.stop_percentile}th %ile) - "
                            f"Stopping charging"
                        )

                        success = await self.tessie_client.stop_charging(vin)
                        if success:
                            state["status"] = "paused_by_price"
                            state["paused_by_smart_charging"] = True
                            state["last_action_time"] = datetime.now(timezone.utc)
                            state["last_action"] = "stop"
                            action_taken = "stopped"

                            # Write action to InfluxDB
                            self._write_action(
                                vin, display_name, "stop",
                                current_price_cents, current_percentile or 0,
                                stop_threshold
                            )
                        else:
                            logger.error(f"[{display_name}] Failed to stop charging")
                    else:
                        # Control disabled - DRY RUN mode - log prominently
                        logger.warning(
                            f"[{display_name}] ⚡ DRY RUN - PRICE SPIKE DETECTED: {current_price_cents:.2f}¢/kWh "
                            f"({current_percentile}th %ile) > {stop_threshold:.2f}¢ ({self.stop_percentile}th %ile)"
                        )
                        logger.warning(
                            f"[{display_name}] ⚡ DRY RUN - WOULD STOP CHARGING (control disabled)"
                        )

                        # Update state tracking for dry run (so we can track simulated pause)
                        state["status"] = "would_pause"
                        state["simulated_pause"] = True
                        state["last_action_time"] = datetime.now(timezone.utc)
                        state["last_action"] = "stop_simulated"

                        # Write simulated action to InfluxDB for dashboard visibility
                        self._write_action(
                            vin, display_name, "stop_simulated",
                            current_price_cents, current_percentile or 0,
                            stop_threshold
                        )
                        action_taken = "stopped_simulated"

        elif state["paused_by_smart_charging"] or state.get("simulated_pause"):
            # We paused this vehicle (or simulated pause) - check if we should resume
            if current_price_cents < resume_threshold:
                if self._can_take_action(state):
                    # Price dropped - resume charging
                    if self.control_enabled:
                        logger.info(
                            f"[{display_name}] Price normal: {current_price_cents:.2f}¢/kWh "
                            f"({current_percentile}th %ile) < {resume_threshold:.2f}¢ ({self.resume_percentile}th %ile) - "
                            f"Resuming charging"
                        )

                        success = await self.tessie_client.start_charging(vin)
                        if success:
                            state["status"] = "charging"
                            state["paused_by_smart_charging"] = False
                            state["last_action_time"] = datetime.now(timezone.utc)
                            state["last_action"] = "start"
                            action_taken = "started"

                            # Write action to InfluxDB
                            self._write_action(
                                vin, display_name, "start",
                                current_price_cents, current_percentile or 0,
                                resume_threshold
                            )
                        else:
                            logger.error(f"[{display_name}] Failed to resume charging")
                    else:
                        # Control disabled - DRY RUN mode - log prominently
                        logger.warning(
                            f"[{display_name}] ⚡ DRY RUN - PRICE NORMAL: {current_price_cents:.2f}¢/kWh "
                            f"({current_percentile}th %ile) < {resume_threshold:.2f}¢ ({self.resume_percentile}th %ile)"
                        )
                        logger.warning(
                            f"[{display_name}] ⚡ DRY RUN - WOULD RESUME CHARGING (control disabled)"
                        )

                        # Clear simulated pause state
                        state["status"] = "charging"
                        state["simulated_pause"] = False
                        state["last_action_time"] = datetime.now(timezone.utc)
                        state["last_action"] = "start_simulated"

                        # Write simulated action to InfluxDB for dashboard visibility
                        self._write_action(
                            vin, display_name, "start_simulated",
                            current_price_cents, current_percentile or 0,
                            resume_threshold
                        )
                        action_taken = "started_simulated"

        # Update vehicle status
        if not state["paused_by_smart_charging"]:
            state["status"] = "charging" if is_charging else "not_charging"

        return action_taken

    def _write_action(
        self,
        vin: str,
        display_name: str,
        action: str,
        price: float,
        percentile: int,
        threshold: float
    ):
        """Write smart charging action to InfluxDB."""
        try:
            from influxdb_client import Point, WritePrecision

            point = (
                Point("smart_charging_actions")
                .tag("vin", vin)
                .tag("display_name", display_name)
                .tag("action", action)
                .field("price_cents", price)
                .field("percentile", percentile)
                .field("threshold_cents", threshold)
                .time(datetime.now(timezone.utc), WritePrecision.MS)
            )

            self.influx_writer.write_api.write(
                bucket=self.influx_writer.bucket,
                org=self.influx_writer.org,
                record=point
            )
            logger.debug(f"[{display_name}] Wrote smart charging action: {action}")

        except Exception as e:
            logger.error(f"Error writing smart charging action: {e}")

    def write_state(self, vin: str, display_name: str, current_price_cents: float):
        """Write current smart charging state to InfluxDB for dashboard."""
        if not self.enabled:
            return

        state = self._get_vehicle_state(vin)
        stats = self.price_statistics.get_statistics()

        if not stats:
            return

        try:
            from influxdb_client import Point, WritePrecision

            stop_threshold = stats.get(f"p{self.stop_percentile}", 0)
            resume_threshold = stats.get(f"p{self.resume_percentile}", 0)
            current_percentile = self.price_statistics.get_current_percentile(current_price_cents) or 50

            point = (
                Point("smart_charging_state")
                .tag("vin", vin)
                .tag("display_name", display_name)
                .field("enabled", True)
                .field("control_enabled", self.control_enabled)  # True = live mode, False = dry-run mode
                .field("status", state["status"])
                .field("paused_by_price", state["paused_by_smart_charging"])
                .field("simulated_pause", state.get("simulated_pause", False))  # Dry-run detected price spike
                .field("current_price_cents", current_price_cents)
                .field("current_percentile", current_percentile)
                .field("stop_threshold_cents", stop_threshold)
                .field("stop_percentile", self.stop_percentile)
                .field("resume_threshold_cents", resume_threshold)
                .field("resume_percentile", self.resume_percentile)
                .field("days_of_data", stats.get("days_available", 0))
                .time(datetime.now(timezone.utc), WritePrecision.MS)
            )

            self.influx_writer.write_api.write(
                bucket=self.influx_writer.bucket,
                org=self.influx_writer.org,
                record=point
            )

        except Exception as e:
            logger.error(f"Error writing smart charging state: {e}")

    def get_status_summary(self, vin: str, current_price_cents: float) -> dict:
        """Get a summary of smart charging status for a vehicle."""
        state = self._get_vehicle_state(vin)
        stats = self.price_statistics.get_statistics()

        if not stats:
            return {
                "enabled": self.enabled,
                "status": "no_data",
                "message": "Insufficient price history",
            }

        stop_threshold = stats.get(f"p{self.stop_percentile}", 0)
        resume_threshold = stats.get(f"p{self.resume_percentile}", 0)
        current_percentile = self.price_statistics.get_current_percentile(current_price_cents)

        return {
            "enabled": self.enabled,
            "status": state["status"],
            "paused_by_price": state["paused_by_smart_charging"],
            "current_price_cents": current_price_cents,
            "current_percentile": current_percentile,
            "stop_threshold_cents": stop_threshold,
            "resume_threshold_cents": resume_threshold,
            "days_of_data": stats.get("days_available", 0),
        }


class SessionTracker:
    """Tracks charging sessions for each charger with real-time cost calculation."""

    def __init__(self):
        self.sessions: Dict[str, dict] = {}
        self.current_price_cents: float = 0.0  # Current ComEd price
        self.delivery_rate_cents: float = 7.5  # Default delivery rate from bill analysis

    def set_current_price(self, price_cents: float):
        """Update the current electricity price."""
        self.current_price_cents = price_cents

    def update(self, charger_name: str, vitals: TWCVitals) -> Optional[dict]:
        """
        Update session state and return session info if one just ended.
        Tracks incremental energy and cost in real-time.
        Returns dict with session details when a session ends, None otherwise.
        """
        current = self.sessions.get(charger_name)
        now = datetime.now(timezone.utc)

        if vitals.is_charging:
            if current is None:
                # New session started
                self.sessions[charger_name] = {
                    "start_time": now,
                    "last_update_time": now,
                    "last_energy_wh": vitals.session_energy_wh,
                    "peak_power_w": vitals.power_w,
                    "total_energy_wh": 0.0,
                    "total_cost_cents": 0.0,  # Supply cost only
                    "total_full_cost_cents": 0.0,  # Supply + delivery
                    "price_samples": [],  # Track prices during session
                }
                logger.info(f"[{charger_name}] Charging session started")
            else:
                # Calculate incremental energy since last update
                current_energy = vitals.session_energy_wh
                last_energy = current.get("last_energy_wh", 0)

                # Handle session energy reset (TWC resets this counter sometimes)
                if current_energy >= last_energy:
                    incremental_wh = current_energy - last_energy
                else:
                    # Counter reset - use the new value as incremental
                    incremental_wh = current_energy

                if incremental_wh > 0:
                    # Calculate cost for this increment
                    incremental_kwh = incremental_wh / 1000.0
                    supply_cost = incremental_kwh * self.current_price_cents
                    full_rate = self.current_price_cents + self.delivery_rate_cents
                    full_cost = incremental_kwh * full_rate

                    # Accumulate
                    current["total_energy_wh"] += incremental_wh
                    current["total_cost_cents"] += supply_cost
                    current["total_full_cost_cents"] += full_cost

                    # Track price sample for averaging
                    if self.current_price_cents > 0:
                        current["price_samples"].append(self.current_price_cents)

                # Update tracking values
                current["last_energy_wh"] = current_energy
                current["last_update_time"] = now

                # Update peak power
                if vitals.power_w > current.get("peak_power_w", 0):
                    current["peak_power_w"] = vitals.power_w
        else:
            if current is not None:
                # Session ended
                duration = (now - current["start_time"]).total_seconds()

                # Use TWC's session_energy_wh as the authoritative total
                # (in case we missed some increments)
                final_energy_wh = vitals.session_energy_wh
                if final_energy_wh == 0:
                    # If TWC already reset, use our tracked value
                    final_energy_wh = current["total_energy_wh"]

                # Calculate average price during session
                price_samples = current.get("price_samples", [])
                avg_price = sum(price_samples) / len(price_samples) if price_samples else 0

                # Recalculate costs using final energy for accuracy
                final_kwh = final_energy_wh / 1000.0
                supply_cost = final_kwh * avg_price if avg_price > 0 else current["total_cost_cents"]
                full_cost = final_kwh * (avg_price + self.delivery_rate_cents) if avg_price > 0 else current["total_full_cost_cents"]

                session_info = {
                    "charger_name": charger_name,
                    "start_time": current["start_time"],
                    "end_time": now,
                    "duration_s": duration,
                    "energy_wh": final_energy_wh,
                    "peak_power_w": current.get("peak_power_w", 0),
                    "avg_price_cents": avg_price,
                    "supply_cost_cents": supply_cost,
                    "full_cost_cents": full_cost,
                    "delivery_rate_cents": self.delivery_rate_cents,
                }

                logger.info(
                    f"[{charger_name}] Charging session ended: "
                    f"{final_energy_wh/1000:.2f} kWh in {duration/60:.1f} min, "
                    f"cost: ${full_cost/100:.2f} (avg {avg_price:.1f}¢/kWh supply)"
                )

                del self.sessions[charger_name]
                return session_info

        return None

    def get_current_session(self, charger_name: str) -> Optional[dict]:
        """Get the current session state for a charger (for real-time display)."""
        session = self.sessions.get(charger_name)
        if session:
            return {
                "start_time": session["start_time"],
                "energy_wh": session["total_energy_wh"],
                "supply_cost_cents": session["total_cost_cents"],
                "full_cost_cents": session["total_full_cost_cents"],
                "peak_power_w": session["peak_power_w"],
                "duration_s": (datetime.now(timezone.utc) - session["start_time"]).total_seconds(),
            }
        return None


class FleetSessionTracker:
    """Tracks charging sessions from Fleet API by integrating power over time.

    Since Fleet API live_status only provides power (not session energy),
    we calculate session energy by integrating power readings over time:
    energy_wh = sum(power_w * time_delta_hours)
    """

    def __init__(self):
        self.sessions: Dict[str, dict] = {}  # DIN -> session state
        self.current_price_cents: float = 0.0
        self.delivery_rate_cents: float = 7.5  # From config

    def set_current_price(self, price_cents: float):
        """Update the current electricity price."""
        self.current_price_cents = price_cents

    def set_delivery_rate(self, rate_cents: float):
        """Update the delivery rate."""
        self.delivery_rate_cents = rate_cents

    def update(self, wc: FleetWallConnector) -> Optional[dict]:
        """Update session state for a Wall Connector.

        Args:
            wc: FleetWallConnector with current status

        Returns:
            Session info dict if session just ended, None otherwise
        """
        current = self.sessions.get(wc.din)
        now = datetime.now(timezone.utc)

        if wc.is_charging:
            if current is None:
                # New session started
                self.sessions[wc.din] = {
                    "start_time": now,
                    "last_update_time": now,
                    "last_power_w": wc.wall_connector_power,
                    "peak_power_w": wc.wall_connector_power,
                    "total_energy_wh": 0.0,
                    "supply_cost_cents": 0.0,
                    "full_cost_cents": 0.0,
                    "price_samples": [],
                    "vin": wc.vin,
                    "unit_name": wc.unit_name if hasattr(wc, 'unit_name') else f"unit_{wc.unit_number}",
                }
                unit_name = "leader" if wc.is_leader else f"follower_{wc.unit_number}"
                logger.info(f"[Fleet {unit_name}] Session started tracking")
            else:
                # Session continuing - integrate power over time
                time_delta = (now - current["last_update_time"]).total_seconds()

                if time_delta > 0:
                    # Calculate energy: power * time (convert to hours for Wh)
                    # Use average of last and current power for trapezoid integration
                    avg_power_w = (current["last_power_w"] + wc.wall_connector_power) / 2
                    incremental_wh = avg_power_w * (time_delta / 3600.0)

                    if incremental_wh > 0:
                        # Calculate cost for this increment
                        incremental_kwh = incremental_wh / 1000.0
                        supply_cost = incremental_kwh * self.current_price_cents
                        full_rate = self.current_price_cents + self.delivery_rate_cents
                        full_cost = incremental_kwh * full_rate

                        # Accumulate
                        current["total_energy_wh"] += incremental_wh
                        current["supply_cost_cents"] += supply_cost
                        current["full_cost_cents"] += full_cost

                        # Track price sample
                        if self.current_price_cents > 0:
                            current["price_samples"].append(self.current_price_cents)

                # Update tracking values
                current["last_update_time"] = now
                current["last_power_w"] = wc.wall_connector_power

                # Update peak power
                if wc.wall_connector_power > current.get("peak_power_w", 0):
                    current["peak_power_w"] = wc.wall_connector_power

                # Update VIN if changed
                if wc.vin and wc.vin != current.get("vin"):
                    current["vin"] = wc.vin
        else:
            if current is not None:
                # Session ended
                duration = (now - current["start_time"]).total_seconds()

                # Calculate average price during session
                price_samples = current.get("price_samples", [])
                avg_price = sum(price_samples) / len(price_samples) if price_samples else 0

                unit_name = "leader" if wc.is_leader else f"follower_{wc.unit_number}"

                session_info = {
                    "din": wc.din,
                    "unit_name": unit_name,
                    "start_time": current["start_time"],
                    "end_time": now,
                    "duration_s": duration,
                    "energy_wh": current["total_energy_wh"],
                    "peak_power_w": current.get("peak_power_w", 0),
                    "avg_price_cents": avg_price,
                    "supply_cost_cents": current["supply_cost_cents"],
                    "full_cost_cents": current["full_cost_cents"],
                    "vin": current.get("vin"),
                }

                logger.info(
                    f"[Fleet {unit_name}] Session ended: "
                    f"{current['total_energy_wh']/1000:.2f} kWh in {duration/60:.1f} min, "
                    f"cost: ${current['full_cost_cents']/100:.2f}"
                )

                del self.sessions[wc.din]
                return session_info

        return None

    def get_current_session(self, din: str) -> Optional[dict]:
        """Get current session state for a Wall Connector (for real-time display)."""
        session = self.sessions.get(din)
        if session:
            return {
                "din": din,
                "start_time": session["start_time"],
                "energy_wh": session["total_energy_wh"],
                "supply_cost_cents": session["supply_cost_cents"],
                "full_cost_cents": session["full_cost_cents"],
                "peak_power_w": session["peak_power_w"],
                "duration_s": (datetime.now(timezone.utc) - session["start_time"]).total_seconds(),
            }
        return None

    def get_all_active_sessions(self) -> Dict[str, dict]:
        """Get all active session states for dashboard display."""
        result = {}
        for din in self.sessions:
            session = self.get_current_session(din)
            if session:
                result[din] = session
        return result


class VehicleSessionTracker:
    """Tracks charging sessions from the vehicle's perspective."""

    def __init__(self):
        self.sessions: Dict[str, VehicleChargingSession] = {}  # VIN -> Session

    def _get_charger_type(self, vehicle: TessieVehicle) -> str:
        """Determine charger type from vehicle charge state."""
        if vehicle.fast_charger_present:
            return "Supercharger"
        elif vehicle.conn_charge_cable == "SAE":
            return "Wall Connector"
        elif vehicle.conn_charge_cable == "IEC":
            return "IEC (EU)"
        elif vehicle.conn_charge_cable:
            return vehicle.conn_charge_cable
        return "Unknown"

    def update(self, vehicle: TessieVehicle) -> Optional[VehicleChargingSession]:
        """
        Update session state and return completed session if one just ended.

        Returns:
            VehicleChargingSession if a session just ended, None otherwise
        """
        vin = vehicle.vin
        current_session = self.sessions.get(vin)
        now = datetime.now(timezone.utc)

        if vehicle.is_charging:
            if current_session is None:
                # New charging session started
                charger_type = self._get_charger_type(vehicle)
                is_home = not vehicle.fast_charger_present

                self.sessions[vin] = VehicleChargingSession(
                    vin=vin,
                    display_name=vehicle.display_name or f"VIN ...{vin[-6:]}",
                    start_time=now,
                    starting_battery_level=vehicle.battery_level or 0,
                    starting_range=vehicle.battery_range or 0.0,
                    charger_type=charger_type,
                    is_home_charge=is_home,
                    latitude=vehicle.latitude,
                    longitude=vehicle.longitude,
                    peak_power_kw=float(vehicle.charger_power or 0),
                )
                logger.info(
                    f"[{vehicle.display_name}] Vehicle charging session started: "
                    f"{vehicle.battery_level}% SOC, {charger_type}"
                )
            else:
                # Update ongoing session
                current_session.ending_battery_level = vehicle.battery_level or current_session.starting_battery_level
                current_session.ending_range = vehicle.battery_range or current_session.starting_range
                current_session.energy_added_kwh = vehicle.charge_energy_added or 0.0
                current_session.miles_added = (
                    current_session.ending_range - current_session.starting_range
                    if current_session.ending_range > current_session.starting_range
                    else 0.0
                )

                # Update peak power
                current_power = float(vehicle.charger_power or 0)
                if current_power > current_session.peak_power_kw:
                    current_session.peak_power_kw = current_power

        else:
            if current_session is not None:
                # Session ended
                current_session.end_time = now
                current_session.is_active = False
                current_session.ending_battery_level = vehicle.battery_level or current_session.ending_battery_level
                current_session.ending_range = vehicle.battery_range or current_session.ending_range

                # Use vehicle's reported energy_added as the final value
                # (may be reset to 0 if vehicle disconnected, so keep our tracked value if so)
                if vehicle.charge_energy_added and vehicle.charge_energy_added > 0:
                    current_session.energy_added_kwh = vehicle.charge_energy_added

                # Calculate average power
                duration_hours = current_session.duration_s / 3600.0
                if duration_hours > 0 and current_session.energy_added_kwh > 0:
                    current_session.avg_power_kw = current_session.energy_added_kwh / duration_hours
                else:
                    current_session.avg_power_kw = 0.0

                logger.info(
                    f"[{current_session.display_name}] Vehicle charging session ended: "
                    f"{current_session.energy_added_kwh:.2f}kWh, "
                    f"{current_session.starting_battery_level}% -> {current_session.ending_battery_level}% SOC, "
                    f"{current_session.duration_min:.1f} min"
                )

                # Remove from active sessions and return completed session
                del self.sessions[vin]
                return current_session

        return None

    def get_current_session(self, vin: str) -> Optional[VehicleChargingSession]:
        """Get the current active session for a vehicle."""
        return self.sessions.get(vin)

    def get_all_active_sessions(self) -> Dict[str, VehicleChargingSession]:
        """Get all active charging sessions."""
        return self.sessions.copy()


class Collector:
    """Main collector service."""

    # Time window for correlating TWC and vehicle sessions (seconds)
    SESSION_CORRELATION_WINDOW = 300  # 5 minutes

    def __init__(self):
        self.running = False
        self.twc_clients: Dict[str, TWCClient] = {}
        self.comed_client: Optional[ComEdClient] = None
        self.tessie_client: Optional[TessieClient] = None
        self.opower_client: Optional[OpowerClient] = None
        self.influx_writer: Optional[InfluxWriter] = None
        self.session_tracker = SessionTracker()
        self.vehicle_session_tracker = VehicleSessionTracker()
        self.fleet_session_tracker = FleetSessionTracker()
        self.price_statistics: Optional[PriceStatistics] = None
        self.smart_charging: Optional[SmartChargingController] = None

        # Track last poll times
        self.last_vitals: Dict[str, datetime] = {}
        self.last_lifetime: Dict[str, datetime] = {}
        self.last_version: Dict[str, datetime] = {}
        self.last_wifi: Dict[str, datetime] = {}
        self.last_comed: Optional[datetime] = None
        self.last_tessie: Optional[datetime] = None
        self.last_fleet_twc: Optional[datetime] = None

        # Tessie vehicle tracking
        self.tessie_vehicles: Dict[str, TessieVehicle] = {}  # VIN -> Vehicle

        # Fleet API Wall Connector tracking
        self.fleet_energy_site_id: Optional[str] = None
        self.fleet_wall_connectors: Dict[str, FleetWallConnector] = {}  # DIN -> WallConnector

        # Fleet API charge history tracking
        self.last_fleet_charge_history: Optional[datetime] = None
        self.fleet_charge_history_poll_interval: int = settings.fleet_charge_history_interval
        self.vehicle_target_map: Dict[str, str] = {}  # target_id -> vehicle_name mapping

        # Opower (meter data) tracking
        self.last_opower: Optional[datetime] = None
        self.last_opower_token_refresh: Optional[datetime] = None
        self.last_opower_cache_check: Optional[datetime] = None
        self.opower_authenticated: bool = False
        self.opower_expiry_warned: bool = False  # Track if we've warned about expiry
        self.opower_refresh_failures: int = 0  # Consecutive refresh failures

        # Recent completed sessions for correlation (TWC and vehicle)
        # Dict: charger_name -> {end_time, energy_kwh, ...}
        self.recent_twc_sessions: Dict[str, dict] = {}
        # Dict: vin -> VehicleChargingSession
        self.recent_vehicle_sessions: Dict[str, VehicleChargingSession] = {}

    async def start(self):
        """Start the collector service."""
        logger.info("=" * 60)
        logger.info("Tesla Wall Connector Dashboard - Collector Service")
        logger.info("=" * 60)

        # Initialize clients
        self.influx_writer = InfluxWriter()
        self.price_statistics = PriceStatistics(self.influx_writer)
        logger.info(f"Connected to InfluxDB at {settings.influxdb_url}")

        # Initialize Wall Connector clients
        for charger in settings.chargers:
            self.twc_clients[charger.name] = TWCClient(charger)
            logger.info(f"Configured charger: {charger.name} at {charger.ip}")

        if not self.twc_clients:
            logger.error("No chargers configured! Set TWC_CHARGERS environment variable.")
            return

        # Initialize ComEd client if enabled
        if settings.comed_enabled:
            self.comed_client = ComEdClient()
            logger.info("ComEd Hourly Pricing enabled")

        # Initialize Tessie client if enabled and token available
        if settings.tessie_enabled and settings.tessie_access_token:
            self.tessie_client = TessieClient(settings.tessie_access_token)
            logger.info("Tessie vehicle integration enabled")

            # Initialize smart charging controller if Tessie is available
            if settings.smart_charging_enabled:
                self.smart_charging = SmartChargingController(
                    tessie_client=self.tessie_client,
                    price_statistics=self.price_statistics,
                    influx_writer=self.influx_writer
                )
                mode = "LIVE" if settings.smart_charging_control_enabled else "DRY-RUN"
                logger.info(
                    f"Smart charging enabled ({mode} MODE): "
                    f"stop > {settings.smart_charging_stop_percentile}th %ile, "
                    f"resume < {settings.smart_charging_resume_percentile}th %ile"
                )
                if not settings.smart_charging_control_enabled:
                    logger.info(
                        "  ⚡ DRY-RUN: Actions will be logged but NOT sent to vehicle. "
                        "Set SMART_CHARGING_CONTROL_ENABLED=true to enable control."
                    )
            else:
                logger.info("Smart charging disabled")

            # Initialize Fleet API for Wall Connectors if energy_site_id configured
            if settings.fleet_energy_site_id:
                self.fleet_energy_site_id = settings.fleet_energy_site_id
                logger.info(f"Fleet API Wall Connector polling enabled (site: {self.fleet_energy_site_id})")
            else:
                # Try to auto-discover energy site ID
                logger.info("Fleet API: No energy_site_id configured, will attempt auto-discovery...")

        elif settings.tessie_enabled:
            logger.warning("Tessie enabled but no access token found in .secrets file")

        # Initialize Opower client if enabled and credentials available
        if settings.opower_enabled:
            if settings.opower_username and settings.opower_password:
                self.opower_client = OpowerClient(
                    username=settings.opower_username,
                    password=settings.opower_password,
                    mfa_method=settings.opower_mfa_method
                )
                logger.info("ComEd Opower meter data integration enabled")
            elif settings.opower_bearer_token:
                # Pre-authenticated token mode - will validate on startup
                self.opower_client = OpowerClient(
                    username="",  # Not needed with bearer token
                    password="",
                )
                self.opower_client.opower_token = settings.opower_bearer_token
                # Parse actual expiry from JWT token if possible
                try:
                    import base64
                    import json
                    token = settings.opower_bearer_token
                    if token.startswith("Bearer "):
                        token = token[7:]
                    parts = token.split(".")
                    if len(parts) >= 2:
                        # Add padding for base64 decoding
                        payload_b64 = parts[1] + "=="
                        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                        exp = payload.get("exp")
                        if exp:
                            self.opower_client.token_expiry = datetime.fromtimestamp(exp, tz=timezone.utc)
                            time_remaining = (self.opower_client.token_expiry - datetime.now(timezone.utc)).total_seconds()
                            if time_remaining > 0:
                                logger.info(f"ComEd Opower: Bearer token valid for {time_remaining/60:.1f} minutes")
                            else:
                                logger.warning("ComEd Opower: Bearer token has expired")
                except Exception:
                    # If we can't parse expiry, assume 20 minutes (typical Opower token lifetime)
                    self.opower_client.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=20)
                self.opower_authenticated = True
                logger.info("ComEd Opower enabled with bearer token (token keep-alive active)")
            else:
                # No credentials configured - create client and watch for cache file
                # The hot-reload detection will check for .comed_opower_cache.json every 30 seconds
                self.opower_client = OpowerClient(
                    username="",
                    password="",
                )
                logger.info("OPOWER: Enabled - will check for cached session during bootstrap")

        logger.info("-" * 60)
        logger.info(f"Local TWC API: {'ENABLED' if settings.local_twc_enabled else 'DISABLED (using Fleet API only)'}")
        if settings.local_twc_enabled:
            logger.info(f"Vitals polling interval: {settings.twc_poll_vitals_interval}s")
            logger.info(f"Lifetime polling interval: {settings.twc_poll_lifetime_interval}s")
            logger.info(f"Version polling interval: {settings.twc_poll_version_interval}s")
            logger.info(f"WiFi polling interval: {settings.twc_poll_wifi_interval}s")
        if settings.comed_enabled:
            logger.info(f"ComEd polling interval: {settings.comed_poll_interval}s")
        if self.tessie_client:
            logger.info(f"Tessie polling interval: {settings.tessie_poll_interval}s")
            if self.fleet_energy_site_id or not settings.fleet_energy_site_id:
                logger.info(f"Fleet TWC polling interval: {settings.fleet_twc_poll_interval}s")
        if self.opower_client:
            logger.info(f"Opower polling interval: {settings.opower_poll_interval}s")
        logger.info("-" * 60)

        self.running = True

        # Run initial data fetch
        await self._fetch_all_initial()

        # Main polling loop
        await self._run_polling_loop()

    async def stop(self):
        """Stop the collector service."""
        logger.info("Stopping collector service...")
        self.running = False

        # Close clients
        for client in self.twc_clients.values():
            await client.close()

        if self.comed_client:
            await self.comed_client.close()

        if self.tessie_client:
            await self.tessie_client.close()

        if self.opower_client:
            await self.opower_client.close()

        if self.influx_writer:
            self.influx_writer.close()

        logger.info("Collector service stopped")

    async def _fetch_all_initial(self):
        """Fetch all data on startup."""
        logger.info("Fetching initial data from all sources...")

        # Fetch all charger data
        for name, client in self.twc_clients.items():
            charger = client.charger

            # Get all endpoints
            data = await client.get_all()

            if data["vitals"]:
                self.influx_writer.write_vitals(charger, data["vitals"])
                logger.info(f"[{name}] Initial vitals: grid={data['vitals'].grid_v}V, "
                           f"vehicle_connected={data['vitals'].vehicle_connected}")

            if data["lifetime"]:
                self.influx_writer.write_lifetime(charger, data["lifetime"])
                logger.info(f"[{name}] Lifetime: {data['lifetime'].energy_kwh:.1f} kWh, "
                           f"{data['lifetime'].charge_starts} sessions")

            if data["version"]:
                self.influx_writer.write_version(charger, data["version"])
                logger.info(f"[{name}] Firmware: {data['version'].firmware_version}")

            if data["wifi_status"]:
                self.influx_writer.write_wifi_status(charger, data["wifi_status"])
                logger.info(f"[{name}] WiFi: signal={data['wifi_status'].wifi_signal_strength}%")

            now = datetime.now(timezone.utc)
            self.last_vitals[name] = now
            self.last_lifetime[name] = now
            self.last_version[name] = now
            self.last_wifi[name] = now

        # Fetch ComEd prices
        if self.comed_client:
            # Get hourly average (for backwards compatibility)
            hourly_price = await self.comed_client.get_current_hour_average()
            if hourly_price:
                self.influx_writer.write_comed_price(hourly_price, "hourly_avg")
                logger.info(f"ComEd hourly avg: {hourly_price.price_cents}¢/kWh")

            # Get last 24 hours of 5-minute prices
            prices = await self.comed_client.get_5minute_prices()
            if prices:
                self.influx_writer.write_comed_prices_batch(prices, "5min")
                # Use the most recent 5-minute price for real-time decisions
                latest_5min_price = prices[0].price_cents
                self.session_tracker.set_current_price(latest_5min_price)
                self.fleet_session_tracker.set_current_price(latest_5min_price)
                self.fleet_session_tracker.set_delivery_rate(settings.comed_delivery_per_kwh * 100)
                logger.info(f"ComEd 5-min price: {latest_5min_price}¢/kWh (loaded {len(prices)} historical prices)")

            self.last_comed = datetime.now(timezone.utc)

            # Bootstrap historical price data for smart charging statistics
            # This backfills up to 30 days of data if needed
            await self._bootstrap_price_history()

            # Calculate initial price statistics
            if self.price_statistics:
                stats = self.price_statistics.get_statistics(
                    lookback_days=settings.smart_charging_lookback_days,
                    force_recalculate=True
                )
                if stats:
                    logger.info(
                        f"Smart charging thresholds: "
                        f"stop > {stats['p' + str(settings.smart_charging_stop_percentile)]:.2f}¢ "
                        f"({settings.smart_charging_stop_percentile}th %ile), "
                        f"resume < {stats['p' + str(settings.smart_charging_resume_percentile)]:.2f}¢ "
                        f"({settings.smart_charging_resume_percentile}th %ile)"
                    )

        # Fetch Tessie vehicle data
        if self.tessie_client:
            await self._fetch_tessie_initial()

            # Fetch Fleet API Wall Connector data
            await self._fetch_fleet_twc_initial()

        # Fetch Opower meter data (if authenticated)
        if self.opower_client:
            await self._fetch_opower_initial()

        logger.info("Initial data fetch complete")

    async def _fetch_tessie_initial(self):
        """Fetch initial Tessie vehicle data."""
        logger.info("Fetching Tessie vehicle data...")

        try:
            # Get all active vehicles
            vehicles = await self.tessie_client.get_vehicles(only_active=True)

            if not vehicles:
                logger.warning("No active vehicles found in Tessie account")
                return

            logger.info(f"Found {len(vehicles)} active vehicles in Tessie:")

            for vehicle in vehicles:
                self.tessie_vehicles[vehicle.vin] = vehicle
                logger.info(
                    f"  - {vehicle.display_name} ({vehicle.model_name}): "
                    f"{vehicle.battery_level}% SOC, {vehicle.charging_state}"
                )

                # Write to InfluxDB
                self.influx_writer.write_vehicle_state(vehicle)

                # If charging, write detailed charge state
                if vehicle.is_charging or vehicle.is_connected:
                    self.influx_writer.write_vehicle_charge_state(vehicle)

            self.last_tessie = datetime.now(timezone.utc)

        except Exception as e:
            logger.error(f"Error fetching Tessie data: {e}")

    async def _fetch_fleet_twc_initial(self):
        """Fetch initial Fleet API Wall Connector data.

        Also performs auto-discovery of energy_site_id if not configured.
        """
        if not self.tessie_client:
            return

        try:
            # Auto-discover energy site ID if not configured
            if not self.fleet_energy_site_id:
                logger.info("Fleet API: Discovering energy sites...")
                site_ids = await self.tessie_client.get_energy_site_ids()

                if site_ids:
                    # Use the first site found
                    self.fleet_energy_site_id = site_ids[0]
                    logger.info(f"Fleet API: Auto-discovered energy site: {self.fleet_energy_site_id}")

                    if len(site_ids) > 1:
                        logger.warning(
                            f"Fleet API: Multiple energy sites found: {site_ids}. "
                            f"Using first one. Set FLEET_ENERGY_SITE_ID in .env to specify."
                        )
                else:
                    logger.info("Fleet API: No energy sites found (no Wall Connectors registered)")
                    return

            # Fetch Wall Connector status
            logger.info("Fetching Fleet API Wall Connector data...")
            status = await self.tessie_client.get_energy_site_live_status(self.fleet_energy_site_id)

            if not status or not status.wall_connectors:
                logger.warning("Fleet API: No Wall Connectors found in live_status")
                return

            logger.info(f"Fleet API: Found {len(status.wall_connectors)} Wall Connectors:")

            for wc in status.wall_connectors:
                # Store in our tracking dict
                self.fleet_wall_connectors[wc.din] = wc

                # Get friendly name from config
                unit_friendly_name = settings.get_twc_friendly_name(wc.din, wc.unit_number)

                # Resolve vehicle name from VIN (config takes priority, then Tessie API)
                vehicle_name = None
                if wc.vin:
                    tessie_name = self.tessie_vehicles[wc.vin].display_name if wc.vin in self.tessie_vehicles else ""
                    vehicle_name = settings.get_vehicle_friendly_name(wc.vin, tessie_name)

                # Log details
                status_str = f"{wc.power_kw:.1f}kW" if wc.is_charging else wc.state_name
                vehicle_str = f" ({vehicle_name})" if vehicle_name else (f" (VIN: ...{wc.vin[-6:]})" if wc.vin else "")
                logger.info(f"  - {unit_friendly_name} ({wc.serial_number}): {status_str}{vehicle_str}")

                # Write to InfluxDB with friendly name and vehicle name
                self.influx_writer.write_fleet_wall_connector(
                    wc, self.fleet_energy_site_id,
                    unit_friendly_name=unit_friendly_name,
                    vehicle_name=vehicle_name
                )

            self.last_fleet_twc = datetime.now(timezone.utc)

            # Bootstrap charge history
            await self._bootstrap_fleet_charge_history()

        except Exception as e:
            logger.error(f"Error fetching Fleet API Wall Connector data: {e}")

    async def _bootstrap_fleet_charge_history(self):
        """Bootstrap historical charge session data from Fleet API.

        On startup, fetches historical charging sessions and imports them
        into InfluxDB. This provides complete charge history for all
        Wall Connectors (including followers).
        """
        if not self.tessie_client or not self.fleet_energy_site_id:
            return

        logger.info("-" * 60)
        logger.info("Fleet Charge History Bootstrap")

        try:
            # Check if we have any existing charge history data
            latest_session_time = self.influx_writer.get_latest_fleet_charge_session_time(
                self.fleet_energy_site_id
            )

            if latest_session_time:
                # We have existing data - fetch only newer sessions
                latest_dt = datetime.fromtimestamp(latest_session_time, tz=timezone.utc)
                logger.info(f"  Last session: {latest_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                logger.info("  Fetching new sessions since then...")

                sessions = await self.tessie_client.get_charge_sessions_since(
                    self.fleet_energy_site_id,
                    latest_session_time
                )
            else:
                # No existing data - fetch historical sessions (last 30 days)
                logger.info("  No existing charge history - fetching last 30 days...")

                end_date = datetime.now().strftime("%Y-%m-%d")
                start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

                sessions = await self.tessie_client.get_charge_sessions(
                    self.fleet_energy_site_id,
                    start_date=start_date,
                    end_date=end_date
                )

            if sessions:
                # Filter out sessions we already have
                new_sessions = []
                for session in sessions:
                    if not self.influx_writer.has_fleet_charge_session(session, self.fleet_energy_site_id):
                        new_sessions.append(session)

                if new_sessions:
                    # Calculate costs for each session using historical ComEd prices
                    logger.info(f"  Calculating costs for {len(new_sessions)} sessions...")
                    sessions_with_costs = 0
                    for session in new_sessions:
                        self._calculate_session_costs(session)
                        if session.full_cost_cents is not None:
                            sessions_with_costs += 1

                    logger.info(f"  Cost data available for {sessions_with_costs}/{len(new_sessions)} sessions")

                    # Build vehicle name mapping from Tessie vehicles
                    vehicle_map = self._build_vehicle_target_map()

                    # Write sessions to InfluxDB
                    self.influx_writer.write_fleet_charge_sessions_batch(
                        new_sessions,
                        self.fleet_energy_site_id,
                        vehicle_map
                    )

                    # Log summary
                    total_energy = sum(s.energy_kwh for s in new_sessions)
                    total_cost = sum(s.full_cost_cents or 0 for s in new_sessions) / 100.0
                    logger.info(f"  Imported {len(new_sessions)} charge sessions")
                    logger.info(f"  Total energy: {total_energy:.1f} kWh")
                    if total_cost > 0:
                        logger.info(f"  Total cost: ${total_cost:.2f}")

                    # Log per-unit breakdown
                    by_unit = {}
                    for s in new_sessions:
                        unit = s.unit_name
                        if unit not in by_unit:
                            by_unit[unit] = {"count": 0, "energy": 0, "cost": 0}
                        by_unit[unit]["count"] += 1
                        by_unit[unit]["energy"] += s.energy_kwh
                        by_unit[unit]["cost"] += (s.full_cost_cents or 0) / 100.0

                    for unit, data in by_unit.items():
                        cost_str = f", ${data['cost']:.2f}" if data['cost'] > 0 else ""
                        logger.info(f"    - {unit}: {data['count']} sessions, {data['energy']:.1f} kWh{cost_str}")
                else:
                    logger.info("  No new sessions to import")
            else:
                logger.info("  No charge sessions found in Fleet API")

            self.last_fleet_charge_history = datetime.now(timezone.utc)
            logger.info("-" * 60)

        except Exception as e:
            logger.error(f"Error bootstrapping fleet charge history: {e}")
            logger.info("-" * 60)

    def _build_vehicle_target_map(self) -> Dict[str, str]:
        """Build a mapping of vehicle target_ids to display names.

        The Fleet API uses target_id (a UUID) to identify vehicles.
        This method attempts to map those to friendly vehicle names.

        Note: The target_id in Fleet API doesn't directly match VIN,
        so we may need to use other methods to correlate them.
        For now, we track what we can from live_status VINs.
        """
        # Try to build map from Wall Connector data (VIN when connected)
        # This is a best-effort approach
        vehicle_map = {}

        # Add from Tessie vehicles (using VIN as a fallback identifier)
        for vin, vehicle in self.tessie_vehicles.items():
            if vehicle.display_name:
                # Note: target_id != VIN, but we can use display name if we
                # see the same vehicle charging
                vehicle_map[vin] = vehicle.display_name

        # Also store in instance for future use
        self.vehicle_target_map = vehicle_map
        return vehicle_map

    def _calculate_session_costs(self, session: FleetChargeSession) -> FleetChargeSession:
        """Calculate costs for a fleet charge session using historical prices.

        Looks up the average ComEd price during the session's time window
        and calculates supply cost, delivery cost, and full cost.

        Args:
            session: FleetChargeSession to calculate costs for

        Returns:
            Same session with cost fields populated
        """
        # Get average price during this session
        avg_price = self.influx_writer.get_average_price_for_period(
            session.start_time,
            session.end_time
        )

        if avg_price is not None:
            # Calculate costs
            session.avg_price_cents = avg_price
            session.supply_cost_cents = avg_price * session.energy_kwh

            # Delivery rate from config (in dollars/kWh, convert to cents)
            delivery_rate_cents = settings.comed_delivery_per_kwh * 100
            session.delivery_cost_cents = delivery_rate_cents * session.energy_kwh

            session.full_cost_cents = session.supply_cost_cents + session.delivery_cost_cents

        return session

    async def _run_polling_loop(self):
        """Main polling loop."""
        logger.info("Starting polling loop...")

        while self.running:
            now = datetime.now(timezone.utc)
            tasks = []

            # Local TWC API polling (legacy - can be disabled if using Fleet API only)
            if settings.local_twc_enabled:
                # Check each charger
                for name, client in self.twc_clients.items():
                    charger = client.charger

                    # Vitals polling
                    last = self.last_vitals.get(name)
                    if last is None or (now - last).total_seconds() >= settings.twc_poll_vitals_interval:
                        tasks.append(self._poll_vitals(name, client, charger))
                        self.last_vitals[name] = now

                    # Lifetime polling
                    last = self.last_lifetime.get(name)
                    if last is None or (now - last).total_seconds() >= settings.twc_poll_lifetime_interval:
                        tasks.append(self._poll_lifetime(name, client, charger))
                        self.last_lifetime[name] = now

                    # Version polling (infrequent)
                    last = self.last_version.get(name)
                    if last is None or (now - last).total_seconds() >= settings.twc_poll_version_interval:
                        tasks.append(self._poll_version(name, client, charger))
                        self.last_version[name] = now

                    # WiFi polling
                    last = self.last_wifi.get(name)
                    if last is None or (now - last).total_seconds() >= settings.twc_poll_wifi_interval:
                        tasks.append(self._poll_wifi(name, client, charger))
                        self.last_wifi[name] = now

            # ComEd polling
            if self.comed_client:
                if self.last_comed is None or (now - self.last_comed).total_seconds() >= settings.comed_poll_interval:
                    tasks.append(self._poll_comed())
                    self.last_comed = now

            # Tessie polling
            if self.tessie_client:
                if self.last_tessie is None or (now - self.last_tessie).total_seconds() >= settings.tessie_poll_interval:
                    tasks.append(self._poll_tessie())
                    self.last_tessie = now

            # Fleet API Wall Connector polling
            if self.tessie_client and self.fleet_energy_site_id:
                if self.last_fleet_twc is None or (now - self.last_fleet_twc).total_seconds() >= settings.fleet_twc_poll_interval:
                    tasks.append(self._poll_fleet_twc())
                    self.last_fleet_twc = now

                # Fleet API charge history polling (sessions delayed by hours/days)
                if self.last_fleet_charge_history is None or (now - self.last_fleet_charge_history).total_seconds() >= self.fleet_charge_history_poll_interval:
                    tasks.append(self._poll_fleet_charge_history())
                    self.last_fleet_charge_history = now

            # Opower polling (meter data - hourly by default)
            if self.opower_client:
                if self.opower_authenticated:
                    # Token keep-alive refresh (every 10 min by default)
                    if self.last_opower_token_refresh is None or (now - self.last_opower_token_refresh).total_seconds() >= settings.opower_token_refresh_interval:
                        tasks.append(self._refresh_opower_token())
                        self.last_opower_token_refresh = now

                    # Data polling (hourly by default)
                    if self.last_opower is None or (now - self.last_opower).total_seconds() >= settings.opower_poll_interval:
                        tasks.append(self._poll_opower())
                        self.last_opower = now
                else:
                    # Not authenticated - check for cache file every 30 seconds
                    if self.last_opower_cache_check is None or (now - self.last_opower_cache_check).total_seconds() >= 30:
                        tasks.append(self._check_opower_cache())
                        self.last_opower_cache_check = now

            # Execute all pending polls concurrently
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            # Sleep for 1 second between loop iterations
            await asyncio.sleep(1)

    async def _poll_vitals(self, name: str, client: TWCClient, charger: ChargerConfig):
        """Poll vitals for a charger."""
        vitals = await client.get_vitals()
        if vitals:
            self.influx_writer.write_vitals(charger, vitals)

            # Track sessions
            session_ended = self.session_tracker.update(name, vitals)
            if session_ended:
                # Write completed session to InfluxDB
                self.influx_writer.write_session(charger, session_ended)

                # Store for correlation with vehicle sessions
                self.recent_twc_sessions[name] = session_ended.copy()
                self._try_correlate_sessions(charger_name=name)

            # Write current session state for real-time dashboard
            current_session = self.session_tracker.get_current_session(name)
            if current_session:
                self.influx_writer.write_session_state(charger, current_session)
                logger.info(f"[{name}] Charging: {vitals.power_w/1000:.1f}kW, "
                           f"{current_session['energy_wh']/1000:.2f}kWh, "
                           f"cost: ${current_session['full_cost_cents']/100:.2f}")

    async def _poll_lifetime(self, name: str, client: TWCClient, charger: ChargerConfig):
        """Poll lifetime stats for a charger."""
        lifetime = await client.get_lifetime()
        if lifetime:
            self.influx_writer.write_lifetime(charger, lifetime)

    async def _poll_version(self, name: str, client: TWCClient, charger: ChargerConfig):
        """Poll version info for a charger."""
        version = await client.get_version()
        if version:
            self.influx_writer.write_version(charger, version)

    async def _poll_wifi(self, name: str, client: TWCClient, charger: ChargerConfig):
        """Poll WiFi status for a charger."""
        wifi = await client.get_wifi_status()
        if wifi:
            self.influx_writer.write_wifi_status(charger, wifi)

    def _try_correlate_sessions(self, charger_name: str = None, vin: str = None):
        """Try to correlate TWC and vehicle sessions that ended around the same time.

        This is called when either a TWC session or vehicle session completes.
        We look for matching sessions that ended within the correlation window.
        """
        now = datetime.now(timezone.utc)

        # Clean up old sessions (older than correlation window)
        for cname in list(self.recent_twc_sessions.keys()):
            session = self.recent_twc_sessions[cname]
            age = (now - session["end_time"]).total_seconds()
            if age > self.SESSION_CORRELATION_WINDOW * 2:
                del self.recent_twc_sessions[cname]

        for v in list(self.recent_vehicle_sessions.keys()):
            session = self.recent_vehicle_sessions[v]
            age = (now - session.end_time).total_seconds()
            if age > self.SESSION_CORRELATION_WINDOW * 2:
                del self.recent_vehicle_sessions[v]

        # Try to find correlations
        # For each TWC session, find vehicle sessions that overlap in time
        for cname, twc_session in list(self.recent_twc_sessions.items()):
            twc_start = twc_session["start_time"]
            twc_end = twc_session["end_time"]
            twc_energy_kwh = twc_session["energy_wh"] / 1000.0

            for v, vehicle_session in list(self.recent_vehicle_sessions.items()):
                # Check if sessions overlap (with some tolerance)
                veh_start = vehicle_session.start_time
                veh_end = vehicle_session.end_time

                # Sessions should start within the correlation window of each other
                start_diff = abs((twc_start - veh_start).total_seconds())
                end_diff = abs((twc_end - veh_end).total_seconds())

                if start_diff <= self.SESSION_CORRELATION_WINDOW and end_diff <= self.SESSION_CORRELATION_WINDOW:
                    # Found a correlation!
                    vehicle_energy_kwh = vehicle_session.energy_added_kwh

                    logger.info(
                        f"Correlated sessions: TWC [{cname}] and Vehicle [{vehicle_session.display_name}]"
                    )

                    # Get the charger config
                    charger = None
                    for c in settings.chargers:
                        if c.name == cname:
                            charger = c
                            break

                    if charger and twc_energy_kwh > 0 and vehicle_energy_kwh > 0:
                        self.influx_writer.write_charging_efficiency(
                            charger=charger,
                            twc_energy_kwh=twc_energy_kwh,
                            vehicle_energy_kwh=vehicle_energy_kwh,
                            vehicle_display_name=vehicle_session.display_name,
                            vin=vehicle_session.vin,
                            start_time=twc_start,
                        )

                    # Remove both sessions from recent lists (already correlated)
                    del self.recent_twc_sessions[cname]
                    del self.recent_vehicle_sessions[v]
                    return  # Only correlate one pair at a time

    async def _bootstrap_price_history(self):
        """Bootstrap historical price data from ComEd API on startup.

        This method checks how much historical price data we have and backfills
        missing data from the ComEd API. The API returns 5-minute prices and
        supports date range queries, but is limited to ~1000 records per request
        (~3.5 days of 5-minute data).

        This ensures we have enough data for rolling statistics calculation
        (smart charging thresholds).
        """
        if not self.comed_client:
            return

        lookback_days = settings.smart_charging_lookback_days
        now = datetime.now(timezone.utc)
        target_start = now - timedelta(days=lookback_days)

        # Check how much data we already have
        days_available = self.influx_writer.get_price_data_days_available(lookback_days)
        oldest_data = self.influx_writer.get_oldest_price_data_time()

        logger.info("-" * 60)
        logger.info("Price History Bootstrap")
        logger.info(f"  Target: {lookback_days} days of price data")
        logger.info(f"  Available: {days_available} days")

        if oldest_data:
            oldest_days_ago = (now - oldest_data).days
            logger.info(f"  Oldest data: {oldest_days_ago} days ago ({oldest_data.strftime('%Y-%m-%d')})")

        # If we have enough data, skip bootstrap
        if days_available >= lookback_days:
            logger.info(f"  ✓ Sufficient price history ({days_available}/{lookback_days} days)")
            logger.info("-" * 60)
            return

        # Calculate how far back we need to go
        if oldest_data:
            # Start from where our data begins and go back
            end_time = oldest_data
        else:
            # No data at all - we already fetched 24h in initial fetch
            # Start from 24h ago and go further back
            end_time = now - timedelta(hours=24)

        logger.info(f"  Backfilling price data...")

        # Backfill in 3-day chunks (ComEd API limit ~1000 records = ~3.5 days of 5min data)
        chunk_days = 3
        total_records = 0
        chunks_fetched = 0
        max_chunks = (lookback_days // chunk_days) + 2  # Safety limit

        while chunks_fetched < max_chunks:
            chunk_end = end_time
            chunk_start = chunk_end - timedelta(days=chunk_days)

            # Don't go further back than our target
            if chunk_start < target_start:
                chunk_start = target_start

            # Check if we already have data for this period
            if self.influx_writer.has_price_data_for_period(chunk_start, chunk_end):
                logger.debug(f"  Skipping {chunk_start.strftime('%Y-%m-%d')} to {chunk_end.strftime('%Y-%m-%d')} - data exists")
                end_time = chunk_start
                chunks_fetched += 1
                if chunk_start <= target_start:
                    break
                continue

            # Fetch from ComEd API
            logger.info(f"  Fetching: {chunk_start.strftime('%Y-%m-%d %H:%M')} to {chunk_end.strftime('%Y-%m-%d %H:%M')}")

            try:
                prices = await self.comed_client.get_historical_prices(chunk_start, chunk_end)
                if prices:
                    self.influx_writer.write_comed_prices_batch(prices, "5min")
                    total_records += len(prices)
                    logger.info(f"    → {len(prices)} records stored")
                else:
                    logger.warning(f"    → No data returned for this period")

                # Small delay to be nice to the API
                await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"  Error fetching price history: {e}")
                # Continue to next chunk instead of failing completely
                await asyncio.sleep(1)

            end_time = chunk_start
            chunks_fetched += 1

            # Stop if we've reached our target
            if chunk_start <= target_start:
                break

        # Report final status
        days_now = self.influx_writer.get_price_data_days_available(lookback_days)
        logger.info(f"  Bootstrap complete: {total_records} records added")
        logger.info(f"  Price data coverage: {days_now}/{lookback_days} days")

        if days_now >= lookback_days:
            logger.info(f"  ✓ Full price history available for smart charging")
        elif days_now >= 7:
            logger.info(f"  ⚠ Partial history ({days_now} days) - statistics will improve over time")
        else:
            logger.warning(f"  ⚠ Limited history ({days_now} days) - smart charging statistics may be unreliable")

        logger.info("-" * 60)

    async def _poll_comed(self):
        """Poll ComEd prices."""
        # Get hourly average (for backwards compatibility)
        hourly_price = await self.comed_client.get_current_hour_average()
        if hourly_price:
            self.influx_writer.write_comed_price(hourly_price, "hourly_avg")

        # Get 5-minute prices - use latest for smart charging decisions
        prices = await self.comed_client.get_5minute_prices()
        if prices:
            self.influx_writer.write_comed_prices_batch(prices, "5min")
            # Use the most recent 5-minute price (first in list) for real-time decisions
            latest_5min_price = prices[0].price_cents
            self.session_tracker.set_current_price(latest_5min_price)
            self.fleet_session_tracker.set_current_price(latest_5min_price)
            logger.info(f"ComEd price: {latest_5min_price}¢/kWh (5-min)")

    async def _poll_tessie(self):
        """Poll Tessie for vehicle data."""
        if not self.tessie_vehicles:
            logger.warning("Tessie: No vehicles to poll")
            return

        try:
            # Poll each known vehicle
            for vin in list(self.tessie_vehicles.keys()):
                vehicle = await self.tessie_client.get_vehicle_state(vin)

                if vehicle:
                    # Update our cached state
                    old_vehicle = self.tessie_vehicles.get(vin)
                    self.tessie_vehicles[vin] = vehicle

                    # Write to InfluxDB
                    self.influx_writer.write_vehicle_state(vehicle)

                    # Log vehicle state (always log for visibility)
                    name = vehicle.display_name or f"VIN ...{vin[-6:]}"
                    logger.info(
                        f"[{name}] {vehicle.state}: "
                        f"{vehicle.battery_level or 0}% SOC, {vehicle.charging_state}"
                    )

                    # Log charging status changes
                    if old_vehicle:
                        if vehicle.is_charging and not old_vehicle.is_charging:
                            logger.info(
                                f"[{name}] Started charging: "
                                f"{vehicle.charger_power}kW at {vehicle.charge_amps}A"
                            )
                        elif not vehicle.is_charging and old_vehicle.is_charging:
                            logger.info(
                                f"[{name}] Stopped charging: "
                                f"{vehicle.charge_energy_added}kWh added, "
                                f"now at {vehicle.battery_level}%"
                            )

                    # Track vehicle charging sessions
                    completed_session = self.vehicle_session_tracker.update(vehicle)
                    if completed_session:
                        # Write completed vehicle session to InfluxDB
                        self.influx_writer.write_vehicle_session(completed_session)

                        # Store for correlation with TWC sessions
                        self.recent_vehicle_sessions[vin] = completed_session
                        self._try_correlate_sessions(vin=vin)

                    # Write current vehicle session state for real-time dashboard
                    current_vehicle_session = self.vehicle_session_tracker.get_current_session(vin)
                    if current_vehicle_session:
                        self.influx_writer.write_vehicle_session_state(current_vehicle_session)

                    # If charging or connected, write detailed charge state
                    if vehicle.is_charging or vehicle.is_connected:
                        self.influx_writer.write_vehicle_charge_state(vehicle)

                        # Log charging progress
                        if vehicle.is_charging:
                            logger.info(
                                f"[{name}] Charging: "
                                f"{vehicle.battery_level}% SOC, {vehicle.charger_power}kW, "
                                f"{vehicle.charge_energy_added:.1f}kWh added, "
                                f"{vehicle.time_to_full_charge:.1f}h remaining"
                            )

                    # Write battery health metrics (if available via Fleet Telemetry)
                    self.influx_writer.write_battery_health(vehicle)

                    # Smart charging evaluation
                    if self.smart_charging and self.smart_charging.enabled:
                        current_price = self.session_tracker.current_price_cents
                        if current_price > 0:
                            # Evaluate and potentially take action
                            action = await self.smart_charging.evaluate_and_act(
                                vin=vin,
                                display_name=name,
                                is_charging=vehicle.is_charging,
                                current_price_cents=current_price
                            )

                            # Write smart charging state for dashboard
                            self.smart_charging.write_state(vin, name, current_price)

                else:
                    logger.warning(f"Tessie: No data returned for VIN ...{vin[-6:]}")

        except Exception as e:
            logger.error(f"Error polling Tessie: {e}")

    async def _poll_fleet_twc(self):
        """Poll Fleet API for Wall Connector data.

        This provides real-time data for all Wall Connectors in a power-sharing
        setup, including follower units that cannot be accessed via local API.
        """
        if not self.fleet_energy_site_id:
            return

        try:
            status = await self.tessie_client.get_energy_site_live_status(self.fleet_energy_site_id)

            if not status or not status.wall_connectors:
                logger.debug("Fleet API: No Wall Connectors in response")
                return

            total_power = 0.0
            charging_count = 0

            for wc in status.wall_connectors:
                # Update our tracking dict
                old_wc = self.fleet_wall_connectors.get(wc.din)
                self.fleet_wall_connectors[wc.din] = wc

                # Get friendly names
                unit_friendly_name = settings.get_twc_friendly_name(wc.din, wc.unit_number)
                vehicle_name = None
                if wc.vin:
                    tessie_name = self.tessie_vehicles[wc.vin].display_name if wc.vin in self.tessie_vehicles else ""
                    vehicle_name = settings.get_vehicle_friendly_name(wc.vin, tessie_name)

                # Write to InfluxDB with friendly name
                self.influx_writer.write_fleet_wall_connector(
                    wc, self.fleet_energy_site_id,
                    unit_friendly_name=unit_friendly_name,
                    vehicle_name=vehicle_name
                )

                # Track totals
                total_power += wc.wall_connector_power
                if wc.is_charging:
                    charging_count += 1

                # Update session tracker (integrates power to calculate energy)
                completed_session = self.fleet_session_tracker.update(wc)
                if completed_session:
                    # A session just ended - write to InfluxDB if meets thresholds
                    energy_kwh = completed_session["energy_wh"] / 1000.0
                    duration_s = completed_session["duration_s"]

                    # Check minimum thresholds (filter out brief plug-ins)
                    min_energy = settings.fleet_session_min_energy_kwh
                    min_duration = settings.fleet_session_min_duration_s

                    if energy_kwh >= min_energy and duration_s >= min_duration:
                        # Write completed session to InfluxDB
                        self.influx_writer.write_fleet_session_from_live_status(
                            completed_session,
                            self.fleet_energy_site_id,
                            vehicle_name=vehicle_name
                        )
                    else:
                        logger.debug(
                            f"[Fleet {unit_friendly_name}] Session below threshold: "
                            f"{energy_kwh:.2f} kWh, {duration_s:.0f}s "
                            f"(min: {min_energy} kWh, {min_duration}s)"
                        )

                # Write current session state to InfluxDB for real-time display
                current_session = self.fleet_session_tracker.get_current_session(wc.din)
                if current_session:
                    self.influx_writer.write_fleet_session_state(
                        wc.din,
                        unit_friendly_name,
                        current_session,
                        self.fleet_energy_site_id,
                        vehicle_name=vehicle_name
                    )

                # Log state changes
                if old_wc:
                    # Check for significant changes
                    was_charging = old_wc.is_charging
                    is_now_charging = wc.is_charging

                    if is_now_charging and not was_charging:
                        vehicle_str = f" ({vehicle_name})" if vehicle_name else (f" (VIN: ...{wc.vin[-6:]})" if wc.vin else "")
                        logger.info(
                            f"[{unit_friendly_name}] Started charging: "
                            f"{wc.power_kw:.1f}kW{vehicle_str}"
                        )
                    elif not is_now_charging and was_charging:
                        logger.info(f"[{unit_friendly_name}] Stopped charging")

            # Log summary if any charging
            if charging_count > 0:
                logger.info(
                    f"[Fleet TWC] {charging_count} charging, "
                    f"total power: {total_power/1000:.1f}kW"
                )

        except Exception as e:
            logger.error(f"Error polling Fleet API Wall Connectors: {e}")

    async def _poll_fleet_charge_history(self):
        """Poll Fleet API for new charge sessions.

        This runs hourly to check for new completed charging sessions.
        Sessions are fetched incrementally from the last known session time.
        """
        if not self.fleet_energy_site_id:
            return

        try:
            # Get the timestamp of our most recent session
            latest_session_time = self.influx_writer.get_latest_fleet_charge_session_time(
                self.fleet_energy_site_id
            )

            if latest_session_time:
                # Fetch sessions since our last known session
                sessions = await self.tessie_client.get_charge_sessions_since(
                    self.fleet_energy_site_id,
                    latest_session_time
                )
            else:
                # No existing data - fetch last 7 days
                end_date = datetime.now().strftime("%Y-%m-%d")
                start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

                sessions = await self.tessie_client.get_charge_sessions(
                    self.fleet_energy_site_id,
                    start_date=start_date,
                    end_date=end_date
                )

            if sessions:
                # Filter out sessions we already have
                new_sessions = []
                for session in sessions:
                    if not self.influx_writer.has_fleet_charge_session(session, self.fleet_energy_site_id):
                        new_sessions.append(session)

                if new_sessions:
                    # Calculate costs for each session
                    for session in new_sessions:
                        self._calculate_session_costs(session)

                    # Get vehicle name mapping
                    vehicle_map = self._build_vehicle_target_map()

                    # Write to InfluxDB
                    self.influx_writer.write_fleet_charge_sessions_batch(
                        new_sessions,
                        self.fleet_energy_site_id,
                        vehicle_map
                    )

                    # Log summary
                    total_energy = sum(s.energy_kwh for s in new_sessions)
                    total_cost = sum(s.full_cost_cents or 0 for s in new_sessions) / 100.0
                    cost_str = f", ${total_cost:.2f}" if total_cost > 0 else ""
                    logger.info(
                        f"[Fleet TWC] Imported {len(new_sessions)} new charge sessions "
                        f"({total_energy:.1f} kWh{cost_str})"
                    )

        except Exception as e:
            logger.error(f"Error polling Fleet API charge history: {e}")

    # =========================================================================
    # Opower (ComEd Meter Data) Methods
    # =========================================================================

    async def _fetch_opower_initial(self):
        """Fetch initial Opower meter data on startup.

        This attempts to authenticate and bootstrap historical data.
        Note: Initial authentication requires MFA - use comed_auth.py for first-time setup.
        """
        logger.info("-" * 60)
        logger.info("ComEd Opower Meter Data Bootstrap")

        try:
            # Try to authenticate (will use cached token if available)
            await self.opower_client.connect()

            if self.opower_authenticated:
                # Already authenticated via bearer token in .secrets
                # Also try to load cached session which has cookies for token refresh
                if self.opower_client._load_cache():
                    logger.info("  Using bearer token with cached session (can refresh)")
                else:
                    logger.info("  Using bearer token (cannot refresh - run setup script for persistent session)")
                    logger.info("    docker-compose run --rm collector python scripts/comed_opower_setup.py")
            else:
                # Try to load cached session
                if self.opower_client._load_cache():
                    self.opower_authenticated = True
                    logger.info("  Loaded cached session successfully")
                else:
                    logger.info("=" * 60)
                    logger.info("OPOWER: No cached session found")
                    logger.info("  To authenticate, run locally:")
                    logger.info("    pip install httpx")
                    logger.info("    python scripts/comed_opower_setup.py")
                    logger.info("  Then copy .comed_opower_cache.json to your server.")
                    logger.info("  The collector will auto-detect within 30 seconds.")
                    logger.info("  See docs/COMED_OPOWER_SETUP.md for details.")
                    logger.info("=" * 60)
                    logger.info("-" * 60)
                    return

            # Get account metadata
            metadata = await self.opower_client.get_metadata()
            if metadata:
                logger.info(f"  Rate plan: {metadata.rate_plan}")
                logger.info(f"  Data resolution: {metadata.read_resolution}")
                if metadata.available_data_range:
                    logger.info(f"  Available data: {metadata.available_data_range}")

            # Check what data we already have
            latest_usage_time = self.influx_writer.get_latest_opower_usage_time()
            latest_cost_time = self.influx_writer.get_latest_opower_cost_time()
            latest_bill_time = self.influx_writer.get_latest_opower_bill_time()

            now = datetime.now(timezone.utc)

            # Fetch bill history (monthly data, 12 months)
            if not latest_bill_time or (now - latest_bill_time).days > 30:
                logger.info("  Fetching bill history...")
                bills = await self.opower_client.get_bill_history(months=12)
                if bills:
                    self.influx_writer.write_opower_bills_batch(bills)
                    logger.info(f"  Imported {len(bills)} monthly bills")

            # Fetch recent daily usage (last 30 days)
            if latest_usage_time:
                start_date = latest_usage_time
            else:
                start_date = now - timedelta(days=30)

            logger.info(f"  Fetching daily usage since {start_date.strftime('%Y-%m-%d')}...")
            usage_data = await self.opower_client.get_usage_data(start_date, now, "DAY")
            if usage_data:
                self.influx_writer.write_opower_usage_batch(usage_data)
                logger.info(f"  Imported {len(usage_data)} daily usage readings")

            # Fetch recent daily cost (last 30 days)
            if latest_cost_time:
                start_date = latest_cost_time
            else:
                start_date = now - timedelta(days=30)

            logger.info(f"  Fetching daily cost since {start_date.strftime('%Y-%m-%d')}...")
            cost_data = await self.opower_client.get_cost_data(start_date, now, "DAY")
            if cost_data:
                self.influx_writer.write_opower_cost_batch(cost_data)
                logger.info(f"  Imported {len(cost_data)} daily cost readings")

                # Calculate average effective rate from cost data
                if cost_data:
                    total_kwh = sum(c.kwh for c in cost_data)
                    total_cost = sum(c.cost_dollars for c in cost_data)
                    if total_kwh > 0:
                        effective_rate = (total_cost / total_kwh) * 100
                        logger.info(f"  Average effective rate: {effective_rate:.2f}¢/kWh (all-in)")

            self.last_opower = now
            logger.info("  Opower bootstrap complete")
            logger.info("-" * 60)

            # Write session status to InfluxDB for dashboard
            self.influx_writer.write_opower_session_status(
                authenticated=True,
                token_expiry=self.opower_client.token_expiry,
                enabled=True
            )

        except OpowerAuthError as e:
            logger.error("=" * 60)
            logger.error(f"OPOWER: AUTHENTICATION FAILED - {e}")
            logger.error("Session may have expired. To fix:")
            logger.error("  1. Run locally: python scripts/comed_opower_setup.py --force")
            logger.error("  2. Restart collector: docker-compose restart collector")
            logger.error("=" * 60)
            self.opower_authenticated = False
            self.influx_writer.write_opower_session_status(
                authenticated=False,
                token_expiry=None,
                enabled=True
            )

        except Exception as e:
            logger.error(f"Error during Opower bootstrap: {e}")
            logger.info("-" * 60)

    async def _poll_opower(self):
        """Poll Opower for new meter data.

        This fetches incremental usage and cost data since the last poll.
        Note: ComEd meter data typically updates once per day.
        """
        try:
            if not self.opower_authenticated:
                return

            # Ensure we're still authenticated
            if not await self.opower_client.ensure_authenticated():
                logger.error("=" * 60)
                logger.error("OPOWER: SESSION EXPIRED!")
                logger.error("Meter data collection is now STOPPED.")
                logger.error("To restore, run locally:")
                logger.error("  python scripts/comed_opower_setup.py --force")
                logger.error("Then restart: docker-compose restart collector")
                logger.error("=" * 60)
                self.opower_authenticated = False
                self.influx_writer.write_opower_session_status(
                    authenticated=False,
                    token_expiry=None,
                    enabled=True
                )
                return

            now = datetime.now(timezone.utc)

            # Fetch recent usage (since last poll or last 7 days)
            latest_usage_time = self.influx_writer.get_latest_opower_usage_time()
            if latest_usage_time:
                start_date = latest_usage_time
            else:
                start_date = now - timedelta(days=7)

            # Only fetch if we might have new data (check daily)
            if (now - start_date).days >= 1:
                usage_data = await self.opower_client.get_usage_data(start_date, now, "DAY")
                if usage_data:
                    self.influx_writer.write_opower_usage_batch(usage_data)
                    logger.info(f"Opower: Imported {len(usage_data)} new usage readings")

                # Fetch cost data for same period
                cost_data = await self.opower_client.get_cost_data(start_date, now, "DAY")
                if cost_data:
                    self.influx_writer.write_opower_cost_batch(cost_data)
                    logger.info(f"Opower: Imported {len(cost_data)} new cost readings")

        except OpowerAuthError as e:
            logger.warning(f"Opower authentication error: {e}")
            self.opower_authenticated = False

        except Exception as e:
            logger.error(f"Error polling Opower: {e}")

    async def _refresh_opower_token(self):
        """Proactively refresh the Opower token to keep the session alive.

        This runs every 10 minutes (configurable) to prevent token expiry.
        The token typically expires after ~20 minutes, so refreshing every 10
        keeps us well ahead of expiry.
        """
        try:
            if not self.opower_authenticated:
                return

            # Check if token is close to expiry (within 15 minutes)
            # Token lasts ~20 min, we check every 10 min, so refresh at 15 min mark
            if self.opower_client.token_expiry:
                time_to_expiry = (self.opower_client.token_expiry - datetime.now(timezone.utc)).total_seconds()
                if time_to_expiry > 900:  # More than 15 minutes left, skip refresh
                    self.opower_expiry_warned = False  # Reset warning flag
                    # Log periodically so users know session is alive (every hour)
                    if not hasattr(self, '_last_opower_alive_log') or \
                       (datetime.now(timezone.utc) - self._last_opower_alive_log).total_seconds() >= 3600:
                        logger.info(f"Opower: Session alive, token valid for {time_to_expiry/60:.0f} min")
                        self._last_opower_alive_log = datetime.now(timezone.utc)
                    return

                # Warn if getting close to expiry
                if time_to_expiry < 300 and not self.opower_expiry_warned:
                    logger.warning(f"Opower: Token expires in {time_to_expiry:.0f}s - attempting refresh...")
                    self.opower_expiry_warned = True

            # Attempt to refresh
            if await self.opower_client.refresh_token():
                logger.info("Opower: Token refreshed successfully - session alive")
                self.opower_expiry_warned = False
                self.opower_refresh_failures = 0  # Reset failure counter
                # Update session status in InfluxDB
                self.influx_writer.write_opower_session_status(
                    authenticated=True,
                    token_expiry=self.opower_client.token_expiry,
                    enabled=True
                )
            else:
                # Refresh failed - track consecutive failures
                self.opower_refresh_failures += 1

                if self.opower_refresh_failures >= 3:
                    # Too many failures - give up and watch for new cache
                    logger.error("=" * 60)
                    logger.error("OPOWER: TOKEN REFRESH FAILED 3 TIMES!")
                    logger.error("  Session is invalid. Switching to cache watch mode.")
                    logger.error("  To restore, run inside Docker:")
                    logger.error("    docker compose run --rm -it collector python /app/project/scripts/comed_opower_setup.py --force")
                    logger.error("  The collector will auto-detect the new cache within 30 seconds.")
                    logger.error("=" * 60)
                    self.opower_authenticated = False
                    self.opower_refresh_failures = 0
                    self.influx_writer.write_opower_session_status(
                        authenticated=False,
                        token_expiry=None,
                        enabled=True
                    )
                else:
                    # Warn but keep trying
                    logger.error("=" * 60)
                    logger.error(f"OPOWER: TOKEN REFRESH FAILED! (attempt {self.opower_refresh_failures}/3)")
                    logger.error("  Will retry on next refresh cycle.")
                    logger.error("=" * 60)
                    # Write expiring status
                    self.influx_writer.write_opower_session_status(
                        authenticated=True,
                        token_expiry=self.opower_client.token_expiry,
                        enabled=True
                    )

        except Exception as e:
            logger.error(f"Opower: Token refresh error: {e}")

    async def _check_opower_cache(self):
        """Check if Opower cache file has been added and initialize if found.

        This runs periodically when Opower is enabled but not authenticated,
        allowing hot-reload when the user runs the setup script.
        """
        try:
            await self.opower_client.connect()

            # Try to load cache
            if self.opower_client._load_cache():
                logger.info("=" * 60)
                logger.info("OPOWER: Cache file detected! Initializing...")
                logger.info("=" * 60)

                self.opower_authenticated = True
                self.opower_expiry_warned = False
                self.opower_refresh_failures = 0  # Reset failure counter

                # Run the full initialization
                await self._fetch_opower_initial()

                if self.opower_authenticated:
                    logger.info("=" * 60)
                    logger.info("OPOWER: Successfully initialized from cache file!")
                    logger.info("  Token refresh is now active (every 10 min)")
                    logger.info("  Data polling is now active (every hour)")
                    logger.info("=" * 60)

        except Exception as e:
            logger.debug(f"Opower cache check: {e}")


async def main():
    """Main entry point."""
    collector = Collector()

    # Handle shutdown signals
    loop = asyncio.get_event_loop()

    def shutdown_handler():
        logger.info("Shutdown signal received")
        asyncio.create_task(collector.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        await collector.start()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        await collector.stop()


if __name__ == "__main__":
    asyncio.run(main())
