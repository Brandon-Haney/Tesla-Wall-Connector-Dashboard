"""ComEd Hourly Pricing API client."""

import aiohttp
import asyncio
import logging
from typing import Optional, List
from datetime import datetime, timedelta
from .models import ComEdPrice

logger = logging.getLogger(__name__)


class ComEdClient:
    """Async client for ComEd Hourly Pricing API."""

    BASE_URL = "https://hourlypricing.comed.com/api"

    def __init__(self, timeout: int = 30):
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

    async def _fetch(self, params: dict) -> Optional[List[dict]]:
        """Fetch data from ComEd API."""
        try:
            session = await self._get_session()
            async with session.get(self.BASE_URL, params=params) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.warning(f"ComEd API HTTP {response.status}")
                    return None
        except asyncio.TimeoutError:
            logger.error("ComEd API timeout")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"ComEd API error: {e}")
            return None
        except Exception as e:
            logger.error(f"ComEd API unexpected error: {e}")
            return None

    async def get_current_hour_average(self) -> Optional[ComEdPrice]:
        """Fetch current hour average price."""
        data = await self._fetch({"type": "currenthouraverage"})
        if data and len(data) > 0:
            try:
                return ComEdPrice(**data[0])
            except Exception as e:
                logger.error(f"Error parsing current hour average: {e}")
        return None

    async def get_5minute_prices(self, hours: int = 24) -> List[ComEdPrice]:
        """Fetch 5-minute prices for the last N hours."""
        data = await self._fetch({"type": "5minutefeed"})
        prices = []
        if data:
            for item in data:
                try:
                    prices.append(ComEdPrice(**item))
                except Exception as e:
                    logger.warning(f"Error parsing price data point: {e}")
        return prices

    async def get_historical_prices(
        self,
        start: datetime,
        end: datetime
    ) -> List[ComEdPrice]:
        """Fetch historical 5-minute prices for a date range."""
        # Format: YYYYMMDDhhmm
        start_str = start.strftime("%Y%m%d%H%M")
        end_str = end.strftime("%Y%m%d%H%M")

        data = await self._fetch({
            "type": "5minutefeed",
            "datestart": start_str,
            "dateend": end_str
        })

        prices = []
        if data:
            for item in data:
                try:
                    prices.append(ComEdPrice(**item))
                except Exception as e:
                    logger.warning(f"Error parsing historical price: {e}")
        return prices

    async def get_current_price(self) -> Optional[float]:
        """Get the current price in cents/kWh (convenience method)."""
        price = await self.get_current_hour_average()
        if price:
            return price.price_cents
        return None
