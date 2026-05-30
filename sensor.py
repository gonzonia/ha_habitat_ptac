"""Sensor platform for Habitat PTAC integration."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
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
        entities.append(HabitatBatterySensor(mqtt_client, device["thing_name"], device["name"]))
        entities.append(HabitatRunningStateSensor(mqtt_client, device["thing_name"], device["name"]))
        entities.append(HabitatErrorCodeSensor(mqtt_client, device["thing_name"], device["name"]))
        entities.append(HabitatFilterLifeSensor(mqtt_client, device["thing_name"], device["name"]))
        entities.append(HabitatFilterRunSensor(mqtt_client, device["thing_name"], device["name"]))

    async_add_entities(entities)


class HabitatBaseSensor(SensorEntity):
    """Base class for Habitat sensors to handle MQTT updates and Device Info."""
    _attr_has_entity_name = True

    def __init__(self, mqtt_client: HabitatMQTTClient, thing_name: str, device_name: str) -> None:
        """Initialize the sensor."""
        self._mqtt_client = mqtt_client
        self._thing_name = thing_name
        self._props = {}
        
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
        self.async_on_remove(self.hass.bus.async_listen(event_name, _handle_update))
        _handle_update()


class HabitatBatterySensor(HabitatBaseSensor):
    """Representation of a Habitat PTAC Battery Sensor."""
    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, mqtt_client: HabitatMQTTClient, thing_name: str, device_name: str) -> None:
        super().__init__(mqtt_client, thing_name, device_name)
        self._attr_unique_id = f"{thing_name}_battery"

    @property
    def native_value(self) -> int | None:
        if not self._props:
            return None
        batt_raw = self._props.get("ep0:sPTAC868:BatteryVoltage_x10")
        if batt_raw is None:
            return None
        try:
            voltage = float(batt_raw) / 100.0
            max_v = 3.2
            min_v = 2.0
            percentage = ((voltage - min_v) / (max_v - min_v)) * 100
            return max(0, min(100, round(percentage)))
        except Exception:
            return None


class HabitatRunningStateSensor(HabitatBaseSensor):
    """Representation of the PTAC Running State."""
    _attr_name = "Running State"
    _attr_icon = "mdi:hvac"

    def __init__(self, mqtt_client: HabitatMQTTClient, thing_name: str, device_name: str) -> None:
        super().__init__(mqtt_client, thing_name, device_name)
        self._attr_unique_id = f"{thing_name}_running_state"

    @property
    def native_value(self) -> str | None:
        if not self._props:
            return None
        state = self._props.get("ep0:sPTAC868:RunningState")
        if state is None:
            return None
        if state == 0:
            return "Idle"
        return f"Active ({state})"


class HabitatErrorCodeSensor(HabitatBaseSensor):
    """Representation of the PTAC Error Code."""
    _attr_name = "Error Code"
    _attr_icon = "mdi:alert-circle-outline"

    def __init__(self, mqtt_client: HabitatMQTTClient, thing_name: str, device_name: str) -> None:
        super().__init__(mqtt_client, thing_name, device_name)
        self._attr_unique_id = f"{thing_name}_error_code"

    @property
    def native_value(self) -> str | None:
        if not self._props:
            return None
        error = self._props.get("ep0:sPTAC868:PTACErrorCode")
        if error == 0:
            return "OK"
        return f"Error Code: {error}" if error is not None else None


class HabitatFilterLifeSensor(HabitatBaseSensor):
    """Representation of the total filter lifespan setting."""
    _attr_name = "Filter Lifespan"
    _attr_icon = "mdi:filter-outline"
    _attr_native_unit_of_measurement = UnitOfTime.DAYS

    def __init__(self, mqtt_client: HabitatMQTTClient, thing_name: str, device_name: str) -> None:
        super().__init__(mqtt_client, thing_name, device_name)
        self._attr_unique_id = f"{thing_name}_filter_life"

    @property
    def native_value(self) -> int | None:
        if not self._props:
            return None
        return self._props.get("ep0:sPTAC868:FilterDays")


class HabitatFilterRunSensor(HabitatBaseSensor):
    """Representation of how many days the current filter has run."""
    _attr_name = "Filter Usage"
    _attr_icon = "mdi:filter-cog-outline"
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, mqtt_client: HabitatMQTTClient, thing_name: str, device_name: str) -> None:
        super().__init__(mqtt_client, thing_name, device_name)
        self._attr_unique_id = f"{thing_name}_filter_run"

    @property
    def native_value(self) -> int | None:
        if not self._props:
            return None
        return self._props.get("ep0:sPTAC868:FilterRunDays")