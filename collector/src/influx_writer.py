"""InfluxDB writer for storing metrics."""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Tuple
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from .config import settings, ChargerConfig
from .models import (
    TWCVitals, TWCLifetime, TWCVersion, TWCWifiStatus, ComEdPrice,
    TessieVehicle, VehicleChargingSession, FleetWallConnector, FleetChargeSession, TessieCharge,
    OpowerUsageRead, OpowerCostRead, OpowerBillSummary
)

logger = logging.getLogger(__name__)


class InfluxWriter:
    """Writes metrics to InfluxDB."""

    def __init__(self):
        self.client = InfluxDBClient(
            url=settings.influxdb_url,
            token=settings.influxdb_token,
            org=settings.influxdb_org
        )
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        self.query_api = self.client.query_api()
        self.bucket = settings.influxdb_bucket
        self.org = settings.influxdb_org

    def close(self):
        """Close the InfluxDB client."""
        self.write_api.close()
        self.client.close()

    def _now(self) -> datetime:
        """Get current UTC timestamp."""
        return datetime.now(timezone.utc)

    def write_vitals(self, charger: ChargerConfig, vitals: TWCVitals):
        """Write vitals data to InfluxDB."""
        try:
            point = (
                Point("twc_vitals")
                .tag("charger_id", charger.name)
                .tag("charger_ip", charger.ip)
                .field("vehicle_connected", vitals.vehicle_connected)
                .field("contactor_closed", vitals.contactor_closed)
                .field("is_charging", vitals.is_charging)
                .field("session_energy_wh", vitals.session_energy_wh)
                .field("session_s", vitals.session_s)
                .field("vehicle_current_a", vitals.vehicle_current_a)
                .field("power_w", vitals.power_w)
                .field("grid_v", vitals.grid_v)
                .field("grid_hz", vitals.grid_hz)
                .field("voltageA_v", vitals.voltageA_v)
                .field("voltageB_v", vitals.voltageB_v)
                .field("voltageC_v", vitals.voltageC_v)
                .field("currentA_a", vitals.currentA_a)
                .field("currentB_a", vitals.currentB_a)
                .field("currentC_a", vitals.currentC_a)
                .field("currentN_a", vitals.currentN_a)
                .field("pcba_temp_c", vitals.pcba_temp_c)
                .field("handle_temp_c", vitals.handle_temp_c)
                .field("mcu_temp_c", vitals.mcu_temp_c)
                .field("relay_coil_v", vitals.relay_coil_v)
                .field("pilot_high_v", vitals.pilot_high_v)
                .field("pilot_low_v", vitals.pilot_low_v)
                .field("uptime_s", vitals.uptime_s)
                .field("evse_state", vitals.evse_state)
                .field("config_status", vitals.config_status)
                .time(self._now(), WritePrecision.MS)
            )

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.debug(f"[{charger.name}] Wrote vitals to InfluxDB")

        except Exception as e:
            logger.error(f"[{charger.name}] Error writing vitals: {e}")

    def write_lifetime(self, charger: ChargerConfig, lifetime: TWCLifetime):
        """Write lifetime statistics to InfluxDB."""
        try:
            point = (
                Point("twc_lifetime")
                .tag("charger_id", charger.name)
                .tag("charger_ip", charger.ip)
                .field("energy_wh", lifetime.energy_wh)
                .field("energy_kwh", lifetime.energy_kwh)
                .field("charge_starts", lifetime.charge_starts)
                .field("charging_time_s", lifetime.charging_time_s)
                .field("charging_hours", lifetime.charging_hours)
                .field("contactor_cycles", lifetime.contactor_cycles)
                .field("contactor_cycles_loaded", lifetime.contactor_cycles_loaded)
                .field("connector_cycles", lifetime.connector_cycles)
                .field("uptime_s", lifetime.uptime_s)
                .field("uptime_days", lifetime.uptime_days)
                .field("alert_count", lifetime.alert_count)
                .field("thermal_foldbacks", lifetime.thermal_foldbacks)
                .field("avg_startup_temp", lifetime.avg_startup_temp)
                .time(self._now(), WritePrecision.MS)
            )

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.debug(f"[{charger.name}] Wrote lifetime to InfluxDB")

        except Exception as e:
            logger.error(f"[{charger.name}] Error writing lifetime: {e}")

    def write_version(self, charger: ChargerConfig, version: TWCVersion):
        """Write version info to InfluxDB."""
        try:
            point = (
                Point("twc_version")
                .tag("charger_id", charger.name)
                .tag("charger_ip", charger.ip)
                .tag("serial_number", version.serial_number)
                .tag("part_number", version.part_number)
                .field("firmware_version", version.firmware_version)
                .field("git_branch", version.git_branch)
                .time(self._now(), WritePrecision.MS)
            )

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.debug(f"[{charger.name}] Wrote version to InfluxDB")

        except Exception as e:
            logger.error(f"[{charger.name}] Error writing version: {e}")

    def write_wifi_status(self, charger: ChargerConfig, wifi: TWCWifiStatus):
        """Write WiFi status to InfluxDB."""
        try:
            point = (
                Point("twc_wifi")
                .tag("charger_id", charger.name)
                .tag("charger_ip", charger.ip)
                .tag("wifi_mac", wifi.wifi_mac)
                .field("wifi_connected", wifi.wifi_connected)
                .field("internet", wifi.internet)
                .field("wifi_signal_strength", wifi.wifi_signal_strength)
                .field("wifi_rssi", wifi.wifi_rssi)
                .field("wifi_snr", wifi.wifi_snr)
                .time(self._now(), WritePrecision.MS)
            )

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.debug(f"[{charger.name}] Wrote wifi status to InfluxDB")

        except Exception as e:
            logger.error(f"[{charger.name}] Error writing wifi status: {e}")

    def write_comed_price(self, price: ComEdPrice, price_type: str = "5min"):
        """Write ComEd price to InfluxDB."""
        try:
            point = (
                Point("comed_price")
                .tag("price_type", price_type)
                .field("price_cents_kwh", price.price_cents)
                .field("price_dollars_kwh", price.price_dollars)
                .time(price.timestamp, WritePrecision.MS)
            )

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.debug(f"Wrote ComEd price ({price_type}): {price.price_cents}¢/kWh")

        except Exception as e:
            logger.error(f"Error writing ComEd price: {e}")

    def write_comed_prices_batch(self, prices: List[ComEdPrice], price_type: str = "5min"):
        """Write multiple ComEd prices to InfluxDB."""
        try:
            points = []
            for price in prices:
                point = (
                    Point("comed_price")
                    .tag("price_type", price_type)
                    .field("price_cents_kwh", price.price_cents)
                    .field("price_dollars_kwh", price.price_dollars)
                    .time(price.timestamp, WritePrecision.MS)
                )
                points.append(point)

            if points:
                self.write_api.write(bucket=self.bucket, org=self.org, record=points)
                logger.info(f"Wrote {len(points)} ComEd prices to InfluxDB")

        except Exception as e:
            logger.error(f"Error writing ComEd prices batch: {e}")

    def write_current_price(self, price_cents: float):
        """Write the current ComEd price (convenience method)."""
        try:
            point = (
                Point("comed_price")
                .tag("price_type", "current")
                .field("price_cents_kwh", price_cents)
                .field("price_dollars_kwh", price_cents / 100.0)
                .time(self._now(), WritePrecision.MS)
            )

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.debug(f"Wrote current ComEd price: {price_cents}¢/kWh")

        except Exception as e:
            logger.error(f"Error writing current price: {e}")

    def write_session_state(self, charger: ChargerConfig, session: dict):
        """Write current charging session state for real-time dashboard display."""
        try:
            point = (
                Point("twc_session_state")
                .tag("charger_id", charger.name)
                .field("energy_wh", session["energy_wh"])
                .field("energy_kwh", session["energy_wh"] / 1000.0)
                .field("supply_cost_cents", session["supply_cost_cents"])
                .field("full_cost_cents", session["full_cost_cents"])
                .field("supply_cost_dollars", session["supply_cost_cents"] / 100.0)
                .field("full_cost_dollars", session["full_cost_cents"] / 100.0)
                .field("duration_s", session["duration_s"])
                .field("duration_min", session["duration_s"] / 60.0)
                .field("peak_power_w", session["peak_power_w"])
                .time(self._now(), WritePrecision.MS)
            )

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.debug(f"[{charger.name}] Wrote session state: {session['energy_wh']/1000:.2f}kWh, ${session['full_cost_cents']/100:.2f}")

        except Exception as e:
            logger.error(f"[{charger.name}] Error writing session state: {e}")

    def write_session(self, charger: ChargerConfig, session: dict):
        """Write completed charging session summary to InfluxDB."""
        try:
            # Write session record with start_time as the timestamp
            point = (
                Point("twc_session")
                .tag("charger_id", charger.name)
                .field("duration_s", session["duration_s"])
                .field("duration_min", session["duration_s"] / 60.0)
                .field("duration_hours", session["duration_s"] / 3600.0)
                .field("energy_wh", session["energy_wh"])
                .field("energy_kwh", session["energy_wh"] / 1000.0)
                .field("peak_power_w", session["peak_power_w"])
                .field("peak_power_kw", session["peak_power_w"] / 1000.0)
                .field("avg_price_cents", session["avg_price_cents"])
                .field("supply_cost_cents", session["supply_cost_cents"])
                .field("supply_cost_dollars", session["supply_cost_cents"] / 100.0)
                .field("full_cost_cents", session["full_cost_cents"])
                .field("full_cost_dollars", session["full_cost_cents"] / 100.0)
                .field("delivery_rate_cents", session["delivery_rate_cents"])
                .time(session["start_time"], WritePrecision.MS)
            )

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.info(
                f"[{charger.name}] Wrote session: {session['energy_wh']/1000:.2f}kWh, "
                f"${session['full_cost_cents']/100:.2f}, avg {session['avg_price_cents']:.1f}¢/kWh"
            )

        except Exception as e:
            logger.error(f"[{charger.name}] Error writing session: {e}")

    # =========================================================================
    # Tessie Vehicle Data (Phase 4)
    # =========================================================================

    def write_vehicle_state(self, vehicle: TessieVehicle):
        """Write Tesla vehicle state from Tessie API to InfluxDB."""
        try:
            # Create short VIN for tagging (last 6 characters)
            short_vin = vehicle.vin[-6:] if len(vehicle.vin) >= 6 else vehicle.vin

            point = (
                Point("tesla_vehicle")
                .tag("vin", vehicle.vin)
                .tag("short_vin", short_vin)
                .tag("display_name", vehicle.display_name)
                .tag("car_type", vehicle.car_type)
                # Vehicle state (ensure non-null for string fields)
                .field("state", vehicle.state or "unknown")
                .field("is_charging", vehicle.is_charging)
                .field("is_connected", vehicle.is_connected)
                # Battery
                .field("battery_level", vehicle.battery_level or 0)
                .field("usable_battery_level", vehicle.usable_battery_level or 0)
                .field("battery_range", vehicle.battery_range or 0.0)
                .field("charge_limit_soc", vehicle.charge_limit_soc or 0)
                # Charging
                .field("charging_state", vehicle.charging_state or "Unknown")
                .field("charger_power", vehicle.charger_power)
                .field("charge_amps", vehicle.charge_amps)
                .field("charger_voltage", vehicle.charger_voltage)
                .field("charge_energy_added", vehicle.charge_energy_added)
                .field("time_to_full_charge", vehicle.time_to_full_charge)
                .field("conn_charge_cable", vehicle.conn_charge_cable)
                .field("fast_charger_present", vehicle.fast_charger_present)
                # Location
                .field("latitude", vehicle.latitude if vehicle.latitude else 0.0)
                .field("longitude", vehicle.longitude if vehicle.longitude else 0.0)
                # Climate (temps added conditionally below)
                .field("is_preconditioning", vehicle.is_preconditioning)
                .field("battery_heater", vehicle.battery_heater)
                # Vehicle info
                .field("odometer", vehicle.odometer)
                .field("car_version", vehicle.car_version)
                .time(self._now(), WritePrecision.MS)
            )

            # Only write temperature fields if they have valid data (not None)
            # This distinguishes "no data available" from "actual 0°C"
            if vehicle.inside_temp is not None:
                point.field("inside_temp", vehicle.inside_temp)
            if vehicle.outside_temp is not None:
                point.field("outside_temp", vehicle.outside_temp)

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.debug(
                f"[{vehicle.display_name}] Wrote vehicle state: "
                f"{vehicle.battery_level}% SOC, {vehicle.charging_state}"
            )

        except Exception as e:
            logger.error(f"[{vehicle.vin}] Error writing vehicle state: {e}")

    def write_vehicle_charge_state(self, vehicle: TessieVehicle):
        """Write detailed charge state for charging analysis."""
        if not vehicle.charge_state:
            return

        try:
            cs = vehicle.charge_state
            short_vin = vehicle.vin[-6:] if len(vehicle.vin) >= 6 else vehicle.vin

            point = (
                Point("tesla_charge_state")
                .tag("vin", vehicle.vin)
                .tag("short_vin", short_vin)
                .tag("display_name", vehicle.display_name)
                .tag("charging_state", cs.charging_state)
                .tag("conn_charge_cable", cs.conn_charge_cable)
                # Battery levels
                .field("battery_level", cs.battery_level)
                .field("usable_battery_level", cs.usable_battery_level)
                .field("battery_range", cs.battery_range)
                .field("est_battery_range", cs.est_battery_range)
                # Charging metrics
                .field("charger_power", cs.charger_power)
                .field("charge_amps", cs.charge_amps)
                .field("charger_voltage", cs.charger_voltage)
                .field("charger_actual_current", cs.charger_actual_current)
                .field("charge_rate", cs.charge_rate)
                # Session data
                .field("charge_energy_added", cs.charge_energy_added)
                .field("charge_miles_added_rated", cs.charge_miles_added_rated)
                # Time
                .field("time_to_full_charge", cs.time_to_full_charge)
                .field("minutes_to_full_charge", cs.minutes_to_full_charge)
                # Charger type detection
                .field("is_wall_connector", cs.is_wall_connector)
                .field("fast_charger_present", cs.fast_charger_present)
                .field("fast_charger_type", cs.fast_charger_type)
                .field("fast_charger_brand", cs.fast_charger_brand)
                .time(self._now(), WritePrecision.MS)
            )

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.debug(
                f"[{vehicle.display_name}] Wrote charge state: "
                f"{cs.charger_power}kW, {cs.charge_energy_added}kWh added"
            )

        except Exception as e:
            logger.error(f"[{vehicle.vin}] Error writing charge state: {e}")

    def write_battery_health(self, vehicle: TessieVehicle):
        """Write battery health metrics from Fleet Telemetry (if available)."""
        if not vehicle.charge_state:
            return

        cs = vehicle.charge_state

        # Only write if we have battery health data (not all vehicles support this)
        if cs.pack_voltage is None and cs.energy_remaining is None:
            return

        try:
            short_vin = vehicle.vin[-6:] if len(vehicle.vin) >= 6 else vehicle.vin

            point = (
                Point("tesla_battery_health")
                .tag("vin", vehicle.vin)
                .tag("short_vin", short_vin)
                .tag("display_name", vehicle.display_name)
            )

            # Only add fields that have values
            if cs.pack_voltage is not None:
                point = point.field("pack_voltage", cs.pack_voltage)
            if cs.pack_current is not None:
                point = point.field("pack_current", cs.pack_current)
            if cs.module_temp_min is not None:
                point = point.field("module_temp_min", cs.module_temp_min)
            if cs.module_temp_max is not None:
                point = point.field("module_temp_max", cs.module_temp_max)
            if cs.energy_remaining is not None:
                point = point.field("energy_remaining", cs.energy_remaining)
            if cs.lifetime_energy_used is not None:
                point = point.field("lifetime_energy_used", cs.lifetime_energy_used)

            point = point.time(self._now(), WritePrecision.MS)

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.debug(f"[{vehicle.display_name}] Wrote battery health metrics")

        except Exception as e:
            logger.error(f"[{vehicle.vin}] Error writing battery health: {e}")

    def write_vehicle_session_state(self, session: VehicleChargingSession):
        """Write current vehicle charging session state for real-time dashboard display."""
        try:
            short_vin = session.vin[-6:] if len(session.vin) >= 6 else session.vin

            point = (
                Point("tesla_session_state")
                .tag("vin", session.vin)
                .tag("short_vin", short_vin)
                .tag("display_name", session.display_name)
                .tag("charger_type", session.charger_type)
                .field("energy_added_kwh", session.energy_added_kwh)
                .field("starting_battery_level", session.starting_battery_level)
                .field("ending_battery_level", session.ending_battery_level)
                .field("soc_gained", session.soc_gained)
                .field("starting_range", session.starting_range)
                .field("ending_range", session.ending_range)
                .field("miles_added", session.miles_added)
                .field("peak_power_kw", session.peak_power_kw)
                .field("duration_s", session.duration_s)
                .field("duration_min", session.duration_min)
                .field("is_home_charge", session.is_home_charge)
                .time(self._now(), WritePrecision.MS)
            )

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.debug(
                f"[{session.display_name}] Wrote session state: "
                f"{session.energy_added_kwh:.2f}kWh, {session.soc_gained}% gained"
            )

        except Exception as e:
            logger.error(f"[{session.vin}] Error writing vehicle session state: {e}")

    def write_vehicle_session(self, session: VehicleChargingSession):
        """Write completed vehicle charging session to InfluxDB."""
        try:
            short_vin = session.vin[-6:] if len(session.vin) >= 6 else session.vin

            point = (
                Point("tesla_session")
                .tag("vin", session.vin)
                .tag("short_vin", short_vin)
                .tag("display_name", session.display_name)
                .tag("charger_type", session.charger_type)
                .tag("is_home_charge", str(session.is_home_charge).lower())
                .field("duration_s", session.duration_s)
                .field("duration_min", session.duration_min)
                .field("energy_added_kwh", session.energy_added_kwh)
                .field("starting_battery_level", session.starting_battery_level)
                .field("ending_battery_level", session.ending_battery_level)
                .field("soc_gained", session.soc_gained)
                .field("starting_range", session.starting_range)
                .field("ending_range", session.ending_range)
                .field("miles_added", session.miles_added)
                .field("peak_power_kw", session.peak_power_kw)
                .field("avg_power_kw", session.avg_power_kw)
                .time(session.start_time, WritePrecision.MS)
            )

            # Add location if available
            if session.latitude is not None:
                point = point.field("latitude", session.latitude)
            if session.longitude is not None:
                point = point.field("longitude", session.longitude)

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.info(
                f"[{session.display_name}] Wrote vehicle session: "
                f"{session.energy_added_kwh:.2f}kWh, {session.soc_gained}% gained, "
                f"{session.duration_min:.1f} min"
            )

        except Exception as e:
            logger.error(f"[{session.vin}] Error writing vehicle session: {e}")

    def write_charging_efficiency(
        self,
        charger: ChargerConfig,
        twc_energy_kwh: float,
        vehicle_energy_kwh: float,
        vehicle_display_name: str,
        vin: str,
        start_time: datetime
    ):
        """Write charging efficiency data correlating TWC and vehicle energy.

        Charging efficiency = (Vehicle kWh received / TWC kWh delivered) × 100%
        Typical values: 85-95% (losses from charger, cables, battery heating/cooling)
        """
        try:
            # Calculate efficiency
            efficiency = 0.0
            loss_kwh = 0.0
            if twc_energy_kwh > 0:
                efficiency = (vehicle_energy_kwh / twc_energy_kwh) * 100.0
                loss_kwh = twc_energy_kwh - vehicle_energy_kwh

            short_vin = vin[-6:] if len(vin) >= 6 else vin

            point = (
                Point("charging_efficiency")
                .tag("charger_id", charger.name)
                .tag("vin", vin)
                .tag("short_vin", short_vin)
                .tag("display_name", vehicle_display_name)
                .field("twc_energy_kwh", twc_energy_kwh)
                .field("vehicle_energy_kwh", vehicle_energy_kwh)
                .field("efficiency_pct", efficiency)
                .field("loss_kwh", loss_kwh)
                .field("loss_pct", 100.0 - efficiency if efficiency > 0 else 0.0)
                .time(start_time, WritePrecision.MS)
            )

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.info(
                f"[{charger.name}] Charging efficiency: "
                f"TWC {twc_energy_kwh:.2f}kWh -> Vehicle {vehicle_energy_kwh:.2f}kWh "
                f"({efficiency:.1f}% efficient, {loss_kwh:.2f}kWh loss)"
            )

        except Exception as e:
            logger.error(f"Error writing charging efficiency: {e}")

    # =========================================================================
    # Price Data Query Methods (Smart Charging - Step 4.4)
    # =========================================================================

    def get_price_data_range(self) -> Tuple[Optional[datetime], Optional[datetime]]:
        """Get the earliest and latest timestamps of stored price data.

        Returns:
            Tuple of (earliest_time, latest_time) or (None, None) if no data
        """
        try:
            # Query for earliest and latest timestamps
            query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: -365d)
                |> filter(fn: (r) => r["_measurement"] == "comed_price")
                |> filter(fn: (r) => r["price_type"] == "5min")
                |> filter(fn: (r) => r["_field"] == "price_cents_kwh")
                |> group()
                |> reduce(
                    fn: (r, accumulator) => ({{
                        min_time: if r._time < accumulator.min_time then r._time else accumulator.min_time,
                        max_time: if r._time > accumulator.max_time then r._time else accumulator.max_time
                    }}),
                    identity: {{min_time: 2100-01-01T00:00:00Z, max_time: 1970-01-01T00:00:00Z}}
                )
            '''

            tables = self.query_api.query(query, org=self.org)

            for table in tables:
                for record in table.records:
                    min_time = record.values.get("min_time")
                    max_time = record.values.get("max_time")
                    if min_time and max_time:
                        return (min_time, max_time)

            return (None, None)

        except Exception as e:
            logger.error(f"Error querying price data range: {e}")
            return (None, None)

    def get_price_data_days_available(self, lookback_days: int = 30) -> int:
        """Count how many days of price data we have in the lookback period.

        Args:
            lookback_days: Number of days to look back

        Returns:
            Number of days with price data
        """
        try:
            # Count distinct days with data by aggregating per day and counting results
            query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: -{lookback_days}d)
                |> filter(fn: (r) => r["_measurement"] == "comed_price")
                |> filter(fn: (r) => r["price_type"] == "5min")
                |> filter(fn: (r) => r["_field"] == "price_cents_kwh")
                |> aggregateWindow(every: 1d, fn: count, createEmpty: false)
                |> filter(fn: (r) => r["_value"] > 0)
                |> group()
                |> count(column: "_value")
            '''

            tables = self.query_api.query(query, org=self.org)

            for table in tables:
                for record in table.records:
                    return int(record.get_value())

            return 0

        except Exception as e:
            logger.error(f"Error counting price data days: {e}")
            return 0

    def get_oldest_price_data_time(self) -> Optional[datetime]:
        """Get the timestamp of the oldest price data point.

        Returns:
            Earliest timestamp or None if no data
        """
        try:
            query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: -365d)
                |> filter(fn: (r) => r["_measurement"] == "comed_price")
                |> filter(fn: (r) => r["price_type"] == "5min")
                |> filter(fn: (r) => r["_field"] == "price_cents_kwh")
                |> first()
                |> keep(columns: ["_time"])
            '''

            tables = self.query_api.query(query, org=self.org)

            for table in tables:
                for record in table.records:
                    return record.get_time()

            return None

        except Exception as e:
            logger.error(f"Error querying oldest price data: {e}")
            return None

    def has_price_data_for_period(self, start: datetime, end: datetime) -> bool:
        """Check if we have any price data for a specific period.

        Args:
            start: Start of period
            end: End of period

        Returns:
            True if data exists for this period
        """
        try:
            start_str = start.strftime("%Y-%m-%dT%H:%M:%SZ")
            end_str = end.strftime("%Y-%m-%dT%H:%M:%SZ")

            query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: {start_str}, stop: {end_str})
                |> filter(fn: (r) => r["_measurement"] == "comed_price")
                |> filter(fn: (r) => r["price_type"] == "5min")
                |> filter(fn: (r) => r["_field"] == "price_cents_kwh")
                |> limit(n: 1)
                |> count()
            '''

            tables = self.query_api.query(query, org=self.org)

            for table in tables:
                for record in table.records:
                    return record.get_value() > 0

            return False

        except Exception as e:
            logger.error(f"Error checking price data for period: {e}")
            return False

    def get_price_values(self, lookback_days: int = 30) -> List[float]:
        """Get all price values from the lookback period for statistics calculation.

        Args:
            lookback_days: Number of days to look back

        Returns:
            List of price values in cents/kWh
        """
        try:
            query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: -{lookback_days}d)
                |> filter(fn: (r) => r["_measurement"] == "comed_price")
                |> filter(fn: (r) => r["price_type"] == "5min")
                |> filter(fn: (r) => r["_field"] == "price_cents_kwh")
                |> keep(columns: ["_value"])
            '''

            tables = self.query_api.query(query, org=self.org)
            values = []

            for table in tables:
                for record in table.records:
                    val = record.get_value()
                    if val is not None:
                        values.append(float(val))

            return values

        except Exception as e:
            logger.error(f"Error getting price values: {e}")
            return []

    def get_average_price_for_period(self, start: datetime, end: datetime) -> Optional[float]:
        """Get average electricity price for a specific time period.

        Used to calculate costs for historical charging sessions.

        Args:
            start: Start of period (datetime with timezone)
            end: End of period (datetime with timezone)

        Returns:
            Average price in cents/kWh, or None if no data
        """
        try:
            start_str = start.strftime("%Y-%m-%dT%H:%M:%SZ")
            end_str = end.strftime("%Y-%m-%dT%H:%M:%SZ")

            query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: {start_str}, stop: {end_str})
                |> filter(fn: (r) => r["_measurement"] == "comed_price")
                |> filter(fn: (r) => r["price_type"] == "5min")
                |> filter(fn: (r) => r["_field"] == "price_cents_kwh")
                |> mean()
            '''

            tables = self.query_api.query(query, org=self.org)

            for table in tables:
                for record in table.records:
                    val = record.get_value()
                    if val is not None:
                        return float(val)

            return None

        except Exception as e:
            logger.error(f"Error getting average price for period: {e}")
            return None

    def write_price_statistics(self, stats: dict):
        """Write price statistics to InfluxDB.

        Args:
            stats: Dictionary with statistical values:
                - mean, median, std_dev
                - p10, p25, p75, p90, p95 (percentiles)
                - min, max
                - count, days_available
        """
        try:
            point = (
                Point("comed_price_stats")
                .field("mean", stats.get("mean", 0.0))
                .field("median", stats.get("median", 0.0))
                .field("std_dev", stats.get("std_dev", 0.0))
                .field("min", stats.get("min", 0.0))
                .field("max", stats.get("max", 0.0))
                .field("p10", stats.get("p10", 0.0))
                .field("p25", stats.get("p25", 0.0))
                .field("p75", stats.get("p75", 0.0))
                .field("p90", stats.get("p90", 0.0))
                .field("p95", stats.get("p95", 0.0))
                .field("count", stats.get("count", 0))
                .field("days_available", stats.get("days_available", 0))
                .time(self._now(), WritePrecision.MS)
            )

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.info(
                f"Wrote price statistics: mean={stats.get('mean', 0):.2f}¢, "
                f"p75={stats.get('p75', 0):.2f}¢, p90={stats.get('p90', 0):.2f}¢"
            )

        except Exception as e:
            logger.error(f"Error writing price statistics: {e}")

    def get_latest_price_statistics(self) -> Optional[dict]:
        """Get the most recent price statistics from InfluxDB.

        Returns:
            Dictionary with statistical values or None if not available
        """
        try:
            query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: -7d)
                |> filter(fn: (r) => r["_measurement"] == "comed_price_stats")
                |> last()
                |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
            '''

            tables = self.query_api.query(query, org=self.org)

            for table in tables:
                for record in table.records:
                    return {
                        "mean": record.values.get("mean", 0.0),
                        "median": record.values.get("median", 0.0),
                        "std_dev": record.values.get("std_dev", 0.0),
                        "min": record.values.get("min", 0.0),
                        "max": record.values.get("max", 0.0),
                        "p10": record.values.get("p10", 0.0),
                        "p25": record.values.get("p25", 0.0),
                        "p75": record.values.get("p75", 0.0),
                        "p90": record.values.get("p90", 0.0),
                        "p95": record.values.get("p95", 0.0),
                        "count": record.values.get("count", 0),
                        "days_available": record.values.get("days_available", 0),
                        "timestamp": record.get_time(),
                    }

            return None

        except Exception as e:
            logger.error(f"Error getting latest price statistics: {e}")
            return None

    # =========================================================================
    # Fleet API Wall Connector Data (Phase 4.5)
    # =========================================================================

    def write_fleet_wall_connector(
        self,
        wc: FleetWallConnector,
        energy_site_id: str,
        unit_friendly_name: Optional[str] = None,
        vehicle_name: Optional[str] = None
    ):
        """Write Fleet API Wall Connector status to InfluxDB.

        This data comes from the Tesla Fleet API (via Tessie) and provides
        real-time status for all Wall Connectors in a power-sharing setup,
        including follower units that cannot be accessed via local API.

        Args:
            wc: FleetWallConnector data
            energy_site_id: The energy site ID from Fleet API
            unit_friendly_name: Optional friendly name for the unit (e.g., "Garage Left")
            vehicle_name: Optional vehicle display name (resolved from VIN)
        """
        try:
            # Use friendly name if provided, otherwise default based on unit number
            unit_name = unit_friendly_name or ("leader" if wc.is_leader else f"follower_{wc.unit_number}")

            point = (
                Point("twc_fleet_status")
                .tag("energy_site_id", energy_site_id)
                .tag("din", wc.din)
                .tag("serial_number", wc.serial_number)
                .tag("unit_type", "leader" if wc.is_leader else "follower")
                .tag("unit_number", str(wc.unit_number))
                .tag("unit_name", unit_name)
                # State fields
                .field("wall_connector_state", wc.wall_connector_state)
                .field("state_name", wc.state_name)
                .field("wall_connector_fault_state", wc.wall_connector_fault_state)
                .field("fault_name", wc.fault_name)
                # Power
                .field("power_w", wc.wall_connector_power)
                .field("power_kw", wc.power_kw)
                # Status booleans
                .field("is_charging", wc.is_charging)
                .field("is_connected", wc.is_connected)
                # Connected vehicle
                .field("connected_vin", wc.vin or "")
                .field("connected_vehicle_name", vehicle_name or "")
                # Power sharing
                .field("ocpp_status", wc.ocpp_status)
                .field("powershare_session_state", wc.powershare_session_state)
                .time(self._now(), WritePrecision.MS)
            )

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.debug(
                f"[{unit_name}] Wrote status: "
                f"{wc.power_kw:.1f}kW, {wc.state_name}"
            )

        except Exception as e:
            logger.error(f"[Fleet TWC] Error writing wall connector data: {e}")

    def write_fleet_wall_connectors_batch(
        self,
        wall_connectors: List[FleetWallConnector],
        energy_site_id: str
    ):
        """Write multiple Fleet API Wall Connectors to InfluxDB.

        Args:
            wall_connectors: List of FleetWallConnector data
            energy_site_id: The energy site ID from Fleet API
        """
        for wc in wall_connectors:
            self.write_fleet_wall_connector(wc, energy_site_id)

    def write_fleet_session_state(
        self,
        din: str,
        unit_name: str,
        session: dict,
        energy_site_id: str,
        vehicle_name: Optional[str] = None
    ):
        """Write current Fleet API session state for real-time dashboard display.

        This stores the calculated session energy and cost based on integrated
        power readings (since Fleet API doesn't provide session energy directly).

        Args:
            din: Device Identification Number
            unit_name: Unit friendly name (e.g., "Garage Left")
            session: Session state dict from FleetSessionTracker
            energy_site_id: The energy site ID from Fleet API
            vehicle_name: Optional vehicle display name (resolved from VIN)
        """
        try:
            point = (
                Point("fleet_session_state")
                .tag("energy_site_id", energy_site_id)
                .tag("din", din)
                .tag("unit_name", unit_name)
                .field("energy_wh", session["energy_wh"])
                .field("energy_kwh", session["energy_wh"] / 1000.0)
                .field("supply_cost_cents", session["supply_cost_cents"])
                .field("full_cost_cents", session["full_cost_cents"])
                .field("supply_cost_dollars", session["supply_cost_cents"] / 100.0)
                .field("full_cost_dollars", session["full_cost_cents"] / 100.0)
                .field("duration_s", session["duration_s"])
                .field("duration_min", session["duration_s"] / 60.0)
                .field("peak_power_w", session["peak_power_w"])
                .field("peak_power_kw", session["peak_power_w"] / 1000.0)
                .field("vehicle_name", vehicle_name or "")
                .time(self._now(), WritePrecision.MS)
            )

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.debug(
                f"[{unit_name}] Wrote session state: "
                f"{session['energy_wh']/1000:.2f}kWh, ${session['full_cost_cents']/100:.2f}"
            )

        except Exception as e:
            logger.error(f"[Fleet TWC] Error writing session state: {e}")

    # =========================================================================
    # Fleet API Charge Sessions (Phase 4.5 - Charge History)
    # =========================================================================

    def write_fleet_charge_session(
        self,
        session: FleetChargeSession,
        energy_site_id: str,
        vehicle_name: Optional[str] = None,
        unit_friendly_name: Optional[str] = None
    ):
        """Write a Fleet API charge session to InfluxDB.

        This stores historical charging sessions from the Fleet API, which includes
        data for all Wall Connectors (leader + followers).

        Args:
            session: FleetChargeSession data
            energy_site_id: The energy site ID from Fleet API
            vehicle_name: Optional vehicle display name (if resolved from target_id)
            unit_friendly_name: Optional friendly name from config (e.g., "Garage Right")
        """
        try:
            # Use vehicle name if available, otherwise look up from target_id config
            display_name = vehicle_name or session.vehicle_name
            if not display_name and session.target_id:
                # Try to resolve from TARGET_ID_VEHICLES config
                display_name = settings.get_vehicle_name_from_target_id(session.target_id)
            else:
                display_name = display_name or "Unknown"

            # Use friendly name from config if provided, otherwise use default
            unit_name = unit_friendly_name or session.unit_name

            point = (
                Point("fleet_charge_session")
                .tag("energy_site_id", energy_site_id)
                .tag("din", session.din)
                .tag("serial_number", session.serial_number)
                .tag("unit_type", "leader" if session.is_leader else "follower")
                .tag("unit_number", str(session.unit_number))
                .tag("unit_name", unit_name)
                .tag("target_id", session.target_id)
                .tag("vehicle_name", display_name)
                # Session timing
                .field("duration_s", session.duration_s)
                .field("duration_min", session.duration_min)
                .field("duration_hours", session.duration_hours)
                # Energy
                .field("energy_wh", session.energy_wh)
                .field("energy_kwh", session.energy_kwh)
                # Calculated metrics
                .field("avg_power_kw", session.avg_power_kw)
                # Use session start time as the timestamp
                .time(session.start_time, WritePrecision.S)
            )

            # Add cost fields if available
            if session.avg_price_cents is not None:
                point = point.field("avg_price_cents", session.avg_price_cents)
            if session.supply_cost_cents is not None:
                point = point.field("supply_cost_cents", session.supply_cost_cents)
                point = point.field("supply_cost_dollars", session.supply_cost_cents / 100.0)
            if session.delivery_cost_cents is not None:
                point = point.field("delivery_cost_cents", session.delivery_cost_cents)
            if session.full_cost_cents is not None:
                point = point.field("full_cost_cents", session.full_cost_cents)
                point = point.field("full_cost_dollars", session.full_cost_cents / 100.0)

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)

            # Log with cost if available
            cost_str = ""
            if session.full_cost_cents is not None:
                cost_str = f", ${session.full_cost_cents/100:.2f}"
            logger.debug(
                f"[Fleet {session.unit_name}] Wrote charge session: "
                f"{session.energy_kwh:.2f}kWh, {session.duration_min:.0f}min{cost_str}"
            )

        except Exception as e:
            logger.error(f"[Fleet TWC] Error writing charge session: {e}")

    def write_fleet_charge_sessions_batch(
        self,
        sessions: List[FleetChargeSession],
        energy_site_id: str,
        vehicle_map: Optional[dict] = None
    ):
        """Write multiple Fleet API charge sessions to InfluxDB.

        Args:
            sessions: List of FleetChargeSession objects
            energy_site_id: The energy site ID from Fleet API
            vehicle_map: Optional dict mapping target_id -> vehicle_name
        """
        for session in sessions:
            vehicle_name = None
            if vehicle_map and session.target_id:
                vehicle_name = vehicle_map.get(session.target_id)

            # Get friendly name from config
            unit_friendly_name = settings.get_twc_friendly_name(session.din, session.unit_number)

            self.write_fleet_charge_session(
                session, energy_site_id, vehicle_name, unit_friendly_name
            )

        if sessions:
            logger.info(f"[Fleet TWC] Wrote {len(sessions)} charge sessions to InfluxDB")

    def get_latest_fleet_charge_session_time(self, energy_site_id: str) -> Optional[int]:
        """Get the timestamp of the most recent fleet charge session.

        Used to determine the starting point for incremental session fetching.

        Args:
            energy_site_id: The energy site ID

        Returns:
            Unix timestamp of the most recent session start, or None if no data
        """
        try:
            query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: -365d)
                |> filter(fn: (r) => r["_measurement"] == "fleet_charge_session")
                |> filter(fn: (r) => r["energy_site_id"] == "{energy_site_id}")
                |> filter(fn: (r) => r["_field"] == "energy_kwh")
                |> last()
                |> keep(columns: ["_time"])
            '''

            tables = self.query_api.query(query, org=self.org)

            for table in tables:
                for record in table.records:
                    timestamp = record.get_time()
                    if timestamp:
                        return int(timestamp.timestamp())

            return None

        except Exception as e:
            logger.error(f"Error getting latest fleet charge session time: {e}")
            return None

    def has_fleet_charge_session(self, session: FleetChargeSession, energy_site_id: str) -> bool:
        """Check if a specific fleet charge session already exists in InfluxDB.

        Used to avoid duplicate session imports.

        Args:
            session: The FleetChargeSession to check
            energy_site_id: The energy site ID

        Returns:
            True if the session already exists
        """
        try:
            # Query for a session at the exact start time with matching DIN
            start_str = session.start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            end_str = session.end_time.strftime("%Y-%m-%dT%H:%M:%SZ")

            query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: {start_str}, stop: {end_str})
                |> filter(fn: (r) => r["_measurement"] == "fleet_charge_session")
                |> filter(fn: (r) => r["energy_site_id"] == "{energy_site_id}")
                |> filter(fn: (r) => r["din"] == "{session.din}")
                |> filter(fn: (r) => r["_field"] == "energy_kwh")
                |> limit(n: 1)
                |> count()
            '''

            tables = self.query_api.query(query, org=self.org)

            for table in tables:
                for record in table.records:
                    return record.get_value() > 0

            return False

        except Exception as e:
            logger.error(f"Error checking for existing fleet charge session: {e}")
            return False

    # =========================================================================
    # Fleet Session from live_status (Step 4.5.9 - Immediate Session Recording)
    # =========================================================================

    def write_fleet_session_from_live_status(
        self,
        session_info: dict,
        energy_site_id: str,
        vehicle_name: Optional[str] = None
    ):
        """Write a completed charging session detected from live_status polling.

        This is the PRIMARY source for immediate session recording (Step 4.5.9).
        Sessions appear in the dashboard within seconds of charge completion,
        rather than waiting hours/days for Fleet API telemetry_history.

        The energy is calculated by integrating power readings over time
        (FleetSessionTracker), which may differ slightly from the Wall Connector's
        internal meter. When telemetry_history data arrives later, we reconcile
        to get the accurate energy value.

        Args:
            session_info: Dict from FleetSessionTracker.update() containing:
                - din: Wall Connector DIN
                - unit_name: Friendly unit name
                - start_time: Session start datetime
                - end_time: Session end datetime
                - duration_s: Duration in seconds
                - energy_wh: Energy in watt-hours (from power integration)
                - peak_power_w: Peak power during session
                - avg_price_cents: Average price during session
                - supply_cost_cents: Supply cost
                - full_cost_cents: Total cost (supply + delivery)
                - vin: Vehicle VIN (if known)
            energy_site_id: The energy site ID from Fleet API
            vehicle_name: Vehicle display name (if known)
        """
        try:
            din = session_info["din"]
            unit_name = session_info.get("unit_name", "unknown")
            start_time = session_info["start_time"]
            duration_s = session_info["duration_s"]
            energy_wh = session_info["energy_wh"]
            energy_kwh = energy_wh / 1000.0
            vin = session_info.get("vin")

            # Calculate duration components
            duration_min = duration_s / 60.0
            duration_hours = duration_s / 3600.0

            # Calculate avg power
            avg_power_kw = energy_kwh / duration_hours if duration_hours > 0 else 0.0

            # Extract serial and unit info from DIN
            serial_number = din.split("--")[-1] if "--" in din else din
            parts = din.split("-")
            unit_number = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
            is_leader = unit_number == 1
            unit_type = "leader" if is_leader else "follower"

            # Get friendly name from config
            unit_friendly_name = settings.get_twc_friendly_name(din, unit_number)

            # Resolve vehicle name
            display_name = vehicle_name or ""
            if not display_name and vin:
                display_name = settings.get_vehicle_friendly_name(vin)

            point = (
                Point("fleet_charge_session")
                .tag("source", "live_status")  # Distinguish from telemetry_history
                .tag("energy_site_id", energy_site_id)
                .tag("din", din)
                .tag("serial_number", serial_number)
                .tag("unit_type", unit_type)
                .tag("unit_number", str(unit_number))
                .tag("unit_name", unit_friendly_name)
                # Session timing
                .field("duration_s", int(duration_s))
                .field("duration_min", duration_min)
                .field("duration_hours", duration_hours)
                # Energy
                .field("energy_wh", energy_wh)
                .field("energy_kwh", energy_kwh)
                # Calculated metrics
                .field("avg_power_kw", avg_power_kw)
                .field("peak_power_kw", session_info.get("peak_power_w", 0) / 1000.0)
                # Reconciliation flag (will be set to true when telemetry_history arrives)
                .field("reconciled", False)
                # Use session start time as the timestamp
                .time(start_time, WritePrecision.S)
            )

            # Add vehicle info if known
            if vin:
                point = point.tag("vin", vin)
            if display_name:
                point = point.tag("vehicle_name", display_name)

            # Add cost fields
            if session_info.get("avg_price_cents") is not None:
                point = point.field("avg_price_cents", session_info["avg_price_cents"])
            if session_info.get("supply_cost_cents") is not None:
                point = point.field("supply_cost_cents", session_info["supply_cost_cents"])
                point = point.field("supply_cost_dollars", session_info["supply_cost_cents"] / 100.0)
            if session_info.get("full_cost_cents") is not None:
                # Calculate delivery cost
                delivery_cost_cents = session_info["full_cost_cents"] - session_info.get("supply_cost_cents", 0)
                point = point.field("delivery_cost_cents", delivery_cost_cents)
                point = point.field("full_cost_cents", session_info["full_cost_cents"])
                point = point.field("full_cost_dollars", session_info["full_cost_cents"] / 100.0)

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)

            # Log the session
            cost_str = ""
            if session_info.get("full_cost_cents") is not None:
                cost_str = f", ${session_info['full_cost_cents']/100:.2f}"
            vehicle_str = f" ({display_name})" if display_name else ""
            logger.info(
                f"[Fleet {unit_friendly_name}] Wrote session from live_status: "
                f"{energy_kwh:.2f}kWh, {duration_min:.0f}min{vehicle_str}{cost_str}"
            )

        except Exception as e:
            logger.error(f"Error writing Fleet session from live_status: {e}")

    def find_matching_live_status_session(
        self,
        din: str,
        start_time: datetime,
        tolerance_minutes: int = 5
    ) -> Optional[dict]:
        """Find a live_status session that matches a telemetry_history session.

        Used for reconciliation: when telemetry_history arrives, we check if
        we already recorded this session from live_status and update the energy
        value with the more accurate Wall Connector meter reading.

        Args:
            din: Wall Connector DIN
            start_time: Session start time from telemetry_history
            tolerance_minutes: Time window to match sessions (default 5 min)

        Returns:
            Dict with session info if found, None otherwise
        """
        try:
            # Query for live_status sessions near this time for this DIN
            start_range = start_time - timedelta(minutes=tolerance_minutes)
            end_range = start_time + timedelta(minutes=tolerance_minutes)

            query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: {start_range.strftime('%Y-%m-%dT%H:%M:%SZ')}, stop: {end_range.strftime('%Y-%m-%dT%H:%M:%SZ')})
                |> filter(fn: (r) => r["_measurement"] == "fleet_charge_session")
                |> filter(fn: (r) => r["source"] == "live_status")
                |> filter(fn: (r) => r["din"] == "{din}")
                |> filter(fn: (r) => r["_field"] == "energy_kwh" or r["_field"] == "reconciled")
                |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
            '''

            tables = self.query_api.query(query, org=self.org)

            for table in tables:
                for record in table.records:
                    # Check if not already reconciled
                    if not record.values.get("reconciled", False):
                        return {
                            "time": record.get_time(),
                            "energy_kwh": record.values.get("energy_kwh", 0),
                        }

            return None

        except Exception as e:
            logger.error(f"Error finding matching live_status session: {e}")
            return None

    def update_session_with_telemetry_data(
        self,
        din: str,
        start_time: datetime,
        energy_kwh_from_telemetry: float
    ) -> bool:
        """Update a live_status session with accurate energy from telemetry_history.

        When telemetry_history data arrives (delayed hours/days), we update the
        live_status session with the Wall Connector's meter reading, which is
        more accurate than our power integration.

        Note: InfluxDB doesn't support in-place updates, so we write a new point
        with the same timestamp. The reconciled field marks it as updated.

        Args:
            din: Wall Connector DIN
            start_time: Session start time
            energy_kwh_from_telemetry: Accurate energy value from telemetry_history

        Returns:
            True if update successful
        """
        # For now, we'll log that reconciliation would happen
        # Full implementation would require reading all fields and rewriting
        # This is tracked as Step 4.5.9.3 in ACTION_PLAN.md
        logger.debug(
            f"[Fleet {din}] Would reconcile session at {start_time} "
            f"with telemetry energy: {energy_kwh_from_telemetry:.2f} kWh"
        )
        return True

    # =========================================================================
    # ComEd Opower Data (Phase 4.6 - Meter Data Integration)
    # =========================================================================

    def write_opower_usage(self, usage: OpowerUsageRead):
        """Write Opower usage data to InfluxDB.

        This is actual metered usage from the smart meter, which matches
        what appears on your ComEd bill.

        Args:
            usage: OpowerUsageRead with timestamp, kwh, and resolution
        """
        try:
            point = (
                Point("comed_meter_usage")
                .tag("resolution", usage.resolution)
                .field("kwh", usage.kwh)
                .field("wh", usage.wh)
                .time(usage.timestamp, WritePrecision.S)
            )

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.debug(f"Wrote Opower usage: {usage.kwh:.2f} kWh ({usage.resolution})")

        except Exception as e:
            logger.error(f"Error writing Opower usage: {e}")

    def write_opower_usage_batch(self, usage_reads: List[OpowerUsageRead]):
        """Write multiple Opower usage readings to InfluxDB.

        Args:
            usage_reads: List of OpowerUsageRead objects
        """
        try:
            points = []
            for usage in usage_reads:
                point = (
                    Point("comed_meter_usage")
                    .tag("resolution", usage.resolution)
                    .field("kwh", usage.kwh)
                    .field("wh", usage.wh)
                    .time(usage.timestamp, WritePrecision.S)
                )
                points.append(point)

            if points:
                self.write_api.write(bucket=self.bucket, org=self.org, record=points)
                logger.info(f"Wrote {len(points)} Opower usage readings to InfluxDB")

        except Exception as e:
            logger.error(f"Error writing Opower usage batch: {e}")

    def write_opower_cost(self, cost: OpowerCostRead):
        """Write Opower cost data to InfluxDB.

        This is the actual billed cost from ComEd, including all fees,
        delivery charges, and taxes.

        Args:
            cost: OpowerCostRead with timestamp, kwh, cost, and resolution
        """
        try:
            point = (
                Point("comed_meter_cost")
                .tag("resolution", cost.resolution)
                .field("kwh", cost.kwh)
                .field("cost_dollars", cost.cost_dollars)
                .field("cost_cents", cost.cost_cents)
                .field("effective_rate_cents", cost.effective_rate_cents)
                .time(cost.timestamp, WritePrecision.S)
            )

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.debug(
                f"Wrote Opower cost: {cost.kwh:.2f} kWh, ${cost.cost_dollars:.2f} "
                f"({cost.effective_rate_cents:.2f}¢/kWh)"
            )

        except Exception as e:
            logger.error(f"Error writing Opower cost: {e}")

    def write_opower_cost_batch(self, cost_reads: List[OpowerCostRead]):
        """Write multiple Opower cost readings to InfluxDB.

        Args:
            cost_reads: List of OpowerCostRead objects
        """
        try:
            points = []
            for cost in cost_reads:
                point = (
                    Point("comed_meter_cost")
                    .tag("resolution", cost.resolution)
                    .field("kwh", cost.kwh)
                    .field("cost_dollars", cost.cost_dollars)
                    .field("cost_cents", cost.cost_cents)
                    .field("effective_rate_cents", cost.effective_rate_cents)
                    .time(cost.timestamp, WritePrecision.S)
                )
                points.append(point)

            if points:
                self.write_api.write(bucket=self.bucket, org=self.org, record=points)
                logger.info(f"Wrote {len(points)} Opower cost readings to InfluxDB")

        except Exception as e:
            logger.error(f"Error writing Opower cost batch: {e}")

    def write_opower_bill(self, bill: OpowerBillSummary):
        """Write Opower bill summary to InfluxDB.

        Monthly bill summary with breakdown of charges.

        Args:
            bill: OpowerBillSummary with bill details
        """
        try:
            if not bill.bill_date:
                logger.warning("Bill has no date, skipping")
                return

            point = (
                Point("comed_bill")
                .tag("estimated", str(bill.is_estimated).lower())
                .field("total_kwh", bill.total_kwh)
                .field("total_cost_dollars", bill.total_cost_dollars)
                .field("usage_charges_dollars", bill.usage_charges_dollars)
                .field("non_usage_charges_dollars", bill.non_usage_charges_dollars)
                .field("effective_rate_cents", bill.effective_rate_cents)
                .time(bill.bill_date, WritePrecision.S)
            )

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.info(
                f"Wrote Opower bill: {bill.total_kwh:.0f} kWh, ${bill.total_cost_dollars:.2f} "
                f"({bill.effective_rate_cents:.2f}¢/kWh all-in)"
            )

        except Exception as e:
            logger.error(f"Error writing Opower bill: {e}")

    def write_opower_bills_batch(self, bills: List[OpowerBillSummary]):
        """Write multiple Opower bill summaries to InfluxDB.

        Args:
            bills: List of OpowerBillSummary objects
        """
        for bill in bills:
            self.write_opower_bill(bill)

    def write_opower_session_status(
        self,
        authenticated: bool,
        token_expiry: Optional[datetime] = None,
        enabled: bool = True
    ):
        """Write Opower session/authentication status to InfluxDB.

        This is used by the Meter & Bills dashboard to show real-time
        connection status to the Opower API.

        Args:
            authenticated: Whether we have a valid authenticated session
            token_expiry: When the current token expires (if authenticated)
            enabled: Whether Opower integration is enabled
        """
        try:
            now = self._now()

            # Calculate seconds until token expires (negative if expired)
            token_expires_in_s = 0
            if token_expiry:
                token_expires_in_s = (token_expiry - now).total_seconds()

            # Determine status: 2=connected, 1=expiring soon, 0=expired/error, -1=disabled
            if not enabled:
                status = -1
            elif not authenticated:
                status = 0
            elif token_expires_in_s < 300:  # Less than 5 minutes
                status = 1  # Expiring soon
            else:
                status = 2  # Connected

            point = (
                Point("opower_session_status")
                .field("authenticated", 1 if authenticated else 0)
                .field("enabled", 1 if enabled else 0)
                .field("status", status)
                .field("token_expires_in_s", int(token_expires_in_s))
                .time(now, WritePrecision.MS)
            )

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.debug(f"Wrote Opower session status: authenticated={authenticated}, status={status}")

        except Exception as e:
            logger.error(f"Error writing Opower session status: {e}")

    def get_latest_opower_usage_time(self, resolution: str = "DAY") -> Optional[datetime]:
        """Get the timestamp of the most recent Opower usage data.

        Used to determine the starting point for incremental fetching.

        Args:
            resolution: Data resolution ("DAY", "HOUR", "HALF_HOUR")

        Returns:
            Datetime of the most recent usage, or None if no data
        """
        try:
            query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: -365d)
                |> filter(fn: (r) => r["_measurement"] == "comed_meter_usage")
                |> filter(fn: (r) => r["resolution"] == "{resolution}")
                |> filter(fn: (r) => r["_field"] == "kwh")
                |> last()
                |> keep(columns: ["_time"])
            '''

            tables = self.query_api.query(query, org=self.org)

            for table in tables:
                for record in table.records:
                    return record.get_time()

            return None

        except Exception as e:
            logger.error(f"Error getting latest Opower usage time: {e}")
            return None

    def get_latest_opower_cost_time(self, resolution: str = "DAY") -> Optional[datetime]:
        """Get the timestamp of the most recent Opower cost data.

        Args:
            resolution: Data resolution ("DAY", "HOUR")

        Returns:
            Datetime of the most recent cost data, or None if no data
        """
        try:
            query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: -365d)
                |> filter(fn: (r) => r["_measurement"] == "comed_meter_cost")
                |> filter(fn: (r) => r["resolution"] == "{resolution}")
                |> filter(fn: (r) => r["_field"] == "kwh")
                |> last()
                |> keep(columns: ["_time"])
            '''

            tables = self.query_api.query(query, org=self.org)

            for table in tables:
                for record in table.records:
                    return record.get_time()

            return None

        except Exception as e:
            logger.error(f"Error getting latest Opower cost time: {e}")
            return None

    def get_latest_opower_bill_time(self) -> Optional[datetime]:
        """Get the timestamp of the most recent Opower bill.

        Returns:
            Datetime of the most recent bill, or None if no data
        """
        try:
            query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: -730d)
                |> filter(fn: (r) => r["_measurement"] == "comed_bill")
                |> filter(fn: (r) => r["_field"] == "total_kwh")
                |> last()
                |> keep(columns: ["_time"])
            '''

            tables = self.query_api.query(query, org=self.org)

            for table in tables:
                for record in table.records:
                    return record.get_time()

            return None

        except Exception as e:
            logger.error(f"Error getting latest Opower bill time: {e}")
            return None
