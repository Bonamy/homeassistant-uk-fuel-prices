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

    async def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> Any:
        """Make an authenticated GET request with retry logic.

        Retries on transient errors (500, 502, 503, 504, timeouts) with
        exponential backoff.  A 401 triggers a single token refresh.
        """
        retryable_statuses = {500, 502, 503, 504}
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            await self._ensure_token()
            headers = {"Authorization": f"Bearer {self._access_token}"}

            try:
                async with self._session.get(
                    url, headers=headers, params=params
                ) as resp:
                    if resp.status == 401:
                        # Token may have expired, refresh and retry
                        _LOGGER.debug("Got 401, refreshing token (attempt %d)", attempt)
                        self._access_token = None
                        await self._ensure_token()
                        headers["Authorization"] = f"Bearer {self._access_token}"
                        async with self._session.get(
                            url, headers=headers, params=params
                        ) as retry_resp:
                            if retry_resp.status != 200:
                                text = await retry_resp.text()
                                raise FuelFinderApiError(
                                    f"API request failed after token refresh ({retry_resp.status}): {text}"
                                )
                            return await retry_resp.json()

                    if resp.status in retryable_statuses:
                        text = await resp.text()
                        last_error = FuelFinderApiError(
                            f"API returned {resp.status} (attempt {attempt}/{max_retries}): {text}"
                        )
                        _LOGGER.warning(
                            "API returned %d for %s (attempt %d/%d), retrying...",
                            resp.status, url, attempt, max_retries,
                        )
                        if attempt < max_retries:
                            delay = 2 ** attempt  # 2s, 4s, 8s
                            await asyncio.sleep(delay)
                            continue
                        raise last_error

                    if resp.status != 200:
                        text = await resp.text()
                        raise FuelFinderApiError(
                            f"API request failed ({resp.status}): {text}"
                        )
                    return await resp.json()

            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                last_error = FuelFinderApiError(
                    f"Connection error (attempt {attempt}/{max_retries}): {err}"
                )
                _LOGGER.warning(
                    "Connection error for %s (attempt %d/%d): %s",
                    url, attempt, max_retries, err,
                )
                if attempt < max_retries:
                    delay = 2 ** attempt
                    await asyncio.sleep(delay)
                    continue
                raise FuelFinderApiError(
                    f"Connection failed after {max_retries} attempts: {err}"
                ) from err

        # Should not reach here, but just in case
        raise last_error or FuelFinderApiError("Request failed after all retries")

    async def _fetch_all_batches(
        self, url: str, label: str, since: str | None = None
    ) -> list[dict[str, Any]]:
        """Fetch all records across paginated batches with error resilience.

        Retries individual failed batches and continues fetching remaining
        batches even if one fails. Includes a 2-second delay between batches
        to respect the API rate limit of 30 req/min (1 concurrent request).

        Args:
            since: Optional timestamp (YYYY-MM-DD HH:MM:SS) for incremental
                   fetching via the effective-start-timestamp parameter.
        """
        all_records: list[dict[str, Any]] = []
        batch = 1
        failed_batches: list[int] = []
        consecutive_empty = 0

        while True:
            if batch > 1:
                await asyncio.sleep(2)

            try:
                params: dict[str, Any] = {"batch-number": batch}
                if since:
                    params["effective-start-timestamp"] = since
                data = await self._get(url, params)
                records = (
                    data
                    if isinstance(data, list)
                    else data.get("results", data.get("data", []))
                )

                if not records:
                    consecutive_empty += 1
                    if consecutive_empty >= 2:
                        # Two empty responses in a row means we're done
                        break
                    _LOGGER.debug(
                        "%s batch %d returned empty, trying next batch", label, batch
                    )
                    batch += 1
                    continue

                consecutive_empty = 0
                all_records.extend(records)
                _LOGGER.debug(
                    "%s batch %d: got %d records (total: %d)",
                    label, batch, len(records), len(all_records),
                )

                if len(records) < BATCH_SIZE:
                    break
                batch += 1

            except FuelFinderApiError as err:
                failed_batches.append(batch)
                _LOGGER.warning(
                    "%s batch %d failed: %s — skipping to next batch",
                    label, batch, err,
                )
                # Don't stop on a single batch failure; try the next one
                # but cap at 2 consecutive failures to avoid infinite loops
                if len(failed_batches) >= 2 and failed_batches[-1] == failed_batches[-2] + 1:
                    _LOGGER.error(
                        "%s: two consecutive batch failures (batches %d-%d), stopping",
                        label, failed_batches[-2], failed_batches[-1],
                    )
                    break
                batch += 1
                continue

        if failed_batches:
            _LOGGER.warning(
                "%s: completed with %d failed batches: %s (got %d records total)",
                label, len(failed_batches), failed_batches, len(all_records),
            )
        else:
            _LOGGER.debug(
                "%s: fetched %d records across %d batches",
                label, len(all_records), batch,
            )

        if not all_records and failed_batches:
            raise FuelFinderApiError(
                f"Failed to fetch any {label.lower()} — all batches failed"
            )

        return all_records

    async def fetch_all_stations(
        self, since: str | None = None
    ) -> list[dict[str, Any]]:
        """Fetch station records across all batches.

        Args:
            since: Optional timestamp (YYYY-MM-DD HH:MM:SS) to fetch only
                   stations updated since that time.
        """
        label = "Stations (incremental)" if since else "Stations (full)"
        return await self._fetch_all_batches(STATIONS_URL, label, since=since)

    async def fetch_all_prices(
        self, since: str | None = None
    ) -> list[dict[str, Any]]:
        """Fetch fuel price records across all batches.

        Args:
            since: Optional timestamp (YYYY-MM-DD HH:MM:SS) to fetch only
                   prices updated since that time.
        """
        label = "Prices (incremental)" if since else "Prices (full)"
        return await self._fetch_all_batches(PRICES_URL, label, since=since)

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
