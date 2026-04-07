"""Ghidra headless integration for reversing-mcp."""
from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .errors import StructuredToolError

LOGGER = logging.getLogger("reversing_mcp.ghidra")

GHIDRA_HEADLESS = Path("/opt/ghidra/support/analyzeHeadless")
GHIDRA_SCRIPTS_DIR = Path(__file__).parent / "ghidra_scripts"


def ghidra_available() -> bool:
    return GHIDRA_HEADLESS.exists()


def _run_ghidra_headless(
    binary_path: str,
    script_name: str,
    script_args: list[str] | None = None,
    timeout_seconds: int = 300,
    project_dir: str | None = None,
) -> dict[str, Any]:
    """Run analyzeHeadless with a postScript on a binary, return parsed JSON from stdout."""
    if not ghidra_available():
        raise StructuredToolError(
            "missing_prerequisite",
            "ghidra_not_installed",
            "Ghidra headless is not installed at /opt/ghidra.",
        )

    script_path = GHIDRA_SCRIPTS_DIR / script_name
    if not script_path.exists():
        raise StructuredToolError(
            "invalid_request",
            "ghidra_script_not_found",
            f"Built-in Ghidra script '{script_name}' not found.",
        )

    with tempfile.TemporaryDirectory(prefix="ghidra_proj_") as tmpdir:
        proj_dir = project_dir or tmpdir
        cmd = [
            str(GHIDRA_HEADLESS),
            proj_dir,
            "mcp_project",
            "-import", binary_path,
            "-overwrite",
            "-scriptPath", str(GHIDRA_SCRIPTS_DIR),
            "-postScript", script_name,
        ]
        if script_args:
            cmd.extend(script_args)

        LOGGER.info("ghidra_headless cmd=%s", " ".join(cmd))

        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise StructuredToolError(
                "timeout_or_resource_limit",
                "ghidra_timeout",
                f"Ghidra headless timed out after {timeout_seconds}s.",
                details={"binary": binary_path, "script": script_name},
            ) from exc

        # Ghidra prints a lot to stdout; our scripts print JSON as the last line
        stdout = completed.stdout
        stderr = completed.stderr

        # Find JSON output — our scripts print a single JSON object
        json_output = None
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    json_output = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue

        if json_output is None:
            # Check if Ghidra itself failed
            if completed.returncode != 0:
                raise StructuredToolError(
                    "backend_failure",
                    "ghidra_execution_failed",
                    f"Ghidra headless exited with code {completed.returncode}.",
                    details={"stderr": stderr[-2000:] if stderr else "", "stdout_tail": stdout[-1000:] if stdout else ""},
                )
            raise StructuredToolError(
                "backend_failure",
                "ghidra_no_output",
                "Ghidra script produced no JSON output.",
                details={"stdout_tail": stdout[-1000:] if stdout else ""},
            )

        return json_output


def ghidra_decompile_function(
    binary_path: str,
    function_address: int,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Decompile a function using Ghidra's decompiler."""
    hex_addr = f"0x{function_address:x}"
    return _run_ghidra_headless(
        binary_path,
        "decompile_function.py",
        script_args=[hex_addr],
        timeout_seconds=timeout_seconds,
    )


def ghidra_export_analysis(
    binary_path: str,
    output_path: str | None = None,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """Run full Ghidra analysis and export functions, strings, imports."""
    args = [output_path] if output_path else []
    return _run_ghidra_headless(
        binary_path,
        "export_analysis.py",
        script_args=args,
        timeout_seconds=timeout_seconds,
    )


def ghidra_run_custom_script(
    binary_path: str,
    script_content: str,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Run a user-provided Ghidra Python script against a binary."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="ghidra_user_", delete=False
    ) as f:
        f.write(script_content)
        f.flush()
        script_path = f.name

    # We need to put it where Ghidra can find it
    tmp_script = Path(script_path)
    try:
        cmd = [
            str(GHIDRA_HEADLESS),
            "/tmp",
            "mcp_custom",
            "-import", binary_path,
            "-overwrite",
            "-scriptPath", str(tmp_script.parent),
            "-postScript", tmp_script.name,
        ]

        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )

        return {
            "ok": True,
            "result": {
                "return_code": completed.returncode,
                "stdout": completed.stdout[-10000:] if completed.stdout else "",
                "stderr": completed.stderr[-5000:] if completed.stderr else "",
            },
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": {
                "category": "timeout_or_resource_limit",
                "code": "ghidra_script_timeout",
                "message": f"Custom Ghidra script timed out after {timeout_seconds}s.",
            },
        }
    finally:
        tmp_script.unlink(missing_ok=True)
