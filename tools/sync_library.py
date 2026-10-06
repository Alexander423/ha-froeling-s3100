"""Keep the library bundled in the integration identical to src/froeling_s3100.

While the integration is distributed through HACS only, the library ships
inside it as the subpackage ``custom_components/froeling_s3100/s3100``.
``python tools/sync_library.py`` copies it, ``--check`` fails on drift (CI).
"""

import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SOURCE = ROOT / "src" / "froeling_s3100"
TARGET = ROOT / "custom_components" / "froeling_s3100" / "s3100"
FILES = sorted(p.name for p in SOURCE.iterdir() if p.suffix == ".py")


def check() -> int:
    target_files = sorted(p.name for p in TARGET.glob("*.py"))
    _, mismatch, errors = filecmp.cmpfiles(SOURCE, TARGET, FILES, shallow=False)
    if mismatch or errors or target_files != FILES:
        print(f"Bundled library is out of date: {mismatch + errors}. Run tools/sync_library.py")
        return 1
    print("Bundled library is in sync")
    return 0


def sync() -> int:
    shutil.rmtree(TARGET, ignore_errors=True)
    TARGET.mkdir(parents=True)
    for name in FILES:
        shutil.copy2(SOURCE / name, TARGET / name)
    print(f"Copied {len(FILES)} files to {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv else sync())
