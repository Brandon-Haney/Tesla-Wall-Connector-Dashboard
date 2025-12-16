"""Configuration management for the TWC Dashboard API."""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # InfluxDB
    influxdb_url: str = Field(default="http://localhost:8086", alias="INFLUXDB_URL")
    influxdb_token: str = Field(default="twc-dashboard-token", alias="INFLUXDB_TOKEN")
    influxdb_org: str = Field(default="home", alias="INFLUXDB_ORG")
    influxdb_bucket: str = Field(default="twc_dashboard", alias="INFLUXDB_BUCKET")

    # API Settings
    api_title: str = "TWC Dashboard API"
    api_version: str = "1.0.0"

    # ComEd delivery rate for full cost calculation (cents per kWh)
    comed_delivery_per_kwh: float = Field(default=7.5, alias="COMED_DELIVERY_PER_KWH")

    # Timezone
    tz: str = Field(default="America/Chicago", alias="TZ")

    class Config:
        env_file = ".env"
        extra = "ignore"


# Global settings instance
settings = Settings()
