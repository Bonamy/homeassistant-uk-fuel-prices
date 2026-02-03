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
from .const import DEFAULT_SCAN_INTERVAL, fuel_display_labels

_LOGGER = logging.getLogger(__name__)


class FuelPricesCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to fetch and process fuel price data.

    Fetches all stations and prices once, then processes them for each
    selected fuel type.  Subsequent updates use incremental fetching.

    Data structure returned::

        {
            "fuel_labels": {"E10": "Petrol", "B7_STANDARD": "Diesel"},
            "by_fuel": {
                "E10": {
                    "top3": [...],
                    "stations": {...},
                },
                "B7_STANDARD": {
                    "top3": [...],
                    "stations": {...},
                },
            },
        }
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api: FuelFinderApi,
        session: aiohttp.ClientSession,
        home_lat: float,
        home_lon: float,
        radius: float,
        fuel_types: list[str],
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
        self._fuel_types = fuel_types
        self._ors_api_key = ors_api_key

        # Cached raw data keyed by node_id for incremental merging
        self._cached_stations: dict[str, dict[str, Any]] = {}
        self._cached_prices: dict[str, dict[str, Any]] = {}
        self._last_fetch_time: str | None = None
        self._normal_interval = timedelta(seconds=DEFAULT_SCAN_INTERVAL)
        self._retry_interval = timedelta(minutes=5)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data and process for all selected fuel types."""
        is_incremental = self._last_fetch_time is not None

        try:
            stations_raw, prices_raw = await self._fetch_data()
        except FuelFinderApiError as err:
            if is_incremental and self._cached_stations:
                _LOGGER.warning(
                    "Incremental fetch failed (%s), using cached data "
                    "(%d stations, %d prices)",
                    err, len(self._cached_stations), len(self._cached_prices),
                )
                stations_raw = list(self._cached_stations.values())
                prices_raw = list(self._cached_prices.values())
            else:
                self.update_interval = self._retry_interval
                _LOGGER.warning(
                    "Initial fetch failed, will retry in %s: %s",
                    self._retry_interval, err,
                )
                raise UpdateFailed(f"Error fetching fuel data: {err}") from err

        # Merge into cache
        if is_incremental:
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
                "Incremental update: merged %d station updates, "
                "%d price updates into cache (%d total stations, %d total prices)",
                updated_stations, updated_prices,
                len(self._cached_stations), len(self._cached_prices),
            )
            all_stations_raw = list(self._cached_stations.values())
            all_prices_raw = list(self._cached_prices.values())
        else:
            for station in stations_raw:
                nid = station.get("node_id", "")
                if nid:
                    self._cached_stations[nid] = station
            for price in prices_raw:
                nid = price.get("node_id", "")
                if nid:
                    self._cached_prices[nid] = price
            _LOGGER.info(
                "Full fetch: cached %d stations, %d prices",
                len(self._cached_stations), len(self._cached_prices),
            )
            all_stations_raw = stations_raw
            all_prices_raw = prices_raw

        self._last_fetch_time = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # Build nearby station lookup (shared across all fuel types)
        stations_by_id = self._build_station_lookup(all_stations_raw)

        # Build display labels for selected fuel types
        labels = fuel_display_labels(self._fuel_types)

        # Process each fuel type
        by_fuel: dict[str, dict[str, Any]] = {}
        for fuel_code in self._fuel_types:
            by_fuel[fuel_code] = self._process_fuel_type(
                fuel_code, stations_by_id, all_prices_raw
            )

        # Optionally enrich top3 with driving distances (all fuel types)
        if self._ors_api_key:
            for fuel_code, fuel_data in by_fuel.items():
                top3 = fuel_data.get("top3", [])
                if top3:
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

        # Restore normal polling interval after success
        if self.update_interval != self._normal_interval:
            _LOGGER.info(
                "Data loaded successfully, restoring normal %s polling interval",
                self._normal_interval,
            )
            self.update_interval = self._normal_interval

        return {
            "fuel_labels": labels,
            "by_fuel": by_fuel,
        }

    def _build_station_lookup(
        self, stations_raw: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Filter stations by radius and build lookup dict by node_id."""
        stations_by_id: dict[str, dict[str, Any]] = {}
        skipped_closed = 0
        skipped_no_location = 0
        skipped_out_of_range = 0

        for station in stations_raw:
            node_id = station.get("node_id", "")
            if not node_id:
                continue

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
        return stations_by_id

    def _process_fuel_type(
        self,
        fuel_code: str,
        stations_by_id: dict[str, dict[str, Any]],
        prices_raw: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Process prices for a single fuel type against nearby stations."""
        candidates: list[dict[str, Any]] = []
        matched = 0
        no_fuel = 0
        bad_price = 0

        for price_record in prices_raw:
            node_id = price_record.get("node_id", "")
            station = stations_by_id.get(node_id)
            if not station:
                continue

            found_fuel = False
            for fp in price_record.get("fuel_prices", []):
                if fp.get("fuel_type") == fuel_code:
                    found_fuel = True
                    raw_price = fp.get("price")
                    cleaned = clean_price(raw_price)
                    if cleaned is not None:
                        entry = {**station}
                        entry["price"] = cleaned
                        entry["fuel_type"] = fuel_code
                        entry["last_update"] = fp.get("price_last_updated", "")
                        candidates.append(entry)
                        matched += 1
                    else:
                        bad_price += 1
                        _LOGGER.debug(
                            "Station %s (%s) had invalid %s price: %s",
                            station.get("station_name"), node_id,
                            fuel_code, raw_price,
                        )
                    break
            if not found_fuel and station:
                no_fuel += 1

        candidates.sort(key=lambda x: (x["price"], x["distance_miles"]))

        top3 = candidates[:3]
        matched_by_id: dict[str, dict[str, Any]] = {}
        for entry in candidates:
            matched_by_id[entry["node_id"]] = entry

        _LOGGER.info(
            "Results for %s: %d stations with valid prices, %d no %s price, "
            "%d invalid prices, top 3 selected",
            fuel_code, matched, no_fuel, fuel_code, bad_price,
        )

        return {
            "top3": top3,
            "stations": matched_by_id,
        }

    async def _fetch_data(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Fetch stations and prices from the API sequentially."""
        since = self._last_fetch_time
        if since:
            _LOGGER.info("Performing incremental fetch since %s", since)
        else:
            _LOGGER.info("Performing full initial fetch")
        stations = await self._api.fetch_all_stations(since=since)
        prices = await self._api.fetch_all_prices(since=since)
        return stations, prices
