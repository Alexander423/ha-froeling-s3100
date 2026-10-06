"""Decoding of the S3100 configuration blocks and live messages."""

from __future__ import annotations

import logging
from datetime import datetime

from .known_parameters import known_service_parameters
from .models import (
    Catalog,
    FaultEvent,
    Measurement,
    MeasurementKind,
    MenuEntry,
    OptionFlag,
    Parameter,
    ParameterKind,
    RawEntry,
    ValueFormat,
    WeeklyProgram,
)
from .protocol import TEXT_ENCODING, bcd, decode_text, s16, u16

_LOGGER = logging.getLogger(__name__)

CONFIG_COMMANDS = frozenset(
    {"MA", "MB", "MC", "MD", "ME", "MF", "MG", "MH", "MK", "ML", "MM", "MS", "MT", "MU", "MV", "MW", "MZ"}
)
ROOT_MENU = 1
OPERATING_MODE_NAMES = frozenset({"zustand"})


class DecodeError(ValueError):
    """A frame did not match the expected layout."""


def parse_controller_time(p: bytes) -> datetime:
    """Decode an M2 payload (BCD sec, min, hour, day, month, weekday, year)."""
    if len(p) != 7:
        raise DecodeError(f"M2 length {len(p)} != 7")
    return datetime(2000 + bcd(p[6]), bcd(p[4]), bcd(p[3]), bcd(p[2]), bcd(p[1]), bcd(p[0]))


def parse_fault(p: bytes, error_texts: dict[int, str]) -> FaultEvent:
    """Decode an MU (history) or M3 (live) fault record."""
    if len(p) != 10:
        raise DecodeError(f"fault record length {len(p)} != 10")
    try:
        timestamp: datetime | None = datetime(
            2000 + bcd(p[9]), bcd(p[7]), bcd(p[6]), bcd(p[5]), bcd(p[4]), bcd(p[3])
        )
    except ValueError:
        timestamp = None
    return FaultEvent(
        error_id=p[0],
        text=error_texts.get(p[0], f"Fehler {p[0]}"),
        flags=p[1],
        state=p[2],
        timestamp=timestamp,
        raw=bytes(p),
    )


def parse_parameter_change(p: bytes) -> tuple[int, int]:
    """Decode an MI payload into (parameter id, raw value)."""
    if len(p) != 4:
        raise DecodeError(f"MI length {len(p)} != 4")
    return u16(p, 0), s16(p, 2)


def parse_values(p: bytes, catalog: Catalog) -> list[int]:
    """Split an M1 payload into signed raw values."""
    if len(p) != catalog.m1_length:
        raise DecodeError(f"M1 length {len(p)} != {catalog.m1_length}")
    return [s16(p, i) for i in range(0, len(p), 2)]


def _text(p: bytes) -> str:
    return decode_text(p)


class CatalogBuilder:
    """Accumulates configuration frames into a :class:`Catalog`."""

    def __init__(self) -> None:
        self.catalog = Catalog()
        self.errors: list[str] = []

    def feed(self, command: str, p: bytes) -> None:
        """Process one configuration frame."""
        self.catalog.frames.append((command, bytes(p)))
        handler = getattr(self, f"_parse_{command.lower()}", None)
        try:
            if handler is None:
                self.catalog.raw_entries.append(RawEntry(command, bytes(p)))
            else:
                handler(p)
        except (DecodeError, IndexError, ValueError) as err:
            self.errors.append(f"{command} {p.hex()}: {err}")
            self.catalog.raw_entries.append(RawEntry(command, bytes(p)))

    def _parse_ma(self, p: bytes) -> None:
        if len(p) < 5:
            raise DecodeError("MA too short")
        kind_char = chr(p[0])
        kind = (
            MeasurementKind(kind_char)
            if kind_char in MeasurementKind._value2member_map_
            else MeasurementKind.UNKNOWN
        )
        self.catalog.measurements.append(
            Measurement(
                position=len(self.catalog.measurements),
                kind=kind,
                reference=u16(p, 1),
                extra=u16(p, 3),
                name=_text(p[5:]),
            )
        )

    def _parse_mb(self, p: bytes) -> None:
        if len(p) < 4:
            raise DecodeError("MB too short")
        self.catalog.texts.setdefault(u16(p, 0), {})[u16(p, 2)] = _text(p[4:])

    def _parse_mc(self, p: bytes) -> None:
        if len(p) != 8:
            raise DecodeError("MC length != 8")
        index = u16(p, 0)
        self.catalog.formats[index] = ValueFormat(
            index=index,
            unit_char=p[2:3].decode(TEXT_ENCODING),
            decimals=p[3],
            divisor=u16(p, 4),
            extra=u16(p, 6),
        )

    def _parse_md(self, p: bytes) -> None:
        if len(p) < 7:
            raise DecodeError("MD too short")
        self.catalog.menu.append(
            MenuEntry(
                kind=chr(p[0]),
                menu=u16(p, 1),
                submenu=u16(p, 3),
                parameter=u16(p, 5),
                text=_text(p[7:]).rstrip(". ").strip(),
            )
        )

    def _parse_me(self, p: bytes) -> None:
        if len(p) != 17:
            raise DecodeError("ME length != 17")
        param_id = u16(p, 0)
        self.catalog.parameters[param_id] = Parameter(
            id=param_id,
            unit_char=p[2:3].decode(TEXT_ENCODING),
            decimals=p[3],
            divisor=u16(p, 4),
            raw_min=s16(p, 6),
            raw_max=s16(p, 8),
            raw_default=s16(p, 10),
            constraint=chr(p[12]) if p[12] else "",
            linked_id=u16(p, 13),
            raw_value=s16(p, 15),
            raw=bytes(p),
        )

    def _parse_mf(self, p: bytes) -> None:
        if len(p) < 2:
            raise DecodeError("MF too short")
        self.catalog.operating_modes[u16(p, 0)] = _text(p[2:])

    def _parse_mg(self, p: bytes) -> None:
        if len(p) != 18:
            raise DecodeError("MG length != 18")
        self.catalog.weekly_programs.append(
            WeeklyProgram(circuit=u16(p, 0), days=tuple(p[3:10]), days_alt=tuple(p[11:18]), raw=bytes(p))
        )

    def _parse_ml(self, p: bytes) -> None:
        self.catalog.raw_entries.append(RawEntry("ML", bytes(p), _text(p[10:])))

    def _parse_mm(self, p: bytes) -> None:
        if len(p) < 4:
            raise DecodeError("MM too short")
        self.catalog.options.append(OptionFlag(group=u16(p, 0), mask=u16(p, 2), text=_text(p[4:])))

    def _parse_mw(self, p: bytes) -> None:
        if len(p) < 2:
            raise DecodeError("MW too short")
        self.catalog.menu_titles[u16(p, 0)] = _text(p[2:])

    def _parse_mt(self, p: bytes) -> None:
        if len(p) < 1:
            raise DecodeError("MT too short")
        self.catalog.error_texts[p[0]] = _text(p[1:])

    def _parse_mu(self, p: bytes) -> None:
        # Error texts (MT) arrive before the history, so texts resolve here.
        self.catalog.error_history.append(parse_fault(p, self.catalog.error_texts))

    def _parse_ms(self, p: bytes) -> None:
        # Firmware version in BCD, e.g. 24 20 ... = V24.20.
        if len(p) < 2:
            raise DecodeError("MS too short")
        self.catalog.firmware = f"{bcd(p[0])}.{bcd(p[1]):02d}"
        self.catalog.raw_entries.append(RawEntry("MS", bytes(p)))

    def _parse_mz(self, p: bytes) -> None:
        if len(p) != 1:
            raise DecodeError("MZ length != 1")
        self.catalog.blocks.append(chr(p[0]))

    def finish(self) -> Catalog:
        """Link the tables together and return the finished catalog."""
        catalog = self.catalog
        for measurement in catalog.measurements:
            if measurement.kind is MeasurementKind.TEXT:
                measurement.texts = catalog.texts.get(measurement.reference, {})
            else:
                measurement.value_format = catalog.formats.get(measurement.reference)
                if (
                    measurement.name.lower().rstrip(":") in OPERATING_MODE_NAMES
                    and measurement.unit is None
                    and catalog.operating_modes
                ):
                    measurement.texts = catalog.operating_modes
        for fault in catalog.error_history:
            fault.text = catalog.error_texts.get(fault.error_id, fault.text)
        self._apply_menu(catalog)
        for param_id, name in known_service_parameters().items():
            param = catalog.parameters.get(param_id)
            if param is not None and not param.in_customer_menu:
                param.name = name
                param.documented = True
        return catalog

    @staticmethod
    def _apply_menu(catalog: Catalog) -> None:
        submenus = {e.submenu: e for e in catalog.menu if e.kind == "A"}

        def path(menu: int) -> tuple[str, ...] | None:
            parts: list[str] = []
            seen: set[int] = set()
            while menu != ROOT_MENU:
                entry = submenus.get(menu)
                if entry is None or menu in seen:
                    return None
                seen.add(menu)
                if entry.menu != ROOT_MENU:
                    parts.append(entry.text)
                menu = entry.menu
            return tuple(reversed(parts))

        for entry in catalog.menu:
            if entry.kind in ("A", "P"):
                continue
            param = catalog.parameters.get(entry.parameter)
            if param is None:
                continue
            menu_path = path(entry.menu)
            reachable = menu_path is not None
            if param.in_customer_menu and not reachable:
                continue  # Keep the first reachable entry.
            param.name = entry.text
            param.menu_path = menu_path or ()
            param.in_customer_menu = reachable
            kind_char = entry.kind
            param.kind = (
                ParameterKind(kind_char)
                if kind_char in ParameterKind._value2member_map_
                else ParameterKind.UNKNOWN
            )


def build_catalog(frames: list[tuple[str, bytes]]) -> Catalog:
    """Rebuild a catalog from recorded configuration frames (e.g. a cache)."""
    builder = CatalogBuilder()
    for command, payload in frames:
        builder.feed(command, payload)
    return builder.finish()
