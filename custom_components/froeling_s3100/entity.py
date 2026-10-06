"""Base entity for the Fröling S3100."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from froeling_s3100 import Parameter, ParameterKind

from .const import DEFAULT_NAME, DOMAIN, MANUFACTURER, MODEL
from .coordinator import FroelingCoordinator


class FroelingEntity(CoordinatorEntity[FroelingCoordinator]):
    """Common behaviour of all S3100 entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FroelingCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=DEFAULT_NAME,
        )

    @property
    def available(self) -> bool:
        """Entities are available while the session delivers values."""
        return super().available and self.coordinator.data.online


class FroelingParameterEntity(FroelingEntity):
    """Base for entities representing a controller parameter."""

    def __init__(self, coordinator: FroelingCoordinator, parameter: Parameter) -> None:
        """Initialize the entity."""
        super().__init__(coordinator, f"param_{parameter.id}")
        self._parameter = parameter
        self._attr_name = parameter_name(parameter)

    @property
    def parameter_value(self) -> float | None:
        """Current value in physical units."""
        raw = self.coordinator.data.parameters.get(self._parameter.id)
        return None if raw is None else self._parameter.to_value(raw)


def parameter_name(parameter: Parameter) -> str:
    """Readable name built from the controller menu."""
    return " - ".join((*parameter.menu_path, parameter.display_name))


def writable_platform(coordinator: FroelingCoordinator, parameter: Parameter) -> str:
    """Platform that represents a customer parameter."""
    if coordinator.writes_enabled:
        if parameter.kind is ParameterKind.NUMBER:
            return Platform.NUMBER
        if parameter.kind is ParameterKind.TIME:
            return Platform.TIME
        if parameter.kind is ParameterKind.BOOLEAN:
            return Platform.SWITCH
    if parameter.kind is ParameterKind.BOOLEAN:
        return Platform.BINARY_SENSOR
    return Platform.SENSOR


def customer_parameters(coordinator: FroelingCoordinator, platform: str) -> list[Parameter]:
    """Customer-menu parameters handled by ``platform``."""
    return [
        p
        for p in coordinator.catalog.parameters.values()
        if p.in_customer_menu and writable_platform(coordinator, p) == platform
    ]


@callback
def remove_moved_entities(hass: HomeAssistant, coordinator: FroelingCoordinator, platform: str) -> None:
    """Drop registry entries of parameters now represented by another platform."""
    registry = er.async_get(hass)
    entry_id = coordinator.config_entry.entry_id
    owner = {
        f"{entry_id}_param_{p.id}": writable_platform(coordinator, p)
        for p in coordinator.catalog.parameters.values()
        if p.in_customer_menu
    }
    for entity in er.async_entries_for_config_entry(registry, entry_id):
        target = owner.get(entity.unique_id)
        if target is not None and entity.domain == platform and target != platform:
            registry.async_remove(entity.entity_id)
