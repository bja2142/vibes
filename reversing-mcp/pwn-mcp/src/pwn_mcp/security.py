from __future__ import annotations

from pathlib import Path

from .config import get_workspace_root, get_output_root, get_sessions_root, DEFAULT_EXEC_TIMEOUT_SECONDS
from .errors import path_missing, path_outside_workspace, PwnMcpError
from .utils import ensure_dir


class WorkspaceSecurity:
    def __init__(
        self,
        workspace_root: Path | None = None,
        output_root: Path | None = None,
        sessions_root: Path | None = None,
    ) -> None:
        self.workspace_root = (workspace_root or get_workspace_root()).resolve()
        self.output_root = (output_root or get_output_root()).resolve()
        self.sessions_root = (sessions_root or get_sessions_root()).resolve()
        ensure_dir(self.output_root)
        ensure_dir(self.sessions_root)

    def resolve_binary(self, path: str) -> Path:
        """
        Resolve a user-supplied binary path to an absolute path within the workspace.
        Read-only — does not create anything.
        """
        candidate = self._normalize(path)
        resolved = candidate.resolve()
        self._require_within(resolved, self.workspace_root, "Binary")
        if not resolved.exists():
            raise path_missing(str(resolved), str(self.workspace_root))
        if not resolved.is_file():
            raise PwnMcpError(
                "invalid_request", "path_not_regular_file",
                f"Binary path '{resolved}' is not a regular file.",
                details={"path": str(resolved)},
            )
        return resolved

    def session_dir(self, session_id: str) -> Path:
        """Return (and create) the per-session working directory under sessions_root."""
        d = self.sessions_root / session_id
        ensure_dir(d)
        return d

    def output_dir(self, session_id: str, subdir: str = "") -> Path:
        """Return (and create) a per-session output directory under output_root."""
        d = self.output_root / session_id
        if subdir:
            d = d / subdir
        ensure_dir(d)
        return d

    def resolve_output_file(self, session_id: str, filename: str) -> Path:
        """Return a safe output file path under the session output dir."""
        d = self.output_dir(session_id)
        # Strip any directory components from filename to prevent traversal
        safe_name = Path(filename).name
        return d / safe_name

    def _normalize(self, path: str) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise PwnMcpError("invalid_request", "path_required", "Path cannot be empty.")
        if "\x00" in path:
            raise PwnMcpError("invalid_request", "path_null_byte", "Path may not contain null bytes.")
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        return candidate

    def _require_within(self, resolved: Path, root: Path, label: str) -> None:
        try:
            resolved.relative_to(root)
        except ValueError:
            raise path_outside_workspace(str(resolved), str(root))
