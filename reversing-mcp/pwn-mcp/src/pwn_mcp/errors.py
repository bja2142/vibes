from __future__ import annotations

from typing import Any


class PwnMcpError(Exception):
    """Base error for all pwn-mcp structured failures."""

    def __init__(
        self,
        category: str,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.code = code
        self.message = message
        self.details = details or {}
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable,
        }


# Common factory helpers

def tool_not_found(tool: str, hint: str = "") -> PwnMcpError:
    msg = f"Required tool '{tool}' was not found in PATH."
    if hint:
        msg += f" {hint}"
    return PwnMcpError("configuration_error", "tool_not_found", msg, details={"tool": tool})


def path_missing(resolved: str, workspace_root: str) -> PwnMcpError:
    return PwnMcpError(
        "missing_prerequisite",
        "path_missing",
        f"Path '{resolved}' does not exist. Workspace root is '{workspace_root}'; "
        "provide a path relative to or within this directory.",
        details={"path": resolved, "workspace_root": workspace_root},
    )


def path_outside_workspace(resolved: str, workspace_root: str) -> PwnMcpError:
    return PwnMcpError(
        "invalid_request",
        "path_outside_workspace",
        f"Path '{resolved}' escapes the configured workspace root '{workspace_root}'.",
        details={"path": resolved, "workspace_root": workspace_root},
    )


def session_not_found(session_id: str) -> PwnMcpError:
    return PwnMcpError(
        "invalid_request",
        "session_not_found",
        f"Session '{session_id}' does not exist or has been destroyed.",
        details={"session_id": session_id},
    )


def process_not_found(process_id: str) -> PwnMcpError:
    return PwnMcpError(
        "invalid_request",
        "process_not_found",
        f"Process '{process_id}' is not tracked in this session.",
        details={"process_id": process_id},
    )


def execution_timeout(timeout_seconds: int) -> PwnMcpError:
    return PwnMcpError(
        "timeout_or_resource_limit",
        "execution_timeout",
        f"Process exceeded the {timeout_seconds}s wall-clock timeout and was killed.",
        details={"timeout_seconds": timeout_seconds},
        retryable=False,
    )


def arch_not_supported(arch: str) -> PwnMcpError:
    return PwnMcpError(
        "invalid_request",
        "arch_not_supported",
        f"Architecture '{arch}' is not supported for dynamic analysis.",
        details={"arch": arch},
    )
