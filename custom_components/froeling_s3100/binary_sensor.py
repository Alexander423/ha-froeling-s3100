"""Binary sensors for the Fröling S3100."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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
from .measurements import lookup
from .s3100 import Measurement, MeasurementKind

PARALLEL_UPDATES = 0

# The status line shows this text while the boiler is in a fault state.
FAULT_MARKER = "störung"
# Status texts (German, as sent by the controller) while there is a fire.
FIRE_STATES = frozenset({"anheizen", "heizen", "feuerhaltung", "vorwärmphase", "zünden"})
HEATING_UP_STATE = "anheizen"

type Value = float | int | str | None


def _status(value: Value) -> str | None:
    return " ".join(value.split()).lower() if isinstance(value, str) else None


def _positive(value: Value) -> bool | None:
    return value > 0 if isinstance(value, int | float) else None


def _fault(value: Value) -> bool | None:
    status = _status(value)
    return None if status is None else FAULT_MARKER in status


def _fire(value: Value) -> bool | None:
    status = _status(value)
    return None if status is None else status in FIRE_STATES


def _heating_up(value: Value) -> bool | None:
    status = _status(value)
    return None if status is None else status == HEATING_UP_STATE


@dataclass(frozen=True, slots=True)
class Derived:
    """A binary sensor computed from one measurement."""

    key: str
    translation_key: str
    predicate: Callable[[Value], bool | None]
    device_class: BinarySensorDeviceClass | None = None
    placeholders: dict[str, str] | None = None


STATUS_SENSORS = (
    Derived("fault", "fault", _fault, BinarySensorDeviceClass.PROBLEM),
    Derived("fire_active", "fire_active", _fire, BinarySensorDeviceClass.HEAT),
    Derived("heating_up", "heating_up", _heating_up),
)
RUNNING_SENSORS = {
    "buffer_pump": "buffer_pump_running",
    "induced_draft_fan": "induced_draft_fan_running",
}


def derived_sensors(measurements: list[Measurement]) -> list[tuple[Derived, str]]:
    """Derived sensors available for the announced measurements."""
    result: list[tuple[Derived, str]] = []
    for measurement in measurements:
        if measurement.kind is MeasurementKind.TEXT and not any(d.key == "fault" for d, _ in result):
            result += [(d, measurement.key) for d in STATUS_SENSORS]
            continue
        info, placeholders = lookup(measurement.name)
        if info is None:
            continue
        if info.translation_key == "flow_temperature_target":
            number = placeholders["number"]
            result.append(
                (
                    Derived(f"circuit_{number}_demand", "circuit_demand", _positive, None, placeholders),
                    measurement.key,
                )
            )
        elif (name := RUNNING_SENSORS.get(info.translation_key)) is not None:
            result.append((Derived(name, name, _positive, BinarySensorDeviceClass.RUNNING), measurement.key))
    return result


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    coordinator = entry.runtime_data
    remove_moved_entities(hass, coordinator, Platform.BINARY_SENSOR)
    entities: list[BinarySensorEntity] = [ConnectivitySensor(coordinator)]
    entities += [
        DerivedBinarySensor(coordinator, derived, source)
        for derived, source in derived_sensors(coordinator.catalog.measurements)
    ]
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


class DerivedBinarySensor(FroelingEntity, BinarySensorEntity):
    """A state derived from a measurement (e.g. the status line)."""

    def __init__(self, coordinator: FroelingCoordinator, derived: Derived, source_key: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, derived.key)
        self._derived = derived
        self._source_key = source_key
        self._attr_translation_key = derived.translation_key
        self._attr_device_class = derived.device_class
        if derived.placeholders:
            self._attr_translation_placeholders = derived.placeholders

    @property
    def is_on(self) -> bool | None:
        """Evaluate the source value."""
        return self._derived.predicate(self.coordinator.data.values.get(self._source_key))


class ParameterBinarySensor(FroelingParameterEntity, BinarySensorEntity):
    """A yes/no customer parameter shown read-only."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        """Parameter state."""
        value = self.parameter_value
        return None if value is None else bool(value)
