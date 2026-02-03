"""Data update coordinator for UK Fuel Prices."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    FuelFinderApi,
    FuelFinderApiError,
    clean_price,
    get_brand_icon,
    get_driving_distances,
    haversine_miles,
)
from .const import DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class FuelPricesCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to fetch and process fuel price data.

    On the first update a full fetch of all stations and prices is performed.
    Subsequent updates use the ``effective-start-timestamp`` API parameter to
    request only records that changed since the last successful fetch, then
    merge them into the cached data.  This dramatically reduces API calls from
    ~34 batches down to typically 1-2.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api: FuelFinderApi,
        session: aiohttp.ClientSession,
        home_lat: float,
        home_lon: float,
        radius: float,
        fuel_type: str,
        ors_api_key: str | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="UK Fuel Prices",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self._api = api
        self._session = session
        self._home_lat = home_lat
        self._home_lon = home_lon
        self._radius = radius
        self._fuel_type = fuel_type
        self._ors_api_key = ors_api_key

        # Cached raw data keyed by node_id for incremental merging
        self._cached_stations: dict[str, dict[str, Any]] = {}
        self._cached_prices: dict[str, dict[str, Any]] = {}
        self._last_fetch_time: str | None = None
        self._normal_interval = timedelta(seconds=DEFAULT_SCAN_INTERVAL)
        self._retry_interval = timedelta(minutes=5)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch station and price data, return all stations and top 3 cheapest."""
        is_incremental = self._last_fetch_time is not None

        try:
            stations_raw, prices_raw = await self._fetch_data()
        except FuelFinderApiError as err:
            if is_incremental and self._cached_stations:
                # Incremental fetch failed but we have cached data — use it
                _LOGGER.warning(
                    "Incremental fetch failed (%s), using cached data "
                    "(%d stations, %d prices)",
                    err, len(self._cached_stations), len(self._cached_prices),
                )
                stations_raw = list(self._cached_stations.values())
                prices_raw = list(self._cached_prices.values())
            else:
                # Initial full fetch failed — retry in 5 minutes not 2 hours
                self.update_interval = self._retry_interval
                _LOGGER.warning(
                    "Initial fetch failed, will retry in %s: %s",
                    self._retry_interval, err,
                )
                raise UpdateFailed(f"Error fetching fuel data: {err}") from err

        # Merge into cache
        if is_incremental:
            # Merge incremental updates into cached data
            updated_stations = 0
            for station in stations_raw:
                nid = station.get("node_id", "")
                if nid:
                    self._cached_stations[nid] = station
                    updated_stations += 1
            updated_prices = 0
            for price in prices_raw:
                nid = price.get("node_id", "")
                if nid:
                    self._cached_prices[nid] = price
                    updated_prices += 1
            _LOGGER.info(
                "Incremental update for %s: merged %d station updates, "
                "%d price updates into cache (%d total stations, %d total prices)",
                self._fuel_type, updated_stations, updated_prices,
                len(self._cached_stations), len(self._cached_prices),
            )
            # Use the full cached dataset for processing
            all_stations = list(self._cached_stations.values())
            all_prices = list(self._cached_prices.values())
        else:
            # First fetch — populate the cache
            for station in stations_raw:
                nid = station.get("node_id", "")
                if nid:
                    self._cached_stations[nid] = station
            for price in prices_raw:
                nid = price.get("node_id", "")
                if nid:
                    self._cached_prices[nid] = price
            _LOGGER.info(
                "Full fetch for %s: cached %d stations, %d prices",
                self._fuel_type, len(self._cached_stations),
                len(self._cached_prices),
            )
            all_stations = stations_raw
            all_prices = prices_raw

        # Record the timestamp for next incremental fetch
        self._last_fetch_time = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        _LOGGER.info(
            "Processing data: %d stations, %d price records, fuel_type=%s",
            len(all_stations), len(all_prices), self._fuel_type,
        )

        # Build station lookup by node_id
        stations_by_id: dict[str, dict[str, Any]] = {}
        skipped_closed = 0
        skipped_no_location = 0
        skipped_out_of_range = 0
        for station in all_stations:
            node_id = station.get("node_id", "")
            if not node_id:
                continue

            # Skip closed stations
            if station.get("permanent_closure") or station.get("temporary_closure"):
                skipped_closed += 1
                continue

            location = station.get("location", {})
            try:
                lat = float(location.get("latitude", 0))
                lon = float(location.get("longitude", 0))
            except (TypeError, ValueError):
                skipped_no_location += 1
                continue
            if lat == 0 or lon == 0:
                skipped_no_location += 1
                continue

            dist = haversine_miles(self._home_lat, self._home_lon, lat, lon)
            if dist > self._radius:
                skipped_out_of_range += 1
                continue

            brand = station.get("brand_name", "")
            address_parts = [
                location.get("address_line_1", ""),
                location.get("address_line_2", ""),
                location.get("city", ""),
            ]
            address = ", ".join(p for p in address_parts if p)

            stations_by_id[node_id] = {
                "node_id": node_id,
                "station_name": station.get("trading_name", "Unknown"),
                "brand": brand or "Unknown",
                "brand_icon": get_brand_icon(brand),
                "address": address,
                "postcode": location.get("postcode", ""),
                "latitude": lat,
                "longitude": lon,
                "distance_miles": round(dist, 1),
            }

        _LOGGER.debug(
            "Station filtering: %d in range, %d closed, %d no location, %d out of range",
            len(stations_by_id), skipped_closed, skipped_no_location, skipped_out_of_range,
        )

        # Match prices to nearby stations
        candidates: list[dict[str, Any]] = []
        matched_stations = 0
        no_fuel_type_count = 0
        bad_price_count = 0
        for price_record in all_prices:
            node_id = price_record.get("node_id", "")
            station = stations_by_id.get(node_id)
            if not station:
                continue

            # Find the price for our fuel type
            found_fuel = False
            for fp in price_record.get("fuel_prices", []):
                if fp.get("fuel_type") == self._fuel_type:
                    found_fuel = True
                    raw_price = fp.get("price")
                    cleaned = clean_price(raw_price)
                    if cleaned is not None:
                        entry = {**station}
                        entry["price"] = cleaned
                        entry["fuel_type"] = self._fuel_type
                        entry["last_update"] = fp.get("price_last_updated", "")
                        candidates.append(entry)
                        matched_stations += 1
                    else:
                        bad_price_count += 1
                        _LOGGER.debug(
                            "Station %s (%s) had invalid %s price: %s",
                            station.get("station_name"), node_id,
                            self._fuel_type, raw_price,
                        )
                    break
            if not found_fuel and station:
                no_fuel_type_count += 1

        # Sort by price, then distance as tiebreaker
        candidates.sort(key=lambda x: (x["price"], x["distance_miles"]))

        top3 = candidates[:3]

        # Build a dict of matched stations keyed by node_id for per-station sensors
        matched_by_id: dict[str, dict[str, Any]] = {}
        for entry in candidates:
            matched_by_id[entry["node_id"]] = entry

        # Optionally enrich with driving distances
        if self._ors_api_key and top3:
            coords = [(s["latitude"], s["longitude"]) for s in top3]
            driving_dists = await get_driving_distances(
                self._session,
                self._ors_api_key,
                (self._home_lat, self._home_lon),
                coords,
            )
            for i, entry in enumerate(top3):
                if i < len(driving_dists) and driving_dists[i] is not None:
                    entry["driving_distance_miles"] = driving_dists[i]

        _LOGGER.info(
            "Results for %s: %d stations with valid prices, %d no %s price, "
            "%d invalid prices, top 3 cheapest selected",
            self._fuel_type, matched_stations, no_fuel_type_count,
            self._fuel_type, bad_price_count,
        )

        # Restore normal polling interval after a successful fetch (may have
        # been shortened to the retry interval after a previous failure).
        if self.update_interval != self._normal_interval:
            _LOGGER.info(
                "Data loaded successfully, restoring normal %s polling interval",
                self._normal_interval,
            )
            self.update_interval = self._normal_interval

        return {
            "top3": top3,
            "stations": matched_by_id,
        }

    async def _fetch_data(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Fetch stations and prices from the API sequentially.

        Uses incremental fetching with ``effective-start-timestamp`` when
        cached data is available.  The API only allows 1 concurrent request,
        so we fetch sequentially to avoid throttling.
        """
        since = self._last_fetch_time
        if since:
            _LOGGER.info(
                "Performing incremental fetch since %s", since
            )
        else:
            _LOGGER.info("Performing full initial fetch")
        stations = await self._api.fetch_all_stations(since=since)
        prices = await self._api.fetch_all_prices(since=since)
        return stations, prices
