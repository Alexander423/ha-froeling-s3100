"""Build an integration folder with the library vendored, for manual testing.

The release version depends on the ``froeling-s3100`` package from PyPI.
This test build copies the library into the integration instead, so it can
be dropped into ``/config/custom_components`` without publishing anything.
"""

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).parent.parent
SOURCE = ROOT / "custom_components" / "froeling_s3100"
TARGET = ROOT / "build" / "froeling_s3100"

VENDOR_SHIM = '''
import sys as _sys
from pathlib import Path as _Path

_VENDOR = str(_Path(__file__).parent / "vendor")
if _VENDOR not in _sys.path:  # Test build: use the bundled library.
    _sys.path.insert(0, _VENDOR)
'''

shutil.rmtree(TARGET, ignore_errors=True)
shutil.copytree(SOURCE, TARGET, ignore=shutil.ignore_patterns("__pycache__"))
shutil.copytree(
    ROOT / "src" / "froeling_s3100",
    TARGET / "vendor" / "froeling_s3100",
    ignore=shutil.ignore_patterns("__pycache__"),
)

init = TARGET / "__init__.py"
text = init.read_text(encoding="utf-8")
marker = "from __future__ import annotations\n"
init.write_text(text.replace(marker, marker + VENDOR_SHIM, 1), encoding="utf-8")

manifest_path = TARGET / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
manifest["requirements"] = []
manifest["version"] = manifest["version"] + "-test"
manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"Built {TARGET}")
