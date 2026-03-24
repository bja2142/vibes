from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import pwd
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_CARVED_BYTE_BUDGET,
    DEFAULT_DECOMPILATION_CHAR_LIMIT,
    DEFAULT_MAX_ARTIFACTS_PER_SESSION,
    DEFAULT_MAX_INPUT_SIZE_BYTES,
    DEFAULT_PARSER_CPU_SECONDS,
    DEFAULT_PARSER_MEMORY_MB,
    DEFAULT_PARSER_TIMEOUT_SECONDS,
    DEFAULT_RECURSION_DEPTH_LIMIT,
    DEFAULT_STRING_COUNT_LIMIT,
    SERVER_NAME,
    SERVER_VERSION,
    get_workspace_root,
)
from .errors import StructuredToolError
from .utils import ensure_dir

SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return max(1, int(raw))


def _package_version(name: str, fallback: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return fallback


@dataclass(slots=True)
class ResourceLimits:
    max_input_size_bytes: int
    max_artifacts_per_session: int
    parser_timeout_seconds: int
    parser_memory_mb: int
    parser_cpu_seconds: int
    recursion_depth_limit: int
    decompilation_char_limit: int
    string_count_limit: int
    carved_byte_budget: int

    @classmethod
    def from_env(cls) -> "ResourceLimits":
        return cls(
            max_input_size_bytes=_env_int("REVERSING_MCP_MAX_INPUT_SIZE_BYTES", DEFAULT_MAX_INPUT_SIZE_BYTES),
            max_artifacts_per_session=_env_int("REVERSING_MCP_MAX_ARTIFACTS_PER_SESSION", DEFAULT_MAX_ARTIFACTS_PER_SESSION),
            parser_timeout_seconds=_env_int("REVERSING_MCP_PARSER_TIMEOUT_SECONDS", DEFAULT_PARSER_TIMEOUT_SECONDS),
            parser_memory_mb=_env_int("REVERSING_MCP_PARSER_MEMORY_MB", DEFAULT_PARSER_MEMORY_MB),
            parser_cpu_seconds=_env_int("REVERSING_MCP_PARSER_CPU_SECONDS", DEFAULT_PARSER_CPU_SECONDS),
            recursion_depth_limit=_env_int("REVERSING_MCP_RECURSION_DEPTH_LIMIT", DEFAULT_RECURSION_DEPTH_LIMIT),
            decompilation_char_limit=_env_int("REVERSING_MCP_DECOMPILATION_CHAR_LIMIT", DEFAULT_DECOMPILATION_CHAR_LIMIT),
            string_count_limit=_env_int("REVERSING_MCP_STRING_COUNT_LIMIT", DEFAULT_STRING_COUNT_LIMIT),
            carved_byte_budget=_env_int("REVERSING_MCP_CARVED_BYTE_BUDGET", DEFAULT_CARVED_BYTE_BUDGET),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkspaceSecurity:
    def __init__(self, workspace_root: Path | None = None, resource_limits: ResourceLimits | None = None) -> None:
        self.workspace_root = ensure_dir((workspace_root or get_workspace_root()).resolve())
        self.resource_limits = resource_limits or ResourceLimits.from_env()

    def resolve_input_file(self, path: str, *, purpose: str) -> Path:
        candidate = self._normalize_user_path(path, code="path_required", message=f"{purpose} path cannot be empty.")
        resolved = candidate.resolve(strict=False)
        self._require_within_workspace(resolved, code="path_outside_workspace", message=f"{purpose} path '{resolved}' escapes the configured workspace root.")
        if not resolved.exists():
            raise StructuredToolError(
                "missing_prerequisite",
                "path_missing",
                f"{purpose} path '{resolved}' does not exist.",
                details={"path": str(resolved), "workspace_root": str(self.workspace_root)},
            )
        if not resolved.is_file():
            raise StructuredToolError(
                "unsupported_format",
                "path_not_regular_file",
                f"{purpose} path '{resolved}' is not a regular file.",
                details={"path": str(resolved)},
            )
        size_bytes = resolved.stat().st_size
        if size_bytes > self.resource_limits.max_input_size_bytes:
            raise StructuredToolError(
                "timeout_or_resource_limit",
                "input_size_limit_exceeded",
                f"{purpose} path '{resolved}' exceeds the configured input size limit.",
                details={
                    "path": str(resolved),
                    "size_bytes": size_bytes,
                    "max_input_size_bytes": self.resource_limits.max_input_size_bytes,
                },
            )
        return resolved

    def resolve_output_file(self, path: str, *, purpose: str, ensure_parent: bool = True) -> Path:
        candidate = self._normalize_user_path(path, code="output_path_required", message=f"{purpose} output path cannot be empty.")
        resolved_parent = candidate.parent.resolve(strict=False)
        self._require_within_workspace(
            resolved_parent,
            code="path_outside_workspace",
            message=f"{purpose} output parent '{resolved_parent}' escapes the configured workspace root.",
        )
        if ensure_parent:
            ensure_dir(resolved_parent)
        if candidate.exists():
            resolved = candidate.resolve(strict=False)
            self._require_within_workspace(
                resolved,
                code="path_outside_workspace",
                message=f"{purpose} output path '{resolved}' escapes the configured workspace root.",
            )
            if resolved.is_dir():
                raise StructuredToolError(
                    "invalid_request",
                    "output_path_is_directory",
                    f"{purpose} output path '{resolved}' points to a directory, not a file.",
                    details={"path": str(resolved)},
                )
            return resolved
        return resolved_parent / candidate.name

    def sanitize_filename(self, unsafe_name: str | None, *, default_stem: str = "artifact") -> dict[str, Any]:
        original = unsafe_name or ""
        normalized = original.replace("\x00", "").replace("\\", "/")
        basename = Path(normalized).name
        safe_name = SAFE_FILENAME_RE.sub("_", basename).strip("._-")
        if not safe_name:
            safe_name = default_stem
        if len(safe_name) > 128:
            safe_name = safe_name[:128].rstrip("._-") or default_stem
        return {
            "original_name": original,
            "safe_name": safe_name,
            "sanitized": safe_name != basename or original != basename,
        }

    def derive_output_file(self, *, subdir: str, unsafe_name: str | None, default_stem: str = "artifact") -> dict[str, Any]:
        subdir_candidate = self._normalize_user_path(subdir, code="output_subdir_required", message="Output subdir cannot be empty.")
        resolved_dir = (self.workspace_root / subdir_candidate).resolve(strict=False)
        self._require_within_workspace(
            resolved_dir,
            code="path_outside_workspace",
            message=f"Derived output directory '{resolved_dir}' escapes the configured workspace root.",
        )
        ensure_dir(resolved_dir)
        name_info = self.sanitize_filename(unsafe_name, default_stem=default_stem)
        output_path = resolved_dir / name_info["safe_name"]
        return {
            "path": str(output_path),
            "relative_path": self._relative_to_workspace(output_path),
            "file_name": name_info["safe_name"],
            "provenance": name_info,
        }

    def validate_artifact_capacity(self, current_count: int) -> None:
        if current_count >= self.resource_limits.max_artifacts_per_session:
            raise StructuredToolError(
                "timeout_or_resource_limit",
                "artifact_count_limit_exceeded",
                "The session has reached the configured artifact-count limit.",
                details={
                    "current_count": current_count,
                    "max_artifacts_per_session": self.resource_limits.max_artifacts_per_session,
                },
            )

    def runtime_policy_report(self) -> dict[str, Any]:
        try:
            user_name = pwd.getpwuid(os.getuid()).pw_name
        except KeyError:  # pragma: no cover - platform fallback
            user_name = str(os.getuid())
        return {
            "workspace_root": str(self.workspace_root),
            "resource_limits": self.resource_limits.to_dict(),
            "sample_containment": {
                "static_only": True,
                "shell_execution_allowed": False,
                "sample_controlled_identifiers_sanitized": True,
                "sample_controlled_filenames_sanitized": True,
            },
            "parser_isolation": {
                "enabled": True,
                "transport": "subprocess",
                "shell": False,
                "user": user_name,
            },
            "tool_versions": {
                "server": SERVER_VERSION,
                "python": platform.python_version(),
                "mcp": _package_version("mcp", "unknown"),
                "platform": platform.platform(),
                "ghidra_headless_available": (self.workspace_root.parent / "opt" / "ghidra" / "support" / "analyzeHeadless").exists() or Path("/opt/ghidra/support/analyzeHeadless").exists(),
            },
        }

    def _normalize_user_path(self, path: str, *, code: str, message: str) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise StructuredToolError("invalid_request", code, message)
        if "\x00" in path:
            raise StructuredToolError("invalid_request", "path_contains_null_byte", "Path inputs may not contain null bytes.")
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        return candidate

    def _require_within_workspace(self, resolved_path: Path, *, code: str, message: str) -> None:
        try:
            resolved_path.relative_to(self.workspace_root)
        except ValueError as exc:
            raise StructuredToolError(
                "invalid_request",
                code,
                message,
                details={"path": str(resolved_path), "workspace_root": str(self.workspace_root)},
            ) from exc

    def _relative_to_workspace(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.workspace_root))
        except ValueError:
            return str(path)


class ParserSandbox:
    def __init__(self, security: WorkspaceSecurity) -> None:
        self.security = security

    def run_probe(self, path: str, *, simulate: str | None = None) -> dict[str, Any]:
        target_path = self.security.resolve_input_file(path, purpose="Parser probe")
        return self._run_worker(
            {
                "operation": "probe_file",
                "path": str(target_path),
                "simulate": simulate,
                "resource_limits": self.security.resource_limits.to_dict(),
            }
        )

    def triage_artifact(self, path: str, *, hints: dict[str, Any] | None = None, string_preview_limit: int = 20) -> dict[str, Any]:
        target_path = self.security.resolve_input_file(path, purpose="Artifact triage")
        return self._run_worker(
            {
                "operation": "triage_file",
                "path": str(target_path),
                "hints": hints or {},
                "string_preview_limit": int(string_preview_limit),
                "resource_limits": self.security.resource_limits.to_dict(),
            }
        )

    def list_strings(
        self,
        path: str,
        *,
        hints: dict[str, Any] | None = None,
        cursor: int = 0,
        limit: int = 50,
        min_length: int = 4,
        encoding: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        target_path = self.security.resolve_input_file(path, purpose="String extraction")
        return self._run_worker(
            {
                "operation": "list_strings",
                "path": str(target_path),
                "hints": hints or {},
                "cursor": int(cursor),
                "limit": int(limit),
                "min_length": int(min_length),
                "encoding": encoding,
                "query": query,
                "resource_limits": self.security.resource_limits.to_dict(),
            }
        )

    def translate_address(
        self,
        path: str,
        *,
        input_kind: str,
        value: int,
        hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_path = self.security.resolve_input_file(path, purpose="Address translation")
        return self._run_worker(
            {
                "operation": "translate_address",
                "path": str(target_path),
                "hints": hints or {},
                "input_kind": input_kind,
                "value": int(value),
                "resource_limits": self.security.resource_limits.to_dict(),
            }
        )

    def list_child_artifacts(
        self,
        path: str,
        *,
        hints: dict[str, Any] | None = None,
        cursor: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        target_path = self.security.resolve_input_file(path, purpose="Child artifact mapping")
        return self._run_worker(
            {
                "operation": "list_children",
                "path": str(target_path),
                "hints": hints or {},
                "cursor": int(cursor),
                "limit": int(limit),
                "resource_limits": self.security.resource_limits.to_dict(),
            }
        )

    def lookup_external_enrichment(
        self,
        path: str,
        *,
        providers: list[str] | None = None,
        opt_in: bool = False,
    ) -> dict[str, Any]:
        target_path = self.security.resolve_input_file(path, purpose="External enrichment lookup")
        return self._run_worker(
            {
                "operation": "lookup_external_enrichment",
                "path": str(target_path),
                "providers": providers or [],
                "opt_in": bool(opt_in),
                "resource_limits": self.security.resource_limits.to_dict(),
            }
        )

    def analyze_program(self, path: str, *, hints: dict[str, Any] | None = None) -> dict[str, Any]:
        target_path = self.security.resolve_input_file(path, purpose="Program analysis")
        return self._run_worker(
            {
                "operation": "analyze_program",
                "path": str(target_path),
                "hints": hints or {},
                "resource_limits": self.security.resource_limits.to_dict(),
            },
            timeout_seconds=max(self.security.resource_limits.parser_timeout_seconds + 1, 121),
        )

    def disassemble_function(
        self,
        path: str,
        *,
        analysis: dict[str, Any],
        function_address: int,
        cursor: int = 0,
        limit: int = 200,
        instruction_mode_override: str | None = None,
    ) -> dict[str, Any]:
        target_path = self.security.resolve_input_file(path, purpose="Function disassembly")
        return self._run_worker(
            {
                "operation": "disassemble_function",
                "path": str(target_path),
                "analysis": analysis,
                "function_address": int(function_address),
                "cursor": int(cursor),
                "limit": int(limit),
                "instruction_mode_override": instruction_mode_override,
                "resource_limits": self.security.resource_limits.to_dict(),
            }
        )

    def disassemble_range(
        self,
        path: str,
        *,
        analysis: dict[str, Any],
        input_kind: str,
        start_value: int,
        size: int,
        cursor: int = 0,
        limit: int = 200,
        instruction_mode_override: str | None = None,
    ) -> dict[str, Any]:
        target_path = self.security.resolve_input_file(path, purpose="Range disassembly")
        return self._run_worker(
            {
                "operation": "disassemble_range",
                "path": str(target_path),
                "analysis": analysis,
                "input_kind": input_kind,
                "start_value": int(start_value),
                "size": int(size),
                "cursor": int(cursor),
                "limit": int(limit),
                "instruction_mode_override": instruction_mode_override,
                "resource_limits": self.security.resource_limits.to_dict(),
            }
        )

    def decompile_function(
        self,
        path: str,
        *,
        function_address: int,
        char_limit: int,
        line_limit: int,
    ) -> dict[str, Any]:
        target_path = self.security.resolve_input_file(path, purpose="Function decompilation")
        return self._run_worker(
            {
                "operation": "decompile_function",
                "path": str(target_path),
                "function_address": int(function_address),
                "char_limit": int(char_limit),
                "line_limit": int(line_limit),
                "resource_limits": self.security.resource_limits.to_dict(),
            }
        )

    def read_bytes(
        self,
        path: str,
        *,
        input_kind: str,
        value: int,
        length: int,
        hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_path = self.security.resolve_input_file(path, purpose="Raw byte inspection")
        return self._run_worker(
            {
                "operation": "read_bytes",
                "path": str(target_path),
                "input_kind": input_kind,
                "value": int(value),
                "length": int(length),
                "hints": hints or {},
                "resource_limits": self.security.resource_limits.to_dict(),
            }
        )

    def search_program(
        self,
        path: str,
        *,
        analysis: dict[str, Any],
        kind: str,
        query: str | None = None,
        start_address: int | None = None,
        end_address: int | None = None,
        cursor: int = 0,
        limit: int = 50,
        case_sensitive: bool = False,
    ) -> dict[str, Any]:
        target_path = self.security.resolve_input_file(path, purpose="Program search")
        return self._run_worker(
            {
                "operation": "search_program",
                "path": str(target_path),
                "analysis": analysis,
                "kind": kind,
                "query": query,
                "start_address": start_address,
                "end_address": end_address,
                "cursor": int(cursor),
                "limit": int(limit),
                "case_sensitive": bool(case_sensitive),
                "resource_limits": self.security.resource_limits.to_dict(),
            }
        )

    def build_analysis_synopsis(self, analysis: dict[str, Any], *, artifact_summary: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._run_worker(
            {
                "operation": "build_analysis_synopsis",
                "path": str(self.security.workspace_root / ".reversing-mcp"),
                "analysis": analysis,
                "artifact_summary": artifact_summary,
                "resource_limits": self.security.resource_limits.to_dict(),
            }
        )

    def _run_worker(self, payload: dict[str, Any], *, timeout_seconds: int | None = None) -> dict[str, Any]:
        payload = {
            **payload,
            "resource_limits": self.security.resource_limits.to_dict(),
        }
        effective_timeout = max(1, int(timeout_seconds or (self.security.resource_limits.parser_timeout_seconds + 1)))
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "reversing_mcp.parser_worker"],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            operation_name = payload.get("operation")
            timeout_message_seconds = max(1, effective_timeout - 1)
            raise StructuredToolError(
                "timeout_or_resource_limit",
                "parser_timeout",
                f"Isolated analysis worker timed out after {timeout_message_seconds} seconds.",
                details={"path": payload.get("path"), "operation": operation_name, "timeout_seconds": timeout_message_seconds},
            ) from exc

        if completed.returncode == 0:
            try:
                return json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                raise StructuredToolError(
                    "backend_failure",
                    "parser_output_invalid",
                    "The isolated analysis worker returned an invalid response payload.",
                    details={"stdout": completed.stdout[:500]},
                ) from exc

        if completed.stdout.strip():
            try:
                parsed = json.loads(completed.stdout)
                if parsed.get("error"):
                    error = parsed["error"]
                    raise StructuredToolError(
                        error.get("category", "backend_failure"),
                        error.get("code", "parser_failed"),
                        error.get("message", "The isolated analysis worker failed."),
                        details=error.get("details", {}),
                    )
            except json.JSONDecodeError:
                pass

        raise StructuredToolError(
            "backend_failure",
            "parser_crashed",
            "The isolated analysis worker crashed before returning a structured response.",
            details={
                "path": payload.get("path"),
                "operation": payload.get("operation"),
                "returncode": completed.returncode,
                "stderr": completed.stderr[:500],
            },
        )
