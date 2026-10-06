"""Writable times of day, e.g. heating phases (only when writes are enabled)."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FroelingConfigEntry
from .entity import FroelingParameterEntity, customer_parameters, remove_moved_entities

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up time entities."""
    coordinator = entry.runtime_data
    remove_moved_entities(hass, coordinator, Platform.TIME)
    async_add_entities(ParameterTime(coordinator, p) for p in customer_parameters(coordinator, Platform.TIME))


class ParameterTime(FroelingParameterEntity, TimeEntity):
    """A time-of-day customer parameter (minutes since midnight)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> time | None:
        """Current time."""
        value = self.parameter_value
        if value is None:
            return None
        minutes = int(value) % (24 * 60)
        return time(minutes // 60, minutes % 60)

    async def async_set_value(self, value: time) -> None:
        """Write the time to the controller."""
        await self.coordinator.async_write_parameter(self._parameter.id, value.hour * 60 + value.minute)
