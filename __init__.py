"""UK Fuel Prices integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FuelFinderApi
from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_FUEL_TYPE,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_ORS_API_KEY,
    CONF_RADIUS,
    DOMAIN,
)
from .coordinator import FuelPricesCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up UK Fuel Prices from a config entry."""
    session = async_get_clientsession(hass)

    api = FuelFinderApi(
        session,
        entry.data[CONF_CLIENT_ID],
        entry.data[CONF_CLIENT_SECRET],
    )

    coordinator = FuelPricesCoordinator(
        hass,
        api,
        session,
        home_lat=entry.data[CONF_LATITUDE],
        home_lon=entry.data[CONF_LONGITUDE],
        radius=entry.data[CONF_RADIUS],
        fuel_type=entry.data[CONF_FUEL_TYPE],
        ors_api_key=entry.data.get(CONF_ORS_API_KEY) or None,
    )

    # Use async_refresh instead of async_config_entry_first_refresh so the
    # integration still loads when the API is temporarily unavailable (e.g.
    # maintenance).  Sensors will show as unavailable until data arrives.
    await coordinator.async_refresh()
    _LOGGER.debug(
        "Initial refresh complete, data available: %s",
        coordinator.data is not None,
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
