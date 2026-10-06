"""The Fröling Lambdatronic S3100 integration."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_HOST, CONF_PORT, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store

from .const import CONF_ENABLE_WRITES, DOMAIN, ONLINE_TIMEOUT, SETUP_TIMEOUT, STORAGE_VERSION
from .coordinator import FroelingConfigEntry, FroelingCoordinator
from .s3100 import Catalog, S3100Client, S3100TimeoutError, build_catalog

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]


def _store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, list[list[str]]]]:
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")


def _load_cached_catalog(data: dict[str, list[list[str]]] | None) -> Catalog | None:
    if not data or not data.get("frames"):
        return None
    try:
        catalog = build_catalog([(cmd, bytes.fromhex(payload)) for cmd, payload in data["frames"]])
    except (ValueError, TypeError):
        _LOGGER.warning("Ignoring invalid cached S3100 configuration")
        return None
    return catalog if catalog.measurements else None


async def async_setup_entry(hass: HomeAssistant, entry: FroelingConfigEntry) -> bool:
    """Set up the S3100 from a config entry."""
    store = _store(hass, entry.entry_id)
    cached = _load_cached_catalog(await store.async_load())
    client = S3100Client(entry.data[CONF_HOST], entry.data[CONF_PORT], catalog=cached)
    await client.start()
    try:
        if cached is None:
            # First start: entities are built from the catalog, so wait for it.
            await client.wait_ready(SETUP_TIMEOUT)
        else:
            await client.wait_online(ONLINE_TIMEOUT)
    except S3100TimeoutError as err:
        await client.stop()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

    coordinator = FroelingCoordinator(hass, entry, client, store)
    if cached is None and client.catalog is not None:
        await coordinator.async_save_catalog(client.catalog)
    entry.runtime_data = coordinator
    coordinator.async_start()
    entry.async_on_unload(client.stop)

    async def _async_stop(_event: Event) -> None:
        await client.stop()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop))

    _update_writes_issue(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: FroelingConfigEntry) -> None:
    """Reload when the options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: FroelingConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: FroelingConfigEntry) -> None:
    """Clean up storage and issues when the entry is deleted."""
    await _store(hass, entry.entry_id).async_remove()
    ir.async_delete_issue(hass, DOMAIN, f"writes_enabled_{entry.entry_id}")


def _update_writes_issue(hass: HomeAssistant, entry: FroelingConfigEntry) -> None:
    issue_id = f"writes_enabled_{entry.entry_id}"
    if entry.options.get(CONF_ENABLE_WRITES, False):
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="writes_enabled",
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
