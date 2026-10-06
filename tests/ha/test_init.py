"""Tests for setup, entities, writes and diagnostics."""

from __future__ import annotations

import asyncio
from datetime import datetime, time
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.froeling_s3100.const import DOMAIN
from custom_components.froeling_s3100.s3100 import S3100WriteError
from custom_components.froeling_s3100.s3100.simulator import SimulatedController

from .conftest import make_entry, setup_entry, wait_for_values


def entity_id(hass: HomeAssistant, entry: MockConfigEntry, platform: str, key: str) -> str:
    """Resolve an entity id from its unique id suffix."""
    result = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")
    assert result is not None, f"{platform} {key} not registered"
    return result


async def test_setup_and_measurements(hass: HomeAssistant, simulator: SimulatedController) -> None:
    entry = make_entry(simulator)
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    boiler = hass.states.get(entity_id(hass, entry, "sensor", "value_0"))
    assert boiler is not None
    assert float(boiler.state) == 22.5
    assert boiler.attributes["unit_of_measurement"] == "°C"
    assert boiler.attributes["device_class"] == "temperature"
    assert boiler.name == "Fröling S3100 Boiler temperature"

    status = hass.states.get(entity_id(hass, entry, "sensor", "text_2"))
    assert status is not None
    assert status.state == "Feuer-Aus"
    assert "Heizen" in status.attributes["options"]

    mode = hass.states.get(entity_id(hass, entry, "sensor", "value_28"))
    assert mode is not None
    assert mode.state == "Sommerbetrieb"

    flow = hass.states.get(entity_id(hass, entry, "sensor", "value_11"))
    assert flow is not None
    assert flow.name == "Fröling S3100 Flow temperature circuit 1"

    hours = hass.states.get(entity_id(hass, entry, "sensor", "value_18"))
    assert hours is not None
    assert hours.attributes["state_class"] == "total_increasing"

    assert hass.states.get(entity_id(hass, entry, "binary_sensor", "connected")).state == STATE_ON
    assert hass.states.get(entity_id(hass, entry, "binary_sensor", "fault")).state == STATE_OFF
    last_fault = hass.states.get(entity_id(hass, entry, "sensor", "last_fault"))
    assert last_fault.state == "Puffer zu kalt NACHLEGEN"

    # Read-only parameter from the customer menu.
    param = hass.states.get(entity_id(hass, entry, "sensor", "param_117"))
    assert param is not None
    assert float(param.state) == 50
    assert param.name == "Fröling S3100 Boiler - GewünschteBoiler temperatur"

    # Disabled by default.
    registry = er.async_get(hass)
    kty = registry.async_get(entity_id(hass, entry, "sensor", "value_41"))
    assert kty.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    heating_time = registry.async_get(entity_id(hass, entry, "sensor", "param_33"))
    assert heating_time.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    # Service parameters are never exposed.
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_param_16") is None
    # No write entities without the option.
    assert registry.async_get_entity_id("number", DOMAIN, f"{entry.entry_id}_param_117") is None

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_not_ready(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "127.0.0.1", "port": 1})
    entry.add_to_hass(hass)
    with patch("custom_components.froeling_s3100.SETUP_TIMEOUT", 0.3):
        await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_cached_catalog(hass: HomeAssistant, simulator: SimulatedController) -> None:
    entry = make_entry(simulator)
    await setup_entry(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)

    # Second start uses the cache and only waits for the login.
    with patch("custom_components.froeling_s3100.SETUP_TIMEOUT", 0.001):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    await wait_for_values(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)

    # Cache is removed together with the entry.
    await hass.config_entries.async_remove(entry.entry_id)


async def test_cached_catalog_offline(hass: HomeAssistant, simulator: SimulatedController) -> None:
    entry = make_entry(simulator)
    await setup_entry(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    simulator.ignore_logins = 1000
    with patch("custom_components.froeling_s3100.ONLINE_TIMEOUT", 0.3):
        await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_connection_loss(hass: HomeAssistant, simulator: SimulatedController) -> None:
    entry = make_entry(simulator)
    await setup_entry(hass, entry)
    boiler = entity_id(hass, entry, "sensor", "value_0")
    connected = entity_id(hass, entry, "binary_sensor", "connected")

    simulator.ignore_logins = 1000
    simulator.drop_clients()
    for _ in range(100):
        await asyncio.sleep(0.02)
        await hass.async_block_till_done()
        if hass.states.get(boiler).state == STATE_UNAVAILABLE:
            break
    assert hass.states.get(boiler).state == STATE_UNAVAILABLE
    assert hass.states.get(connected).state == STATE_OFF

    simulator.ignore_logins = 0
    await wait_for_values(hass, entry)
    for _ in range(100):
        await asyncio.sleep(0.02)
        await hass.async_block_till_done()
        if hass.states.get(boiler).state != STATE_UNAVAILABLE:
            break
    assert float(hass.states.get(boiler).state) == 22.5
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_fault_event(hass: HomeAssistant, simulator: SimulatedController) -> None:
    entry = make_entry(simulator)
    await setup_entry(hass, entry)
    event = entity_id(hass, entry, "event", "fault_event")
    assert hass.states.get(event).state == "unknown"

    simulator.inject_fault(bytes.fromhex("0dc80412300807100026"))
    for _ in range(100):
        await asyncio.sleep(0.02)
        await hass.async_block_till_done()
        if hass.states.get(event).state != "unknown":
            break
    state = hass.states.get(event)
    assert state.attributes["event_type"] == "fault"
    assert state.attributes["text"] == "Zündversuch ist nicht gelungen!"
    last_fault = hass.states.get(entity_id(hass, entry, "sensor", "last_fault"))
    assert last_fault.state == "Zündversuch ist nicht gelungen!"
    last_time = hass.states.get(entity_id(hass, entry, "sensor", "last_fault_time"))
    assert dt_util.parse_datetime(last_time.state) == datetime(
        2026, 10, 7, 8, 30, 12, tzinfo=dt_util.get_default_time_zone()
    )
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_fault_binary_sensor(hass: HomeAssistant, simulator: SimulatedController) -> None:
    simulator.m1 = b"\x00\x00" + simulator.m1[2:]  # status text id 0 = "Störung !!!"
    entry = make_entry(simulator)
    await setup_entry(hass, entry)
    assert hass.states.get(entity_id(hass, entry, "binary_sensor", "fault")).state == STATE_ON
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_writes(
    hass: HomeAssistant, entity_registry_enabled_by_default: None, simulator: SimulatedController
) -> None:
    entry = make_entry(simulator, writes=True)
    await setup_entry(hass, entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"writes_enabled_{entry.entry_id}")

    number = entity_id(hass, entry, "number", "param_117")
    state = hass.states.get(number)
    assert float(state.state) == 50
    assert state.attributes["min"] == 20
    assert state.attributes["max"] == 100
    await hass.services.async_call(
        "number", "set_value", {ATTR_ENTITY_ID: number, "value": 55}, blocking=True
    )
    assert simulator.parameters[117] == 110
    assert float(hass.states.get(number).state) == 55

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number", "set_value", {ATTR_ENTITY_ID: number, "value": 101}, blocking=True
        )

    switch = entity_id(hass, entry, "switch", "param_166")
    await hass.services.async_call("switch", "turn_on", {ATTR_ENTITY_ID: switch}, blocking=True)
    assert simulator.parameters[166] == 1
    await hass.services.async_call("switch", "turn_off", {ATTR_ENTITY_ID: switch}, blocking=True)
    assert simulator.parameters[166] == 0

    clock = entity_id(hass, entry, "time", "param_33")
    assert hass.states.get(clock).state == "05:00:00"
    await hass.services.async_call(
        "time", "set_value", {ATTR_ENTITY_ID: clock, "time": time(5, 30)}, blocking=True
    )
    assert simulator.parameters[33] == 330

    # Constraint from the controller: buffer min temp >= heating release (30 °C).
    buffer_min = entity_id(hass, entry, "number", "param_174")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number", "set_value", {ATTR_ENTITY_ID: buffer_min, "value": 25}, blocking=True
        )

    simulator.reject_writes = True
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "number", "set_value", {ATTR_ENTITY_ID: number, "value": 52}, blocking=True
        )

    # The read-only sensors moved to the writable platforms.
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_param_117") is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_write_errors_translated(hass: HomeAssistant, simulator: SimulatedController) -> None:
    entry = make_entry(simulator, writes=True)
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data
    with (
        patch.object(coordinator.client, "write_parameter", side_effect=S3100WriteError("x")),
        pytest.raises(HomeAssistantError) as err,
    ):
        await coordinator.async_write_parameter(117, 50)
    assert err.value.translation_key == "write_failed"

    coordinator.writes_enabled = False
    with pytest.raises(ServiceValidationError) as err2:
        await coordinator.async_write_parameter(117, 50)
    assert err2.value.translation_key == "writes_disabled"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_writes_disabled_removes_issue_and_entities(
    hass: HomeAssistant, simulator: SimulatedController
) -> None:
    entry = make_entry(simulator, writes=True)
    await setup_entry(hass, entry)
    hass.config_entries.async_update_entry(entry, options={**entry.options, "enable_writes": False})
    await hass.async_block_till_done()
    await wait_for_values(hass, entry)
    assert not ir.async_get(hass).async_get_issue(DOMAIN, f"writes_enabled_{entry.entry_id}")
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("number", DOMAIN, f"{entry.entry_id}_param_117") is None
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_param_117")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_catalog_change_triggers_reload(hass: HomeAssistant, simulator: SimulatedController) -> None:
    entry = make_entry(simulator)
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        coordinator._catalog_signature = (("other",), ())
        coordinator._handle_catalog(coordinator.catalog)
    reload.assert_called_once_with(entry.entry_id)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_diagnostics(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, simulator: SimulatedController
) -> None:
    entry = make_entry(simulator)
    await setup_entry(hass, entry)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diag["entry"]["data"]["host"] == "**REDACTED**"
    assert diag["connection"]["state"] == "ready"
    assert diag["connection"]["statistics"]["frames_rx"]["MA"] == 35
    assert len(diag["catalog"]["measurements"]) == 35
    assert len(diag["catalog"]["parameters"]) == 228
    assert diag["catalog"]["error_history"][0]["text"] == "Puffer zu kalt NACHLEGEN"
    assert "192.168" not in str(diag)
    assert await hass.config_entries.async_unload(entry.entry_id)
