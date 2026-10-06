"""Tests for the config and options flow."""

from __future__ import annotations

from contextlib import nullcontext
from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.froeling_s3100.const import (
    CONF_ENABLE_WRITES,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)
from custom_components.froeling_s3100.s3100.simulator import SimulatedController

from .conftest import make_entry, setup_entry


@pytest.fixture(autouse=True)
def short_probe() -> None:
    """Fail probes quickly."""
    with patch("custom_components.froeling_s3100.config_flow.PROBE_TIMEOUT", 0.5):
        yield


async def test_user_flow(hass: HomeAssistant, simulator: SimulatedController) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.froeling_s3100.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: " 127.0.0.1 ", CONF_PORT: simulator.port}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Fröling S3100"
    assert result["data"] == {CONF_HOST: "127.0.0.1", CONF_PORT: simulator.port}


@pytest.mark.parametrize(
    ("setup", "error"),
    [
        ("closed", "cannot_connect"),
        ("silent", "no_response"),
        ("crash", "unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, simulator: SimulatedController, setup: str, error: str
) -> None:
    port = 1 if setup == "closed" else simulator.port
    simulator.ignore_logins = 100 if setup == "silent" else 0
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with (
        patch(
            "custom_components.froeling_s3100.config_flow.async_probe",
            side_effect=RuntimeError("boom"),
        )
        if setup == "crash"
        else nullcontext()
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: port}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    # The user can recover from the error.
    simulator.ignore_logins = 0
    with patch("custom_components.froeling_s3100.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: simulator.port}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_already_configured(hass: HomeAssistant, simulator: SimulatedController) -> None:
    make_entry(simulator).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: simulator.port}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure(hass: HomeAssistant, simulator: SimulatedController) -> None:
    entry = make_entry(simulator)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, data={CONF_HOST: "127.0.0.1", CONF_PORT: 2})
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: 1}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    with patch("custom_components.froeling_s3100.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: simulator.port}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_PORT] == simulator.port


async def test_reconfigure_duplicate(hass: HomeAssistant, simulator: SimulatedController) -> None:
    entry = make_entry(simulator)
    entry.add_to_hass(hass)
    other = make_entry(simulator)
    other.add_to_hass(hass)
    hass.config_entries.async_update_entry(other, data={CONF_HOST: "10.0.0.2", CONF_PORT: 4196})
    result = await other.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: simulator.port}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(hass: HomeAssistant, simulator: SimulatedController) -> None:
    entry = make_entry(simulator)
    await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UPDATE_INTERVAL: 30, CONF_ENABLE_WRITES: True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {CONF_UPDATE_INTERVAL: 30, CONF_ENABLE_WRITES: True}
    assert entry.runtime_data.writes_enabled
    assert await hass.config_entries.async_unload(entry.entry_id)
