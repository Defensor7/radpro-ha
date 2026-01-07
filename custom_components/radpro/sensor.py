"""Sensor platform for RadPro integration."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import RadProCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up RadPro sensors from a config entry."""
    coordinator: RadProCoordinator = hass.data[DOMAIN][entry.entry_id]
    _create_sensors(coordinator, async_add_entities)


async def async_setup_platform(
    hass: HomeAssistant, config, async_add_entities, discovery_info=None
) -> None:
    """Set up RadPro sensors from YAML (deprecated)."""
    coordinator: RadProCoordinator = hass.data[DOMAIN].get("yaml")
    if coordinator:
        _create_sensors(coordinator, async_add_entities)


def _create_sensors(
    coordinator: RadProCoordinator, async_add_entities: AddEntitiesCallback
) -> None:
    """Create sensor entities."""
    async_add_entities(
        [
            RadProDeviceInfoSensor(coordinator),
            RadProValueSensor(
                coordinator, "cps", "Radiation CPS", "cps", "mdi:radioactive",
                precision=2,  # 0.17, 0.83 CPS - need decimals for low background
            ),
            RadProValueSensor(
                coordinator, "cpm", "Radiation CPM", "cpm", "mdi:radioactive",
                precision=0,  # 15, 42 CPM - standard Geiger counter display
            ),
            RadProValueSensor(
                coordinator, "usvh", "Radiation µSv/h", "µSv/h", "mdi:radioactive",
                precision=2,  # 0.08, 0.15 µSv/h - standard dosimeter precision
            ),
            RadProValueSensor(
                coordinator,
                "pulse_count",
                "Tube Pulse Count",
                "pulses",
                "mdi:counter",
                state_class=SensorStateClass.TOTAL_INCREASING,
                precision=0,
            ),
        ],
        True,
    )


class RadProSensorBase(SensorEntity):
    """Base class for RadPro sensors with common device info."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: RadProCoordinator) -> None:
        """Initialize the sensor."""
        self.coordinator = coordinator
        # Store device_id at init time to ensure consistent identifiers
        self._device_id = coordinator.device_info.device_id or f"radpro_{coordinator.io.port.replace('/', '_')}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info to link all sensors to one device."""
        di = self.coordinator.device_info
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=f"RadPro {di.model or 'Dosimeter'}",
            manufacturer="Gissio",
            model=di.hardware_id or "RadPro",
            sw_version=di.sw_version,
            serial_number=di.device_id,
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.last_update_success

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )


class RadProValueSensor(RadProSensorBase):
    """Sensor for radiation measurements (CPS, CPM, µSv/h, pulse count)."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: RadProCoordinator,
        key: str,
        name: str,
        unit: str,
        icon: str,
        state_class: SensorStateClass = SensorStateClass.MEASUREMENT,
        precision: int | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.key = key
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_unique_id = f"{self._device_id}_{key}"
        self._attr_state_class = state_class
        if precision is not None:
            self._attr_suggested_display_precision = precision

    @property
    def native_value(self):
        """Return the sensor value."""
        data = self.coordinator.data or {}
        return data.get(self.key)


class RadProDeviceInfoSensor(RadProSensorBase):
    """Diagnostic sensor showing device ID and battery voltage."""

    _attr_icon = "mdi:identifier"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Device Info"

    def __init__(self, coordinator: RadProCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_device_info"

    @property
    def native_value(self):
        """Return the software version as the sensor state."""
        di = self.coordinator.device_info
        return di.software_id or di.device_id or "unknown"

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes parsed from deviceId response.
        
        deviceId format: [hardware-id];[software-id];[device-id]
        Example: FS2011 (STM32F051C8);Rad Pro 2.0/en;b5706d937087f975b5812810
        """
        di = self.coordinator.device_info
        attrs = {
            "hardware_id": di.hardware_id,
            "software_id": di.software_id,
            "device_id": di.device_id,
            "model": di.model,
            "version": di.sw_version,
            "port": self.coordinator.io.port,
            "battery_voltage_per_cell": di.battery_voltage,
        }
        return {k: v for k, v in attrs.items() if v is not None}
