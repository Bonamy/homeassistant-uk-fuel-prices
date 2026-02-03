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

from .const import CONF_FUEL_TYPES, DOMAIN, FUEL_TYPES
from .coordinator import FuelPricesCoordinator

_LOGGER = logging.getLogger(__name__)

RANK_LABELS = {
    0: "#1",
    1: "#2",
    2: "#3",
}


def _slugify(text: str) -> str:
    """Create a slug from text for use in entity IDs."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the fuel price sensors."""
    coordinator: FuelPricesCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Get configured fuel types (with legacy fallback)
    fuel_types = entry.data.get(CONF_FUEL_TYPES)
    if not fuel_types:
        legacy = entry.data.get("fuel_type")
        fuel_types = [legacy] if legacy else ["E10"]

    entities: list[SensorEntity] = []

    # Create 3 cheapest ranking sensors per fuel type
    for fuel_code in fuel_types:
        for rank in range(3):
            entities.append(
                CheapestFuelSensor(coordinator, entry, fuel_code, rank)
            )

    # Track which per-station sensors we've already created (keyed by fuel+node)
    known_station_keys: set[str] = set()

    # Create per-station sensors for any stations already in data
    if coordinator.data and coordinator.data.get("by_fuel"):
        for fuel_code in fuel_types:
            fuel_data = coordinator.data["by_fuel"].get(fuel_code, {})
            for node_id in fuel_data.get("stations", {}):
                key = f"{fuel_code}_{node_id}"
                entities.append(
                    StationFuelSensor(coordinator, entry, fuel_code, node_id)
                )
                known_station_keys.add(key)

    async_add_entities(entities)

    # Listen for new stations appearing in future updates
    @callback
    def _async_check_new_stations() -> None:
        """Add sensors for any newly discovered stations."""
        if not coordinator.data or not coordinator.data.get("by_fuel"):
            return
        new_entities: list[SensorEntity] = []
        for fuel_code in fuel_types:
            fuel_data = coordinator.data["by_fuel"].get(fuel_code, {})
            for node_id in fuel_data.get("stations", {}):
                key = f"{fuel_code}_{node_id}"
                if key not in known_station_keys:
                    known_station_keys.add(key)
                    new_entities.append(
                        StationFuelSensor(coordinator, entry, fuel_code, node_id)
                    )
        if new_entities:
            _LOGGER.debug("Adding %d new station sensors", len(new_entities))
            async_add_entities(new_entities)

    entry.async_on_unload(
        coordinator.async_add_listener(_async_check_new_stations)
    )


def _get_fuel_label(coordinator: FuelPricesCoordinator, fuel_code: str) -> str:
    """Get the display label for a fuel code from coordinator data."""
    if coordinator.data:
        labels = coordinator.data.get("fuel_labels", {})
        if fuel_code in labels:
            return labels[fuel_code]
    # Fallback to full name
    return FUEL_TYPES.get(fuel_code, fuel_code)


def _build_attributes(data: dict[str, Any], fuel_label: str) -> dict[str, Any]:
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
        "fuel_type": fuel_label,
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
        fuel_code: str,
        rank: int,
    ) -> None:
        super().__init__(coordinator)
        self._fuel_code = fuel_code
        self._rank = rank
        self._rank_label = RANK_LABELS[rank]
        self._attr_unique_id = f"{entry.entry_id}_{fuel_code}_cheapest_{rank + 1}"

    @property
    def _fuel_label(self) -> str:
        """Get the smart display label for this sensor's fuel type."""
        return _get_fuel_label(self.coordinator, self._fuel_code)

    @property
    def name(self) -> str:
        """Return a dynamic name including fuel type, rank, and station."""
        label = self._fuel_label
        data = self._station_data
        if data:
            brand = data.get("brand", "")
            distance = data.get("distance_miles", "?")
            if brand and brand != "Unknown":
                return f"{label} {self._rank_label} — {brand} ({distance} mi)"
            name = data.get("station_name", "Unknown")
            return f"{label} {self._rank_label} — {name} ({distance} mi)"
        return f"{label} {self._rank_label}"

    @property
    def _station_data(self) -> dict[str, Any] | None:
        """Return the data for this sensor's rank and fuel type."""
        if self.coordinator.data:
            by_fuel = self.coordinator.data.get("by_fuel", {})
            fuel_data = by_fuel.get(self._fuel_code, {})
            top3 = fuel_data.get("top3", [])
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
        return _build_attributes(data, self._fuel_label)

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
        fuel_code: str,
        node_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._fuel_code = fuel_code
        self._node_id = node_id
        self._attr_unique_id = (
            f"{entry.entry_id}_{fuel_code}_station_{node_id[:16]}"
        )

        # Set a friendly name from the current data
        data = self._station_data
        fuel_label = _get_fuel_label(coordinator, fuel_code)
        if data:
            brand = data.get("brand", "")
            name = data.get("station_name", "Unknown")
            distance = data.get("distance_miles", "?")
            if brand and brand != "Unknown":
                self._attr_name = (
                    f"{brand} — {name} ({distance} mi) — {fuel_label}"
                )
            else:
                self._attr_name = f"{name} ({distance} mi) — {fuel_label}"
        else:
            self._attr_name = f"Fuel Station {node_id[:8]} — {fuel_label}"

    @property
    def _station_data(self) -> dict[str, Any] | None:
        """Return the data for this specific station and fuel type."""
        if self.coordinator.data:
            by_fuel = self.coordinator.data.get("by_fuel", {})
            fuel_data = by_fuel.get(self._fuel_code, {})
            stations = fuel_data.get("stations", {})
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
        fuel_label = _get_fuel_label(self.coordinator, self._fuel_code)
        return _build_attributes(data, fuel_label)

    @property
    def available(self) -> bool:
        """Return True if data exists for this station."""
        return super().available and self._station_data is not None
