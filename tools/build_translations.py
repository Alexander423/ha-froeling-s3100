"""Resolve [%key:...%] references in strings.json into translations/en.json."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent / "custom_components" / "froeling_s3100"
COMMON = {
    "common::config_flow::data::host": "Host",
    "common::config_flow::data::port": "Port",
    "common::config_flow::error::unknown": "Unexpected error",
    "common::config_flow::abort::already_configured_device": "Device is already configured",
    "common::config_flow::abort::reconfigure_successful": "Re-configuration was successful",
}

strings = json.loads((ROOT / "strings.json").read_text(encoding="utf-8"))


def lookup(key: str) -> str:
    if key in COMMON:
        return COMMON[key]
    parts = key.split("::")
    assert parts[:2] == ["component", "froeling_s3100"], key
    node = strings
    for part in parts[2:]:
        node = node[part]
    return resolve(node)


def resolve(node):
    if isinstance(node, dict):
        return {k: resolve(v) for k, v in node.items()}
    return re.sub(r"\[%key:([^%]+)%\]", lambda m: lookup(m.group(1)), node)


(ROOT / "translations" / "en.json").write_text(
    json.dumps(resolve(strings), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
