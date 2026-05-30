"""Sensor platform for Habitat PTAC integration."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
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
    """Set up the Habitat PTAC sensors."""
    data = hass.data[DOMAIN][entry.entry_id]
    mqtt_client: HabitatMQTTClient = data["mqtt_client"]
    devices = data["devices"]

    entities = []
    for device in devices:
        entities.append(
            HabitatBatterySensor(
                mqtt_client,
                device["thing_name"],
                device["name"],
            )
        )

    async_add_entities(entities)


class HabitatBatterySensor(SensorEntity):
    """Representation of a Habitat PTAC Battery Sensor."""

    _attr_has_entity_name = True
    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, mqtt_client: HabitatMQTTClient, thing_name: str, device_name: str) -> None:
        """Initialize the sensor."""
        self._mqtt_client = mqtt_client
        self._thing_name = thing_name
        self._device_name = device_name
        self._props = {}
        
        self._attr_unique_id = f"{thing_name}_battery"
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
        
        # Trigger initial update immediately
        _handle_update()

    @property
    def native_value(self) -> int | None:
        """Return the current battery percentage."""
        if not self._props:
            return None
            
        batt_raw = self._props.get("ep0:sPTAC868:BatteryVoltage_x10")
        if batt_raw is None:
            return None
            
        try:
            # Explicitly cast to float to prevent TypeErrors, divide by 100 for true voltage
            voltage = float(batt_raw) / 100.0
            
            # Map 2 AA batteries: 3.0V is 100%, 2.0V is 0%
            percentage = ((voltage - 2.0) / (3.0 - 2.0)) * 100
            
            return max(0, min(100, round(percentage)))
            
        except Exception as err:
            _LOGGER.error("Battery math error on %s: %s", self._thing_name, err)
            return None
    
