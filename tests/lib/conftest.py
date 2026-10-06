"""Shared fixtures for library tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from froeling_s3100.simulator import Recording, SimulatedController

CAPTURE = Path(__file__).parent.parent / "fixtures" / "s3100_capture.jsonl"


@pytest.fixture
def recording() -> Recording:
    """The configuration recorded from a real FHG Turbo 3000 / S3100."""
    return Recording.from_jsonl(CAPTURE)


@pytest.fixture
async def simulator(recording: Recording) -> AsyncIterator[SimulatedController]:
    """A running simulated controller with a fast clock."""
    sim = SimulatedController(recording, interval=0.05)
    await sim.start()
    yield sim
    await sim.stop()
