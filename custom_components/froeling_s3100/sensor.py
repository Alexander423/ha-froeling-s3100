"""Sensors for the Fröling S3100."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    Platform,
    UnitOfElectricPotential,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import FroelingConfigEntry, FroelingCoordinator
from .entity import (
    FroelingEntity,
    FroelingParameterEntity,
    customer_parameters,
    remove_moved_entities,
)
from .measurements import lookup, service_parameters
from .s3100 import Measurement, MeasurementKind, Parameter

PARALLEL_UPDATES = 0

UNITS: dict[str, str] = {
    "°C": UnitOfTemperature.CELSIUS,
    "%": PERCENTAGE,
    "h": UnitOfTime.HOURS,
    "min": UnitOfTime.MINUTES,
    "s": UnitOfTime.SECONDS,
    "V": UnitOfElectricPotential.VOLT,
    "rpm": REVOLUTIONS_PER_MINUTE,
}
DEVICE_CLASS_BY_UNIT: dict[str, SensorDeviceClass] = {
    UnitOfTemperature.CELSIUS: SensorDeviceClass.TEMPERATURE,
    UnitOfTime.HOURS: SensorDeviceClass.DURATION,
    UnitOfTime.MINUTES: SensorDeviceClass.DURATION,
    UnitOfTime.SECONDS: SensorDeviceClass.DURATION,
    UnitOfElectricPotential.VOLT: SensorDeviceClass.VOLTAGE,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors from the announced catalog."""
    coordinator = entry.runtime_data
    remove_moved_entities(hass, coordinator, Platform.SENSOR)
    entities: list[SensorEntity] = [
        MeasurementSensor(coordinator, m) for m in coordinator.catalog.measurements
    ]
    entities += [ParameterSensor(coordinator, p) for p in customer_parameters(coordinator, Platform.SENSOR)]
    circuits: set[str] = set()
    for measurement in coordinator.catalog.measurements:
        info, placeholders = lookup(measurement.name)
        if info is not None and info.translation_key == "flow_temperature":
            circuits.add(placeholders["number"])
    for param_id, (key, enabled, placeholders) in service_parameters(circuits).items():
        if (parameter := coordinator.catalog.parameters.get(param_id)) is not None and not (
            parameter.in_customer_menu
        ):
            entities.append(ServiceParameterSensor(coordinator, parameter, key, enabled, placeholders))
    entities += [
        LastFaultSensor(coordinator),
        LastFaultTimeSensor(coordinator),
        ControllerTimeSensor(coordinator),
    ]
    async_add_entities(entities)


def _precision(measurement: Measurement) -> int:
    fmt = measurement.value_format
    if fmt is None:
        return 0
    if fmt.divisor in (0, 1, 10**fmt.decimals):
        return fmt.decimals
    return max(fmt.decimals, 1)


class MeasurementSensor(FroelingEntity, SensorEntity):
    """A value from the cyclic M1 telegram."""

    def __init__(self, coordinator: FroelingCoordinator, measurement: Measurement) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, measurement.key)
        self._key = measurement.key
        info, placeholders = lookup(measurement.name)
        if measurement.kind is MeasurementKind.TEXT:
            self._attr_translation_key = "status"
        elif info is not None:
            self._attr_translation_key = info.translation_key
            self._attr_translation_placeholders = placeholders
            self._attr_entity_registry_enabled_default = info.enabled_default
            self._attr_entity_category = info.entity_category
        else:
            self._attr_name = measurement.name.rstrip(": ") or measurement.key

        if measurement.texts is not None:
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(dict.fromkeys(measurement.texts.values()))
            return

        unit = UNITS.get(measurement.unit or "")
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = (info.device_class if info else None) or DEVICE_CLASS_BY_UNIT.get(
            unit or ""
        )
        self._attr_state_class = info.state_class if info else SensorStateClass.MEASUREMENT
        self._attr_suggested_display_precision = _precision(measurement)

    @property
    def native_value(self) -> float | int | str | None:
        """Latest value."""
        value = self.coordinator.data.values.get(self._key)
        if self._attr_device_class is SensorDeviceClass.ENUM and value not in (self._attr_options or []):
            return None
        return value


class ParameterSensor(FroelingParameterEntity, SensorEntity):
    """A customer parameter shown read-only."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: FroelingCoordinator, parameter: Parameter) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, parameter)
        if parameter.is_time:
            self._attr_entity_registry_enabled_default = False
            return
        unit = UNITS.get(parameter.unit or "")
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = DEVICE_CLASS_BY_UNIT.get(unit or "")
        self._attr_suggested_display_precision = parameter.decimals

    @property
    def native_value(self) -> float | str | None:
        """Current parameter value."""
        value = self.parameter_value
        if value is None or not self._parameter.is_time:
            return value
        minutes = int(value)
        return f"{minutes // 60:02d}:{minutes % 60:02d}"


class ServiceParameterSensor(ParameterSensor):
    """A documented service parameter, always read-only."""

    def __init__(
        self,
        coordinator: FroelingCoordinator,
        parameter: Parameter,
        translation_key: str,
        enabled: bool,
        placeholders: dict[str, str],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, parameter)
        del self._attr_name  # Use the translated name instead of the raw one.
        self._attr_translation_key = translation_key
        self._attr_translation_placeholders = placeholders
        self._attr_entity_registry_enabled_default = enabled


class LastFaultSensor(FroelingEntity, SensorEntity):
    """Text of the most recent fault."""

    _attr_translation_key = "last_fault"

    def __init__(self, coordinator: FroelingCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "last_fault")

    @property
    def native_value(self) -> str | None:
        """Fault text."""
        fault = self.coordinator.data.last_fault
        return fault.text if fault else None


class LastFaultTimeSensor(FroelingEntity, SensorEntity):
    """When the most recent fault was recorded."""

    _attr_translation_key = "last_fault_time"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: FroelingCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "last_fault_time")

    @property
    def native_value(self) -> datetime | None:
        """Fault timestamp in the controller's (local) time."""
        fault = self.coordinator.data.last_fault
        if fault is None or fault.timestamp is None:
            return None
        return fault.timestamp.replace(tzinfo=dt_util.get_default_time_zone())


class ControllerTimeSensor(FroelingEntity, SensorEntity):
    """The controller's real-time clock, useful to spot drift."""

    _attr_translation_key = "controller_time"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: FroelingCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "controller_time")

    @property
    def native_value(self) -> datetime | None:
        """Controller clock."""
        return self.coordinator.data.controller_time
