"""
Protocol-aware fuzzing via boofuzz.

Runs user-provided boofuzz scripts for grammar-based network protocol fuzzing.
"""
from __future__ import annotations

import subprocess
import tempfile
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..config import DEFAULT_SCRIPT_TIMEOUT_SECONDS
from ..errors import PwnMcpError

if TYPE_CHECKING:
    from ..app import PwnMcpApp


BOOFUZZ_PREAMBLE = """\
from boofuzz import *
import sys

"""


def run_boofuzz_script(
    app: "PwnMcpApp",
    session_id: str,
    script: str,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Run a boofuzz protocol fuzzing script with `from boofuzz import *` pre-imported."""
    session = app.sessions.get(session_id)
    if session is None:
        raise PwnMcpError("not_found", "session_not_found", f"Session '{session_id}' not found.")

    timeout = timeout_seconds or DEFAULT_SCRIPT_TIMEOUT_SECONDS
    full_script = BOOFUZZ_PREAMBLE + script

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", prefix="boofuzz_", delete=False) as f:
        f.write(full_script)
        f.flush()
        script_path = f.name

    try:
        result = subprocess.run(
            ["python3", script_path],
            capture_output=True, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise PwnMcpError("timeout_or_resource_limit", "boofuzz_timeout", f"Boofuzz script timed out after {timeout}s.")
    finally:
        import os
        os.unlink(script_path)

    return {
        "ok": True,
        "result": {
            "return_code": result.returncode,
            "stdout": result.stdout[:10000],
            "stderr": result.stderr[:5000] if result.returncode != 0 else "",
        },
    }


# ── Registration ──────────────────────────────────────────────────────────────

def register(app: "PwnMcpApp") -> dict[str, dict]:
    def _h(fn):
        def wrapper(**kwargs):
            return fn(app, **kwargs)
        return wrapper

    _sid = {"type": "string", "description": "Session ID."}

    return {
        "run_boofuzz_script": {
            "handler": _h(run_boofuzz_script),
            "schema": Tool(
                name="run_boofuzz_script",
                description="Run a boofuzz protocol fuzzing script. `from boofuzz import *` is pre-imported. Use for grammar-based network protocol fuzzing.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "script": {"type": "string", "description": "Python script body using boofuzz API."},
                        "timeout_seconds": {"type": "integer", "description": "Timeout in seconds (default 30)."},
                    },
                    "required": ["session_id", "script"],
                    "additionalProperties": False,
                },
            ),
        },
    }
