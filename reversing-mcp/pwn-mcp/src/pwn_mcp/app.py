from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.types import Tool

from .config import SERVER_NAME, SERVER_VERSION, QEMU_USER_MAP
from .errors import PwnMcpError
from .jobs import JobStore
from .security import WorkspaceSecurity
from .store import SessionStore
from .utils import which_tool


class PwnMcpApp:
    def __init__(
        self,
        workspace_root: Path | None = None,
        output_root: Path | None = None,
        sessions_root: Path | None = None,
    ) -> None:
        self.security = WorkspaceSecurity(
            workspace_root=workspace_root,
            output_root=output_root,
            sessions_root=sessions_root,
        )
        self.sessions = SessionStore(self.security.sessions_root)
        self.jobs = JobStore()
        self._tools: dict[str, dict[str, Any]] = {}
        self._register_tools()

    # ------------------------------------------------------------------
    # Tool registry
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        """Import and register all tool modules."""
        from .tools import (
            process, gdb, tracing, exploit, seccomp, rr, coverage, frida_tools,
            bridge, libc_tools, solver, protocol_fuzz, symbolic, emulation,
            assembly, re_triage, toolchain,
        )

        for module in (
            process, gdb, tracing, exploit, seccomp, rr, coverage, frida_tools,
            bridge, libc_tools, solver, protocol_fuzz, symbolic, emulation,
            assembly, re_triage, toolchain,
        ):
            tools = module.register(self)
            self._tools.update(tools)

        # Built-in job management tools
        self._tools.update(self._job_tools())

    def _job_tools(self) -> dict[str, dict]:
        return {
            "get_job": {
                "handler": self._get_job,
                "schema": Tool(
                    name="get_job",
                    description="Get the status and result of an async job.",
                    inputSchema={
                        "type": "object",
                        "properties": {"job_id": {"type": "string"}},
                        "required": ["job_id"],
                        "additionalProperties": False,
                    },
                ),
            },
            "cancel_job": {
                "handler": self._cancel_job,
                "schema": Tool(
                    name="cancel_job",
                    description="Cancel a running async job.",
                    inputSchema={
                        "type": "object",
                        "properties": {"job_id": {"type": "string"}},
                        "required": ["job_id"],
                        "additionalProperties": False,
                    },
                ),
            },
            "list_jobs": {
                "handler": self._list_jobs,
                "schema": Tool(
                    name="list_jobs",
                    description="List all jobs for a given session.",
                    inputSchema={
                        "type": "object",
                        "properties": {"session_id": {"type": "string"}},
                        "required": ["session_id"],
                        "additionalProperties": False,
                    },
                ),
            },
        }

    def _get_job(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            raise PwnMcpError("not_found", "job_not_found", f"Job '{job_id}' not found.")
        return {"ok": True, "result": job.to_dict()}

    def _cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            raise PwnMcpError("not_found", "job_not_found", f"Job '{job_id}' not found.")
        job.cancel()
        return {"ok": True, "result": {"job_id": job_id, "status": "cancelled"}}

    def _list_jobs(self, session_id: str) -> dict[str, Any]:
        jobs = self.jobs.list_for_session(session_id)
        return {"ok": True, "result": {"jobs": [j.to_dict() for j in jobs]}}

    def tool_definitions(self) -> list[Tool]:
        return [entry["schema"] for entry in self._tools.values()]

    def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        entry = self._tools.get(name)
        if entry is None:
            raise PwnMcpError(
                "invalid_request", "unknown_tool",
                f"Tool '{name}' is not implemented by this server.",
            )
        return entry["handler"](**arguments)

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def get_capabilities(self) -> dict[str, Any]:
        tool_availability = {
            name: which_tool(name) is not None
            for name in [
                "gdb-multiarch", "rr", "strace", "ltrace", "valgrind",
                "uftrace", "frida", "checksec",
                "one_gadget", "ropper", "seccomp-tools",
                "capa", "floss", "yara", "r2", "rabin2", "rasm2", "nasm",
            ]
        }

        return {
            "ok": True,
            "result": {
                "server": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "supported_architectures": list(QEMU_USER_MAP.keys()),
                "tool_count": len(self._tools),
                "tool_names": sorted(self._tools.keys()),
                "tool_availability": tool_availability,
                "workspace_root": str(self.security.workspace_root),
                "output_root": str(self.security.output_root),
            },
        }
