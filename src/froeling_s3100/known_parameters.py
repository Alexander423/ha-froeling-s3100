"""Names of service parameters that are not part of the customer menu.

The controller only names parameters that appear in the customer menu. The
names below were matched against the Fröling "Lambdatronic S 3100
Bedienungsanleitung SERVICE TECHNIKER ab V24.16" (B 018 16 06) by unit,
range and factory default on a FHG Turbo 3000 with firmware V24.20. Only
matches with high confidence are listed. Parameter ids may differ on other
firmware versions, so the integration only uses them read-only.
"""

from __future__ import annotations

from typing import Final

# id -> name as printed in the service manual (German, like the controller).
SERVICE_PARAMETER_NAMES: Final[dict[int, str]] = {
    0: "Ausschalten über Soll.Temp.+",
    1: "Maximale Anheizzeit",
    2: "Minimale Abgastemperatur",
    3: "Maximale Abgastemperatur",
    5: "Abgastemp. Feuer-AUS",
    13: "Ausschalten über max. Sollt.+",
    25: "Restsauerstoffgehalt Soll",
    26: "Restsauerstoff Feuer-AUS",
    29: "P.Luft 0% (Klappenspannung)",
    30: "P.Luft 100% (Klappenspannung)",
    31: "S.Luft 0% (Klappenspannung)",
    32: "S.Luft 100% (Klappenspannung)",
    119: "Mindestrücklauftemperatur",
    120: "Die Pumpen laufen ab",
    121: "Rücklaufanhebepumpe Min.Drehz.",
    127: "Pufferpumpe min. Drehz.",
    173: "Boilerpumpe Min.Drehz.",
}

# First parameter id of each heating circuit block (circuit number -> id).
HEATING_CIRCUIT_BLOCKS: Final[dict[int, int]] = {1: 89, 2: 102, 3: 191, 4: 204}
HEATING_CIRCUIT_OFFSETS: Final[dict[int, str]] = {
    6: "Vorlauftemp. Maximalwert",
    9: "Mischer Laufzeit",
    10: "Frostschutztemperatur",
}


def known_service_parameters() -> dict[int, str]:
    """All documented service parameter names by id."""
    names = dict(SERVICE_PARAMETER_NAMES)
    for circuit, start in HEATING_CIRCUIT_BLOCKS.items():
        for offset, name in HEATING_CIRCUIT_OFFSETS.items():
            names[start + offset] = f"Heizkreis {circuit} {name}"
    return names
