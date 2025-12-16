"""API response models."""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class ChargerStatus(BaseModel):
    """Current status of a wall connector."""

    charger_id: str
    timestamp: datetime
    power_w: float = 0.0
    grid_v: float = 0.0
    grid_hz: float = 0.0
    vehicle_current_a: float = 0.0
    vehicle_connected: bool = False
    contactor_closed: bool = False
    session_energy_wh: float = 0.0
    session_duration_s: int = 0
    pcba_temp_c: float = 0.0
    handle_temp_c: float = 0.0
    mcu_temp_c: float = 0.0
    uptime_s: int = 0

    @property
    def is_charging(self) -> bool:
        return self.contactor_closed and self.vehicle_current_a > 0


class ChargerLifetime(BaseModel):
    """Lifetime statistics for a wall connector."""

    charger_id: str
    timestamp: datetime
    energy_wh: float = 0.0
    charge_starts: int = 0
    charging_time_s: int = 0
    uptime_s: int = 0
    contactor_cycles: int = 0
    alert_count: int = 0


class ChargerInfo(BaseModel):
    """Version and hardware info for a wall connector."""

    charger_id: str
    firmware_version: str = ""
    part_number: str = ""
    serial_number: str = ""


class CurrentPrice(BaseModel):
    """Current electricity price."""

    timestamp: datetime
    price_cents_kwh: float
    price_type: str  # "5min" or "hourly_avg"
    full_rate_cents_kwh: float  # Including delivery


class ChargingSession(BaseModel):
    """A completed or active charging session."""

    charger_id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_s: int = 0
    energy_wh: float = 0.0
    supply_cost_cents: float = 0.0
    full_cost_cents: float = 0.0
    avg_price_cents: float = 0.0
    peak_power_w: float = 0.0
    is_active: bool = False


class SessionSummary(BaseModel):
    """Summary statistics for a time period."""

    start_date: datetime
    end_date: datetime
    total_sessions: int = 0
    total_energy_wh: float = 0.0
    total_supply_cost_cents: float = 0.0
    total_full_cost_cents: float = 0.0
    avg_price_cents: float = 0.0
    total_duration_s: int = 0


class EnergyDataPoint(BaseModel):
    """A single energy data point for time series."""

    timestamp: datetime
    energy_wh: float = 0.0
    power_w: float = 0.0
    price_cents: Optional[float] = None


class ExportRequest(BaseModel):
    """Request parameters for data export."""

    start_date: datetime
    end_date: datetime
    charger_id: Optional[str] = None  # None = all chargers
    include_sessions: bool = True
    include_energy: bool = True
    include_prices: bool = True


class HealthStatus(BaseModel):
    """API health status."""

    status: str = "healthy"
    influxdb_connected: bool = False
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    version: str = "1.0.0"


# =============================================================================
# Vehicle Models (Tessie Integration)
# =============================================================================

class VehicleStatus(BaseModel):
    """Current status of a Tesla vehicle from Tessie."""

    vin: str
    display_name: str
    timestamp: datetime
    state: str = "unknown"  # online, asleep, offline, driving
    battery_level: int = 0
    battery_range: float = 0.0
    charging_state: str = "Unknown"  # Charging, Complete, Disconnected, Stopped
    charge_limit_soc: int = 0
    charger_power: float = 0.0
    charge_amps: int = 0
    charger_voltage: int = 0
    charge_energy_added: float = 0.0
    time_to_full_charge: float = 0.0
    charge_port_door_open: bool = False
    charge_port_latch: str = ""
    conn_charge_cable: str = ""
    inside_temp: Optional[float] = None
    outside_temp: Optional[float] = None
    climate_on: bool = False


class VehicleSession(BaseModel):
    """A Tesla vehicle charging session from Tessie."""

    vin: str
    display_name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_s: int = 0
    energy_added_kwh: float = 0.0
    starting_battery_level: int = 0
    ending_battery_level: int = 0
    soc_gained: int = 0
    peak_power_kw: float = 0.0
    charger_type: str = ""  # TWC, Supercharger, etc.
