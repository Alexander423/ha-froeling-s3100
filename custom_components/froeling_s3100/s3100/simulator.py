"""A simulated S3100 for tests and development.

It replays a recorded configuration and answers like the real controller:
ACKs the login, sends the configuration frame by frame (waiting for each
ACK), then M2 every ``interval`` seconds and, after ``Rb``, M1 as well.
``RI`` writes are acknowledged and confirmed with ``MI``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .protocol import ACK, FrameParser, encode_frame


def _to_bcd(value: int) -> int:
    return ((value // 10) << 4) | (value % 10)


def encode_time(moment: datetime) -> bytes:
    """Encode an M2 payload."""
    return bytes(
        [
            _to_bcd(moment.second),
            _to_bcd(moment.minute),
            _to_bcd(moment.hour),
            _to_bcd(moment.day),
            _to_bcd(moment.month),
            moment.isoweekday(),
            _to_bcd(moment.year % 100),
        ]
    )


@dataclass
class Recording:
    """Configuration frames and one M1 payload taken from a capture."""

    config: list[tuple[str, bytes]] = field(default_factory=list)
    m1: bytes = b""

    @classmethod
    def from_jsonl(cls, path: str | Path) -> Recording:
        """Load a capture produced by ``tools/s3100_capture.py``."""
        recording = cls()
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if entry.get("dir") != "rx" or "payload" not in entry or entry.get("note") == "ack":
                continue
            payload = bytes.fromhex(entry["payload"])
            if entry["cmd"] == "M1":
                recording.m1 = payload
            elif entry["cmd"] not in ("M2", "M3", "MI") and entry["cmd"].startswith("M"):
                recording.config.append((entry["cmd"], payload))
        return recording


class SimulatedController:
    """TCP server speaking the S3100 protocol."""

    def __init__(self, recording: Recording, *, interval: float = 1.0, ack_timeout: float = 1.0) -> None:
        self.recording = recording
        self.interval = interval
        self.ack_timeout = ack_timeout
        self.parameters: dict[int, int] = {}
        self.received: list[tuple[str, bytes]] = []
        self.ignore_logins = 0
        self.reject_writes = False
        self.m1 = recording.m1
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._fault_queue: asyncio.Queue[bytes] = asyncio.Queue()

    @property
    def port(self) -> int:
        """Port the server listens on."""
        assert self._server is not None
        port: int = self._server.sockets[0].getsockname()[1]
        return port

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> None:
        """Start listening."""
        self._server = await asyncio.start_server(self._handle, host, port)

    async def stop(self) -> None:
        """Stop the server and drop all clients."""
        for writer in list(self._writers):
            writer.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def drop_clients(self) -> None:
        """Close all client connections (simulates a cable fault)."""
        for writer in list(self._writers):
            writer.close()

    async def foreign_login(self, config: list[tuple[str, bytes]]) -> None:
        """Simulate another bridge client logging in (e.g. with service level).

        The controller's answer (Ra ACK and a configuration dump) reaches
        every connected client.
        """
        frames = [("Ra", ACK), *config]
        for writer in list(self._writers):
            for command, payload in frames:
                writer.write(encode_frame(command.encode("ascii"), payload))
            await writer.drain()

    def inject_fault(self, payload: bytes) -> None:
        """Send an M3 fault record to the client."""
        self._fault_queue.put_nowait(payload)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        parser = FrameParser()
        acks: asyncio.Queue[str] = asyncio.Queue()
        started = asyncio.Event()
        session: asyncio.Task[None] | None = None

        async def send(command: str, payload: bytes) -> None:
            writer.write(encode_frame(command.encode("ascii"), payload))
            await writer.drain()

        async def run_session() -> None:
            await send("Ra", ACK)
            for command, payload in self.recording.config:
                await send(command, payload)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(acks.get(), self.ack_timeout)
            while True:
                await send("M2", encode_time(datetime.now()))
                if started.is_set():
                    await send("M1", self.m1)
                while not self._fault_queue.empty():
                    await send("M3", self._fault_queue.get_nowait())
                await asyncio.sleep(self.interval)

        try:
            while data := await reader.read(1024):
                for frame in parser.feed(data):
                    self.received.append((frame.command, frame.payload))
                    if frame.command.startswith("M"):
                        acks.put_nowait(frame.command)
                    elif frame.command == "Ra":
                        if self.ignore_logins > 0:
                            self.ignore_logins -= 1
                            continue
                        if session is None:
                            session = asyncio.create_task(run_session())
                    elif frame.command == "Rb":
                        await send("Rb", ACK)
                        started.set()
                    elif frame.command == "RI":
                        if self.reject_writes:
                            await send("RI", b"\x00")
                            continue
                        await send("RI", ACK)
                        param = int.from_bytes(frame.payload[:2], "big")
                        self.parameters[param] = int.from_bytes(frame.payload[2:4], "big", signed=True)
                        await send("MI", frame.payload[:4])
        except (ConnectionError, OSError):
            pass
        finally:
            if session is not None:
                session.cancel()
                with contextlib.suppress(asyncio.CancelledError, ConnectionError, OSError):
                    await session
            self._writers.discard(writer)
            writer.close()


async def _main() -> None:  # pragma: no cover - manual development helper
    parser = argparse.ArgumentParser(description="Run a simulated S3100")
    parser.add_argument("capture", type=Path)
    parser.add_argument("--port", type=int, default=4196)
    args = parser.parse_args()
    sim = SimulatedController(Recording.from_jsonl(args.capture))
    await sim.start("0.0.0.0", args.port)
    print(f"Simulated S3100 listening on port {sim.port}")
    await asyncio.Event().wait()


def main() -> None:  # pragma: no cover
    """Console entry point."""
    asyncio.run(_main())


if __name__ == "__main__":  # pragma: no cover
    main()
