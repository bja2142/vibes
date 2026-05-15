"""
Z3 constraint solver tool: run Z3 scripts for solving exploit constraints.
"""
from __future__ import annotations

import importlib.util
import subprocess
import tempfile
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..config import DEFAULT_SCRIPT_TIMEOUT_SECONDS
from ..errors import PwnMcpError

if TYPE_CHECKING:
    from ..app import PwnMcpApp


Z3_PREAMBLE = """\
from z3 import *
import json as _json

def _solve_and_print(solver, variables):
    \"\"\"Helper: check solver and print model as JSON.\"\"\"
    result = solver.check()
    if result == sat:
        model = solver.model()
        solution = {}
        for v in variables:
            val = model.evaluate(v)
            solution[str(v)] = str(val)
        print(_json.dumps({"sat": True, "model": solution}))
    elif result == unsat:
        print(_json.dumps({"sat": False, "reason": "unsatisfiable"}))
    else:
        print(_json.dumps({"sat": False, "reason": "unknown"}))

"""


def run_z3_script(
    app: "PwnMcpApp",
    session_id: str,
    script: str,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Run a Z3 Python script with `from z3 import *` pre-imported."""
    if importlib.util.find_spec("z3") is None:
        raise PwnMcpError("tool_not_found", "z3_missing", "The 'z3' Python package is not installed.")

    session = app.sessions.get(session_id)
    if session is None:
        raise PwnMcpError("not_found", "session_not_found", f"Session '{session_id}' not found.")

    timeout = timeout_seconds or DEFAULT_SCRIPT_TIMEOUT_SECONDS
    full_script = Z3_PREAMBLE + script

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", prefix="z3_", delete=False) as f:
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
        raise PwnMcpError("timeout_or_resource_limit", "z3_timeout", f"Z3 script timed out after {timeout}s.")
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
        "run_z3_script": {
            "handler": _h(run_z3_script),
            "schema": Tool(
                name="run_z3_script",
                description="Run a Z3 constraint solver script. `from z3 import *` is pre-imported. Use for solving buffer overflow offsets, checksum constraints, custom XOR transformations, etc.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "script": {"type": "string", "description": "Python script body using z3 API. `from z3 import *` and `_solve_and_print(solver, variables)` helper are pre-imported."},
                        "timeout_seconds": {"type": "integer", "description": "Timeout in seconds (default 30)."},
                    },
                    "required": ["session_id", "script"],
                    "additionalProperties": False,
                },
            ),
        },
    }
