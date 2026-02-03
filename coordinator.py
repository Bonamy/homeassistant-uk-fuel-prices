"""Data update coordinator for UK Fuel Prices."""

from __future__ import annotations

from datetime import timedelta
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
    """Coordinator to fetch and process fuel price data."""

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

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch station and price data, return all stations and top 3 cheapest."""
        try:
            stations_raw, prices_raw = await self._fetch_data()
        except FuelFinderApiError as err:
            raise UpdateFailed(f"Error fetching fuel data: {err}") from err

        _LOGGER.info(
            "Processing data: %d raw stations, %d raw price records, fuel_type=%s",
            len(stations_raw), len(prices_raw), self._fuel_type,
        )

        # Build station lookup by node_id
        stations_by_id: dict[str, dict[str, Any]] = {}
        skipped_closed = 0
        skipped_no_location = 0
        skipped_out_of_range = 0
        for station in stations_raw:
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
        for price_record in prices_raw:
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

        # Build a dict of all stations keyed by node_id for per-station sensors
        all_stations: dict[str, dict[str, Any]] = {}
        for entry in candidates:
            all_stations[entry["node_id"]] = entry

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
        return {
            "top3": top3,
            "stations": all_stations,
        }

    async def _fetch_data(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Fetch stations and prices from the API sequentially.

        The API only allows 1 concurrent request, so we fetch sequentially
        to avoid throttling.
        """
        stations = await self._api.fetch_all_stations()
        prices = await self._api.fetch_all_prices()
        return stations, prices
