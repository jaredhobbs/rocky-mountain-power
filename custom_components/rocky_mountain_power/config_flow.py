"""Config flow for Rocky Mountain Power integration."""
from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from rocky_mountain_power import (
    CannotConnect,
    InvalidAuth,
    RockyMountainPower,
)
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_SELENIUM_HOST, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_SELENIUM_HOST, default="5f203b37-selenium-standalone-chrome"): str,
    }
)


def _validate_login(login_data: dict[str, str]) -> dict[str, str]:
    """Validate login data and return any errors."""
    api = RockyMountainPower(
        login_data[CONF_USERNAME],
        login_data[CONF_PASSWORD],
        login_data[CONF_SELENIUM_HOST],
    )
    errors: dict[str, str] = {}
    try:
        api.login()
    except InvalidAuth:
        errors["base"] = "invalid_auth"
    except CannotConnect:
        errors["base"] = "cannot_connect"
    finally:
        api.end_session()
    return errors


class RockyMountainPowerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for RockyMountainPower."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize a new RockyMountainPowerConfigFlow."""
        self.reauth_entry: config_entries.ConfigEntry | None = None
        self.utility_info: dict[str, Any] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._async_abort_entries_match(
                {
                    CONF_USERNAME: user_input[CONF_USERNAME],
                }
            )

            errors = _validate_login(user_input)
            if not errors:
                return self._async_create_rocky_mountain_power_entry(user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    @callback
    def _async_create_rocky_mountain_power_entry(self, data: dict[str, Any]) -> FlowResult:
        """Create the config entry."""
        return self.async_create_entry(
            title=f"Rocky Mountain Power ({data[CONF_USERNAME]})",
            data=data,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """Handle configuration by re-auth."""
        self.reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Dialog that informs the user that reauth is required."""
        assert self.reauth_entry
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**self.reauth_entry.data, **user_input}
            errors = _validate_login(data)
            if not errors:
                self.hass.config_entries.async_update_entry(
                    self.reauth_entry, data=data
                )
                await self.hass.config_entries.async_reload(self.reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")
        schema = {
            vol.Required(CONF_USERNAME): self.reauth_entry.data[CONF_USERNAME],
            vol.Required(CONF_PASSWORD): str,
        }
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(schema),
            errors=errors,
        )
