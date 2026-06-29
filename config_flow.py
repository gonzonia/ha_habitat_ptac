"""Config flow for Habitat PTAC integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .auth import authenticate, get_aws_credentials, get_devices, get_identity_id
from .const import (
    CONF_DEVICES,
    CONF_IDENTITY_ID,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def _validate_and_discover(
    hass: HomeAssistant, username: str, password: str
) -> dict[str, Any]:
    """Authenticate and discover devices. Raises InvalidAuth on failure."""
    try:
        id_token, _access_token, refresh_token = await hass.async_add_executor_job(
            authenticate, username, password
        )
        identity_id = await hass.async_add_executor_job(get_identity_id, id_token)
        credentials = await hass.async_add_executor_job(
            get_aws_credentials, identity_id, id_token
        )
        devices = await hass.async_add_executor_job(get_devices, identity_id, credentials)

        if not devices:
            raise NoDevicesFound

        return {
            CONF_REFRESH_TOKEN: refresh_token,
            CONF_IDENTITY_ID: identity_id,
            CONF_DEVICES: [
                {
                    "gateway_id": d.gateway_id,
                    "thing_name": d.thing_name,
                    "name": d.name,
                }
                for d in devices
            ],
        }
    except NoDevicesFound:
        raise
    except Exception as err:
        _LOGGER.error("Authentication failed: %s", err)
        raise InvalidAuth from err


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Habitat PTAC."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            try:
                discovered = await _validate_and_discover(
                    self.hass, username, password
                )
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except NoDevicesFound:
                errors["base"] = "no_devices"
            except Exception:
                _LOGGER.exception("Unexpected error during setup")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(username.lower())
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=username,
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        **discovered,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.FlowResult:
        """Handle re-authentication when the refresh token has expired."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Show a password prompt and re-authenticate."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        username = entry.data[CONF_USERNAME]

        if user_input is not None:
            try:
                discovered = await _validate_and_discover(
                    self.hass, username, user_input[CONF_PASSWORD]
                )
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during re-authentication")
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={
                        **entry.data,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        **discovered,
                    },
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": username},
            errors=errors,
        )


class InvalidAuth(HomeAssistantError):
    """Raised when credentials are invalid."""


class NoDevicesFound(HomeAssistantError):
    """Raised when no PTAC devices are associated with the account."""
