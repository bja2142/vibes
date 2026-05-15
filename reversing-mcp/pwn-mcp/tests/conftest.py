from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from pwn_mcp.app import PwnMcpApp
from pwn_mcp.security import WorkspaceSecurity

TEST_BINARIES = Path(__file__).resolve().parents[1] / "test_binaries"
# Workspace root: use the test_binaries directory itself so binaries resolve within it
TMP_WORKSPACE = TEST_BINARIES
TMP_OUTPUT    = Path("/tmp/pwn-mcp-test-output")
TMP_SESSIONS  = Path("/tmp/pwn-mcp-test-sessions")


def _perf_paranoia() -> int:
    try:
        return int(Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip())
    except Exception:
        return 4  # unknown → assume restricted


@pytest.fixture(scope="session", autouse=True)
def setup_test_dirs():
    for d in (TMP_OUTPUT, TMP_SESSIONS):
        d.mkdir(parents=True, exist_ok=True)


@pytest.fixture(scope="session")
def app():
    return PwnMcpApp(
        workspace_root=TMP_WORKSPACE,
        output_root=TMP_OUTPUT,
        sessions_root=TMP_SESSIONS,
    )


@pytest.fixture
def session(app):
    s = app.sessions.create()
    yield s
    app.sessions.destroy(s.session_id)


def binary(name: str) -> str:
    """Return workspace-relative path to a test binary (just the filename since workspace IS test_binaries dir)."""
    return name


# ── Markers / skip conditions ─────────────────────────────────────────────────

def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long-running integration probes or extended traces")
    config.addinivalue_line("markers", "requires_rr: needs perf_event_paranoia <= 1")
    config.addinivalue_line("markers", "requires_root: needs root/cap_sys_ptrace")


def pytest_collection_modifyitems(items):
    paranoia = _perf_paranoia()
    for item in items:
        if item.get_closest_marker("requires_rr") and paranoia > 1:
            item.add_marker(
                pytest.mark.skip(
                    reason=f"perf_event_paranoid={paranoia} (need <=1 for rr)"
                )
            )
