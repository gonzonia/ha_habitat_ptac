"""Habitat PTAC integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.device_registry import DeviceEntry

from .auth import get_aws_credentials, refresh_tokens
from .const import (
    CONF_DEVICES,
    CONF_IDENTITY_ID,
    CONF_REFRESH_TOKEN,
    CONF_USERNAME,
    CREDENTIAL_REFRESH_INTERVAL_MINUTES,
    DOMAIN,
)
from .mqtt_client import HabitatMQTTClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE, Platform.SENSOR, Platform.BINARY_SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Habitat PTAC from a config entry."""
    username = entry.data[CONF_USERNAME]
    identity_id = entry.data[CONF_IDENTITY_ID]
    refresh_token = entry.data[CONF_REFRESH_TOKEN]
    devices = entry.data[CONF_DEVICES]

    id_token, _ = await hass.async_add_executor_job(
        refresh_tokens, username, refresh_token
    )
    credentials = await hass.async_add_executor_job(
        get_aws_credentials, identity_id, id_token
    )

    thing_names = [d["thing_name"] for d in devices]
    gateway_ids = [d["gateway_id"] for d in devices]
    _LOGGER.debug("Checking device states for gateway_ids: %s", gateway_ids)

    # Shared state store — updated by MQTT callbacks, read by climate entities
    device_states: dict[str, dict] = {tn: {} for tn in thing_names}

    def state_callback(thing_name: str, props: dict) -> None:
        """Called from MQTT thread when a device reports new state."""
        device_states[thing_name].update(props)
        short_id = thing_name.split("-")[1] if "-" in thing_name else thing_name[-12:]
        event_name = f"{DOMAIN}_state_update_{short_id}"
        hass.loop.call_soon_threadsafe(hass.bus.fire, event_name)

    mqtt_client = HabitatMQTTClient(thing_names, state_callback, identity_id, gateway_ids[0])
    await hass.async_add_executor_job(mqtt_client.connect, credentials)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "mqtt_client": mqtt_client,
        "device_states": device_states,
        "devices": devices,
    }

    # Schedule credential refresh every 50 minutes (they expire in 60)
    async def _refresh_credentials(_now=None) -> None:
        try:
            new_id_token, _ = await hass.async_add_executor_job(
                refresh_tokens, username, refresh_token
            )
            new_credentials = await hass.async_add_executor_job(
                get_aws_credentials, identity_id, new_id_token
            )
            await hass.async_add_executor_job(mqtt_client.reconnect, new_credentials)
            _LOGGER.info("Refreshed AWS IoT credentials for %s", username)
        except Exception as err:
            _LOGGER.error("Failed to refresh credentials: %s", err)

    entry.async_on_unload(
        async_track_time_interval(
            hass,
            _refresh_credentials,
            timedelta(minutes=CREDENTIAL_REFRESH_INTERVAL_MINUTES),
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await hass.async_add_executor_job(data["mqtt_client"].disconnect)
    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow the user to delete a device from the UI."""
    _LOGGER.info("User requested to delete device: %s", device_entry.name)
    
    # Returning True tells Home Assistant it has permission to wipe the 
    # device (and all of its attached entities) from the registry.
    return True