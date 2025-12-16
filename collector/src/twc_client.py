"""Tesla Wall Connector API client."""

import aiohttp
import asyncio
import logging
from typing import Optional
from .models import TWCVitals, TWCLifetime, TWCVersion, TWCWifiStatus
from .config import ChargerConfig

logger = logging.getLogger(__name__)


class TWCClient:
    """Async client for Tesla Wall Connector Gen 3 API."""

    ENDPOINTS = {
        "vitals": "/api/1/vitals",
        "lifetime": "/api/1/lifetime",
        "version": "/api/1/version",
        "wifi_status": "/api/1/wifi_status",
    }

    def __init__(self, charger: ChargerConfig, timeout: int = 10):
        self.charger = charger
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _fetch(self, endpoint: str) -> Optional[dict]:
        """Fetch data from an endpoint."""
        url = f"{self.charger.base_url}{endpoint}"
        try:
            session = await self._get_session()
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.warning(
                        f"[{self.charger.name}] HTTP {response.status} from {endpoint}"
                    )
                    return None
        except asyncio.TimeoutError:
            logger.error(f"[{self.charger.name}] Timeout fetching {endpoint}")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"[{self.charger.name}] Error fetching {endpoint}: {e}")
            return None
        except Exception as e:
            logger.error(f"[{self.charger.name}] Unexpected error fetching {endpoint}: {e}")
            return None

    async def get_vitals(self) -> Optional[TWCVitals]:
        """Fetch current vitals."""
        data = await self._fetch(self.ENDPOINTS["vitals"])
        if data:
            try:
                return TWCVitals(**data)
            except Exception as e:
                logger.error(f"[{self.charger.name}] Error parsing vitals: {e}")
        return None

    async def get_lifetime(self) -> Optional[TWCLifetime]:
        """Fetch lifetime statistics."""
        data = await self._fetch(self.ENDPOINTS["lifetime"])
        if data:
            try:
                # Handle potential 'nan' values in JSON (firmware bug)
                for key, value in data.items():
                    if isinstance(value, float) and (value != value):  # NaN check
                        data[key] = 0.0
                return TWCLifetime(**data)
            except Exception as e:
                logger.error(f"[{self.charger.name}] Error parsing lifetime: {e}")
        return None

    async def get_version(self) -> Optional[TWCVersion]:
        """Fetch version information."""
        data = await self._fetch(self.ENDPOINTS["version"])
        if data:
            try:
                return TWCVersion(**data)
            except Exception as e:
                logger.error(f"[{self.charger.name}] Error parsing version: {e}")
        return None

    async def get_wifi_status(self) -> Optional[TWCWifiStatus]:
        """Fetch WiFi status."""
        data = await self._fetch(self.ENDPOINTS["wifi_status"])
        if data:
            try:
                return TWCWifiStatus(**data)
            except Exception as e:
                logger.error(f"[{self.charger.name}] Error parsing wifi_status: {e}")
        return None

    async def get_all(self) -> dict:
        """Fetch all endpoints concurrently."""
        vitals, lifetime, version, wifi = await asyncio.gather(
            self.get_vitals(),
            self.get_lifetime(),
            self.get_version(),
            self.get_wifi_status(),
            return_exceptions=True
        )

        return {
            "vitals": vitals if not isinstance(vitals, Exception) else None,
            "lifetime": lifetime if not isinstance(lifetime, Exception) else None,
            "version": version if not isinstance(version, Exception) else None,
            "wifi_status": wifi if not isinstance(wifi, Exception) else None,
        }
