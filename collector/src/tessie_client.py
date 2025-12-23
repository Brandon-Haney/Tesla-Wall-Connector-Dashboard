"""Tessie API client for Tesla vehicle data and Fleet API access.

Tessie provides access to Tesla's Fleet API with additional enhancements:
- Automatic vehicle wake-up
- Automatic firmware error handling
- Charging history tracking
- Unlimited polling with Pro subscription
- Energy site access (Wall Connectors, Powerwalls)

API Documentation: https://developer.tessie.com/reference
"""

import aiohttp
import asyncio
import logging
from typing import Optional, List
from .models import TessieVehicle, TessieChargeState, TessieCharge, FleetEnergySiteLiveStatus, FleetWallConnector, FleetChargeSession

logger = logging.getLogger(__name__)


class TessieClient:
    """Async client for Tessie API."""

    BASE_URL = "https://api.tessie.com"

    def __init__(self, access_token: str, timeout: int = 30):
        """Initialize Tessie client.

        Args:
            access_token: Tessie API access token from dash.tessie.com/settings/api
            timeout: Request timeout in seconds
        """
        self.access_token = access_token
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session with auth headers."""
        if self._session is None or self._session.closed:
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json",
            }
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers=headers
            )
        return self._session

    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _fetch(self, endpoint: str, params: Optional[dict] = None) -> Optional[dict]:
        """Fetch data from Tessie API endpoint.

        Args:
            endpoint: API endpoint path (e.g., "/vehicles")
            params: Optional query parameters

        Returns:
            JSON response as dict, or None on error
        """
        url = f"{self.BASE_URL}{endpoint}"
        try:
            session = await self._get_session()
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 401:
                    logger.error("Tessie API: Authentication failed - check your access token")
                    return None
                elif response.status == 429:
                    logger.warning("Tessie API: Rate limited - backing off")
                    return None
                elif response.status == 408:
                    logger.warning("Tessie API: Vehicle is asleep or unavailable")
                    return None
                else:
                    logger.warning(f"Tessie API: HTTP {response.status} from {endpoint}")
                    return None
        except asyncio.TimeoutError:
            logger.error(f"Tessie API: Timeout fetching {endpoint}")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"Tessie API: Error fetching {endpoint}: {e}")
            return None
        except Exception as e:
            logger.error(f"Tessie API: Unexpected error fetching {endpoint}: {e}")
            return None

    async def get_vehicles(self, only_active: bool = True) -> List[TessieVehicle]:
        """Get all vehicles associated with the account.

        Args:
            only_active: If True, only return active vehicles

        Returns:
            List of TessieVehicle objects
        """
        params = {"only_active": "true" if only_active else "false"}
        data = await self._fetch("/vehicles", params=params)

        if not data or "results" not in data:
            return []

        vehicles = []
        for vehicle_data in data["results"]:
            try:
                vehicles.append(TessieVehicle.from_api_response(vehicle_data))
            except Exception as e:
                logger.error(f"Tessie API: Error parsing vehicle data: {e}")

        return vehicles

    async def get_vehicle_state(self, vin: str) -> Optional[TessieVehicle]:
        """Get current state for a specific vehicle.

        This endpoint uses automatic wake-up if the vehicle is asleep.

        Args:
            vin: Vehicle Identification Number

        Returns:
            TessieVehicle with current state, or None on error
        """
        data = await self._fetch(f"/{vin}/state")

        if not data:
            return None

        try:
            return TessieVehicle.from_api_response(data)
        except Exception as e:
            logger.error(f"Tessie API: Error parsing vehicle state for {vin}: {e}")
            return None

    async def get_charge_state(self, vin: str) -> Optional[TessieChargeState]:
        """Get current charge state for a vehicle.

        Args:
            vin: Vehicle Identification Number

        Returns:
            TessieChargeState, or None on error
        """
        # The state endpoint includes charge_state
        data = await self._fetch(f"/{vin}/state")

        if not data or "charge_state" not in data:
            return None

        try:
            return TessieChargeState.from_api_response(data["charge_state"])
        except Exception as e:
            logger.error(f"Tessie API: Error parsing charge state for {vin}: {e}")
            return None

    async def get_charges(
        self,
        vin: str,
        from_timestamp: Optional[int] = None,
        to_timestamp: Optional[int] = None,
        origin: Optional[str] = None
    ) -> List[TessieCharge]:
        """Get charging history for a vehicle.

        Args:
            vin: Vehicle Identification Number
            from_timestamp: Optional start Unix timestamp
            to_timestamp: Optional end Unix timestamp
            origin: Optional filter by charge location type

        Returns:
            List of TessieCharge objects
        """
        params = {}
        if from_timestamp:
            params["from"] = str(from_timestamp)
        if to_timestamp:
            params["to"] = str(to_timestamp)
        if origin:
            params["origin"] = origin

        data = await self._fetch(f"/{vin}/charges", params=params if params else None)

        if not data or "results" not in data:
            return []

        charges = []
        for charge_data in data["results"]:
            try:
                charges.append(TessieCharge.from_api_response(charge_data))
            except Exception as e:
                logger.error(f"Tessie API: Error parsing charge data: {e}")

        return charges

    async def get_location(self, vin: str) -> Optional[dict]:
        """Get current vehicle location.

        Args:
            vin: Vehicle Identification Number

        Returns:
            Dict with latitude, longitude, heading, or None
        """
        data = await self._fetch(f"/{vin}/state")

        if not data or "drive_state" not in data:
            return None

        drive_state = data["drive_state"]
        return {
            "latitude": drive_state.get("latitude"),
            "longitude": drive_state.get("longitude"),
            "heading": drive_state.get("heading"),
        }

    async def wake_vehicle(self, vin: str) -> bool:
        """Wake up a sleeping vehicle.

        Note: Tessie's state endpoint auto-wakes vehicles, so this is
        typically not needed unless you want explicit control.

        Args:
            vin: Vehicle Identification Number

        Returns:
            True if wake command was successful
        """
        data = await self._fetch(f"/{vin}/wake")
        return data is not None and data.get("result", False)

    # Charge control methods (for Phase 4.4)
    async def start_charging(self, vin: str) -> bool:
        """Start charging the vehicle.

        Args:
            vin: Vehicle Identification Number

        Returns:
            True if command was successful
        """
        data = await self._fetch(f"/{vin}/command/start_charging")
        return data is not None and data.get("result", False)

    async def stop_charging(self, vin: str) -> bool:
        """Stop charging the vehicle.

        Args:
            vin: Vehicle Identification Number

        Returns:
            True if command was successful
        """
        data = await self._fetch(f"/{vin}/command/stop_charging")
        return data is not None and data.get("result", False)

    async def set_charge_limit(self, vin: str, percent: int) -> bool:
        """Set the charge limit percentage.

        Args:
            vin: Vehicle Identification Number
            percent: Target SOC percentage (50-100)

        Returns:
            True if command was successful
        """
        percent = max(50, min(100, percent))  # Clamp to valid range
        data = await self._fetch(f"/{vin}/command/set_charge_limit?percent={percent}")
        return data is not None and data.get("result", False)

    async def set_charging_amps(self, vin: str, amps: int) -> bool:
        """Set the charging current.

        Args:
            vin: Vehicle Identification Number
            amps: Target charging amps

        Returns:
            True if command was successful
        """
        data = await self._fetch(f"/{vin}/command/set_charging_amps?amps={amps}")
        return data is not None and data.get("result", False)

    # =========================================================================
    # Fleet API Energy Site Methods (Wall Connectors)
    # =========================================================================

    async def get_products(self) -> Optional[dict]:
        """Get all products (vehicles and energy sites) associated with the account.

        This endpoint returns:
        - Vehicles (cars)
        - Energy sites (Wall Connectors, Powerwalls, Solar)

        Returns:
            Raw API response with all products, or None on error
        """
        data = await self._fetch("/api/1/products")
        return data

    async def get_energy_site_ids(self) -> List[str]:
        """Get list of energy site IDs from the account.

        Returns:
            List of energy_site_id strings
        """
        data = await self.get_products()
        if not data or "response" not in data:
            return []

        site_ids = []
        for product in data["response"]:
            if "energy_site_id" in product:
                site_ids.append(str(product["energy_site_id"]))

        return site_ids

    async def get_energy_site_live_status(
        self,
        energy_site_id: str
    ) -> Optional[FleetEnergySiteLiveStatus]:
        """Get live status for an energy site (Wall Connectors).

        Args:
            energy_site_id: The energy site ID from get_products

        Returns:
            FleetEnergySiteLiveStatus with wall connector data, or None on error
        """
        data = await self._fetch(f"/api/1/energy_sites/{energy_site_id}/live_status")

        if not data:
            return None

        try:
            return FleetEnergySiteLiveStatus.from_api_response(data)
        except Exception as e:
            logger.error(f"Fleet API: Error parsing live_status for site {energy_site_id}: {e}")
            return None

    async def get_wall_connectors(
        self,
        energy_site_id: str
    ) -> List[FleetWallConnector]:
        """Get Wall Connector data for an energy site.

        Convenience method that returns just the wall connector list.

        Args:
            energy_site_id: The energy site ID

        Returns:
            List of FleetWallConnector objects
        """
        status = await self.get_energy_site_live_status(energy_site_id)

        if status:
            return status.wall_connectors

        return []

    async def get_energy_site_info(self, energy_site_id: str) -> Optional[dict]:
        """Get site info for an energy site.

        Returns configuration and status information about the energy site.

        Args:
            energy_site_id: The energy site ID

        Returns:
            Raw API response with site info, or None on error
        """
        data = await self._fetch(f"/api/1/energy_sites/{energy_site_id}/site_info")
        return data

    async def get_energy_site_telemetry_history(
        self,
        energy_site_id: str,
        kind: str = "charge",
        start_date: str = None,
        end_date: str = None,
        time_zone: str = "America/Chicago"
    ) -> Optional[dict]:
        """Get telemetry history for an energy site.

        Args:
            energy_site_id: The energy site ID
            kind: Type of history - "charge" for wall connector charging history
            start_date: Start date (ISO 8601 with timezone, e.g., "2025-12-01T00:00:00-06:00")
            end_date: End date (ISO 8601 with timezone)
            time_zone: Timezone for the data (used to generate default dates)

        Returns:
            Raw API response with telemetry history, or None on error
        """
        from datetime import datetime, timedelta
        import urllib.parse

        # Map timezone names to offsets (common ones)
        tz_offsets = {
            "America/Chicago": "-06:00",
            "America/New_York": "-05:00",
            "America/Los_Angeles": "-08:00",
            "America/Denver": "-07:00",
            "UTC": "+00:00",
        }
        tz_offset = tz_offsets.get(time_zone, "-06:00")

        # Default to last 7 days if not specified
        # Fleet API requires ISO 8601 format with timezone
        if not end_date:
            end_date = datetime.now().strftime(f"%Y-%m-%dT23:59:59{tz_offset}")
        elif len(end_date) == 10:  # Simple YYYY-MM-DD format
            end_date = f"{end_date}T23:59:59{tz_offset}"

        if not start_date:
            start_date = (datetime.now() - timedelta(days=7)).strftime(f"%Y-%m-%dT00:00:00{tz_offset}")
        elif len(start_date) == 10:  # Simple YYYY-MM-DD format
            start_date = f"{start_date}T00:00:00{tz_offset}"

        # URL encode the dates (they contain colons and plus signs)
        start_encoded = urllib.parse.quote(start_date, safe='')
        end_encoded = urllib.parse.quote(end_date, safe='')

        params = f"kind={kind}&start_date={start_encoded}&end_date={end_encoded}&time_zone={time_zone}"
        data = await self._fetch(f"/api/1/energy_sites/{energy_site_id}/telemetry_history?{params}")
        return data

    async def get_energy_site_calendar_history(
        self,
        energy_site_id: str,
        kind: str = "energy",
        period: str = "day",
        start_date: str = None,
        end_date: str = None,
        time_zone: str = "America/Chicago"
    ) -> Optional[dict]:
        """Get calendar history for an energy site.

        Args:
            energy_site_id: The energy site ID
            kind: Type of history - "energy" for energy usage
            period: Aggregation period - "day", "week", "month", "year"
            start_date: Start date (YYYY-MM-DD format)
            end_date: End date (YYYY-MM-DD format)
            time_zone: Timezone for the data

        Returns:
            Raw API response with calendar history, or None on error
        """
        from datetime import datetime, timedelta

        # Default to last 30 days if not specified
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")
        if not start_date:
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        params = f"kind={kind}&period={period}&start_date={start_date}&end_date={end_date}&time_zone={time_zone}"
        data = await self._fetch(f"/api/1/energy_sites/{energy_site_id}/calendar_history?{params}")
        return data

    async def get_charge_sessions(
        self,
        energy_site_id: str,
        start_date: str = None,
        end_date: str = None,
        time_zone: str = "America/Chicago"
    ) -> List[FleetChargeSession]:
        """Get charging sessions from Fleet API telemetry_history.

        This returns all charging sessions for Wall Connectors at the energy site,
        including sessions from follower units that cannot be accessed via local API.

        Args:
            energy_site_id: The energy site ID
            start_date: Start date (YYYY-MM-DD format or ISO 8601 with timezone)
            end_date: End date (YYYY-MM-DD format or ISO 8601 with timezone)
            time_zone: Timezone for the data (used if start_date/end_date are simple dates)

        Returns:
            List of FleetChargeSession objects
        """
        data = await self.get_energy_site_telemetry_history(
            energy_site_id=energy_site_id,
            kind="charge",
            start_date=start_date,
            end_date=end_date,
            time_zone=time_zone
        )

        if not data:
            return []

        # Extract sessions from response
        # Handle {"response": null} case from API
        response = data.get("response", data)
        if response is None:
            return []
        charge_history = response.get("charge_history") or []

        sessions = []
        for session_data in charge_history:
            try:
                session = FleetChargeSession.from_api_response(session_data)
                # Skip sessions with no energy (invalid data)
                if session.energy_wh > 0 and session.duration_s > 0:
                    sessions.append(session)
            except Exception as e:
                logger.error(f"Fleet API: Error parsing charge session: {e}")

        logger.info(f"Fleet API: Fetched {len(sessions)} charge sessions from telemetry_history")
        return sessions

    async def get_charge_sessions_since(
        self,
        energy_site_id: str,
        since_timestamp: int,
        time_zone: str = "America/Chicago"
    ) -> List[FleetChargeSession]:
        """Get charging sessions since a specific timestamp.

        Convenience method for incremental polling of new sessions.

        Args:
            energy_site_id: The energy site ID
            since_timestamp: Unix timestamp to fetch sessions after
            time_zone: Timezone for the data

        Returns:
            List of FleetChargeSession objects that started after since_timestamp
        """
        from datetime import datetime, timedelta, timezone as tz

        # Convert timestamp to ISO 8601 date
        since_dt = datetime.fromtimestamp(since_timestamp, tz=tz.utc)
        # Go back 1 day to ensure we don't miss any (we'll filter by timestamp)
        start_dt = since_dt - timedelta(days=1)
        end_dt = datetime.now(tz=tz.utc) + timedelta(days=1)

        # Format dates in ISO 8601
        start_date = start_dt.strftime("%Y-%m-%d")
        end_date = end_dt.strftime("%Y-%m-%d")

        sessions = await self.get_charge_sessions(
            energy_site_id=energy_site_id,
            start_date=start_date,
            end_date=end_date,
            time_zone=time_zone
        )

        # Filter to only sessions that started after our threshold
        return [s for s in sessions if s.start_timestamp > since_timestamp]
