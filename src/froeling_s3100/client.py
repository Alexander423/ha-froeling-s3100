"""Asynchronous client for the Fröling Lambdatronic S3100.

The controller pushes everything by itself once a session is established:

1. Host sends ``Ra`` (login). The controller ACKs and transmits its whole
   configuration (blocks MA ... MV, each terminated by ``MZ``).
2. The first ``M2`` (clock, sent every second) marks the end of the
   configuration. The host answers with ``Rb``, after which the controller
   sends ``M1`` (all measurements) every second.
3. Every frame must be acknowledged. Faults arrive as ``M3``, parameter
   changes as ``MI``.

The client never polls. It only sends ACKs, the login sequence and, when
explicitly requested, a validated parameter write.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from .catalog import (
    CONFIG_COMMANDS,
    CatalogBuilder,
    DecodeError,
    parse_controller_time,
    parse_fault,
    parse_parameter_change,
    parse_values,
)
from .exceptions import (
    S3100ConnectionError,
    S3100Error,
    S3100NotReadyError,
    S3100ProtocolError,
    S3100TimeoutError,
    S3100WriteError,
    S3100WriteNotAllowedError,
)
from .models import Catalog, FaultEvent, Parameter, ParameterKind
from .protocol import (
    ACK,
    CMD_LOGIN,
    CMD_START,
    CMD_WRITE,
    LOGIN_CUSTOMER,
    LOGIN_SERVICE,
    START_PAYLOAD,
    Frame,
    FrameParser,
    encode_frame,
)
from .stats import Statistics

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 4196
READ_SIZE = 1024
MAX_M1_ERRORS = 3
WRITABLE_KINDS = frozenset(
    {ParameterKind.NUMBER, ParameterKind.TIME, ParameterKind.BOOLEAN, ParameterKind.WEEKDAY}
)


class LoginLevel(StrEnum):
    """User level sent with the login command."""

    CUSTOMER = "customer"
    SERVICE = "service"


class ConnectionState(StrEnum):
    """State of the client."""

    STOPPED = "stopped"
    CONNECTING = "connecting"
    LOGGING_IN = "logging_in"
    LOADING = "loading"
    READY = "ready"
    DISCONNECTED = "disconnected"


class EventType(StrEnum):
    """Kinds of events delivered to listeners."""

    STATE = "state"
    CATALOG = "catalog"
    VALUES = "values"
    TIME = "time"
    FAULT = "fault"
    PARAMETER = "parameter"
    UNKNOWN_FRAME = "unknown_frame"


@dataclass(frozen=True, slots=True)
class Event:
    """Something happened on the link."""

    type: EventType
    data: Any = None


@dataclass(frozen=True, slots=True)
class UnknownFrame:
    """A frame the client could not interpret, kept for reverse engineering."""

    time: float
    command: str
    payload: bytes
    reason: str


Listener = Callable[[Event], None]


class S3100Client:
    """Maintains a session with an S3100 behind a transparent TCP bridge."""

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        *,
        login_level: LoginLevel = LoginLevel.CUSTOMER,
        connect_timeout: float = 10.0,
        idle_timeout: float = 15.0,
        login_retry_interval: float = 5.0,
        max_login_attempts: int = 12,
        backoff_min: float = 2.0,
        backoff_max: float = 120.0,
        write_min_interval: float = 2.0,
        write_timeout: float = 10.0,
        catalog: Catalog | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.login_level = login_level
        self._connect_timeout = connect_timeout
        self._idle_timeout = idle_timeout
        self._login_retry_interval = login_retry_interval
        self._max_login_attempts = max_login_attempts
        self._backoff_min = backoff_min
        self._backoff_max = backoff_max
        self._write_min_interval = write_min_interval
        self._write_timeout = write_timeout

        self.stats = Statistics()
        self.catalog: Catalog | None = catalog
        self.raw_values: list[int] = []
        self.values: dict[str, float | int | str] = {}
        self.values_time: float | None = None
        self.controller_time: datetime | None = None
        self.live_faults: deque[FaultEvent] = deque(maxlen=50)
        self.unknown_frames: deque[UnknownFrame] = deque(maxlen=200)
        self.decode_errors: deque[str] = deque(maxlen=50)

        self._state = ConnectionState.STOPPED
        self._listeners: list[Listener] = []
        self._task: asyncio.Task[None] | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._parser = FrameParser()
        self._builder: CatalogBuilder | None = None
        self._loading_started = 0.0
        self._pending: dict[str, tuple[asyncio.Future[bool], float]] = {}
        self._pending_change: tuple[int, asyncio.Future[int]] | None = None
        self._write_lock = asyncio.Lock()
        self._last_write = 0.0
        self._last_m1 = 0.0
        self._m1_errors = 0
        self._ready_event = asyncio.Event()
        self._online_event = asyncio.Event()
        self._stopping = False
        self._ready_since: float | None = None

    # ------------------------------------------------------------------ API

    @property
    def state(self) -> ConnectionState:
        """Current connection state."""
        return self._state

    @property
    def is_ready(self) -> bool:
        """Return True when values are flowing."""
        return self._state is ConnectionState.READY

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Register a listener; returns a function that removes it."""
        self._listeners.append(listener)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return remove

    async def start(self) -> None:
        """Start the background session task."""
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name=f"s3100-{self.host}")

    async def stop(self) -> None:
        """Stop the session and close the connection."""
        self._stopping = True
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._close()
        self._set_state(ConnectionState.STOPPED)

    async def wait_ready(self, timeout: float) -> Catalog:
        """Wait until the catalog is loaded and values are flowing."""
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout)
        except TimeoutError as err:
            raise S3100TimeoutError(
                self.stats.last_error or f"controller not ready after {timeout:.0f} s"
            ) from err
        assert self.catalog is not None
        return self.catalog

    async def wait_online(self, timeout: float) -> None:
        """Wait until the controller acknowledged the login."""
        try:
            await asyncio.wait_for(self._online_event.wait(), timeout)
        except TimeoutError as err:
            raise S3100TimeoutError(
                self.stats.last_error or f"controller did not answer within {timeout:.0f} s"
            ) from err

    async def write_parameter(self, param_id: int, value: float) -> Parameter:
        """Change a parameter and wait for the controller to confirm it.

        Only parameters from the customer menu are accepted, and the value
        must respect the limits announced by the controller. The write is
        confirmed by the controller's MI notification.
        """
        param = self._writable_parameter(param_id)
        raw = self.validate_write(param, value)
        async with self._write_lock:
            wait = self._last_write + self._write_min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            loop = asyncio.get_running_loop()
            change: asyncio.Future[int] = loop.create_future()
            self._pending_change = (param_id, change)
            try:
                payload = param_id.to_bytes(2, "big") + raw.to_bytes(2, "big", signed=True)
                _LOGGER.info(
                    "Writing parameter %s (%s) = %s (raw %s)", param_id, param.display_name, value, raw
                )
                accepted = await self._request(CMD_WRITE, payload, self._write_timeout)
                if not accepted:
                    raise S3100WriteError(f"controller rejected parameter {param_id}")
                confirmed = await asyncio.wait_for(change, self._write_timeout)
            except TimeoutError as err:
                raise S3100WriteError(f"no confirmation for parameter {param_id}") from err
            finally:
                self._pending_change = None
                self._last_write = time.monotonic()
        if confirmed != raw:
            raise S3100WriteError(f"controller stored {confirmed} instead of {raw} for parameter {param_id}")
        return param

    def validate_write(self, param: Parameter, value: float) -> int:
        """Check limits and return the raw value to send."""
        if param.kind not in WRITABLE_KINDS:
            raise S3100WriteNotAllowedError(f"parameter {param.id} has unsupported type {param.kind}")
        if not param.minimum <= value <= param.maximum:
            raise S3100WriteNotAllowedError(
                f"{value} outside {param.minimum}..{param.maximum} for parameter {param.id}"
            )
        if param.kind is ParameterKind.BOOLEAN and value not in (0, 1):
            raise S3100WriteNotAllowedError("boolean parameter must be 0 or 1")
        assert self.catalog is not None
        linked = self.catalog.parameters.get(param.linked_id)
        if linked is not None and linked.id != param.id:
            if param.constraint == "X" and value > linked.value:
                raise S3100WriteNotAllowedError(f"must not exceed {linked.display_name} ({linked.value})")
            if param.constraint == "N" and value < linked.value:
                raise S3100WriteNotAllowedError(f"must not be below {linked.display_name} ({linked.value})")
        return param.to_raw(value)

    def _writable_parameter(self, param_id: int) -> Parameter:
        if not self.is_ready or self.catalog is None:
            raise S3100NotReadyError("session not ready")
        param = self.catalog.parameters.get(param_id)
        if param is None:
            raise S3100WriteNotAllowedError(f"unknown parameter {param_id}")
        if not param.in_customer_menu:
            raise S3100WriteNotAllowedError(f"parameter {param_id} is not in the customer menu")
        return param

    # ------------------------------------------------------------ internals

    def _emit(self, event_type: EventType, data: Any = None) -> None:
        event = Event(event_type, data)
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:  # a listener must not kill the session
                _LOGGER.exception("Error in S3100 listener")

    def _set_state(self, state: ConnectionState) -> None:
        if state is self._state:
            return
        _LOGGER.debug("State %s -> %s", self._state, state)
        self._state = state
        if state is ConnectionState.READY:
            self._ready_event.set()
        else:
            self._ready_event.clear()
        if state in (ConnectionState.LOADING, ConnectionState.READY):
            self._online_event.set()
        else:
            self._online_event.clear()
        self._emit(EventType.STATE, state)

    async def _run(self) -> None:
        delay = self._backoff_min
        while not self._stopping:
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except (OSError, S3100Error) as err:
                self.stats.last_error = f"{type(err).__name__}: {err}"
                _LOGGER.warning("S3100 session to %s:%s ended: %s", self.host, self.port, err)
            finally:
                await self._close()
                self._fail_pending(S3100ConnectionError("connection lost"))
            if self._stopping:
                break
            self._set_state(ConnectionState.DISCONNECTED)
            if self._ready_since is not None and time.monotonic() - self._ready_since > 60:
                delay = self._backoff_min  # The last session was healthy.
            self._ready_since = None
            self.stats.reconnects += 1
            sleep_for = delay * random.uniform(0.8, 1.2)
            _LOGGER.debug("Reconnecting in %.1f s", sleep_for)
            await asyncio.sleep(sleep_for)
            delay = min(delay * 2, self._backoff_max)

    async def _session(self) -> None:
        self._set_state(ConnectionState.CONNECTING)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self._connect_timeout
            )
        except (OSError, TimeoutError) as err:
            self.stats.connect_failures += 1
            raise S3100ConnectionError(f"cannot connect to {self.host}:{self.port}: {err}") from err
        self._writer = writer
        self.stats.connects += 1
        self.stats.connected_since = time.time()
        self._parser.reset()
        self._builder = None
        self._m1_errors = 0
        self._last_m1 = 0.0
        self._set_state(ConnectionState.LOGGING_IN)
        await self._send_login()
        login_attempts = 1

        while True:
            logging_in = self._state is ConnectionState.LOGGING_IN
            timeout = self._login_retry_interval if logging_in else self._idle_timeout
            try:
                data = await asyncio.wait_for(reader.read(READ_SIZE), timeout)
            except TimeoutError:
                if logging_in and login_attempts < self._max_login_attempts:
                    login_attempts += 1
                    await self._send_login()
                    continue
                self.stats.timeouts += 1
                if logging_in:
                    raise S3100TimeoutError("controller does not answer the login") from None
                raise S3100TimeoutError(f"no data for {timeout:.0f} s") from None
            if not data:
                raise S3100ConnectionError("bridge closed the connection")
            self.stats.bytes_rx += len(data)
            frames = self._parser.feed(data)
            self.stats.checksum_errors = self._parser.checksum_errors
            self.stats.discarded_bytes = self._parser.discarded_bytes
            for frame in frames:
                await self._handle(frame)

    async def _send_login(self) -> None:
        self.stats.login_attempts += 1
        payload = LOGIN_SERVICE if self.login_level is LoginLevel.SERVICE else LOGIN_CUSTOMER
        await self._send(CMD_LOGIN, payload, track=True)

    async def _close(self) -> None:
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.close()
            with contextlib.suppress(OSError, asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 2)

    def _fail_pending(self, err: Exception) -> None:
        futures: list[asyncio.Future[Any]] = [future for future, _ in self._pending.values()]
        self._pending.clear()
        if self._pending_change is not None:
            futures.append(self._pending_change[1])
        for future in futures:
            if not future.done():
                future.set_exception(err)
                # Login/start requests are tracked for latency only and never
                # awaited; mark the exception as retrieved to avoid warnings.
                future.exception()

    async def _send(self, command: bytes, payload: bytes, *, track: bool = False) -> None:
        if self._writer is None:
            raise S3100ConnectionError("not connected")
        data = encode_frame(command, payload)
        if track:
            key = command.decode("ascii")
            previous = self._pending.get(key)
            if previous is not None and not previous[0].done():
                future = previous[0]
            else:
                future = asyncio.get_running_loop().create_future()
            self._pending[key] = (future, time.monotonic())
        self._writer.write(data)
        await self._writer.drain()
        self.stats.bytes_tx += len(data)
        name = command.decode("ascii")
        self.stats.frames_tx[f"{name} ack" if payload == ACK and name.startswith("M") else name] += 1

    async def _request(self, command: bytes, payload: bytes, timeout: float) -> bool:
        await self._send(command, payload, track=True)
        future, _ = self._pending[command.decode("ascii")]
        return await asyncio.wait_for(asyncio.shield(future), timeout)

    async def _ack(self, frame: Frame) -> None:
        await self._send(frame.command.encode("ascii"), ACK)

    async def _handle(self, frame: Frame) -> None:
        self.stats.mark_frame()
        command = frame.command
        if command.startswith("R"):
            self._handle_response(frame)
            return
        self.stats.frames_rx[command] += 1

        if self._state is ConnectionState.LOGGING_IN and command in ("M1", "M2", "M3", "MI"):
            # An old session is still running. Leaving it un-ACKed makes the
            # controller drop it so the next login gets a fresh catalog.
            self.stats.stale_frames += 1
            return

        await self._ack(frame)

        if command in CONFIG_COMMANDS:
            self._handle_config(command, frame.payload)
        elif command == "M2":
            await self._handle_time(frame.payload)
        elif command == "M1":
            self._handle_values(frame.payload)
        elif command == "M3":
            self._handle_fault(frame.payload)
        elif command == "MI":
            self._handle_parameter_change(frame.payload)
        else:
            self._remember_unknown(frame, "unknown command")

    def _handle_response(self, frame: Frame) -> None:
        result = "ack" if frame.is_ack else "nack" if frame.is_nack else "?"
        self.stats.frames_rx[f"{frame.command} {result}"] += 1
        pending = self._pending.pop(frame.command, None)
        if not (frame.is_ack or frame.is_nack):
            self._remember_unknown(frame, "unexpected response payload")
            return
        if frame.is_nack:
            self.stats.nacks_received += 1
        if pending is None:
            return
        future, sent = pending
        self.stats.ack_latency.add(time.monotonic() - sent)
        if not future.done():
            future.set_result(frame.is_ack)
        if frame.command == "Ra" and frame.is_ack and self._state is ConnectionState.LOGGING_IN:
            self._start_loading()

    def _start_loading(self) -> None:
        self._builder = CatalogBuilder()
        self._loading_started = time.monotonic()
        self._set_state(ConnectionState.LOADING)

    def _handle_config(self, command: str, payload: bytes) -> None:
        if self._builder is None:
            # The controller re-sends its configuration (e.g. after a menu change).
            _LOGGER.info("Controller started sending a new configuration")
            self._start_loading()
        assert self._builder is not None
        self._builder.feed(command, payload)

    async def _handle_time(self, payload: bytes) -> None:
        try:
            self.controller_time = parse_controller_time(payload)
        except (DecodeError, ValueError) as err:
            self._decode_error(f"M2 {payload.hex()}: {err}")
        else:
            self._emit(EventType.TIME, self.controller_time)

        if self._builder is not None:
            builder, self._builder = self._builder, None
            catalog = builder.finish()
            for error in builder.errors:
                self._decode_error(error)
            if not catalog.measurements:
                raise S3100ProtocolError("configuration contained no measurements")
            self.catalog = catalog
            self.stats.catalog_load_seconds = round(time.monotonic() - self._loading_started, 2)
            _LOGGER.info(
                "S3100 catalog loaded in %.1f s: %d measurements, %d parameters",
                self.stats.catalog_load_seconds,
                len(catalog.measurements),
                len(catalog.parameters),
            )
            self._emit(EventType.CATALOG, catalog)
            await self._send(CMD_START, START_PAYLOAD, track=True)
            self.stats.sessions_ready += 1
            self._ready_since = time.monotonic()
            self._set_state(ConnectionState.READY)

    def _handle_values(self, payload: bytes) -> None:
        if self.catalog is None:
            self._remember_unknown(Frame("M1", payload), "M1 without catalog")
            return
        try:
            raw = parse_values(payload, self.catalog)
        except DecodeError as err:
            self._decode_error(str(err))
            self._m1_errors += 1
            if self._m1_errors >= MAX_M1_ERRORS:
                raise S3100ProtocolError("M1 layout does not match the catalog") from err
            return
        self._m1_errors = 0
        now = time.monotonic()
        if self._last_m1:
            self.stats.m1_interval.add(now - self._last_m1)
        self._last_m1 = now
        self.raw_values = raw
        self.values = {m.key: m.decode(v) for m, v in zip(self.catalog.measurements, raw, strict=True)}
        self.values_time = time.time()
        self._emit(EventType.VALUES, self.values)

    def _handle_fault(self, payload: bytes) -> None:
        texts = self.catalog.error_texts if self.catalog else {}
        try:
            fault = parse_fault(payload, texts)
        except DecodeError as err:
            self._decode_error(f"M3 {payload.hex()}: {err}")
            self._remember_unknown(Frame("M3", payload), "undecodable fault")
            return
        _LOGGER.warning("S3100 fault: %s (state %s)", fault.text, fault.state)
        self.live_faults.append(fault)
        self._emit(EventType.FAULT, fault)

    def _handle_parameter_change(self, payload: bytes) -> None:
        try:
            param_id, raw = parse_parameter_change(payload)
        except DecodeError as err:
            self._decode_error(f"MI {payload.hex()}: {err}")
            return
        param = self.catalog.parameters.get(param_id) if self.catalog else None
        if param is not None:
            param.raw_value = raw
        if self._pending_change is not None and self._pending_change[0] == param_id:
            future = self._pending_change[1]
            if not future.done():
                future.set_result(raw)
        _LOGGER.info("S3100 parameter %s changed to raw %s", param_id, raw)
        self._emit(EventType.PARAMETER, (param_id, raw))

    def _decode_error(self, message: str) -> None:
        self.stats.decode_errors += 1
        self.decode_errors.append(message)
        _LOGGER.debug("Decode error: %s", message)

    def _remember_unknown(self, frame: Frame, reason: str) -> None:
        entry = UnknownFrame(time.time(), frame.command, frame.payload, reason)
        self.unknown_frames.append(entry)
        _LOGGER.debug("Unknown frame %s %s (%s)", frame.command, frame.payload.hex(), reason)
        self._emit(EventType.UNKNOWN_FRAME, entry)


async def async_probe(host: str, port: int = DEFAULT_PORT, *, timeout: float = 15.0) -> None:
    """Check that an S3100 answers the login behind ``host:port``.

    Raises :class:`S3100ConnectionError` if the bridge is unreachable and
    :class:`S3100TimeoutError` if the controller does not acknowledge the
    login (wiring, baud rate or controller switched off).
    """
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), min(timeout, 10))
    except (OSError, TimeoutError) as err:
        raise S3100ConnectionError(f"cannot connect to {host}:{port}: {err}") from err
    parser = FrameParser()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    next_login = 0.0
    try:
        while (remaining := deadline - loop.time()) > 0:
            if loop.time() >= next_login:
                writer.write(encode_frame(CMD_LOGIN, LOGIN_CUSTOMER))
                await writer.drain()
                next_login = loop.time() + 5
            try:
                data = await asyncio.wait_for(
                    reader.read(READ_SIZE), min(remaining, next_login - loop.time())
                )
            except TimeoutError:
                continue
            if not data:
                raise S3100ConnectionError("bridge closed the connection")
            if any(f.command == "Ra" and f.is_ack for f in parser.feed(data)):
                return
        raise S3100TimeoutError("controller did not acknowledge the login")
    finally:
        writer.close()
        with contextlib.suppress(OSError, TimeoutError):
            await asyncio.wait_for(writer.wait_closed(), 2)
