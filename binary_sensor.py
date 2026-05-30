"""Binary Sensor platform for Habitat PTAC integration."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .mqtt_client import HabitatMQTTClient

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Habitat PTAC binary sensors."""
    data = hass.data[DOMAIN][entry.entry_id]
    mqtt_client: HabitatMQTTClient = data["mqtt_client"]
    devices = data["devices"]

    entities = []
    for device in devices:
        entities.append(HabitatLostLinkSensor(mqtt_client, device["thing_name"], device["name"]))

    async_add_entities(entities)


class HabitatLostLinkSensor(BinarySensorEntity):
    """Representation of a Habitat PTAC Lost Link Binary Sensor."""

    _attr_has_entity_name = True
    _attr_name = "Base Unit Connection"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, mqtt_client: HabitatMQTTClient, thing_name: str, device_name: str) -> None:
        """Initialize the sensor."""
        self._mqtt_client = mqtt_client
        self._thing_name = thing_name
        self._props = {}
        
        self._attr_unique_id = f"{thing_name}_lost_link"
        
        parts = thing_name.split("-")
        model_name = parts[2] if len(parts) >= 3 else "Habitat PTAC"
        
        self._attr_device_info = {
            "identifiers": {(DOMAIN, thing_name)},
            "name": device_name,
            "manufacturer": "Habitat",
            "model": model_name,
        }

    async def async_added_to_hass(self) -> None:
        """Subscribe to MQTT state updates."""
        @callback
        def _handle_update(_event=None) -> None:
            new_props = self._mqtt_client.get_latest_props(self._thing_name)
            if new_props:
                self._props = new_props
                self.async_write_ha_state()

        short_id = self._thing_name.split("-")[1] if "-" in self._thing_name else self._thing_name[-12:]
        event_name = f"{DOMAIN}_state_update_{short_id}"
        self.async_on_remove(
            self.hass.bus.async_listen(event_name, _handle_update)
        )
        _handle_update()

    @property
    def is_on(self) -> bool | None:
        """Return true if there is a problem (Link is Lost)."""
        if not self._props:
            return None
            
        link_status = self._props.get("ep0:sPTAC868:BaseModuleLostLinkStatus")
        
        # 0 = Linked (No Problem), 1 = Lost (Problem)
        if link_status == 1:
            return True
        elif link_status == 0:
            return False
            
        return None