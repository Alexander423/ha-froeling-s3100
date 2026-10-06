"""Data models for everything the S3100 announces about itself."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

# Unit characters used by the controller, mapped to readable units.
UNIT_MAP: dict[str, str | None] = {
    "°": "°C",
    "%": "%",
    "h": "h",
    "m": "min",
    "s": "s",
    "V": "V",
    "U": "rpm",
    " ": None,
    "": None,
}

TIME_MAX_RAW = 0x2400  # Parameters with this maximum are times of day.


def _scale(raw: int, divisor: int) -> float:
    """Divide by the controller divisor without losing resolution."""
    if divisor in (0, 1):
        return raw
    return round(raw / divisor, 3)


def map_unit(char: str) -> str | None:
    """Translate a controller unit character."""
    return UNIT_MAP.get(char, char.strip() or None)


class MeasurementKind(StrEnum):
    """How an M1 value is interpreted."""

    NUMBER = "I"
    TEXT = "S"
    UNKNOWN = "?"


class ParameterKind(StrEnum):
    """Parameter type taken from the customer menu (MD) entry."""

    NUMBER = "I"
    TIME = "Z"
    BOOLEAN = "B"
    WEEKDAY = "W"
    SELECT = "S"
    UNKNOWN = "?"


@dataclass(slots=True)
class ValueFormat:
    """Format record from the MC block."""

    index: int
    unit_char: str
    decimals: int
    divisor: int
    extra: int

    @property
    def unit(self) -> str | None:
        """Readable unit."""
        return map_unit(self.unit_char)

    def scale(self, raw: int) -> float:
        """Convert a raw value to its physical value."""
        return _scale(raw, self.divisor)


@dataclass(slots=True)
class Measurement:
    """A measurement announced by MA. ``position`` is its slot in M1."""

    position: int
    kind: MeasurementKind
    reference: int
    extra: int
    name: str
    value_format: ValueFormat | None = None
    texts: dict[int, str] | None = None

    @property
    def key(self) -> str:
        """Stable identifier, independent of the position in M1."""
        if self.kind is MeasurementKind.TEXT:
            return f"text_{self.reference}"
        return f"value_{self.reference}"

    @property
    def unit(self) -> str | None:
        """Readable unit, if any."""
        return self.value_format.unit if self.value_format else None

    @property
    def decimals(self) -> int:
        """Decimal places used by the controller display."""
        return self.value_format.decimals if self.value_format else 0

    def decode(self, raw: int) -> float | str | int:
        """Interpret a raw M1 value."""
        if self.texts is not None:
            return self.texts.get(raw, f"#{raw}")
        if self.kind is MeasurementKind.TEXT:
            return f"#{raw}"
        if self.value_format is not None:
            return self.value_format.scale(raw)
        return raw


@dataclass(slots=True)
class MenuEntry:
    """An entry of the customer menu tree (MD block)."""

    kind: str
    menu: int
    submenu: int
    parameter: int
    text: str


@dataclass(slots=True)
class Parameter:
    """A controller parameter (ME block) with optional menu metadata."""

    id: int
    unit_char: str
    decimals: int
    divisor: int
    raw_min: int
    raw_max: int
    raw_default: int
    constraint: str
    linked_id: int
    raw_value: int
    raw: bytes
    name: str | None = None
    menu_path: tuple[str, ...] = ()
    kind: ParameterKind = ParameterKind.UNKNOWN
    in_customer_menu: bool = False
    documented: bool = False

    @property
    def is_time(self) -> bool:
        """Return True for times of day (stored as minutes since midnight)."""
        return self.kind is ParameterKind.TIME or (self.raw_max == TIME_MAX_RAW and self.raw_min == 0)

    @property
    def unit(self) -> str | None:
        """Readable unit."""
        if self.is_time:
            return None
        return map_unit(self.unit_char)

    @property
    def _limit_factor(self) -> int:
        return int(10**self.decimals)

    @property
    def minimum(self) -> float:
        """Smallest allowed value in physical units."""
        if self.is_time:
            return 0
        return self.raw_min / self._limit_factor

    @property
    def maximum(self) -> float:
        """Largest allowed value in physical units."""
        if self.is_time:
            return 24 * 60 - 1
        return self.raw_max / self._limit_factor

    @property
    def default(self) -> float:
        """Factory default in physical units (not meaningful for times)."""
        return self.raw_default / self._limit_factor

    @property
    def step(self) -> float:
        """Smallest step the controller can represent."""
        if self.is_time:
            return 1
        return 1 / self._limit_factor

    @property
    def value(self) -> float:
        """Current value in physical units (minutes for times)."""
        return self.to_value(self.raw_value)

    def to_value(self, raw: int) -> float:
        """Convert a raw value to physical units."""
        if self.is_time:
            return raw
        return _scale(raw, self.divisor)

    def to_raw(self, value: float) -> int:
        """Convert a physical value to the raw representation."""
        if self.is_time:
            return int(value)
        return round(value * (self.divisor or 1))

    @property
    def display_name(self) -> str:
        """Human readable name."""
        if self.name:
            return self.name
        return f"Parameter {self.id}"


@dataclass(slots=True)
class FaultEvent:
    """A fault from the error history (MU) or a live fault (M3)."""

    error_id: int
    text: str
    flags: int
    state: int
    timestamp: datetime | None
    raw: bytes


@dataclass(slots=True)
class WeeklyProgram:
    """Weekly program (MG): heating-time number per day, Monday first."""

    circuit: int
    days: tuple[int, ...]
    days_alt: tuple[int, ...]
    raw: bytes


@dataclass(slots=True)
class OptionFlag:
    """An installed-feature flag (MM)."""

    group: int
    mask: int
    text: str


@dataclass(slots=True)
class RawEntry:
    """A config record whose layout is not understood yet."""

    command: str
    payload: bytes
    text: str = ""


@dataclass(slots=True)
class Catalog:
    """Everything the controller announced after login."""

    measurements: list[Measurement] = field(default_factory=list)
    texts: dict[int, dict[int, str]] = field(default_factory=dict)
    formats: dict[int, ValueFormat] = field(default_factory=dict)
    menu: list[MenuEntry] = field(default_factory=list)
    parameters: dict[int, Parameter] = field(default_factory=dict)
    operating_modes: dict[int, str] = field(default_factory=dict)
    weekly_programs: list[WeeklyProgram] = field(default_factory=list)
    options: list[OptionFlag] = field(default_factory=list)
    menu_titles: dict[int, str] = field(default_factory=dict)
    error_texts: dict[int, str] = field(default_factory=dict)
    error_history: list[FaultEvent] = field(default_factory=list)
    raw_entries: list[RawEntry] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    frames: list[tuple[str, bytes]] = field(default_factory=list)
    firmware: str | None = None

    @property
    def m1_length(self) -> int:
        """Expected M1 payload length."""
        return 2 * len(self.measurements)
