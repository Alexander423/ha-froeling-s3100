"""Writable yes/no customer parameters (only when writes are enabled)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
    """Set up switch entities."""
    coordinator = entry.runtime_data
    remove_moved_entities(hass, coordinator, Platform.SWITCH)
    async_add_entities(
        ParameterSwitch(coordinator, p) for p in customer_parameters(coordinator, Platform.SWITCH)
    )


class ParameterSwitch(FroelingParameterEntity, SwitchEntity):
    """A yes/no customer parameter."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool | None:
        """Parameter state."""
        value = self.parameter_value
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the option."""
        await self.coordinator.async_write_parameter(self._parameter.id, 1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the option."""
        await self.coordinator.async_write_parameter(self._parameter.id, 0)
