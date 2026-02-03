"""API client for GOV.UK Fuel Finder."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

import aiohttp

from .const import (
    BATCH_SIZE,
    BRAND_DOMAINS,
    ORS_MATRIX_URL,
    PRICE_MAX,
    PRICE_MIN,
    PRICES_URL,
    STATIONS_URL,
    TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)


class FuelFinderApiError(Exception):
    """Raised when the API returns an error."""


class FuelFinderAuthError(FuelFinderApiError):
    """Raised when authentication fails."""


class FuelFinderApi:
    """Client for the GOV.UK Fuel Finder API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._session = session
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token: str | None = None
        self._token_expiry: float = 0

    async def _ensure_token(self) -> None:
        """Obtain or refresh the OAuth access token."""
        if self._access_token and time.time() < self._token_expiry - 60:
            return

        data = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "scope": "fuelfinder.read",
        }

        try:
            async with self._session.post(
                TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise FuelFinderAuthError(
                        f"OAuth token request failed ({resp.status}): {text}"
                    )
                result = await resp.json()
        except aiohttp.ClientError as err:
            raise FuelFinderApiError(f"Connection error during auth: {err}") from err

        # Response is wrapped: {"success": true, "data": {"access_token": ...}}
        token_data = result.get("data", result)
        self._access_token = token_data["access_token"]
        self._token_expiry = time.time() + token_data.get("expires_in", 3600)

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        """Make an authenticated GET request."""
        await self._ensure_token()

        headers = {"Authorization": f"Bearer {self._access_token}"}
        try:
            async with self._session.get(
                url, headers=headers, params=params
            ) as resp:
                if resp.status == 401:
                    # Token may have expired, retry once
                    self._access_token = None
                    await self._ensure_token()
                    headers["Authorization"] = f"Bearer {self._access_token}"
                    async with self._session.get(
                        url, headers=headers, params=params
                    ) as retry_resp:
                        if retry_resp.status != 200:
                            text = await retry_resp.text()
                            raise FuelFinderApiError(
                                f"API request failed ({retry_resp.status}): {text}"
                            )
                        return await retry_resp.json()
                if resp.status != 200:
                    text = await resp.text()
                    raise FuelFinderApiError(
                        f"API request failed ({resp.status}): {text}"
                    )
                return await resp.json()
        except aiohttp.ClientError as err:
            raise FuelFinderApiError(f"Connection error: {err}") from err

    async def fetch_all_stations(self) -> list[dict[str, Any]]:
        """Fetch all station records across all batches.

        Includes a 2-second delay between batches to respect the API rate
        limit of 30 requests per minute (1 concurrent request).
        """
        all_stations: list[dict[str, Any]] = []
        batch = 1
        while True:
            if batch > 1:
                await asyncio.sleep(2)
            data = await self._get(STATIONS_URL, {"batch-number": batch})
            stations = data if isinstance(data, list) else data.get("results", data.get("data", []))
            if not stations:
                break
            all_stations.extend(stations)
            if len(stations) < BATCH_SIZE:
                break
            batch += 1
        _LOGGER.debug("Fetched %d stations across %d batches", len(all_stations), batch)
        return all_stations

    async def fetch_all_prices(self) -> list[dict[str, Any]]:
        """Fetch all fuel price records across all batches.

        Includes a 2-second delay between batches to respect the API rate
        limit of 30 requests per minute (1 concurrent request).
        """
        all_prices: list[dict[str, Any]] = []
        batch = 1
        while True:
            if batch > 1:
                await asyncio.sleep(2)
            data = await self._get(PRICES_URL, {"batch-number": batch})
            prices = data if isinstance(data, list) else data.get("results", data.get("data", []))
            if not prices:
                break
            all_prices.extend(prices)
            if len(prices) < BATCH_SIZE:
                break
            batch += 1
        _LOGGER.debug("Fetched %d price records across %d batches", len(all_prices), batch)
        return all_prices

    async def test_connection(self) -> bool:
        """Test that credentials are valid by fetching a token."""
        await self._ensure_token()
        return True


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in miles using Haversine formula."""
    r = 3958.8  # Earth radius in miles
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    )
    return r * 2 * math.asin(math.sqrt(a))


def clean_price(raw_price: float | None) -> float | None:
    """Normalise a price value to pence per litre.

    Handles common data errors:
    - Values like 1.289 -> 128.9 (multiply by 100)
    - Values like 1319.0 -> 131.9 (divide by 10)
    """
    if raw_price is None:
        return None

    price = float(raw_price)

    if price < 10:
        # Likely in pounds, e.g. 1.289
        price = price * 100
    elif price > 1000:
        # Likely an extra digit, e.g. 1319.0
        price = price / 10

    if PRICE_MIN <= price <= PRICE_MAX:
        return round(price, 1)

    return None


def get_brand_icon(brand: str | None) -> str | None:
    """Get a Clearbit logo URL for a brand."""
    if not brand:
        return None
    brand_lower = brand.lower().strip()
    domain = BRAND_DOMAINS.get(brand_lower)
    if domain:
        return f"https://logo.clearbit.com/{domain}"
    return None


async def get_driving_distances(
    session: aiohttp.ClientSession,
    api_key: str,
    home_coords: tuple[float, float],
    station_coords: list[tuple[float, float]],
) -> list[float | None]:
    """Get driving distances from home to stations using OpenRouteService Matrix API.

    Returns distances in miles, or None for failed lookups.
    """
    if not station_coords:
        return []

    # ORS expects [longitude, latitude]
    locations = [[home_coords[1], home_coords[0]]]
    for lat, lon in station_coords:
        locations.append([lon, lat])

    body = {
        "locations": locations,
        "sources": [0],
        "destinations": list(range(1, len(locations))),
        "metrics": ["distance"],
    }

    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }

    try:
        async with session.post(
            ORS_MATRIX_URL, json=body, headers=headers
        ) as resp:
            if resp.status != 200:
                _LOGGER.warning("ORS Matrix API returned %d", resp.status)
                return [None] * len(station_coords)
            data = await resp.json()
    except aiohttp.ClientError as err:
        _LOGGER.warning("ORS Matrix API error: %s", err)
        return [None] * len(station_coords)

    distances_metres = data.get("distances", [[]])[0]
    results: list[float | None] = []
    for d in distances_metres:
        if d is None or d < 0:
            results.append(None)
        else:
            results.append(round(d / 1609.344, 1))
    return results
