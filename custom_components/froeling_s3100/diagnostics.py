"""Diagnostics for the Fröling S3100.

Includes everything needed to reverse engineer further parts of the
protocol: the full catalog with raw bytes, unknown frames and statistics.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .coordinator import FroelingConfigEntry
from .s3100 import FaultEvent, Parameter

TO_REDACT = {CONF_HOST}


def _fault(fault: FaultEvent) -> dict[str, Any]:
    return {
        "error_id": fault.error_id,
        "text": fault.text,
        "flags": fault.flags,
        "state": fault.state,
        "timestamp": fault.timestamp.isoformat() if fault.timestamp else None,
        "raw": fault.raw.hex(),
    }


def _parameter(param: Parameter) -> dict[str, Any]:
    return {
        "id": param.id,
        "name": param.name,
        "menu_path": list(param.menu_path),
        "kind": param.kind.value,
        "customer_menu": param.in_customer_menu,
        "unit": param.unit,
        "value": param.value,
        "minimum": param.minimum,
        "maximum": param.maximum,
        "default": param.default,
        "decimals": param.decimals,
        "divisor": param.divisor,
        "constraint": param.constraint,
        "linked_id": param.linked_id,
        "raw": param.raw.hex(),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FroelingConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    client = coordinator.client
    catalog = client.catalog
    data: dict[str, Any] = {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "connection": {
            "state": client.state.value,
            "login_level": client.login_level.value,
            "statistics": client.stats.as_dict(),
            "decode_errors": list(client.decode_errors),
        },
        "controller_time": client.controller_time.isoformat() if client.controller_time else None,
        "live_faults": [_fault(f) for f in client.live_faults],
        "unknown_frames": [
            {"time": u.time, "command": u.command, "payload": u.payload.hex(), "reason": u.reason}
            for u in client.unknown_frames
        ],
    }
    if catalog is None:
        return data
    data["catalog"] = {
        "blocks": catalog.blocks,
        "measurements": [
            {
                "position": m.position,
                "key": m.key,
                "name": m.name,
                "kind": m.kind.value,
                "reference": m.reference,
                "extra": m.extra,
                "unit": m.unit,
                "format": asdict(m.value_format) if m.value_format else None,
                "raw_value": client.raw_values[m.position] if m.position < len(client.raw_values) else None,
                "value": client.values.get(m.key),
            }
            for m in catalog.measurements
        ],
        "texts": {str(page): texts for page, texts in catalog.texts.items()},
        "unused_formats": [
            asdict(f)
            for idx, f in catalog.formats.items()
            if idx not in {m.reference for m in catalog.measurements}
        ],
        "parameters": [_parameter(p) for p in catalog.parameters.values()],
        "menu": [asdict(e) for e in catalog.menu],
        "operating_modes": catalog.operating_modes,
        "weekly_programs": [
            {"circuit": w.circuit, "days": w.days, "days_alt": w.days_alt, "raw": w.raw.hex()}
            for w in catalog.weekly_programs
        ],
        "options": [asdict(o) for o in catalog.options],
        "menu_titles": catalog.menu_titles,
        "error_texts": catalog.error_texts,
        "error_history": [_fault(f) for f in catalog.error_history],
        "raw_entries": [
            {"command": r.command, "payload": r.payload.hex(), "text": r.text} for r in catalog.raw_entries
        ],
    }
    return data
