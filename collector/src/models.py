"""Data models for Tesla Wall Connector API responses."""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class TWCVitals(BaseModel):
    """Real-time vitals from the Wall Connector."""

    contactor_closed: bool = False
    vehicle_connected: bool = False
    session_s: int = 0
    grid_v: float = 0.0
    grid_hz: float = 0.0
    vehicle_current_a: float = 0.0
    currentA_a: float = 0.0
    currentB_a: float = 0.0
    currentC_a: float = 0.0
    currentN_a: float = 0.0
    voltageA_v: float = 0.0
    voltageB_v: float = 0.0
    voltageC_v: float = 0.0
    relay_coil_v: float = 0.0
    pcba_temp_c: float = 0.0
    handle_temp_c: float = 0.0
    mcu_temp_c: float = 0.0
    uptime_s: int = 0
    input_thermopile_uv: int = 0
    prox_v: float = 0.0
    pilot_high_v: float = 0.0
    pilot_low_v: float = 0.0
    session_energy_wh: float = 0.0
    config_status: int = 0
    evse_state: int = 0
    current_alerts: List[str] = Field(default_factory=list)

    @property
    def power_w(self) -> float:
        """Calculate current power draw in watts."""
        # P = V * I for single phase, or sum of phases
        # Using grid voltage and vehicle current for simplicity
        return self.grid_v * self.vehicle_current_a

    @property
    def session_energy_kwh(self) -> float:
        """Session energy in kWh."""
        return self.session_energy_wh / 1000.0

    @property
    def is_charging(self) -> bool:
        """Check if actively charging."""
        return self.contactor_closed and self.vehicle_current_a > 0


class TWCLifetime(BaseModel):
    """Lifetime statistics from the Wall Connector."""

    contactor_cycles: int = 0
    contactor_cycles_loaded: int = 0
    alert_count: int = 0
    thermal_foldbacks: int = 0
    avg_startup_temp: float = 0.0
    charge_starts: int = 0
    energy_wh: float = 0.0
    connector_cycles: int = 0
    uptime_s: int = 0
    charging_time_s: int = 0

    @property
    def energy_kwh(self) -> float:
        """Lifetime energy in kWh."""
        return self.energy_wh / 1000.0

    @property
    def charging_hours(self) -> float:
        """Total charging time in hours."""
        return self.charging_time_s / 3600.0

    @property
    def uptime_days(self) -> float:
        """Total uptime in days."""
        return self.uptime_s / 86400.0


class TWCVersion(BaseModel):
    """Version information from the Wall Connector."""

    firmware_version: str = ""
    git_branch: str = ""
    part_number: str = ""
    serial_number: str = ""
    web_service: Optional[str] = None


class TWCWifiStatus(BaseModel):
    """WiFi status from the Wall Connector."""

    wifi_ssid: str = ""
    wifi_signal_strength: int = 0
    wifi_rssi: int = 0
    wifi_snr: int = 0
    wifi_connected: bool = False
    wifi_infra_ip: str = ""
    internet: bool = False
    wifi_mac: str = ""


class ComEdPrice(BaseModel):
    """ComEd hourly pricing data point."""

    millisUTC: int
    price: str  # Price comes as string from API

    @property
    def price_cents(self) -> float:
        """Price in cents per kWh."""
        return float(self.price)

    @property
    def price_dollars(self) -> float:
        """Price in dollars per kWh."""
        return float(self.price) / 100.0

    @property
    def timestamp(self) -> datetime:
        """Convert millisUTC to datetime."""
        return datetime.utcfromtimestamp(self.millisUTC / 1000.0)


class ChargingSession(BaseModel):
    """Represents a charging session (derived data)."""

    charger_name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_s: int = 0
    energy_wh: float = 0.0
    peak_power_w: float = 0.0
    avg_power_w: float = 0.0
    total_cost_cents: float = 0.0
    avg_price_cents: float = 0.0
    is_active: bool = True


# =============================================================================
# Fleet API Wall Connector Models (via Tessie/Tesla Fleet API)
# =============================================================================

class FleetWallConnector(BaseModel):
    """Wall Connector data from Tesla Fleet API live_status endpoint.

    This provides real-time data for all Wall Connectors in a power-sharing setup,
    including follower units that cannot be accessed via local API.
    """

    # Device Identification Number (format: "1457768-02-G--ABC12345678")
    din: str
    # Vehicle VIN currently connected (if any)
    vin: Optional[str] = None

    # State values (based on observed data):
    # wall_connector_state: 1=charging, 4=idle/connected, etc.
    # wall_connector_fault_state: 2=no fault, 8=? (possibly power limiting)
    wall_connector_state: int = 0
    wall_connector_fault_state: int = 0

    # Power in watts (e.g., 6826.141)
    wall_connector_power: float = 0.0

    # OCPP status: 1=connected
    ocpp_status: int = 0

    # Power sharing session state: 1=active
    powershare_session_state: int = 0

    @property
    def serial_number(self) -> str:
        """Extract serial number from DIN.

        DIN format: "1457768-02-G--ABC12345678"
        Serial is the last part after "--"
        """
        if "--" in self.din:
            return self.din.split("--")[-1]
        return self.din

    @property
    def is_leader(self) -> bool:
        """Check if this is the leader (primary) unit.

        In the DIN "1457768-01-G--xxx", the "01" typically indicates leader.
        "02", "03", etc. are followers.
        """
        parts = self.din.split("-")
        if len(parts) >= 2:
            return parts[1] == "01"
        return False

    @property
    def unit_number(self) -> int:
        """Get the unit number (1=leader, 2+=followers)."""
        parts = self.din.split("-")
        if len(parts) >= 2:
            try:
                return int(parts[1])
            except ValueError:
                return 0
        return 0

    @property
    def is_charging(self) -> bool:
        """Check if this unit is actively charging."""
        # wall_connector_state 1 appears to be charging
        return self.wall_connector_state == 1 and self.wall_connector_power > 0

    @property
    def is_connected(self) -> bool:
        """Check if a vehicle is connected."""
        return self.vin is not None and len(self.vin) > 0

    @property
    def power_kw(self) -> float:
        """Power in kilowatts."""
        return self.wall_connector_power / 1000.0

    @property
    def state_name(self) -> str:
        """Human-readable state name."""
        # Based on observed values - may need refinement
        state_map = {
            0: "Unknown",
            1: "Charging",
            2: "Ready",
            3: "Waiting",
            4: "Connected",
            5: "Disconnected",
        }
        return state_map.get(self.wall_connector_state, f"State {self.wall_connector_state}")

    @property
    def fault_name(self) -> str:
        """Human-readable fault state name."""
        # Based on observed values - may need refinement
        fault_map = {
            0: "Unknown",
            2: "No Fault",
            8: "Power Limited",  # Possibly thermal or power sharing limit
        }
        return fault_map.get(self.wall_connector_fault_state, f"Fault {self.wall_connector_fault_state}")

    @classmethod
    def from_api_response(cls, data: dict) -> "FleetWallConnector":
        """Create from Fleet API live_status response."""
        return cls(
            din=data.get("din", ""),
            vin=data.get("vin"),
            wall_connector_state=data.get("wall_connector_state", 0),
            wall_connector_fault_state=data.get("wall_connector_fault_state", 0),
            wall_connector_power=data.get("wall_connector_power", 0.0),
            ocpp_status=data.get("ocpp_status", 0),
            powershare_session_state=data.get("powershare_session_state", 0),
        )


class FleetEnergySiteLiveStatus(BaseModel):
    """Live status response from Fleet API energy site endpoint.

    Contains real-time data for all Wall Connectors at a site.
    """

    wall_connectors: List[FleetWallConnector] = Field(default_factory=list)
    timestamp: Optional[datetime] = None

    @classmethod
    def from_api_response(cls, data: dict) -> "FleetEnergySiteLiveStatus":
        """Create from Fleet API response."""
        response = data.get("response", data)

        wall_connectors = []
        for wc_data in response.get("wall_connectors", []):
            wall_connectors.append(FleetWallConnector.from_api_response(wc_data))

        # Parse timestamp if present
        timestamp = None
        ts_str = response.get("timestamp")
        if ts_str:
            try:
                # Format: "2025-12-07T00:00:09-06:00"
                timestamp = datetime.fromisoformat(ts_str)
            except (ValueError, TypeError):
                pass

        return cls(
            wall_connectors=wall_connectors,
            timestamp=timestamp,
        )


# =============================================================================
# Tessie API Models (Phase 4)
# =============================================================================

class TessieChargeState(BaseModel):
    """Charge state from Tessie API.

    Note: Many fields can be None when vehicle is asleep or data unavailable.
    """

    # Battery state
    battery_level: Optional[int] = 0
    usable_battery_level: Optional[int] = 0
    battery_range: Optional[float] = 0.0
    est_battery_range: Optional[float] = 0.0
    ideal_battery_range: Optional[float] = 0.0

    # Charge settings
    charge_limit_soc: Optional[int] = 0
    charge_limit_soc_std: Optional[int] = 0
    charge_limit_soc_min: Optional[int] = 0
    charge_limit_soc_max: Optional[int] = 0

    # Charging status
    charging_state: Optional[str] = "Disconnected"  # Charging, Complete, Disconnected, Stopped
    charge_amps: Optional[int] = 0
    charger_actual_current: Optional[int] = 0
    charger_voltage: Optional[int] = 0
    charger_power: Optional[int] = 0  # kW
    charger_phases: Optional[int] = None
    charge_rate: Optional[float] = 0.0  # miles/hour

    # Session data
    charge_energy_added: Optional[float] = 0.0  # kWh
    charge_miles_added_rated: Optional[float] = 0.0
    charge_miles_added_ideal: Optional[float] = 0.0

    # Time estimates
    time_to_full_charge: Optional[float] = 0.0  # hours
    minutes_to_full_charge: Optional[int] = 0

    # Charger identification
    conn_charge_cable: Optional[str] = ""  # "SAE" for J1772/TWC, "IEC" for European
    fast_charger_type: Optional[str] = ""
    fast_charger_brand: Optional[str] = ""
    fast_charger_present: Optional[bool] = False

    # Charge port
    charge_port_door_open: Optional[bool] = False
    charge_port_latch: Optional[str] = ""  # "Engaged", "Disengaged"
    charge_port_color: Optional[str] = ""

    # Scheduling
    scheduled_charging_pending: Optional[bool] = False
    scheduled_charging_start_time: Optional[int] = None

    # Battery health / telemetry (available on some models via Fleet Telemetry)
    pack_voltage: Optional[float] = None  # Battery pack voltage (e.g., 452.66V)
    pack_current: Optional[float] = None  # Battery pack current (e.g., -0.5A)
    module_temp_min: Optional[float] = None  # Min battery module temp (°C)
    module_temp_max: Optional[float] = None  # Max battery module temp (°C)
    energy_remaining: Optional[float] = None  # kWh remaining in pack
    lifetime_energy_used: Optional[float] = None  # Total kWh used lifetime

    @property
    def is_charging(self) -> bool:
        """Check if vehicle is actively charging."""
        return self.charging_state == "Charging" if self.charging_state else False

    @property
    def is_connected(self) -> bool:
        """Check if vehicle is connected to a charger."""
        return self.charging_state not in (None, "Disconnected") if self.charging_state else False

    @property
    def is_wall_connector(self) -> bool:
        """Check if connected to a Tesla Wall Connector (likely)."""
        # SAE cable type is used by Wall Connector and J1772
        # No fast charger present indicates AC charging (TWC)
        return self.conn_charge_cable == "SAE" and not (self.fast_charger_present or False)

    @classmethod
    def from_api_response(cls, data: dict) -> "TessieChargeState":
        """Create from Tessie API response."""
        return cls(
            battery_level=data.get("battery_level", 0),
            usable_battery_level=data.get("usable_battery_level", 0),
            battery_range=data.get("battery_range", 0.0),
            est_battery_range=data.get("est_battery_range", 0.0),
            ideal_battery_range=data.get("ideal_battery_range", 0.0),
            charge_limit_soc=data.get("charge_limit_soc", 0),
            charge_limit_soc_std=data.get("charge_limit_soc_std", 0),
            charge_limit_soc_min=data.get("charge_limit_soc_min", 0),
            charge_limit_soc_max=data.get("charge_limit_soc_max", 0),
            charging_state=data.get("charging_state", "Disconnected"),
            charge_amps=data.get("charge_amps", 0),
            charger_actual_current=data.get("charger_actual_current", 0),
            charger_voltage=data.get("charger_voltage", 0),
            charger_power=data.get("charger_power", 0),
            charger_phases=data.get("charger_phases"),
            charge_rate=data.get("charge_rate", 0.0),
            charge_energy_added=data.get("charge_energy_added", 0.0),
            charge_miles_added_rated=data.get("charge_miles_added_rated", 0.0),
            charge_miles_added_ideal=data.get("charge_miles_added_ideal", 0.0),
            time_to_full_charge=data.get("time_to_full_charge", 0.0),
            minutes_to_full_charge=data.get("minutes_to_full_charge", 0),
            conn_charge_cable=data.get("conn_charge_cable", ""),
            fast_charger_type=data.get("fast_charger_type", ""),
            fast_charger_brand=data.get("fast_charger_brand", ""),
            fast_charger_present=data.get("fast_charger_present", False),
            charge_port_door_open=data.get("charge_port_door_open", False),
            charge_port_latch=data.get("charge_port_latch", ""),
            charge_port_color=data.get("charge_port_color", ""),
            scheduled_charging_pending=data.get("scheduled_charging_pending", False),
            scheduled_charging_start_time=data.get("scheduled_charging_start_time"),
            # Battery health fields (from Fleet Telemetry, may not be available on all vehicles)
            pack_voltage=data.get("pack_voltage"),
            pack_current=data.get("pack_current"),
            module_temp_min=data.get("module_temp_min"),
            module_temp_max=data.get("module_temp_max"),
            energy_remaining=data.get("energy_remaining"),
            lifetime_energy_used=data.get("lifetime_energy_used"),
        )


class TessieVehicle(BaseModel):
    """Vehicle data from Tessie API.

    Note: Many fields can be None when vehicle is asleep or data unavailable.
    """

    # Identification
    vin: str
    display_name: Optional[str] = ""
    is_active: bool = True
    state: Optional[str] = "offline"  # online, asleep, offline, driving, charging

    # Vehicle info
    car_type: Optional[str] = ""  # model3, modely, models, modelx
    car_version: Optional[str] = ""  # Software version
    odometer: Optional[float] = 0.0

    # Battery (from charge_state)
    battery_level: Optional[int] = 0
    usable_battery_level: Optional[int] = 0
    battery_range: Optional[float] = 0.0
    charge_limit_soc: Optional[int] = 0

    # Charging (from charge_state)
    charging_state: Optional[str] = "Disconnected"
    charger_power: Optional[int] = 0
    charge_amps: Optional[int] = 0
    charger_voltage: Optional[int] = 0
    charge_energy_added: Optional[float] = 0.0
    time_to_full_charge: Optional[float] = 0.0
    conn_charge_cable: Optional[str] = ""
    fast_charger_present: Optional[bool] = False

    # Location (from drive_state)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    heading: Optional[int] = None

    # Climate (from climate_state)
    inside_temp: Optional[float] = None
    outside_temp: Optional[float] = None
    is_preconditioning: Optional[bool] = False
    battery_heater: Optional[bool] = False

    # Charge state (full object for detailed access)
    charge_state: Optional[TessieChargeState] = None

    @property
    def is_charging(self) -> bool:
        """Check if vehicle is actively charging."""
        return self.charging_state == "Charging" or self.state == "charging"

    @property
    def is_connected(self) -> bool:
        """Check if vehicle is connected to a charger."""
        return self.charging_state not in (None, "Disconnected") if self.charging_state else False

    @property
    def model_name(self) -> str:
        """Get human-readable model name."""
        model_map = {
            "model3": "Model 3",
            "modely": "Model Y",
            "models": "Model S",
            "modelx": "Model X",
            "lychee": "Model S",  # Code name
            "tamarind": "Model X",  # Code name
        }
        return model_map.get(self.car_type.lower(), self.car_type)

    @classmethod
    def from_api_response(cls, data: dict) -> "TessieVehicle":
        """Create from Tessie API response."""
        charge_state_data = data.get("charge_state", {})
        drive_state = data.get("drive_state", {})
        climate_state = data.get("climate_state", {})
        vehicle_state = data.get("vehicle_state", {})
        vehicle_config = data.get("vehicle_config", {})

        charge_state = None
        if charge_state_data:
            charge_state = TessieChargeState.from_api_response(charge_state_data)

        return cls(
            vin=data.get("vin", ""),
            display_name=data.get("display_name", vehicle_state.get("vehicle_name", "")),
            is_active=data.get("is_active", True),
            state=data.get("state", "offline"),
            car_type=vehicle_config.get("car_type", ""),
            car_version=vehicle_state.get("car_version", ""),
            odometer=vehicle_state.get("odometer", 0.0),
            # Battery from charge_state
            battery_level=charge_state_data.get("battery_level", 0),
            usable_battery_level=charge_state_data.get("usable_battery_level", 0),
            battery_range=charge_state_data.get("battery_range", 0.0),
            charge_limit_soc=charge_state_data.get("charge_limit_soc", 0),
            # Charging from charge_state
            charging_state=charge_state_data.get("charging_state", "Disconnected"),
            charger_power=charge_state_data.get("charger_power", 0),
            charge_amps=charge_state_data.get("charge_amps", 0),
            charger_voltage=charge_state_data.get("charger_voltage", 0),
            charge_energy_added=charge_state_data.get("charge_energy_added", 0.0),
            time_to_full_charge=charge_state_data.get("time_to_full_charge", 0.0),
            conn_charge_cable=charge_state_data.get("conn_charge_cable", ""),
            fast_charger_present=charge_state_data.get("fast_charger_present", False),
            # Location from drive_state
            latitude=drive_state.get("latitude"),
            longitude=drive_state.get("longitude"),
            heading=drive_state.get("heading"),
            # Climate from climate_state
            inside_temp=climate_state.get("inside_temp"),
            outside_temp=climate_state.get("outside_temp"),
            is_preconditioning=climate_state.get("is_preconditioning", False),
            battery_heater=climate_state.get("battery_heater", False),
            # Full charge state object
            charge_state=charge_state,
        )


class TessieCharge(BaseModel):
    """Charging session from Tessie charge history API."""

    id: int
    started_at: int  # Unix timestamp
    ended_at: Optional[int] = None  # Unix timestamp
    location: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_supercharger: bool = False
    odometer: float = 0.0
    energy_added: float = 0.0  # kWh
    energy_used: float = 0.0  # kWh
    miles_added: float = 0.0
    miles_added_ideal: float = 0.0
    starting_battery: int = 0
    ending_battery: int = 0
    cost: Optional[float] = None  # dollars

    @property
    def start_time(self) -> datetime:
        """Start time as datetime."""
        return datetime.utcfromtimestamp(self.started_at)

    @property
    def end_time(self) -> Optional[datetime]:
        """End time as datetime."""
        if self.ended_at:
            return datetime.utcfromtimestamp(self.ended_at)
        return None

    @property
    def duration_minutes(self) -> Optional[int]:
        """Duration in minutes."""
        if self.ended_at:
            return (self.ended_at - self.started_at) // 60
        return None

    @property
    def is_home_charge(self) -> bool:
        """Likely a home charge (not Supercharger)."""
        return not self.is_supercharger

    @classmethod
    def from_api_response(cls, data: dict) -> "TessieCharge":
        """Create from Tessie API response."""
        return cls(
            id=data.get("id", 0),
            started_at=data.get("started_at", 0),
            ended_at=data.get("ended_at"),
            location=data.get("location", ""),
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
            is_supercharger=data.get("is_supercharger", False),
            odometer=data.get("odometer", 0.0),
            energy_added=data.get("energy_added", 0.0),
            energy_used=data.get("energy_used", 0.0),
            miles_added=data.get("miles_added", 0.0),
            miles_added_ideal=data.get("miles_added_ideal", 0.0),
            starting_battery=data.get("starting_battery", 0),
            ending_battery=data.get("ending_battery", 0),
            cost=data.get("cost"),
        )


class VehicleChargingSession(BaseModel):
    """Represents a vehicle charging session tracked by the collector.

    This is distinct from TessieCharge (Tessie's historical record) - this tracks
    sessions in real-time from the vehicle's perspective during charging.
    """

    vin: str
    display_name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    starting_battery_level: int = 0
    ending_battery_level: int = 0
    starting_range: float = 0.0
    ending_range: float = 0.0
    energy_added_kwh: float = 0.0  # From vehicle's charge_energy_added
    miles_added: float = 0.0
    peak_power_kw: float = 0.0
    avg_power_kw: float = 0.0
    charger_type: str = ""  # "Wall Connector", "Supercharger", "J1772", etc.
    is_home_charge: bool = True
    is_active: bool = True
    # Location for matching with chargers
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @property
    def duration_s(self) -> float:
        """Duration in seconds."""
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return (datetime.now(timezone.utc) - self.start_time).total_seconds()

    @property
    def duration_min(self) -> float:
        """Duration in minutes."""
        return self.duration_s / 60.0

    @property
    def soc_gained(self) -> int:
        """SOC percentage gained during session."""
        return self.ending_battery_level - self.starting_battery_level


# =============================================================================
# Fleet API Charge Session (from telemetry_history?kind=charge)
# =============================================================================

class FleetChargeSession(BaseModel):
    """Charging session from Fleet API telemetry_history endpoint.

    This provides historical charging data for Wall Connectors from the Fleet API,
    which is more comprehensive than local API data and includes:
    - Data for ALL Wall Connectors (leader + followers)
    - Which vehicle charged (target_id)
    - Which Wall Connector unit (din)
    - Energy added and duration

    Data structure from API:
    {
        "charge_start_time": {"seconds": 1764570574},
        "charge_duration": {"seconds": 8199},
        "energy_added_wh": 21508,
        "target_id": {"text": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
        "din": "1457768-02-G--ABC12345678"
    }
    """

    # Session timing
    start_timestamp: int  # Unix timestamp (from charge_start_time.seconds)
    duration_s: int  # Duration in seconds

    # Energy
    energy_wh: float  # Energy added in watt-hours

    # Device identification
    din: str  # Wall Connector DIN (e.g., "1457768-02-G--ABC12345678")
    target_id: str  # Vehicle UUID from Fleet API

    # Optional: Vehicle name (populated after lookup)
    vehicle_name: Optional[str] = None

    # Cost data (calculated from historical ComEd prices)
    avg_price_cents: Optional[float] = None  # Average supply price during session
    supply_cost_cents: Optional[float] = None  # Supply cost only
    delivery_cost_cents: Optional[float] = None  # Delivery cost (fixed rate * kWh)
    full_cost_cents: Optional[float] = None  # Total cost (supply + delivery)

    @property
    def start_time(self) -> datetime:
        """Start time as datetime (UTC)."""
        from datetime import timezone
        return datetime.fromtimestamp(self.start_timestamp, tz=timezone.utc)

    @property
    def end_time(self) -> datetime:
        """End time as datetime (UTC)."""
        from datetime import timezone
        return datetime.fromtimestamp(self.start_timestamp + self.duration_s, tz=timezone.utc)

    @property
    def energy_kwh(self) -> float:
        """Energy added in kilowatt-hours."""
        return self.energy_wh / 1000.0

    @property
    def duration_min(self) -> float:
        """Duration in minutes."""
        return self.duration_s / 60.0

    @property
    def duration_hours(self) -> float:
        """Duration in hours."""
        return self.duration_s / 3600.0

    @property
    def avg_power_kw(self) -> float:
        """Average power in kilowatts."""
        if self.duration_s > 0:
            return self.energy_kwh / self.duration_hours
        return 0.0

    @property
    def serial_number(self) -> str:
        """Extract serial number from DIN.

        DIN format: "1457768-02-G--ABC12345678"
        Serial is the last part after "--"
        """
        if "--" in self.din:
            return self.din.split("--")[-1]
        return self.din

    @property
    def unit_number(self) -> int:
        """Get the unit number (1=leader, 2+=followers) from DIN."""
        parts = self.din.split("-")
        if len(parts) >= 2:
            try:
                return int(parts[1])
            except ValueError:
                return 0
        return 0

    @property
    def is_leader(self) -> bool:
        """Check if this session was on the leader unit."""
        return self.unit_number == 1

    @property
    def unit_name(self) -> str:
        """Get a friendly unit name (lowercase for consistency with config)."""
        if self.is_leader:
            return "leader"
        return f"follower_{self.unit_number}"

    @classmethod
    def from_api_response(cls, data: dict) -> "FleetChargeSession":
        """Create from Fleet API telemetry_history response item."""
        return cls(
            start_timestamp=data.get("charge_start_time", {}).get("seconds", 0),
            duration_s=data.get("charge_duration", {}).get("seconds", 0),
            energy_wh=data.get("energy_added_wh", 0),
            din=data.get("din", ""),
            target_id=data.get("target_id", {}).get("text", ""),
        )
