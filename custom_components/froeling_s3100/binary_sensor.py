"""Binary sensors for the Fröling S3100."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FroelingConfigEntry, FroelingCoordinator
from .entity import (
    FroelingEntity,
    FroelingParameterEntity,
    customer_parameters,
    remove_moved_entities,
)
from .s3100 import MeasurementKind

PARALLEL_UPDATES = 0

# The status line shows this text while the boiler is in a fault state.
FAULT_MARKER = "störung"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    coordinator = entry.runtime_data
    remove_moved_entities(hass, coordinator, Platform.BINARY_SENSOR)
    entities: list[BinarySensorEntity] = [ConnectivitySensor(coordinator)]
    status = next((m.key for m in coordinator.catalog.measurements if m.kind is MeasurementKind.TEXT), None)
    if status is not None:
        entities.append(FaultSensor(coordinator, status))
    entities += [
        ParameterBinarySensor(coordinator, p)
        for p in customer_parameters(coordinator, Platform.BINARY_SENSOR)
    ]
    async_add_entities(entities)


class ConnectivitySensor(FroelingEntity, BinarySensorEntity):
    """Whether the session with the controller is up."""

    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: FroelingCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "connected")

    @property
    def available(self) -> bool:
        """Always available, it reports the link itself."""
        return True

    @property
    def is_on(self) -> bool:
        """Session state."""
        return self.coordinator.data.online


class FaultSensor(FroelingEntity, BinarySensorEntity):
    """On while the controller display reports a fault."""

    _attr_translation_key = "fault"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: FroelingCoordinator, status_key: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "fault")
        self._status_key = status_key

    @property
    def is_on(self) -> bool | None:
        """Fault state derived from the status text."""
        status = self.coordinator.data.values.get(self._status_key)
        if not isinstance(status, str):
            return None
        return FAULT_MARKER in status.lower()


class ParameterBinarySensor(FroelingParameterEntity, BinarySensorEntity):
    """A yes/no customer parameter shown read-only."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        """Parameter state."""
        value = self.parameter_value
        return None if value is None else bool(value)
