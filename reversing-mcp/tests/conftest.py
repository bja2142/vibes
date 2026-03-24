from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DEFAULT_WORKSPACE_ROOT = ROOT / ".tmp_pytest_workspace"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DEFAULT_WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("REVERSING_MCP_WORKSPACE_ROOT", str(DEFAULT_WORKSPACE_ROOT))
existing_pythonpath = os.environ.get("PYTHONPATH", "")
pythonpath_entries = [entry for entry in existing_pythonpath.split(os.pathsep) if entry]
if str(SRC) not in pythonpath_entries:
    os.environ["PYTHONPATH"] = os.pathsep.join([str(SRC), *pythonpath_entries])
