"""InfluxDB client for querying TWC data."""

from influxdb_client import InfluxDBClient
from influxdb_client.client.flux_table import FluxTable
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import logging

from .config import settings
from .models import (
    ChargerStatus,
    ChargerLifetime,
    ChargerInfo,
    CurrentPrice,
    ChargingSession,
    SessionSummary,
    EnergyDataPoint,
    VehicleStatus,
    VehicleSession,
    MeterUsage,
    MeterCost,
    BillSummary,
    MeterComparison,
)

logger = logging.getLogger(__name__)


class InfluxClient:
    """Client for querying TWC data from InfluxDB."""

    def __init__(self):
        self.client = InfluxDBClient(
            url=settings.influxdb_url,
            token=settings.influxdb_token,
            org=settings.influxdb_org,
        )
        self.query_api = self.client.query_api()
        self.bucket = settings.influxdb_bucket
        self.org = settings.influxdb_org

    def check_connection(self) -> bool:
        """Check if InfluxDB is reachable."""
        try:
            self.client.ping()
            return True
        except Exception as e:
            logger.error(f"InfluxDB connection check failed: {e}")
            return False

    def get_charger_ids(self) -> List[str]:
        """Get list of all charger IDs."""
        query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: -24h)
            |> filter(fn: (r) => r._measurement == "twc_vitals")
            |> keep(columns: ["charger_id"])
            |> distinct(column: "charger_id")
        '''
        try:
            result = self.query_api.query(query, org=self.org)
            chargers = []
            for table in result:
                for record in table.records:
                    charger_id = record.values.get("charger_id")
                    if charger_id and charger_id not in chargers:
                        chargers.append(charger_id)
            return chargers
        except Exception as e:
            logger.error(f"Failed to get charger IDs: {e}")
            return []

    def get_charger_status(self, charger_id: Optional[str] = None) -> List[ChargerStatus]:
        """Get current status for charger(s)."""
        charger_filter = ""
        if charger_id:
            charger_filter = f'|> filter(fn: (r) => r.charger_id == "{charger_id}")'

        query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: -5m)
            |> filter(fn: (r) => r._measurement == "twc_vitals")
            {charger_filter}
            |> last()
            |> pivot(rowKey: ["_time", "charger_id"], columnKey: ["_field"], valueColumn: "_value")
        '''
        try:
            result = self.query_api.query(query, org=self.org)
            statuses = []
            for table in result:
                for record in table.records:
                    values = record.values
                    statuses.append(ChargerStatus(
                        charger_id=values.get("charger_id", "unknown"),
                        timestamp=values.get("_time", datetime.utcnow()),
                        power_w=float(values.get("power_w", 0)),
                        grid_v=float(values.get("grid_v", 0)),
                        grid_hz=float(values.get("grid_hz", 0)),
                        vehicle_current_a=float(values.get("vehicle_current_a", 0)),
                        vehicle_connected=bool(values.get("vehicle_connected", False)),
                        contactor_closed=bool(values.get("contactor_closed", False)),
                        session_energy_wh=float(values.get("session_energy_wh", 0)),
                        session_duration_s=int(values.get("session_s", 0)),
                        pcba_temp_c=float(values.get("pcba_temp_c", 0)),
                        handle_temp_c=float(values.get("handle_temp_c", 0)),
                        mcu_temp_c=float(values.get("mcu_temp_c", 0)),
                        uptime_s=int(values.get("uptime_s", 0)),
                    ))
            return statuses
        except Exception as e:
            logger.error(f"Failed to get charger status: {e}")
            return []

    def get_charger_lifetime(self, charger_id: Optional[str] = None) -> List[ChargerLifetime]:
        """Get lifetime statistics for charger(s)."""
        charger_filter = ""
        if charger_id:
            charger_filter = f'|> filter(fn: (r) => r.charger_id == "{charger_id}")'

        query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: -1h)
            |> filter(fn: (r) => r._measurement == "twc_lifetime")
            {charger_filter}
            |> last()
            |> pivot(rowKey: ["_time", "charger_id"], columnKey: ["_field"], valueColumn: "_value")
        '''
        try:
            result = self.query_api.query(query, org=self.org)
            stats = []
            for table in result:
                for record in table.records:
                    values = record.values
                    stats.append(ChargerLifetime(
                        charger_id=values.get("charger_id", "unknown"),
                        timestamp=values.get("_time", datetime.utcnow()),
                        energy_wh=float(values.get("energy_wh", 0)),
                        charge_starts=int(values.get("charge_starts", 0)),
                        charging_time_s=int(values.get("charging_time_s", 0)),
                        uptime_s=int(values.get("uptime_s", 0)),
                        contactor_cycles=int(values.get("contactor_cycles", 0)),
                        alert_count=int(values.get("alert_count", 0)),
                    ))
            return stats
        except Exception as e:
            logger.error(f"Failed to get charger lifetime stats: {e}")
            return []

    def get_charger_info(self, charger_id: Optional[str] = None) -> List[ChargerInfo]:
        """Get version info for charger(s)."""
        charger_filter = ""
        if charger_id:
            charger_filter = f'|> filter(fn: (r) => r.charger_id == "{charger_id}")'

        query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: -1h)
            |> filter(fn: (r) => r._measurement == "twc_version")
            {charger_filter}
            |> last()
            |> pivot(rowKey: ["_time", "charger_id"], columnKey: ["_field"], valueColumn: "_value")
        '''
        try:
            result = self.query_api.query(query, org=self.org)
            infos = []
            for table in result:
                for record in table.records:
                    values = record.values
                    infos.append(ChargerInfo(
                        charger_id=values.get("charger_id", "unknown"),
                        firmware_version=str(values.get("firmware_version", "")),
                        part_number=str(values.get("part_number", "")),
                        serial_number=str(values.get("serial_number", "")),
                    ))
            return infos
        except Exception as e:
            logger.error(f"Failed to get charger info: {e}")
            return []

    def get_current_price(self) -> Optional[CurrentPrice]:
        """Get current electricity price."""
        query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: -30m)
            |> filter(fn: (r) => r._measurement == "comed_price")
            |> filter(fn: (r) => r._field == "price_cents_kwh")
            |> filter(fn: (r) => r.price_type == "hourly_avg")
            |> last()
        '''
        try:
            result = self.query_api.query(query, org=self.org)
            for table in result:
                for record in table.records:
                    price = float(record.get_value())
                    return CurrentPrice(
                        timestamp=record.get_time(),
                        price_cents_kwh=price,
                        price_type="hourly_avg",
                        full_rate_cents_kwh=price + settings.comed_delivery_per_kwh,
                    )
            return None
        except Exception as e:
            logger.error(f"Failed to get current price: {e}")
            return None

    def get_sessions(
        self,
        start_date: datetime,
        end_date: datetime,
        charger_id: Optional[str] = None,
    ) -> List[ChargingSession]:
        """Get charging sessions for a time range."""
        charger_filter = ""
        if charger_id:
            charger_filter = f'|> filter(fn: (r) => r.charger_id == "{charger_id}")'

        query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: {start_date.isoformat()}Z, stop: {end_date.isoformat()}Z)
            |> filter(fn: (r) => r._measurement == "twc_session")
            {charger_filter}
            |> pivot(rowKey: ["_time", "charger_id"], columnKey: ["_field"], valueColumn: "_value")
        '''
        try:
            result = self.query_api.query(query, org=self.org)
            sessions = []
            for table in result:
                for record in table.records:
                    values = record.values
                    sessions.append(ChargingSession(
                        charger_id=values.get("charger_id", "unknown"),
                        start_time=values.get("_time", datetime.utcnow()),
                        end_time=values.get("_time"),
                        duration_s=int(values.get("duration_s", 0)),
                        energy_wh=float(values.get("energy_wh", 0)),
                        supply_cost_cents=float(values.get("supply_cost_cents", 0)),
                        full_cost_cents=float(values.get("full_cost_cents", 0)),
                        avg_price_cents=float(values.get("avg_price_cents", 0)),
                        peak_power_w=float(values.get("peak_power_w", 0)),
                        is_active=False,
                    ))
            return sessions
        except Exception as e:
            logger.error(f"Failed to get sessions: {e}")
            return []

    def get_session_summary(
        self,
        start_date: datetime,
        end_date: datetime,
        charger_id: Optional[str] = None,
    ) -> SessionSummary:
        """Get summary statistics for a time period."""
        sessions = self.get_sessions(start_date, end_date, charger_id)

        total_energy = sum(s.energy_wh for s in sessions)
        total_supply_cost = sum(s.supply_cost_cents for s in sessions)
        total_full_cost = sum(s.full_cost_cents for s in sessions)
        total_duration = sum(s.duration_s for s in sessions)

        avg_price = 0.0
        if total_energy > 0:
            avg_price = (total_supply_cost / (total_energy / 1000)) if total_energy > 0 else 0

        return SessionSummary(
            start_date=start_date,
            end_date=end_date,
            total_sessions=len(sessions),
            total_energy_wh=total_energy,
            total_supply_cost_cents=total_supply_cost,
            total_full_cost_cents=total_full_cost,
            avg_price_cents=avg_price,
            total_duration_s=total_duration,
        )

    def get_energy_data(
        self,
        start_date: datetime,
        end_date: datetime,
        charger_id: Optional[str] = None,
        interval: str = "1h",
    ) -> List[EnergyDataPoint]:
        """Get energy consumption data aggregated by interval."""
        charger_filter = ""
        if charger_id:
            charger_filter = f'|> filter(fn: (r) => r.charger_id == "{charger_id}")'

        query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: {start_date.isoformat()}Z, stop: {end_date.isoformat()}Z)
            |> filter(fn: (r) => r._measurement == "twc_vitals")
            |> filter(fn: (r) => r._field == "session_energy_wh" or r._field == "power_w")
            {charger_filter}
            |> aggregateWindow(every: {interval}, fn: mean, createEmpty: false)
            |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
        '''
        try:
            result = self.query_api.query(query, org=self.org)
            data_points = []
            for table in result:
                for record in table.records:
                    values = record.values
                    data_points.append(EnergyDataPoint(
                        timestamp=values.get("_time", datetime.utcnow()),
                        energy_wh=float(values.get("session_energy_wh", 0)),
                        power_w=float(values.get("power_w", 0)),
                    ))
            return data_points
        except Exception as e:
            logger.error(f"Failed to get energy data: {e}")
            return []

    def get_price_history(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> List[Dict[str, Any]]:
        """Get price history for a time range."""
        query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: {start_date.isoformat()}Z, stop: {end_date.isoformat()}Z)
            |> filter(fn: (r) => r._measurement == "comed_price")
            |> filter(fn: (r) => r._field == "price_cents_kwh")
            |> filter(fn: (r) => r.price_type == "hourly_avg")
        '''
        try:
            result = self.query_api.query(query, org=self.org)
            prices = []
            for table in result:
                for record in table.records:
                    prices.append({
                        "timestamp": record.get_time().isoformat(),
                        "price_cents_kwh": float(record.get_value()),
                        "full_rate_cents_kwh": float(record.get_value()) + settings.comed_delivery_per_kwh,
                    })
            return prices
        except Exception as e:
            logger.error(f"Failed to get price history: {e}")
            return []

    def close(self):
        """Close the client connection."""
        self.client.close()

    # =========================================================================
    # Vehicle Methods (Tessie Integration)
    # =========================================================================

    def get_vehicle_ids(self) -> List[Dict[str, str]]:
        """Get list of all vehicles (VIN and display name)."""
        query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: -24h)
            |> filter(fn: (r) => r._measurement == "tesla_vehicle")
            |> filter(fn: (r) => r.display_name != "")
            |> keep(columns: ["vin", "display_name"])
            |> distinct(column: "vin")
        '''
        try:
            result = self.query_api.query(query, org=self.org)
            vehicles = []
            seen_vins = set()
            for table in result:
                for record in table.records:
                    vin = record.values.get("vin")
                    display_name = record.values.get("display_name", "")
                    if vin and vin not in seen_vins:
                        seen_vins.add(vin)
                        vehicles.append({"vin": vin, "display_name": display_name})
            return vehicles
        except Exception as e:
            logger.error(f"Failed to get vehicle IDs: {e}")
            return []

    def get_vehicle_status(self, vin: Optional[str] = None) -> List[VehicleStatus]:
        """Get current status for vehicle(s)."""
        vin_filter = ""
        if vin:
            vin_filter = f'|> filter(fn: (r) => r.vin == "{vin}")'

        query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: -1h)
            |> filter(fn: (r) => r._measurement == "tesla_vehicle")
            |> filter(fn: (r) => r.display_name != "")
            {vin_filter}
            |> last()
            |> pivot(rowKey: ["_time", "vin", "display_name"], columnKey: ["_field"], valueColumn: "_value")
        '''
        try:
            result = self.query_api.query(query, org=self.org)
            statuses = []
            for table in result:
                for record in table.records:
                    values = record.values
                    statuses.append(VehicleStatus(
                        vin=values.get("vin", ""),
                        display_name=values.get("display_name", ""),
                        timestamp=values.get("_time", datetime.utcnow()),
                        state=str(values.get("state", "unknown")),
                        battery_level=int(values.get("battery_level", 0) or 0),
                        battery_range=float(values.get("battery_range", 0) or 0),
                        charging_state=str(values.get("charging_state", "Unknown")),
                        charge_limit_soc=int(values.get("charge_limit_soc", 0) or 0),
                        charger_power=float(values.get("charger_power", 0) or 0),
                        charge_amps=int(values.get("charge_amps", 0) or 0),
                        charger_voltage=int(values.get("charger_voltage", 0) or 0),
                        charge_energy_added=float(values.get("charge_energy_added", 0) or 0),
                        time_to_full_charge=float(values.get("time_to_full_charge", 0) or 0),
                        charge_port_door_open=bool(values.get("charge_port_door_open", False)),
                        charge_port_latch=str(values.get("charge_port_latch", "")),
                        conn_charge_cable=str(values.get("conn_charge_cable", "")),
                        inside_temp=values.get("inside_temp"),
                        outside_temp=values.get("outside_temp"),
                        climate_on=bool(values.get("climate_on", False)),
                    ))
            return statuses
        except Exception as e:
            logger.error(f"Failed to get vehicle status: {e}")
            return []

    def get_vehicle_sessions(
        self,
        start_date: datetime,
        end_date: datetime,
        vin: Optional[str] = None,
    ) -> List[VehicleSession]:
        """Get vehicle charging sessions for a time range."""
        vin_filter = ""
        if vin:
            vin_filter = f'|> filter(fn: (r) => r.vin == "{vin}")'

        query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: {start_date.isoformat()}Z, stop: {end_date.isoformat()}Z)
            |> filter(fn: (r) => r._measurement == "tesla_session")
            |> filter(fn: (r) => r.display_name != "")
            {vin_filter}
            |> pivot(rowKey: ["_time", "vin", "display_name"], columnKey: ["_field"], valueColumn: "_value")
        '''
        try:
            result = self.query_api.query(query, org=self.org)
            sessions = []
            for table in result:
                for record in table.records:
                    values = record.values
                    sessions.append(VehicleSession(
                        vin=values.get("vin", ""),
                        display_name=values.get("display_name", ""),
                        start_time=values.get("_time", datetime.utcnow()),
                        end_time=values.get("_time"),
                        duration_s=int(values.get("duration_s", 0) or 0),
                        energy_added_kwh=float(values.get("energy_added_kwh", 0) or 0),
                        starting_battery_level=int(values.get("starting_battery_level", 0) or 0),
                        ending_battery_level=int(values.get("ending_battery_level", 0) or 0),
                        soc_gained=int(values.get("soc_gained", 0) or 0),
                        peak_power_kw=float(values.get("peak_power_kw", 0) or 0),
                        charger_type=str(values.get("charger_type", "")),
                    ))
            return sessions
        except Exception as e:
            logger.error(f"Failed to get vehicle sessions: {e}")
            return []

    # =========================================================================
    # Meter Data Methods (ComEd Opower Integration)
    # =========================================================================

    def get_meter_usage(
        self,
        start_date: datetime,
        end_date: datetime,
        resolution: str = "DAY",
    ) -> List[MeterUsage]:
        """Get actual meter usage from ComEd smart meter."""
        query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: {start_date.isoformat()}Z, stop: {end_date.isoformat()}Z)
            |> filter(fn: (r) => r._measurement == "comed_meter_usage")
            |> filter(fn: (r) => r.resolution == "{resolution}")
            |> filter(fn: (r) => r._field == "kwh")
        '''
        try:
            result = self.query_api.query(query, org=self.org)
            usage_data = []
            for table in result:
                for record in table.records:
                    usage_data.append(MeterUsage(
                        timestamp=record.get_time(),
                        kwh=float(record.get_value()),
                        resolution=resolution,
                    ))
            return usage_data
        except Exception as e:
            logger.error(f"Failed to get meter usage: {e}")
            return []

    def get_meter_cost(
        self,
        start_date: datetime,
        end_date: datetime,
        resolution: str = "DAY",
    ) -> List[MeterCost]:
        """Get actual billed costs from ComEd."""
        query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: {start_date.isoformat()}Z, stop: {end_date.isoformat()}Z)
            |> filter(fn: (r) => r._measurement == "comed_meter_cost")
            |> filter(fn: (r) => r.resolution == "{resolution}")
            |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
        '''
        try:
            result = self.query_api.query(query, org=self.org)
            cost_data = []
            for table in result:
                for record in table.records:
                    values = record.values
                    cost_data.append(MeterCost(
                        timestamp=values.get("_time", datetime.utcnow()),
                        kwh=float(values.get("kwh", 0) or 0),
                        cost_cents=float(values.get("cost_cents", 0) or 0),
                        effective_rate_cents=float(values.get("effective_rate_cents", 0) or 0),
                        resolution=resolution,
                    ))
            return cost_data
        except Exception as e:
            logger.error(f"Failed to get meter cost: {e}")
            return []

    def get_bills(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> List[BillSummary]:
        """Get monthly bill summaries from ComEd."""
        query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: {start_date.isoformat()}Z, stop: {end_date.isoformat()}Z)
            |> filter(fn: (r) => r._measurement == "comed_bill")
            |> pivot(rowKey: ["_time", "estimated"], columnKey: ["_field"], valueColumn: "_value")
        '''
        try:
            result = self.query_api.query(query, org=self.org)
            bills = []
            for table in result:
                for record in table.records:
                    values = record.values
                    # Bill end date is the timestamp, start is ~30 days before
                    end_time = values.get("_time", datetime.utcnow())
                    start_time = end_time - timedelta(days=30)
                    bills.append(BillSummary(
                        start_date=start_time,
                        end_date=end_time,
                        total_kwh=float(values.get("total_kwh", 0) or 0),
                        total_cost_dollars=float(values.get("total_cost_dollars", 0) or 0),
                        usage_charges_dollars=float(values.get("usage_charges_dollars", 0) or 0),
                        non_usage_charges_dollars=float(values.get("non_usage_charges_dollars", 0) or 0),
                        effective_rate_cents=float(values.get("effective_rate_cents", 0) or 0),
                        is_estimated=str(values.get("estimated", "false")).lower() == "true",
                    ))
            return bills
        except Exception as e:
            logger.error(f"Failed to get bills: {e}")
            return []

    def get_meter_comparison(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> MeterComparison:
        """Compare calculated EV charging costs vs actual meter costs."""
        # Get calculated costs from charging sessions
        sessions = self.get_sessions(start_date, end_date)
        calculated_kwh = sum(s.energy_wh / 1000 for s in sessions)
        calculated_cost = sum(s.full_cost_cents for s in sessions)

        # Get actual costs from meter
        meter_costs = self.get_meter_cost(start_date, end_date, "DAY")
        actual_kwh = sum(m.kwh for m in meter_costs)
        actual_cost = sum(m.cost_cents for m in meter_costs)

        # Calculate derived metrics
        ev_percentage = (calculated_kwh / actual_kwh * 100) if actual_kwh > 0 else 0
        calculated_rate = (calculated_cost / calculated_kwh) if calculated_kwh > 0 else 0
        actual_rate = (actual_cost / actual_kwh) if actual_kwh > 0 else 0

        return MeterComparison(
            start_date=start_date,
            end_date=end_date,
            calculated_kwh=calculated_kwh,
            calculated_cost_cents=calculated_cost,
            actual_kwh=actual_kwh,
            actual_cost_cents=actual_cost,
            ev_percentage_of_usage=ev_percentage,
            calculated_rate_cents=calculated_rate,
            actual_rate_cents=actual_rate,
        )


# Global client instance
influx_client = InfluxClient()
