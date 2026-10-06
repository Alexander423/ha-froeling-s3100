"""Tests for decoding the recorded configuration."""

from __future__ import annotations

from datetime import datetime

import pytest

from froeling_s3100.catalog import (
    CatalogBuilder,
    DecodeError,
    parse_controller_time,
    parse_fault,
    parse_parameter_change,
    parse_values,
)
from froeling_s3100.models import Catalog, MeasurementKind, ParameterKind
from froeling_s3100.simulator import Recording


@pytest.fixture
def catalog(recording: Recording) -> Catalog:
    builder = CatalogBuilder()
    for command, payload in recording.config:
        builder.feed(command, payload)
    assert builder.errors == []
    return builder.finish()


def test_measurements(catalog: Catalog, recording: Recording) -> None:
    assert len(catalog.measurements) == 35
    values = {
        m.name: m.decode(raw)
        for m, raw in zip(catalog.measurements, parse_values(recording.m1, catalog), strict=True)
    }
    assert values["Kesseltemp"] == 22.5
    assert values["Puffert.ob"] == 49
    assert values["Außentemp"] == 18.5
    assert values["Rest-O2"] == 1.9
    assert values["Laufzeit:"] == 2492
    assert values["F-halt:"] == 34.9
    assert values["Zustand"] == "Sommerbetrieb"
    assert values[""] == "Feuer-Aus"


def test_measurement_metadata(catalog: Catalog) -> None:
    by_name = {m.name: m for m in catalog.measurements}
    assert by_name["Kesseltemp"].unit == "°C"
    assert by_name["Kesseltemp"].key == "value_0"
    assert by_name["Laufzeit:"].unit == "h"
    assert by_name[""].kind is MeasurementKind.TEXT
    assert len({m.key for m in catalog.measurements}) == 35


def test_parameters(catalog: Catalog) -> None:
    assert len(catalog.parameters) == 228
    boiler = catalog.parameters[0x75]
    assert boiler.name == "GewünschteBoiler temperatur"
    assert boiler.menu_path == ("Boiler",)
    assert boiler.in_customer_menu
    assert boiler.kind is ParameterKind.NUMBER
    assert (boiler.minimum, boiler.maximum, boiler.value) == (20, 100, 50)
    assert boiler.to_raw(55) == 110

    fixed_point = catalog.parameters[0x06]
    assert fixed_point.value == 2.0
    assert fixed_point.minimum == 0.01

    heating_start = catalog.parameters[0x21]
    assert heating_start.is_time
    assert heating_start.kind is ParameterKind.TIME
    assert heating_start.value == 300  # 05:00
    assert heating_start.menu_path == ("Heizzeiten", "Heizzeit 1")

    negative = catalog.parameters[0x6B]
    assert negative.value == -5
    assert negative.minimum == -20

    service = catalog.parameters[0x10]
    assert not service.in_customer_menu
    assert service.constraint == "X"
    assert service.linked_id == 0x15


def test_shared_parameter_prefers_reachable_menu(catalog: Catalog) -> None:
    param = catalog.parameters[0x71]
    assert param.menu_path == ("Heizkreis 2", "Heizkurve")


def test_tables(catalog: Catalog) -> None:
    assert catalog.operating_modes[1] == "Sommerbetrieb"
    assert catalog.error_texts[0x21] == "Puffer zu kalt NACHLEGEN"
    assert len(catalog.error_history) == 29
    first = catalog.error_history[0]
    assert first.timestamp == datetime(2026, 4, 3, 22, 53, 54)
    assert first.text == "Puffer zu kalt NACHLEGEN"
    assert len(catalog.weekly_programs) == 4
    assert any(o.text == "Lambdasonde vorhanden" for o in catalog.options)
    assert catalog.blocks == list("ABCDEFGKLMWSTUV")


def test_live_messages() -> None:
    assert parse_controller_time(bytes.fromhex("10011806100226")) == datetime(2026, 10, 6, 18, 1, 10)
    assert parse_parameter_change(bytes.fromhex("0075006e")) == (0x75, 110)
    fault = parse_fault(bytes.fromhex("21c80454532203040026"), {0x21: "Puffer zu kalt"})
    assert fault.text == "Puffer zu kalt"
    with pytest.raises(DecodeError):
        parse_controller_time(b"\x00")


def test_bad_record_is_preserved() -> None:
    builder = CatalogBuilder()
    builder.feed("MC", b"\x00")
    builder.feed("MX", b"\x01\x02")
    catalog = builder.finish()
    assert len(builder.errors) == 1
    assert [e.command for e in catalog.raw_entries] == ["MC", "MX"]
