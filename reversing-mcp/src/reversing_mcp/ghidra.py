"""Ghidra integration for reversing-mcp."""
from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import platform
import tempfile
from pathlib import Path
from typing import Any

from .errors import StructuredToolError

LOGGER = logging.getLogger("reversing_mcp.ghidra")

GHIDRA_HEADLESS = Path("/opt/ghidra/support/analyzeHeadless")
GHIDRA_HOME = Path(os.environ.get("REVERSING_MCP_GHIDRA_HOME", "/opt/ghidra"))


def ghidra_available() -> bool:
    return GHIDRA_HEADLESS.exists()


def pyghidra_available() -> bool:
    try:
        import pyghidra  # noqa: F401
    except Exception:
        return False
    return GHIDRA_HOME.exists()


def ghidra_decompiler_available() -> bool:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        os_name = "linux_x86_64"
    elif machine in {"aarch64", "arm64"}:
        os_name = "linux_arm_64"
    else:
        os_name = f"linux_{machine}"
    return (GHIDRA_HOME / "Ghidra" / "Features" / "Decompiler" / "os" / os_name / "decompile").exists()


def _require_pyghidra() -> Any:
    if not ghidra_available():
        raise StructuredToolError(
            "missing_prerequisite",
            "ghidra_not_installed",
            "Ghidra headless is not installed at /opt/ghidra.",
        )
    try:
        import pyghidra
    except Exception as exc:
        raise StructuredToolError(
            "missing_prerequisite",
            "pyghidra_not_installed",
            "PyGhidra is required for Ghidra Python automation in this container.",
        ) from exc
    os.environ.setdefault("GHIDRA_INSTALL_DIR", str(GHIDRA_HOME))
    return pyghidra


def _open_program(binary_path: str, *, analyze: bool, timeout_seconds: int):
    pyghidra = _require_pyghidra()
    # PyGhidra analysis is synchronous and does not expose a direct timeout. The
    # MCP route still accepts timeout_seconds for API compatibility with the old
    # headless-script backend; process-level MCP limits remain the enforcement
    # point for pathological binaries.
    del timeout_seconds
    tmpdir = tempfile.TemporaryDirectory(prefix="ghidra_proj_")
    context = pyghidra.open_program(
        binary_path,
        project_location=tmpdir.name,
        project_name="mcp_project",
        analyze=analyze,
    )
    return tmpdir, context


def _address_to_dict(address) -> tuple[str, int]:
    return str(address), int(address.getOffset())


def _collect_ghidra_analysis(program) -> dict[str, Any]:
    listing = program.getListing()
    memory = program.getMemory()
    symbols = program.getSymbolTable()

    functions = []
    for func in program.getFunctionManager().getFunctions(True):
        entry = func.getEntryPoint()
        body = func.getBody()
        functions.append(
            {
                "name": func.getName(),
                "address": str(entry),
                "address_int": int(entry.getOffset()),
                "size": int(body.getNumAddresses()) if body else 0,
                "is_external": bool(func.isExternal()),
                "is_thunk": bool(func.isThunk()),
                "calling_convention": str(func.getCallingConventionName()) if func.getCallingConventionName() else None,
                "signature": str(func.getSignature()),
            }
        )

    imports = []
    for symbol in symbols.getExternalSymbols():
        imports.append(
            {
                "name": symbol.getName(),
                "address": str(symbol.getAddress()),
                "library": str(symbol.getParentNamespace().getName()) if symbol.getParentNamespace() else None,
            }
        )

    strings = []
    for data in listing.getDefinedData(True):
        data_type = data.getDataType()
        if not data_type:
            continue
        name = data_type.getName().lower()
        if "string" not in name and "char" not in name:
            continue
        with contextlib.suppress(Exception):
            value = data.getValue()
            if value is not None:
                address_text, address_int = _address_to_dict(data.getAddress())
                strings.append({"value": str(value), "address": address_text, "address_int": address_int})

    sections = []
    for block in memory.getBlocks():
        sections.append(
            {
                "name": block.getName(),
                "start": str(block.getStart()),
                "end": str(block.getEnd()),
                "size": int(block.getSize()),
                "permissions": {
                    "read": bool(block.isRead()),
                    "write": bool(block.isWrite()),
                    "execute": bool(block.isExecute()),
                },
            }
        )

    entry_point = None
    for entry_name in ("entry", "_start"):
        with contextlib.suppress(Exception):
            matches = list(symbols.getGlobalSymbols(entry_name))
            if matches:
                entry_point = str(matches[0].getAddress())
                break
    return {
        "program_name": program.getName(),
        "language": str(program.getLanguageID()),
        "compiler": str(program.getCompilerSpec().getCompilerSpecID()),
        "image_base": str(program.getImageBase()),
        "entry_point": entry_point,
        "function_count": len(functions),
        "string_count": len(strings),
        "import_count": len(imports),
        "functions": functions,
        "strings": strings[:5000],
        "imports": imports,
        "sections": sections,
    }


def ghidra_decompile_function(
    binary_path: str,
    function_address: int,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Decompile a function using Ghidra's decompiler."""
    if not ghidra_decompiler_available():
        raise StructuredToolError(
            "missing_prerequisite",
            "ghidra_decompiler_unavailable",
            "Ghidra is installed, but the native decompiler executable for this container architecture is not available.",
            details={"machine": platform.machine(), "ghidra_home": str(GHIDRA_HOME)},
        )
    tmpdir, context = _open_program(binary_path, analyze=True, timeout_seconds=timeout_seconds)
    try:
        with context as api:
            program = api.currentProgram
            address = program.getAddressFactory().getAddress(f"0x{function_address:x}")
            if address is None:
                raise StructuredToolError("invalid_request", "ghidra_invalid_address", f"Invalid Ghidra address 0x{function_address:x}.")
            function = api.getFunctionAt(address) or api.getFunctionContaining(address)
            if function is None:
                raise StructuredToolError("not_found", "ghidra_function_not_found", f"No Ghidra function contains 0x{function_address:x}.")
            from ghidra.app.decompiler import DecompInterface
            from ghidra.util.task import ConsoleTaskMonitor

            decompiler = DecompInterface()
            if not decompiler.openProgram(program):
                raise StructuredToolError(
                    "backend_failure",
                    "ghidra_decompiler_open_failed",
                    "Ghidra decompiler failed to open the program.",
                )
            result = decompiler.decompileFunction(function, int(timeout_seconds), ConsoleTaskMonitor())
            if not result.decompileCompleted():
                raise StructuredToolError(
                    "backend_failure",
                    "ghidra_decompilation_failed",
                    "Ghidra decompilation failed.",
                    details={"message": result.getErrorMessage() or ""},
                )
            source = result.getDecompiledFunction().getC()
            return {
                "function_name": function.getName(),
                "entry_point": str(function.getEntryPoint()),
                "source": source,
                "line_count": len(source.splitlines()) if source else 0,
                "char_count": len(source) if source else 0,
                "warnings": [],
            }
    finally:
        tmpdir.cleanup()


def ghidra_export_analysis(
    binary_path: str,
    output_path: str | None = None,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """Run full Ghidra analysis and export functions, strings, imports."""
    tmpdir, context = _open_program(binary_path, analyze=True, timeout_seconds=timeout_seconds)
    try:
        with context as api:
            result = _collect_ghidra_analysis(api.currentProgram)
    finally:
        tmpdir.cleanup()
    if output_path:
        Path(output_path).write_text(json.dumps({"ok": True, "result": result}, indent=2), encoding="utf-8")
        return {"written_to": output_path, **result}
    return result


def ghidra_run_custom_script(
    binary_path: str,
    script_content: str,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Run a user-provided Python script against a binary through PyGhidra."""
    tmpdir = None
    try:
        tmpdir, context = _open_program(binary_path, analyze=True, timeout_seconds=timeout_seconds)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with context as api, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            namespace = {
                "api": api,
                "program": api.currentProgram,
                "currentProgram": api.currentProgram,
                "monitor": None,
            }
            exec(compile(script_content, "<ghidra_user_script>", "exec"), namespace)
        return {
            "return_code": 0,
            "stdout": stdout.getvalue()[-10000:],
            "stderr": stderr.getvalue()[-5000:],
        }
    except StructuredToolError:
        raise
    except Exception as exc:
        raise StructuredToolError(
            "backend_failure",
            "ghidra_script_failed",
            f"Custom Ghidra script failed: {exc}",
        ) from exc
    finally:
        if tmpdir is not None:
            tmpdir.cleanup()
