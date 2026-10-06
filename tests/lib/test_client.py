"""End-to-end tests of the client against the simulator."""

from __future__ import annotations

import asyncio

import pytest

from froeling_s3100 import (
    ConnectionState,
    EventType,
    S3100Client,
    S3100ConnectionError,
    S3100TimeoutError,
    S3100WriteError,
    S3100WriteNotAllowedError,
    async_probe,
    build_catalog,
)
from froeling_s3100.simulator import SimulatedController


def make_client(sim: SimulatedController) -> S3100Client:
    return S3100Client(
        "127.0.0.1",
        sim.port,
        idle_timeout=1.0,
        login_retry_interval=0.2,
        backoff_min=0.05,
        backoff_max=0.2,
        write_min_interval=0,
        write_timeout=2,
    )


async def test_session_and_values(simulator: SimulatedController) -> None:
    client = make_client(simulator)
    events: list[EventType] = []
    client.subscribe(lambda event: events.append(event.type))
    await client.start()
    try:
        catalog = await client.wait_ready(10)
        assert len(catalog.measurements) == 35
        for _ in range(100):
            if client.values:
                break
            await asyncio.sleep(0.05)
        assert client.values["value_0"] == 22.5
        assert client.controller_time is not None
        assert client.stats.frames_rx["MA"] == 35
        assert client.stats.frames_tx["Ra"] == 1
        assert client.stats.frames_tx["Rb"] == 1
        assert client.stats.checksum_errors == 0
        assert EventType.CATALOG in events
        assert EventType.VALUES in events
        # Every configuration frame was acknowledged.
        acks = [c for c, p in simulator.received if c.startswith("M") and p == b"\x01"]
        assert len(acks) >= len(simulator.recording.config)
    finally:
        await client.stop()
    assert client.state is ConnectionState.STOPPED


async def test_login_retry(simulator: SimulatedController) -> None:
    simulator.ignore_logins = 2
    client = make_client(simulator)
    await client.start()
    try:
        await client.wait_ready(10)
        assert client.stats.login_attempts == 3
    finally:
        await client.stop()


async def test_reconnect_after_drop(simulator: SimulatedController) -> None:
    client = make_client(simulator)
    await client.start()
    try:
        await client.wait_ready(10)
        simulator.drop_clients()
        await asyncio.sleep(0.2)
        await client.wait_ready(10)
        assert client.stats.reconnects >= 1
        assert client.stats.sessions_ready == 2
    finally:
        await client.stop()


async def test_unreachable_host_backs_off() -> None:
    client = S3100Client("127.0.0.1", 1, connect_timeout=0.2, backoff_min=0.05, backoff_max=0.1)
    await client.start()
    try:
        await asyncio.sleep(0.5)
        assert client.stats.connect_failures >= 2
        assert client.state in (ConnectionState.DISCONNECTED, ConnectionState.CONNECTING)
        assert "cannot connect" in (client.stats.last_error or "")
    finally:
        await client.stop()


async def test_fault_event(simulator: SimulatedController) -> None:
    client = make_client(simulator)
    faults = []
    client.subscribe(lambda e: faults.append(e.data) if e.type is EventType.FAULT else None)
    await client.start()
    try:
        await client.wait_ready(10)
        simulator.inject_fault(bytes.fromhex("21c80454532203040026"))
        for _ in range(50):
            if faults:
                break
            await asyncio.sleep(0.05)
        assert faults[0].text == "Puffer zu kalt NACHLEGEN"
    finally:
        await client.stop()


async def test_write_parameter(simulator: SimulatedController) -> None:
    client = make_client(simulator)
    await client.start()
    try:
        await client.wait_ready(10)
        param = await client.write_parameter(0x75, 55)
        assert simulator.parameters[0x75] == 110
        assert param.value == 55
    finally:
        await client.stop()


async def test_write_guards(simulator: SimulatedController) -> None:
    client = make_client(simulator)
    await client.start()
    try:
        await client.wait_ready(10)
        with pytest.raises(S3100WriteNotAllowedError, match="customer menu"):
            await client.write_parameter(0x10, 600)  # service parameter
        with pytest.raises(S3100WriteNotAllowedError, match="outside"):
            await client.write_parameter(0x75, 101)
        with pytest.raises(S3100WriteNotAllowedError, match="unsupported"):
            await client.write_parameter(0x9C, 1)  # selection with unknown meaning
        with pytest.raises(S3100WriteNotAllowedError, match="below"):
            await client.write_parameter(0xAE, 25)  # must stay >= parameter 0x73 (30)
        simulator.reject_writes = True
        with pytest.raises(S3100WriteError, match="rejected"):
            await client.write_parameter(0x75, 52)
        assert simulator.parameters == {}
    finally:
        await client.stop()


async def test_probe(simulator: SimulatedController) -> None:
    await async_probe("127.0.0.1", simulator.port, timeout=5)


async def test_probe_failures(simulator: SimulatedController) -> None:
    with pytest.raises(S3100ConnectionError):
        await async_probe("127.0.0.1", 1, timeout=1)
    simulator.ignore_logins = 10
    with pytest.raises(S3100TimeoutError):
        await async_probe("127.0.0.1", simulator.port, timeout=0.5)


async def test_cached_catalog_roundtrip(simulator: SimulatedController) -> None:
    client = make_client(simulator)
    await client.start()
    try:
        catalog = await client.wait_ready(10)
    finally:
        await client.stop()
    rebuilt = build_catalog(catalog.frames)
    assert [m.key for m in rebuilt.measurements] == [m.key for m in catalog.measurements]
    cached = S3100Client("127.0.0.1", simulator.port, catalog=rebuilt)
    assert cached.catalog is rebuilt


async def test_wait_online(simulator: SimulatedController) -> None:
    client = make_client(simulator)
    await client.start()
    try:
        await client.wait_online(5)
        assert client.state in (ConnectionState.LOADING, ConnectionState.READY)
    finally:
        await client.stop()
    with pytest.raises(S3100TimeoutError):
        await client.wait_online(0.01)
