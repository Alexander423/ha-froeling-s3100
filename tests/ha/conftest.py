"""Fixtures for the Home Assistant integration tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.froeling_s3100.const import (
    CONF_ENABLE_WRITES,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)
from custom_components.froeling_s3100.s3100 import S3100Client
from custom_components.froeling_s3100.s3100.simulator import Recording, SimulatedController

CAPTURE = Path(__file__).parent.parent / "fixtures" / "s3100_capture.jsonl"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components in every test."""


@pytest.fixture(autouse=True)
def fast_client() -> Generator[None]:
    """Make reconnects fast so tests do not wait for real backoff times."""
    with patch.dict(
        S3100Client.__init__.__kwdefaults__,
        {"backoff_min": 0.05, "backoff_max": 0.1, "login_retry_interval": 0.3, "idle_timeout": 2.0},
    ):
        yield


@pytest.fixture
async def simulator() -> AsyncIterator[SimulatedController]:
    """A simulated S3100 replaying a real capture."""
    sim = SimulatedController(Recording.from_jsonl(CAPTURE), interval=0.05)
    await sim.start()
    yield sim
    await sim.stop()


def make_entry(sim: SimulatedController, *, writes: bool = False) -> MockConfigEntry:
    """Config entry pointing at the simulator."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Fröling S3100",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: sim.port},
        options={CONF_UPDATE_INTERVAL: 1, CONF_ENABLE_WRITES: writes},
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up an entry, waiting until values arrived."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await wait_for_values(hass, entry)


async def wait_for_values(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Wait until the coordinator published values."""
    coordinator = entry.runtime_data
    for _ in range(200):
        if coordinator.data.online and coordinator.data.values:
            return
        await asyncio.sleep(0.05)
        await hass.async_block_till_done()
    raise AssertionError("no values published")
