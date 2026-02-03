"""Sensor platform for UK Fuel Prices."""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, FUEL_TYPES
from .coordinator import FuelPricesCoordinator

_LOGGER = logging.getLogger(__name__)

RANK_LABELS = {
    0: "#1 Cheapest",
    1: "#2 Cheapest",
    2: "#3 Cheapest",
}


def _slugify_station(name: str) -> str:
    """Create a slug from a station name for use in entity IDs."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the fuel price sensors."""
    coordinator: FuelPricesCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Always create the 3 cheapest ranking sensors
    entities: list[SensorEntity] = [
        CheapestFuelSensor(coordinator, entry, rank)
        for rank in range(3)
    ]

    # Track which per-station sensors we've already created
    known_stations: set[str] = set()

    # Create per-station sensors for any stations already in data
    if coordinator.data and coordinator.data.get("stations"):
        for node_id in coordinator.data["stations"]:
            entities.append(
                StationFuelSensor(coordinator, entry, node_id)
            )
            known_stations.add(node_id)

    async_add_entities(entities)

    # Listen for new stations appearing in future updates
    @callback
    def _async_check_new_stations() -> None:
        """Add sensors for any newly discovered stations."""
        if not coordinator.data or not coordinator.data.get("stations"):
            return
        new_entities: list[SensorEntity] = []
        for node_id in coordinator.data["stations"]:
            if node_id not in known_stations:
                known_stations.add(node_id)
                new_entities.append(
                    StationFuelSensor(coordinator, entry, node_id)
                )
        if new_entities:
            _LOGGER.debug("Adding %d new station sensors", len(new_entities))
            async_add_entities(new_entities)

    entry.async_on_unload(
        coordinator.async_add_listener(_async_check_new_stations)
    )


def _build_attributes(data: dict[str, Any]) -> dict[str, Any]:
    """Build the common attribute dict from a station data entry."""
    attrs: dict[str, Any] = {
        "station_name": data.get("station_name"),
        "brand": data.get("brand"),
        "brand_icon": data.get("brand_icon"),
        "address": data.get("address"),
        "postcode": data.get("postcode"),
        "distance_miles": data.get("distance_miles"),
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "fuel_type": FUEL_TYPES.get(
            data.get("fuel_type", ""), data.get("fuel_type", "")
        ),
        "fuel_type_code": data.get("fuel_type"),
        "last_update": data.get("last_update"),
    }
    if "driving_distance_miles" in data:
        attrs["driving_distance_miles"] = data["driving_distance_miles"]
    return attrs


class CheapestFuelSensor(CoordinatorEntity[FuelPricesCoordinator], SensorEntity):
    """Sensor showing a fuel station price, ranked by cheapest."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "p/litre"
    _attr_icon = "mdi:gas-station"

    def __init__(
        self,
        coordinator: FuelPricesCoordinator,
        entry: ConfigEntry,
        rank: int,
    ) -> None:
        super().__init__(coordinator)
        self._rank = rank
        self._rank_label = RANK_LABELS[rank]
        self._attr_unique_id = f"{entry.entry_id}_cheapest_fuel_{rank + 1}"

    @property
    def name(self) -> str:
        """Return a dynamic name including the current station."""
        data = self._station_data
        if data:
            brand = data.get("brand", "")
            distance = data.get("distance_miles", "?")
            if brand and brand != "Unknown":
                return f"{self._rank_label} — {brand} ({distance} mi)"
            name = data.get("station_name", "Unknown")
            return f"{self._rank_label} — {name} ({distance} mi)"
        return f"{self._rank_label}"

    @property
    def _station_data(self) -> dict[str, Any] | None:
        """Return the data for this sensor's rank, if available."""
        if self.coordinator.data:
            top3 = self.coordinator.data.get("top3", [])
            if self._rank < len(top3):
                return top3[self._rank]
        return None

    @property
    def native_value(self) -> float | None:
        """Return the fuel price in pence per litre."""
        data = self._station_data
        if data:
            return data.get("price")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return sensor attributes."""
        data = self._station_data
        if not data:
            return {}
        return _build_attributes(data)

    @property
    def available(self) -> bool:
        """Return True if data exists for this rank."""
        return super().available and self._station_data is not None


class StationFuelSensor(CoordinatorEntity[FuelPricesCoordinator], SensorEntity):
    """Sensor tracking the price at a specific fuel station over time."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "p/litre"
    _attr_icon = "mdi:gas-station"

    def __init__(
        self,
        coordinator: FuelPricesCoordinator,
        entry: ConfigEntry,
        node_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._node_id = node_id
        self._attr_unique_id = f"{entry.entry_id}_station_{node_id[:16]}"

        # Set a friendly name from the current data
        data = self._station_data
        if data:
            brand = data.get("brand", "")
            name = data.get("station_name", "Unknown")
            distance = data.get("distance_miles", "?")
            if brand and brand != "Unknown":
                self._attr_name = f"{brand} — {name} ({distance} mi)"
            else:
                self._attr_name = f"{name} ({distance} mi)"
        else:
            self._attr_name = f"Fuel Station {node_id[:8]}"

    @property
    def _station_data(self) -> dict[str, Any] | None:
        """Return the data for this specific station."""
        if self.coordinator.data:
            stations = self.coordinator.data.get("stations", {})
            return stations.get(self._node_id)
        return None

    @property
    def native_value(self) -> float | None:
        """Return the fuel price in pence per litre."""
        data = self._station_data
        if data:
            return data.get("price")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return sensor attributes."""
        data = self._station_data
        if not data:
            return {}
        return _build_attributes(data)

    @property
    def available(self) -> bool:
        """Return True if data exists for this station."""
        return super().available and self._station_data is not None
