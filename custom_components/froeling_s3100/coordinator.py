"""Push coordinator for the Fröling S3100.

The controller pushes all values once per second. The coordinator keeps the
latest state and forwards it to Home Assistant at most once per configured
interval, so the recorder is not flooded. Faults, parameter changes and
connection changes are forwarded immediately.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ENABLE_WRITES,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from .s3100 import (
    Catalog,
    ConnectionState,
    Event,
    EventType,
    FaultEvent,
    S3100Client,
    S3100Error,
    S3100NotReadyError,
    S3100WriteNotAllowedError,
)

_LOGGER = logging.getLogger(__name__)

type FroelingConfigEntry = ConfigEntry[FroelingCoordinator]


@dataclass(slots=True)
class FroelingData:
    """Snapshot handed to the entities."""

    values: dict[str, float | int | str] = field(default_factory=dict)
    controller_time: datetime | None = None
    parameters: dict[int, int] = field(default_factory=dict)
    last_fault: FaultEvent | None = None
    online: bool = False


def fault_signal(entry_id: str) -> str:
    """Dispatcher signal used for live fault events."""
    return f"{DOMAIN}_{entry_id}_fault"


class FroelingCoordinator(DataUpdateCoordinator[FroelingData]):
    """Bridges the push-based client to Home Assistant."""

    config_entry: FroelingConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: FroelingConfigEntry,
        client: S3100Client,
        store: Store[dict[str, list[list[str]]]],
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=None)
        self.client = client
        self._store = store
        self.publish_interval: float = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        self.writes_enabled: bool = entry.options.get(CONF_ENABLE_WRITES, False)
        self._last_publish = 0.0
        self._scheduled: CALLBACK_TYPE | None = None
        self._was_online: bool | None = None
        self._catalog_signature = self.signature(client.catalog)
        self.data = self._snapshot()

    @property
    def catalog(self) -> Catalog:
        """The catalog the entities were created from."""
        assert self.client.catalog is not None
        return self.client.catalog

    @staticmethod
    def signature(catalog: Catalog | None) -> tuple[tuple[str, ...], tuple[int, ...]]:
        """Entity-relevant shape of a catalog."""
        if catalog is None:
            return ((), ())
        return (
            tuple(m.key for m in catalog.measurements),
            tuple(sorted(p.id for p in catalog.parameters.values() if p.in_customer_menu)),
        )

    @callback
    def async_start(self) -> None:
        """Subscribe to client events."""
        self.config_entry.async_on_unload(self.client.subscribe(self._handle_event))
        self.config_entry.async_on_unload(self._cancel_scheduled)

    async def async_save_catalog(self, catalog: Catalog) -> None:
        """Persist the configuration frames for a fast start."""
        await self._store.async_save({"frames": [[cmd, payload.hex()] for cmd, payload in catalog.frames]})

    @callback
    def _cancel_scheduled(self) -> None:
        if self._scheduled is not None:
            self._scheduled()
            self._scheduled = None

    def _snapshot(self) -> FroelingData:
        catalog = self.client.catalog
        last_fault: FaultEvent | None = None
        if self.client.live_faults:
            last_fault = self.client.live_faults[-1]
        elif catalog is not None and catalog.error_history:
            last_fault = catalog.error_history[-1]
        controller_time = self.client.controller_time
        return FroelingData(
            values=dict(self.client.values),
            controller_time=(
                controller_time.replace(tzinfo=dt_util.get_default_time_zone()) if controller_time else None
            ),
            parameters=(
                {pid: p.raw_value for pid, p in catalog.parameters.items()} if catalog is not None else {}
            ),
            last_fault=last_fault,
            online=self.client.is_ready,
        )

    async def _async_update_data(self) -> FroelingData:
        """Return the latest pushed state (used by manual refreshes)."""
        return self._snapshot()

    @callback
    def _publish(self, _now: datetime | None = None) -> None:
        self._scheduled = None
        self._last_publish = time.monotonic()
        self.async_set_updated_data(self._snapshot())

    @callback
    def _publish_throttled(self) -> None:
        if self._scheduled is not None:
            return
        wait = self._last_publish + self.publish_interval - time.monotonic()
        if wait <= 0:
            self._publish()
        else:
            self._scheduled = async_call_later(self.hass, wait, self._publish)

    @callback
    def _handle_event(self, event: Event) -> None:
        if event.type is EventType.VALUES:
            self._publish_throttled()
        elif event.type is EventType.STATE:
            self._handle_state(event.data)
        elif event.type is EventType.CATALOG:
            self._handle_catalog(event.data)
        elif event.type is EventType.FAULT:
            async_dispatcher_send(self.hass, fault_signal(self.config_entry.entry_id), event.data)
            self._cancel_scheduled()
            self._publish()
        elif event.type is EventType.PARAMETER:
            self._cancel_scheduled()
            self._publish()

    @callback
    def _handle_state(self, state: ConnectionState) -> None:
        online = state is ConnectionState.READY
        if online != self._was_online:
            if online:
                if self._was_online is False:
                    _LOGGER.info("Connection to the Fröling S3100 restored")
            elif self._was_online:
                _LOGGER.warning(
                    "Connection to the Fröling S3100 lost (%s), reconnecting",
                    self.client.stats.last_error or state,
                )
            self._was_online = online
            self._cancel_scheduled()
            self._publish()

    @callback
    def _handle_catalog(self, catalog: Catalog) -> None:
        self.config_entry.async_create_background_task(
            self.hass, self.async_save_catalog(catalog), "froeling_s3100_save_catalog"
        )
        signature = self.signature(catalog)
        if signature != self._catalog_signature:
            _LOGGER.info("The S3100 announced a different configuration, reloading entities")
            self._catalog_signature = signature
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

    async def async_write_parameter(self, param_id: int, value: float) -> None:
        """Write a parameter, translating library errors for the UI."""
        if not self.writes_enabled:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="writes_disabled")
        try:
            await self.client.write_parameter(param_id, value)
        except S3100WriteNotAllowedError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="write_not_allowed",
                translation_placeholders={"error": str(err)},
            ) from err
        except S3100NotReadyError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="not_connected") from err
        except S3100Error as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        self._cancel_scheduled()
        self._publish()
