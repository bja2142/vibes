from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worksheet_generator.sample_data import export_sample_manifests


FIXTURES_DIR = ROOT / "fixtures"


if __name__ == "__main__":
    outputs = export_sample_manifests(FIXTURES_DIR)
    for name, path in outputs.items():
        print(f"{name}: {path}")
