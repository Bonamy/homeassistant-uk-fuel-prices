"""Config flow for UK Fuel Prices integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode

from .api import FuelFinderApi, FuelFinderAuthError, FuelFinderApiError
from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_FUEL_TYPES,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_ORS_API_KEY,
    CONF_RADIUS,
    DEFAULT_FUEL_TYPES,
    DEFAULT_RADIUS,
    DOMAIN,
    FUEL_TYPES,
)

_LOGGER = logging.getLogger(__name__)


async def _validate_credentials(
    hass: HomeAssistant, client_id: str, client_secret: str
) -> None:
    """Validate the API credentials by requesting a token."""
    session = aiohttp.ClientSession()
    try:
        api = FuelFinderApi(session, client_id, client_secret)
        await api.test_connection()
    finally:
        await session.close()


# Build options list for the multi-select fuel type selector
_FUEL_TYPE_OPTIONS = [
    {"value": code, "label": label} for code, label in FUEL_TYPES.items()
]


class UkFuelPricesConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for UK Fuel Prices."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow handler."""
        return UkFuelPricesOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await _validate_credentials(
                    self.hass,
                    user_input[CONF_CLIENT_ID],
                    user_input[CONF_CLIENT_SECRET],
                )
            except FuelFinderAuthError:
                errors["base"] = "invalid_auth"
            except FuelFinderApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during config")
                errors["base"] = "unknown"
            else:
                lat = user_input.get(CONF_LATITUDE) or self.hass.config.latitude
                lon = user_input.get(CONF_LONGITUDE) or self.hass.config.longitude

                # Only allow one instance of this integration
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()

                fuel_types = user_input.get(CONF_FUEL_TYPES, DEFAULT_FUEL_TYPES)

                return self.async_create_entry(
                    title="UK Fuel Prices",
                    data={
                        CONF_CLIENT_ID: user_input[CONF_CLIENT_ID],
                        CONF_CLIENT_SECRET: user_input[CONF_CLIENT_SECRET],
                        CONF_LATITUDE: lat,
                        CONF_LONGITUDE: lon,
                        CONF_RADIUS: user_input.get(CONF_RADIUS, DEFAULT_RADIUS),
                        CONF_FUEL_TYPES: fuel_types,
                        CONF_ORS_API_KEY: user_input.get(CONF_ORS_API_KEY, ""),
                    },
                )

        home_lat = self.hass.config.latitude
        home_lon = self.hass.config.longitude

        schema = vol.Schema(
            {
                vol.Required(CONF_CLIENT_ID): str,
                vol.Required(CONF_CLIENT_SECRET): str,
                vol.Optional(CONF_LATITUDE, default=home_lat): vol.Coerce(float),
                vol.Optional(CONF_LONGITUDE, default=home_lon): vol.Coerce(float),
                vol.Optional(CONF_RADIUS, default=DEFAULT_RADIUS): vol.All(
                    vol.Coerce(float), vol.Range(min=1, max=100)
                ),
                vol.Optional(
                    CONF_FUEL_TYPES, default=DEFAULT_FUEL_TYPES
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=_FUEL_TYPE_OPTIONS,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Optional(CONF_ORS_API_KEY): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )


class UkFuelPricesOptionsFlow(OptionsFlow):
    """Handle options for UK Fuel Prices."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            new_data = {**self._config_entry.data, **user_input}
            self.hass.config_entries.async_update_entry(
                self._config_entry, data=new_data
            )
            await self.hass.config_entries.async_reload(self._config_entry.entry_id)
            return self.async_create_entry(title="", data={})

        current = self._config_entry.data
        home_lat = self.hass.config.latitude
        home_lon = self.hass.config.longitude

        # Support legacy single fuel_type config
        current_fuel_types = current.get(CONF_FUEL_TYPES)
        if not current_fuel_types:
            legacy = current.get("fuel_type")
            current_fuel_types = [legacy] if legacy else DEFAULT_FUEL_TYPES

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_LATITUDE,
                    default=current.get(CONF_LATITUDE, home_lat),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_LONGITUDE,
                    default=current.get(CONF_LONGITUDE, home_lon),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_RADIUS,
                    default=current.get(CONF_RADIUS, DEFAULT_RADIUS),
                ): vol.All(vol.Coerce(float), vol.Range(min=1, max=100)),
                vol.Optional(
                    CONF_FUEL_TYPES,
                    default=current_fuel_types,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=_FUEL_TYPE_OPTIONS,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Optional(
                    CONF_ORS_API_KEY,
                    default=current.get(CONF_ORS_API_KEY, ""),
                ): str,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
