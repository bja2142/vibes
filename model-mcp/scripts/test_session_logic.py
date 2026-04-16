#!/usr/bin/env python3

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap


def write_fake_cli(path: Path, name: str) -> None:
    if name == "codex":
        body = """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
out_path = args[args.index("--output-last-message") + 1]
prompt = args[-1]
if "second question" in prompt and "first question" in prompt and "first reply" in prompt:
    reply = "history ok"
elif "first question" in prompt and "second question" not in prompt:
    reply = "first reply"
else:
    reply = "generic reply"
pathlib.Path(out_path).write_text(reply, encoding="utf-8")
"""
    else:
        body = """#!/usr/bin/env python3
import sys

prompt = sys.argv[-1]
if "second question" in prompt and "first question" in prompt and "first reply" in prompt:
    print("history ok")
elif "first question" in prompt and "second question" not in prompt:
    print("first reply")
else:
    print("generic reply")
"""
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        for name in ("claude", "codex", "gemini"):
            write_fake_cli(fake_bin / name, name)

        state_dir = tmp_path / "state"
        os.environ["PATH"] = f"{fake_bin}:{os.environ['PATH']}"
        os.environ["MODEL_MCP_STATE_DIR"] = str(state_dir)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

        server = importlib.import_module("model_mcp.server")

        first = server.run_provider_turn("claude", prompt="first question")
        assert first["output"] == "first reply", first
        assert first["step"] == 1, first
        assert first["steps_remaining"] == 9, first
        assert first["session_id"].startswith("claude-"), first
        assert Path(first["session_state_path"]).exists(), first

        export_path = tmp_path / "exports" / "saved-session.md"
        second = server.run_provider_turn(
            "claude",
            prompt="second question",
            session_id=first["session_id"],
            save_path=str(export_path),
        )
        assert second["session_id"] == first["session_id"], second
        assert second["output"] == "history ok", second
        assert second["step"] == 2, second
        assert second["steps_remaining"] == 8, second
        assert second["saved_to"] == str(export_path), second
        assert export_path.exists(), second

        transcript = export_path.read_text(encoding="utf-8")
        assert "first question" in transcript, transcript
        assert "history ok" in transcript, transcript

        session_json = Path(second["session_state_path"]).read_text(encoding="utf-8")
        session = json.loads(session_json)
        assert len(session["turns"]) == 2, session

        rolling = second["session_id"]
        for index in range(3, 11):
            result = server.run_provider_turn(
                "claude",
                prompt=f"turn {index}",
                session_id=rolling,
            )
            assert result["step"] == index, result

        try:
            server.run_provider_turn(
                "claude",
                prompt="turn 11",
                session_id=rolling,
            )
        except RuntimeError as exc:
            assert "10-step limit" in str(exc), exc
        else:
            raise AssertionError("Expected 10-step limit error")

    print("session logic test passed")


if __name__ == "__main__":
    main()
