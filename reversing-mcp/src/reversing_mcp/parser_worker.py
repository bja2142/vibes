from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from .analysis import (
    analyze_program,
    build_analysis_synopsis,
    decompile_function,
    disassemble_function,
    disassemble_range,
    read_bytes,
    search_program,
)
from .triage import (
    analyze_artifact,
    list_child_artifacts,
    list_strings,
    lookup_external_enrichment,
    translate_artifact_address,
)


def _apply_limits(resource_limits: dict[str, Any]) -> None:
    try:
        import resource
    except ImportError:  # pragma: no cover - non-POSIX fallback
        return

    cpu_seconds = int(resource_limits.get("parser_cpu_seconds", 1))
    memory_mb = int(resource_limits.get("parser_memory_mb", 128))
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        address_space = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (address_space, address_space))
    except (ValueError, OSError):  # pragma: no cover - platform-specific failures
        return


def _structured_error(category: str, code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "result": None,
        "error": {
            "category": category,
            "code": code,
            "message": message,
            "details": details or {},
        },
    }


def _probe_file(path: str, simulate: str | None = None) -> dict[str, Any]:
    target = Path(path)
    if simulate == "crash":
        os.kill(os.getpid(), signal.SIGKILL)
    if simulate == "timeout":
        time.sleep(60)
    if simulate == "unsupported":
        return _structured_error(
            "unsupported_format",
            "parser_probe_unsupported",
            "Parser probe rejected the input format.",
            details={"path": str(target)},
        )
    data = target.read_bytes()
    if data.startswith(b"BROKEN"):
        raise ValueError("Malformed sample triggered a parser failure.")
    return {
        "ok": True,
        "result": {
            "path": path,
            "size_bytes": len(data),
            "prefix_hex": data[:16].hex(),
        },
        "error": None,
    }


def main() -> int:
    payload = json.loads(sys.stdin.read())
    _apply_limits(payload.get("resource_limits", {}))
    try:
        operation = payload.get("operation")
        if operation == "probe_file":
            response = _probe_file(payload["path"], simulate=payload.get("simulate"))
        elif operation == "triage_file":
            response = {"ok": True, "result": analyze_artifact(payload["path"], hints=payload.get("hints"), resource_limits=payload.get("resource_limits"), string_preview_limit=payload.get("string_preview_limit", 20)), "error": None}
        elif operation == "list_strings":
            response = {
                "ok": True,
                "result": list_strings(
                    payload["path"],
                    hints=payload.get("hints"),
                    resource_limits=payload.get("resource_limits"),
                    cursor=payload.get("cursor", 0),
                    limit=payload.get("limit", 50),
                    min_length=payload.get("min_length", 4),
                    encoding=payload.get("encoding"),
                    query=payload.get("query"),
                ),
                "error": None,
            }
        elif operation == "translate_address":
            response = {
                "ok": True,
                "result": translate_artifact_address(
                    payload["path"],
                    hints=payload.get("hints"),
                    resource_limits=payload.get("resource_limits"),
                    input_kind=payload["input_kind"],
                    value=payload["value"],
                ),
                "error": None,
            }
        elif operation == "list_children":
            response = {
                "ok": True,
                "result": list_child_artifacts(
                    payload["path"],
                    hints=payload.get("hints"),
                    resource_limits=payload.get("resource_limits"),
                    cursor=payload.get("cursor", 0),
                    limit=payload.get("limit", 50),
                ),
                "error": None,
            }
        elif operation == "lookup_external_enrichment":
            response = {
                "ok": True,
                "result": lookup_external_enrichment(
                    payload["path"],
                    providers=payload.get("providers"),
                    opt_in=payload.get("opt_in", False),
                ),
                "error": None,
            }
        elif operation == "analyze_program":
            response = {
                "ok": True,
                "result": analyze_program(
                    payload["path"],
                    hints=payload.get("hints"),
                    resource_limits=payload.get("resource_limits"),
                ),
                "error": None,
            }
        elif operation == "disassemble_function":
            response = {
                "ok": True,
                "result": disassemble_function(
                    payload["path"],
                    analysis=payload["analysis"],
                    function_address=payload["function_address"],
                    cursor=payload.get("cursor", 0),
                    limit=payload.get("limit", 200),
                    instruction_mode_override=payload.get("instruction_mode_override"),
                ),
                "error": None,
            }
        elif operation == "disassemble_range":
            response = {
                "ok": True,
                "result": disassemble_range(
                    payload["path"],
                    analysis=payload["analysis"],
                    input_kind=payload["input_kind"],
                    start_value=payload["start_value"],
                    size=payload["size"],
                    cursor=payload.get("cursor", 0),
                    limit=payload.get("limit", 200),
                    instruction_mode_override=payload.get("instruction_mode_override"),
                ),
                "error": None,
            }
        elif operation == "decompile_function":
            response = {
                "ok": True,
                "result": decompile_function(
                    payload["path"],
                    function_address=payload["function_address"],
                    char_limit=payload.get("char_limit", 12000),
                    line_limit=payload.get("line_limit", 200),
                ),
                "error": None,
            }
        elif operation == "read_bytes":
            response = {
                "ok": True,
                "result": read_bytes(
                    payload["path"],
                    input_kind=payload["input_kind"],
                    value=payload["value"],
                    length=payload["length"],
                    hints=payload.get("hints"),
                ),
                "error": None,
            }
        elif operation == "search_program":
            response = {
                "ok": True,
                "result": search_program(
                    payload["path"],
                    analysis=payload["analysis"],
                    kind=payload["kind"],
                    query=payload.get("query"),
                    start_address=payload.get("start_address"),
                    end_address=payload.get("end_address"),
                    cursor=payload.get("cursor", 0),
                    limit=payload.get("limit", 50),
                    case_sensitive=payload.get("case_sensitive", False),
                ),
                "error": None,
            }
        elif operation == "build_analysis_synopsis":
            response = {
                "ok": True,
                "result": build_analysis_synopsis(
                    payload["analysis"],
                    artifact_summary=payload.get("artifact_summary"),
                ),
                "error": None,
            }
        else:
            print(
                json.dumps(
                    _structured_error(
                        "invalid_request",
                        "parser_worker_operation_invalid",
                        "Unsupported parser worker operation.",
                        details={"operation": operation},
                    )
                )
            )
            return 2
        print(json.dumps(response))
        return 0 if response.get("ok") else 2
    except Exception as exc:
        print(
            json.dumps(
                _structured_error(
                    "backend_failure",
                    "parser_worker_failure",
                    str(exc),
                    details={"path": payload.get("path")},
                )
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
