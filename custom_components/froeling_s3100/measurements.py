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
