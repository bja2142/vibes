from __future__ import annotations

import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import session_not_found, process_not_found, PwnMcpError
from .config import DEFAULT_MAX_SESSIONS


@dataclass
class ProcessHandle:
    process_id: str
    pid: int
    arch: str
    binary_path: str
    args: list[str]
    proc: subprocess.Popen
    start_time: float
    stdout_buf: bytearray = field(default_factory=bytearray)
    stderr_buf: bytearray = field(default_factory=bytearray)
    _lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class DebugSession:
    debug_id: str
    session_id: str
    binary_path: str
    framework: str          # gef | pwndbg | vanilla
    gdb_proc: subprocess.Popen | None = None
    rr_proc: subprocess.Popen | None = None
    recording_path: str | None = None
    gdb_port: int | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class FridaSession:
    frida_id: str
    session_id: str
    target: str             # pid or path
    device: Any = None      # frida.Device
    session: Any = None     # frida.Session
    scripts: dict[str, Any] = field(default_factory=dict)


@dataclass
class Session:
    session_id: str
    created_at: float
    arch: str | None
    session_dir: Path
    processes: dict[str, ProcessHandle] = field(default_factory=dict)
    debug_sessions: dict[str, DebugSession] = field(default_factory=dict)
    frida_sessions: dict[str, FridaSession] = field(default_factory=dict)
    recordings: dict[str, str] = field(default_factory=dict)  # recording_id → path
    snapshots: dict[str, str] = field(default_factory=dict)   # name → path
    _lock: threading.Lock = field(default_factory=threading.Lock)


class SessionStore:
    def __init__(self, sessions_root: Path, max_sessions: int = DEFAULT_MAX_SESSIONS) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._sessions_root = sessions_root
        self._max_sessions = max_sessions

    def create(self, arch: str | None = None) -> Session:
        with self._lock:
            if len(self._sessions) >= self._max_sessions:
                raise PwnMcpError(
                    "timeout_or_resource_limit",
                    "session_limit_exceeded",
                    f"Maximum number of concurrent sessions ({self._max_sessions}) reached.",
                )
            session_id = f"sess_{uuid.uuid4().hex[:16]}"
            session_dir = self._sessions_root / session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            import time
            session = Session(
                session_id=session_id,
                created_at=time.time(),
                arch=arch,
                session_dir=session_dir,
            )
            self._sessions[session_id] = session
            return session

    def get(self, session_id: str) -> Session:
        with self._lock:
            s = self._sessions.get(session_id)
        if s is None:
            raise session_not_found(session_id)
        return s

    def destroy(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return
        # Kill any live processes
        for ph in list(session.processes.values()):
            try:
                ph.proc.kill()
                ph.proc.wait(timeout=2)
            except Exception:
                pass
        # Kill any live debug sessions
        for ds in list(session.debug_sessions.values()):
            for proc in (ds.gdb_proc, ds.rr_proc):
                if proc is not None:
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except Exception:
                        pass
        # Clean up session directory
        try:
            shutil.rmtree(session.session_dir, ignore_errors=True)
        except Exception:
            pass

    def list_all(self) -> list[dict[str, Any]]:
        import time
        with self._lock:
            sessions = list(self._sessions.values())
        return [
            {
                "session_id": s.session_id,
                "created_at": s.created_at,
                "arch": s.arch,
                "process_count": len(s.processes),
                "debug_session_count": len(s.debug_sessions),
            }
            for s in sessions
        ]
