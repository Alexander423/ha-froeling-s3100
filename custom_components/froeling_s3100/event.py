"""Fault events reported live by the Fröling S3100."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EVENT_FAULT
from .coordinator import FroelingConfigEntry, FroelingCoordinator, fault_signal
from .entity import FroelingEntity
from .s3100 import FaultEvent

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the fault event entity."""
    async_add_entities([FaultEventEntity(entry.runtime_data)])


class FaultEventEntity(FroelingEntity, EventEntity):
    """Fires whenever the controller reports a fault (M3)."""

    _attr_translation_key = "fault_event"

    def __init__(self, coordinator: FroelingCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator, "fault_event")
        self._attr_event_types = [EVENT_FAULT]

    async def async_added_to_hass(self) -> None:
        """Listen for faults."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, fault_signal(self.coordinator.config_entry.entry_id), self._handle_fault
            )
        )

    @callback
    def _handle_fault(self, fault: FaultEvent) -> None:
        self._trigger_event(
            EVENT_FAULT,
            {
                "error_id": fault.error_id,
                "text": fault.text,
                "state": fault.state,
                "flags": fault.flags,
                "controller_time": fault.timestamp.isoformat() if fault.timestamp else None,
            },
        )
        self.async_write_ha_state()
