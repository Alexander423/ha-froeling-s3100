"""Writable numeric customer parameters (only when writes are enabled)."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory, Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FroelingConfigEntry, FroelingCoordinator
from .entity import FroelingParameterEntity, customer_parameters, remove_moved_entities
from .s3100 import Parameter
from .sensor import UNITS

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up number entities."""
    coordinator = entry.runtime_data
    remove_moved_entities(hass, coordinator, Platform.NUMBER)
    async_add_entities(
        ParameterNumber(coordinator, p) for p in customer_parameters(coordinator, Platform.NUMBER)
    )


class ParameterNumber(FroelingParameterEntity, NumberEntity):
    """A numeric customer parameter."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: FroelingCoordinator, parameter: Parameter) -> None:
        """Initialize the entity with the limits announced by the controller."""
        super().__init__(coordinator, parameter)
        unit = UNITS.get(parameter.unit or "")
        self._attr_native_unit_of_measurement = unit
        if unit == UnitOfTemperature.CELSIUS:
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
        self._attr_native_min_value = parameter.minimum
        self._attr_native_max_value = parameter.maximum
        self._attr_native_step = parameter.step

    @property
    def native_value(self) -> float | None:
        """Current value."""
        return self.parameter_value

    async def async_set_native_value(self, value: float) -> None:
        """Write the value to the controller."""
        await self.coordinator.async_write_parameter(self._parameter.id, value)
