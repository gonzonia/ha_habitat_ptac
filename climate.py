"""Climate platform for Habitat PTAC integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
    ATTR_HVAC_MODE,
    ATTR_TEMPERATURE
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEVICE_TO_HA_FAN,
    DEVICE_TO_HA_MODE,
    DOMAIN,
    HA_TO_DEVICE_FAN,
    HA_TO_DEVICE_MODE,
    RUNNING_STATE_ACTIVE,
    TEMP_MULTIPLIER,
)
from .mqtt_client import HabitatMQTTClient

_LOGGER = logging.getLogger(__name__)

HVAC_MODES = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.FAN_ONLY]
FAN_MODES = ["auto", "low", "high"]

# Device limits: 500–3500 (x100 °C) → 41–95 °F
MIN_TEMP_F = 41.0
MAX_TEMP_F = 95.0


def _c_to_f(celsius: float) -> float:
    """Convert Celsius to Fahrenheit, rounded to one decimal."""
    return round(celsius * 9 / 5 + 32, 1)


def _f_to_c(fahrenheit: float) -> float:
    """Convert Fahrenheit to Celsius."""
    return (fahrenheit - 32) * 5 / 9


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up climate entities from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]

    entities = [
        HabitatClimate(
            hass=hass,
            device=device,
            mqtt_client=data["mqtt_client"],
            device_states=data["device_states"],
        )
        for device in data["devices"]
    ]
    async_add_entities(entities)


class HabitatClimate(ClimateEntity):
    """Climate entity representing one Habitat PTAC unit."""

    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_hvac_modes = HVAC_MODES
    _attr_fan_modes = FAN_MODES
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE
    )
    _attr_min_temp = MIN_TEMP_F
    _attr_max_temp = MAX_TEMP_F
    _attr_target_temperature_step = 1.0
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        device: dict,
        mqtt_client: HabitatMQTTClient,
        device_states: dict[str, dict],
    ) -> None:
        self.hass = hass
        self._device = device
        self._mqtt_client = mqtt_client
        self._device_states = device_states
        self._thing_name: str = device["thing_name"]
        self._attr_unique_id = self._thing_name
        self._attr_name = device["name"]
        
        parts = self._thing_name.split("-")
        model_name = parts[2] if len(parts) >= 3 else "Habitat PTAC"
        
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._thing_name)},
            "name": device["name"],
            "manufacturer": "Habitat",
            "model": model_name,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Subscribe to MQTT state update events for this device."""

        @callback
        def _handle_update(_event) -> None:
            self.async_write_ha_state()

        short_id = self._thing_name.split("-")[1] if "-" in self._thing_name else self._thing_name[-12:]
        event_name = f"{DOMAIN}_state_update_{short_id}"

        self.async_on_remove(
            self.hass.bus.async_listen(event_name, _handle_update)
        )

    # ------------------------------------------------------------------
    # State properties
    # ------------------------------------------------------------------

    @property
    def _props(self) -> dict:
        """Shortcut to this device's property state dict."""
        return self._device_states.get(self._thing_name, {})

    @property
    def current_temperature(self) -> float | None:
        val = self._props.get("ep0:sPTAC868:LocalTemperature_x100")
        if val is not None:
            return _c_to_f(val / TEMP_MULTIPLIER)
        return None

    @property
    def hvac_mode(self) -> HVACMode | None:
        val = self._props.get("ep0:sPTAC868:SystemMode")
        if val is not None:
            ha_mode = DEVICE_TO_HA_MODE.get(val)
            if ha_mode:
                return HVACMode(ha_mode)
        return None

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current running action based on RunningState and Mode."""
        running_state = self._props.get("ep0:sPTAC868:RunningState", 0)
        
        if running_state > 0:
            if self.hvac_mode == HVACMode.COOL:
                return HVACAction.COOLING
            elif self.hvac_mode == HVACMode.HEAT:
                return HVACAction.HEATING
            elif self.hvac_mode == HVACMode.FAN_ONLY:
                return HVACAction.FAN
                
        return HVACAction.IDLE

    @property
    def target_temperature(self) -> float | None:
        mode = self.hvac_mode
        
        # Hide target temperature if the unit is OFF or in FAN_ONLY
        if mode not in (HVACMode.HEAT, HVACMode.COOL):
            return None
            
        # The device uses HeatingSetpoint_x100 as the universal master setpoint
        val = self._props.get("ep0:sPTAC868:HeatingSetpoint_x100")
        
        if val is not None:
            _LOGGER.debug("Target Setpoint (via HeatingSetpoint) is %s - mode %s", val, mode)
            return _c_to_f(val / TEMP_MULTIPLIER)
            
        return None

    @property
    def fan_mode(self) -> str | None:
        val = self._props.get("ep0:sPTAC868:FanMode")
        if val is not None:
            return DEVICE_TO_HA_FAN.get(val)
        return None

    @property
    def available(self) -> bool:
        """Return True if the entity is fully connected to the cloud and gateway."""
        if not self._props:
            return False
            
        cloud_conn = self._props.get("_cloud_conn", 1)
        node_conn = self._props.get("_node_conn", 1)
        connected = self._props.get("_connected", "true")
        
        if cloud_conn == 0 or connected == "false" or node_conn == 0:
            return False
            
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes like battery level and connection status."""
        attrs = {}
        if self._props:
            batt_x10 = self._props.get("ep0:sPTAC868:BatteryVoltage_x10")
            if batt_x10 is not None:
                batt_calc = batt_x10 / 100.0 
                attrs["battery_voltage"] = batt_calc 
                
            cloud_conn = self._props.get("_cloud_conn", 1)
            node_conn = self._props.get("_node_conn", 1)
            attrs["cloud_connected"] = bool(cloud_conn == 1)
            attrs["node_connected"] = bool(node_conn == 1)
            _LOGGER.debug("Battery is %s, cloud is %s, node is %s", batt_calc if batt_x10 is not None else None, attrs.get("cloud_connected"), attrs.get("node_connected"))
            
        return attrs

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the HVAC mode."""
        device_mode = HA_TO_DEVICE_MODE.get(hvac_mode.value)
        if device_mode is None:
            _LOGGER.error("Unsupported HVAC mode: %s", hvac_mode)
            return
        await self.hass.async_add_executor_job(
            self._mqtt_client.publish_command,
            self._thing_name,
            {"ep0:sPTAC868:SetSystemMode": device_mode},
        )

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is None:
            return

        # Convert target °F to °C x100
        temp_c = _f_to_c(temp)
        temp_x100 = int(round(temp_c * TEMP_MULTIPLIER))

        # The device strictly requires SetHeatingSetpoint_x100 for all temperature changes
        await self.hass.async_add_executor_job(
            self._mqtt_client.publish_command,
            self._thing_name,
            {"ep0:sPTAC868:SetHeatingSetpoint_x100": temp_x100},
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the fan mode."""
        device_fan = HA_TO_DEVICE_FAN.get(fan_mode)
        if device_fan is None:
            _LOGGER.error("Unsupported fan mode: %s", fan_mode)
            return
        await self.hass.async_add_executor_job(
            self._mqtt_client.publish_command,
            self._thing_name,
            {"ep0:sPTAC868:SetFanMode": device_fan},
        )