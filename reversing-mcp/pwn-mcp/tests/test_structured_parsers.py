from __future__ import annotations

import struct

from pwn_mcp.tools.coverage import _parse_drcov_log
from pwn_mcp.tools.seccomp import _parse_seccomp_output
from pwn_mcp.tools.tracing import _summarize_strace, _summarize_valgrind


def test_strace_summary_extracts_syscalls_and_errors():
    summary = _summarize_strace(
        "read(0, \"A\", 1) = 1\n"
        "openat(AT_FDCWD, \"/nope\", O_RDONLY) = -1 ENOENT (No such file or directory)\n"
        "read(0, \"B\", 1) = 1\n"
    )
    assert summary["syscall_count"] == 3
    assert summary["top_syscalls"][0] == {"name": "read", "count": 2}
    assert summary["errors"] == [{"errno": "ENOENT", "count": 1}]


def test_valgrind_summary_extracts_error_and_leak_counts():
    summary = _summarize_valgrind(
        "definitely lost: 1,024 bytes in 2 blocks\n"
        "Invalid read of size 8\n"
        "ERROR SUMMARY: 3 errors from 2 contexts\n"
    )
    assert summary["error_count"] == 3
    assert summary["definitely_lost_bytes"] == 1024
    assert summary["invalid_reads"] == 1


def test_seccomp_parser_extracts_syscall_actions():
    parsed = _parse_seccomp_output(
        "if (syscall == read) ALLOW\n"
        "if (syscall == write) ALLOW\n"
        "if (syscall == exit) ALLOW\n"
        "if (syscall == execve) KILL\n"
        "DEFAULT KILL\n"
    )
    assert "read" in parsed["allowed_syscalls"]
    assert "execve" in parsed["blocked_syscalls"]
    assert "no_exec" in parsed["archetypes"]
    assert "strict_io" in parsed["archetypes"]


def test_drcov_parser_extracts_basic_blocks(tmp_path):
    log = tmp_path / "drcov.log"
    header = (
        b"DRCOV VERSION: 2\n"
        b"Module Table: version 2, count 1\n"
        b"Columns: id, containing_id, start, end, entry, checksum, timestamp, path\n"
        b"0, 0, 0x1000, 0x2000, 0x1000, 0, 0, /bin/demo\n"
        b"BB Table: 2 bbs\n"
    )
    blocks = struct.pack("<IHH", 0x10, 4, 0) + struct.pack("<IHH", 0x20, 8, 0)
    log.write_bytes(header + blocks)

    parsed = _parse_drcov_log(log)
    assert parsed["total_blocks"] == 2
    assert parsed["blocks"] == [
        {"module_id": 0, "start": 0x10, "size": 4},
        {"module_id": 0, "start": 0x20, "size": 8},
    ]
