"""Configuration management for the TWC Collector service."""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Dict, Optional
from pathlib import Path
import os


def load_secrets_file(secrets_path: str = ".secrets") -> dict:
    """Load secrets from a separate secrets file.

    The secrets file uses the same format as .env files.
    Returns a dict of key-value pairs.
    """
    secrets = {}

    # Check multiple locations for secrets file
    paths_to_check = [
        Path(secrets_path),  # Current directory
        Path("/app/.secrets"),  # Docker container path
        Path.home() / ".secrets",  # Home directory
    ]

    for path in paths_to_check:
        if path.exists():
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    # Skip comments and empty lines
                    if not line or line.startswith("#"):
                        continue
                    # Parse key=value
                    if "=" in line:
                        key, value = line.split("=", 1)
                        secrets[key.strip()] = value.strip()
            break  # Use first secrets file found

    return secrets


class ChargerConfig:
    """Represents a single Wall Connector configuration."""

    def __init__(self, name: str, ip: str):
        self.name = name
        self.ip = ip
        self.base_url = f"http://{ip}"

    def __repr__(self):
        return f"ChargerConfig(name={self.name}, ip={self.ip})"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # InfluxDB
    influxdb_url: str = Field(default="http://localhost:8086", alias="INFLUXDB_URL")
    influxdb_token: str = Field(default="twc-dashboard-token", alias="INFLUXDB_TOKEN")
    influxdb_org: str = Field(default="home", alias="INFLUXDB_ORG")
    influxdb_bucket: str = Field(default="twc_dashboard", alias="INFLUXDB_BUCKET")

    # Wall Connector(s) - format: "name1:ip1,name2:ip2"
    twc_chargers_raw: str = Field(default="garage:192.168.1.100", alias="TWC_CHARGERS")

    # Local TWC API (Legacy)
    # Set to False to disable local API polling entirely (saves resources when using Fleet API only)
    # Default: True for backwards compatibility
    local_twc_enabled: bool = Field(default=True, alias="LOCAL_TWC_ENABLED")

    # Polling intervals (seconds)
    # NOTE: These defaults have been increased since Fleet API is now the primary data source
    # For legacy setups without Fleet API, you may want to lower these in your .env
    twc_poll_vitals_interval: int = Field(default=30, alias="TWC_POLL_VITALS_INTERVAL")
    twc_poll_lifetime_interval: int = Field(default=300, alias="TWC_POLL_LIFETIME_INTERVAL")
    twc_poll_version_interval: int = Field(default=300, alias="TWC_POLL_VERSION_INTERVAL")
    twc_poll_wifi_interval: int = Field(default=300, alias="TWC_POLL_WIFI_INTERVAL")

    # ComEd
    comed_enabled: bool = Field(default=True, alias="COMED_ENABLED")
    comed_poll_interval: int = Field(default=300, alias="COMED_POLL_INTERVAL")
    comed_delivery_per_kwh: float = Field(default=0.075, alias="COMED_DELIVERY_PER_KWH")  # 7.5 cents

    # Tessie API (Phase 4)
    tessie_enabled: bool = Field(default=False, alias="TESSIE_ENABLED")
    tessie_poll_interval: int = Field(default=60, alias="TESSIE_POLL_INTERVAL")
    # Access token loaded from .secrets file, not environment
    tessie_access_token: Optional[str] = Field(default=None, alias="TESSIE_ACCESS_TOKEN")

    # Fleet API Energy Site (for Wall Connector data via Tesla Fleet API)
    # The energy_site_id can be found by calling GET /api/1/products with your Tessie token
    # This enables polling Wall Connector data for leader/follower setups where
    # follower units cannot be accessed via local API
    fleet_energy_site_id: Optional[str] = Field(default=None, alias="FLEET_ENERGY_SITE_ID")
    fleet_twc_poll_interval: int = Field(default=30, alias="FLEET_TWC_POLL_INTERVAL")
    fleet_charge_history_interval: int = Field(default=900, alias="FLEET_CHARGE_HISTORY_INTERVAL")  # 15 min default

    # Fleet API session recording thresholds (Step 4.5.9)
    # Sessions below these thresholds are not recorded (e.g., brief plug-ins)
    fleet_session_min_energy_kwh: float = Field(default=0.1, alias="FLEET_SESSION_MIN_ENERGY_KWH")
    fleet_session_min_duration_s: int = Field(default=60, alias="FLEET_SESSION_MIN_DURATION_S")

    # Wall Connector friendly names - format: "serial:Name,serial:Name"
    # Serial is the last part of the DIN (e.g., "ABC12345678:Garage Right,DEF98765432:Garage Left")
    # If not specified, units are named "Leader", "Follower 2", etc.
    twc_unit_names_raw: str = Field(default="", alias="TWC_UNIT_NAMES")

    # Vehicle friendly names - format: "VIN:Name,VIN:Name"
    # Used when Tessie returns empty display_name (e.g., when vehicles are asleep)
    # Example: "5YJ3E1ECXPF000001:Model 3,5YJSA1E50NF000002:Model S"
    vehicle_names_raw: str = Field(default="", alias="VEHICLE_NAMES")

    # Target ID to vehicle name mapping - format: "target_id:Name,target_id:Name"
    # Used for Fleet API charge history where target_id (UUID) identifies the vehicle
    # Find target_ids in InfluxDB: from(bucket: "twc_dashboard") |> filter(fn: (r) => r._measurement == "fleet_charge_session") |> keep(columns: ["target_id"]) |> distinct()
    target_id_vehicles_raw: str = Field(default="", alias="TARGET_ID_VEHICLES")

    # Timezone
    tz: str = Field(default="America/Chicago", alias="TZ")

    # Smart Charging (Phase 4.4)
    smart_charging_enabled: bool = Field(default=False, alias="SMART_CHARGING_ENABLED")
    smart_charging_control_enabled: bool = Field(default=False, alias="SMART_CHARGING_CONTROL_ENABLED")
    smart_charging_lookback_days: int = Field(default=30, alias="SMART_CHARGING_LOOKBACK_DAYS")
    smart_charging_stop_percentile: int = Field(default=90, alias="SMART_CHARGING_STOP_PERCENTILE")
    smart_charging_resume_percentile: int = Field(default=75, alias="SMART_CHARGING_RESUME_PERCENTILE")
    smart_charging_min_interval: int = Field(default=600, alias="SMART_CHARGING_MIN_INTERVAL")

    class Config:
        env_file = ".env"
        extra = "ignore"

    @property
    def chargers(self) -> list[ChargerConfig]:
        """Parse charger configuration string into ChargerConfig objects."""
        chargers = []
        for entry in self.twc_chargers_raw.split(","):
            entry = entry.strip()
            if ":" in entry:
                name, ip = entry.split(":", 1)
                chargers.append(ChargerConfig(name=name.strip(), ip=ip.strip()))
        return chargers

    @property
    def twc_unit_names(self) -> Dict[str, str]:
        """Parse TWC unit names into a serial -> name mapping."""
        names = {}
        if not self.twc_unit_names_raw:
            return names
        for entry in self.twc_unit_names_raw.split(","):
            entry = entry.strip()
            if ":" in entry:
                serial, name = entry.split(":", 1)
                names[serial.strip()] = name.strip()
        return names

    @property
    def vehicle_names(self) -> Dict[str, str]:
        """Parse vehicle names into a VIN -> name mapping."""
        names = {}
        if not self.vehicle_names_raw:
            return names
        for entry in self.vehicle_names_raw.split(","):
            entry = entry.strip()
            if ":" in entry:
                vin, name = entry.split(":", 1)
                names[vin.strip()] = name.strip()
        return names

    @property
    def target_id_vehicles(self) -> Dict[str, str]:
        """Parse target ID to vehicle name mapping."""
        names = {}
        if not self.target_id_vehicles_raw:
            return names
        for entry in self.target_id_vehicles_raw.split(","):
            entry = entry.strip()
            if ":" in entry:
                target_id, name = entry.split(":", 1)
                names[target_id.strip()] = name.strip()
        return names

    def get_vehicle_name_from_target_id(self, target_id: str) -> str:
        """Get vehicle name from Fleet API target_id.

        Args:
            target_id: Fleet API target_id (UUID)

        Returns:
            Vehicle name if configured, otherwise truncated target_id
        """
        if target_id in self.target_id_vehicles:
            return self.target_id_vehicles[target_id]
        # Return truncated target_id as fallback
        return f"...{target_id[-8:]}" if target_id else ""

    def get_vehicle_friendly_name(self, vin: str, fallback: str = "") -> str:
        """Get friendly name for a vehicle.

        Args:
            vin: Vehicle VIN
            fallback: Fallback name if VIN not in config (e.g., from Tessie API)

        Returns:
            Friendly name if configured, otherwise fallback or truncated VIN
        """
        if vin in self.vehicle_names:
            return self.vehicle_names[vin]
        if fallback:
            return fallback
        # Return truncated VIN as last resort
        return f"...{vin[-6:]}" if vin else ""

    def get_twc_friendly_name(self, din: str, unit_number: int) -> str:
        """Get friendly name for a Wall Connector.

        Args:
            din: Full DIN (e.g., "1457768-02-G--ABC12345678")
            unit_number: Unit number (1=leader, 2+=followers)

        Returns:
            Friendly name if configured, otherwise "leader" or "follower_N"
        """
        # Extract serial from DIN
        serial = din.split("--")[-1] if "--" in din else din

        # Check if we have a configured name for this serial
        if serial in self.twc_unit_names:
            return self.twc_unit_names[serial]

        # Default naming based on position (lowercase with underscores for consistency)
        if unit_number == 1:
            return "leader"
        else:
            return f"follower_{unit_number}"


def create_settings() -> Settings:
    """Create settings instance, loading secrets from .secrets file."""
    # Load secrets file first
    secrets = load_secrets_file()

    # Merge secrets into environment (secrets file takes precedence for sensitive values)
    # This allows Settings to pick them up via pydantic's env loading
    for key, value in secrets.items():
        # Only set if not already in environment (env vars take precedence)
        # Actually, for secrets we want the .secrets file to be authoritative
        if key.startswith("TESSIE_") or key.endswith("_TOKEN") or key.endswith("_PASSWORD"):
            os.environ[key] = value

    return Settings()


# Global settings instance
settings = create_settings()
