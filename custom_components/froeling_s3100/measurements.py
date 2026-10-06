"""Known S3100 measurement names and how to present them.

The controller announces its measurements with short German display names.
This table maps the names seen on real installations to translated entity
names and device classes. Unknown names are still exposed, using the name
announced by the controller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory


@dataclass(frozen=True, slots=True)
class MeasurementInfo:
    """Presentation hints for a measurement."""

    translation_key: str
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = SensorStateClass.MEASUREMENT
    entity_category: EntityCategory | None = None
    enabled_default: bool = True


TEMP = SensorDeviceClass.TEMPERATURE
DURATION = SensorDeviceClass.DURATION
TOTAL = SensorStateClass.TOTAL_INCREASING
DIAG = EntityCategory.DIAGNOSTIC


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9äöüß]", "", name.lower())


_KNOWN: dict[str, MeasurementInfo] = {
    "zustand": MeasurementInfo("operating_mode", SensorDeviceClass.ENUM, None),
    "rost": MeasurementInfo("grate", entity_category=DIAG),
    "kesseltemp": MeasurementInfo("boiler_temperature", TEMP),
    "kesselsoll": MeasurementInfo("boiler_temperature_target", TEMP),
    "abgastemp": MeasurementInfo("flue_gas_temperature", TEMP),
    "abgassw": MeasurementInfo("flue_gas_temperature_target", TEMP),
    "kessstellgr": MeasurementInfo("boiler_control_value"),
    "saugzug": MeasurementInfo("induced_draft_fan"),
    "primluft": MeasurementInfo("primary_air"),
    "primklpos": MeasurementInfo("primary_air_flap"),
    "sekluft": MeasurementInfo("secondary_air"),
    "sekklpos": MeasurementInfo("secondary_air_flap"),
    "resto2": MeasurementInfo("residual_oxygen"),
    "o2regler": MeasurementInfo("oxygen_controller"),
    "puffertob": MeasurementInfo("buffer_temperature_top", TEMP),
    "pufferoben": MeasurementInfo("buffer_temperature_top", TEMP),
    "puffertmi": MeasurementInfo("buffer_temperature_middle", TEMP),
    "puffertun": MeasurementInfo("buffer_temperature_bottom", TEMP),
    "pufferunten": MeasurementInfo("buffer_temperature_bottom", TEMP),
    "pufferpu": MeasurementInfo("buffer_pump"),
    "boilertemp": MeasurementInfo("hot_water_temperature", TEMP),
    "boiler": MeasurementInfo("hot_water_temperature", TEMP),
    "außentemp": MeasurementInfo("outdoor_temperature", TEMP),
    "feuerraum": MeasurementInfo("combustion_chamber_temperature", TEMP),
    "einschub": MeasurementInfo("feeder"),
    "füllstand": MeasurementInfo("fill_level"),
    "laufzeit": MeasurementInfo("operating_hours", DURATION, TOTAL),
    "betriebsstunden": MeasurementInfo("operating_hours", DURATION, TOTAL),
    "fhalt": MeasurementInfo("fire_hold_hours", DURATION, TOTAL),
    "brennerstarts": MeasurementInfo("burner_starts", None, TOTAL),
    "boardtemp": MeasurementInfo("board_temperature", TEMP, entity_category=DIAG),
}

_NUMBERED: list[tuple[re.Pattern[str], MeasurementInfo]] = [
    (re.compile(r"^vorlauft(\d)sw$"), MeasurementInfo("flow_temperature_target", TEMP)),
    (re.compile(r"^vorlauft(\d)$"), MeasurementInfo("flow_temperature", TEMP)),
    (re.compile(r"^vorlauf(\d)$"), MeasurementInfo("flow_temperature", TEMP)),
    (re.compile(r"^raumtemp(\d)$"), MeasurementInfo("room_temperature", TEMP)),
    (
        re.compile(r"^partysch(\d)$"),
        MeasurementInfo("party_switch", entity_category=DIAG, enabled_default=False),
    ),
    (
        re.compile(r"^kty(\d)h\d$"),
        MeasurementInfo("kty_sensor", TEMP, entity_category=DIAG, enabled_default=False),
    ),
]


def lookup(name: str) -> tuple[MeasurementInfo | None, dict[str, str]]:
    """Return presentation hints and translation placeholders for a name."""
    normalized = _normalize(name)
    if (info := _KNOWN.get(normalized)) is not None:
        return info, {}
    for pattern, info in _NUMBERED:
        if match := pattern.match(normalized):
            return info, {"number": match.group(1)}
    return None, {}


# Documented service parameters (read-only): id -> (translation key, enabled by default).
SERVICE_PARAMETERS: dict[int, tuple[str, bool]] = {
    0: ("switch_off_above_target", False),
    1: ("max_heat_up_time", True),
    2: ("min_flue_gas_temperature", True),
    3: ("max_flue_gas_temperature", True),
    5: ("fire_out_flue_gas_temperature", True),
    13: ("switch_off_above_max_target", False),
    25: ("residual_oxygen_target", True),
    26: ("fire_out_residual_oxygen", True),
    29: ("primary_air_flap_voltage_closed", False),
    30: ("primary_air_flap_voltage_open", False),
    31: ("secondary_air_flap_voltage_closed", False),
    32: ("secondary_air_flap_voltage_open", False),
    119: ("min_return_temperature", True),
    120: ("pumps_start_temperature", True),
    121: ("return_pump_min_speed", False),
    127: ("buffer_pump_min_speed", False),
    173: ("hot_water_pump_min_speed", False),
}
HEATING_CIRCUIT_BLOCKS: dict[int, int] = {1: 89, 2: 102, 3: 191, 4: 204}
HEATING_CIRCUIT_PARAMETERS: dict[int, tuple[str, bool]] = {
    6: ("circuit_max_flow_temperature", True),
    9: ("circuit_mixer_runtime", False),
    10: ("circuit_frost_protection", False),
}


def service_parameters(circuits: set[str]) -> dict[int, tuple[str, bool, dict[str, str]]]:
    """Documented service parameters for the announced heating circuits."""
    result: dict[int, tuple[str, bool, dict[str, str]]] = {
        pid: (key, enabled, {}) for pid, (key, enabled) in SERVICE_PARAMETERS.items()
    }
    for circuit, start in HEATING_CIRCUIT_BLOCKS.items():
        if str(circuit) not in circuits:
            continue
        for offset, (key, enabled) in HEATING_CIRCUIT_PARAMETERS.items():
            result[start + offset] = (key, enabled, {"number": str(circuit)})
    return result
