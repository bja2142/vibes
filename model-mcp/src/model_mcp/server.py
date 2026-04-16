from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
import json
import logging
import math
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
import uuid
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

DEFAULT_CODEX_MODEL = "gpt-5.4"
DEFAULT_CLAUDE_MODEL = "claude-opus-4-6"
DEFAULT_GEMINI_MODEL = "gemini-3.1-pro-preview"
DEFAULT_PORT = 7777
MIN_TIMEOUT_SECONDS = 240
MAX_SESSION_STEPS = 10
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRESET_ROOT = PROJECT_ROOT / "presets"
STATE_ROOT = Path(os.getenv("MODEL_MCP_STATE_DIR", PROJECT_ROOT / ".model_mcp"))
SESSION_ROOT = STATE_ROOT / "sessions"
EXPORT_ROOT = STATE_ROOT / "exports"
JOB_ROOT = STATE_ROOT / "jobs"
PROVIDER_DEFAULT_MODELS = {
    "codex": DEFAULT_CODEX_MODEL,
    "claude": DEFAULT_CLAUDE_MODEL,
    "gemini": DEFAULT_GEMINI_MODEL,
}
VALID_PRESET_MODES = {"single-model", "cross-model"}
PRESET_REGISTRY_CACHE: dict[str, Any] | None = None
APP_LOGGER = logging.getLogger("model_mcp")
VERBOSE_TOOL_LOGGING = os.getenv("MODEL_MCP_VERBOSE_TOOL_LOGGING", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
JOB_EXECUTOR = ThreadPoolExecutor(
    max_workers=int(os.getenv("MODEL_MCP_JOB_WORKERS", "3")),
    thread_name_prefix="model-mcp-job",
)
JOB_FUTURES: dict[str, Future[Any]] = {}
JOB_LOCK = threading.Lock()
TIMEOUT_PROFILES = {
    "codex": {"base_seconds": 90, "seconds_per_token": 0.50, "max_seconds": 900},
    "claude": {"base_seconds": 90, "seconds_per_token": 0.25, "max_seconds": 900},
    "gemini": {"base_seconds": 60, "seconds_per_token": 0.15, "max_seconds": 600},
}


def _append_input(prompt: str, input_text: str | None) -> str:
    if not input_text:
        return prompt
    return f"{prompt}\n\n<relay_input>\n{input_text}\n</relay_input>"


def configure_logging(*, log_level: str, log_file: str | None = None) -> None:
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        "%Y-%m-%dT%H:%M:%S",
    )
    if log_file:
        log_path = Path(log_file).expanduser()
        if not log_path.is_absolute():
            log_path = PROJECT_ROOT / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.FileHandler(log_path, encoding="utf-8")
    else:
        handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "model_mcp"):
        logger = logging.getLogger(logger_name)
        logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        logger.propagate = True


def _preview_text(text: str | None, *, limit: int = 180) -> str:
    if not text:
        return ""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _estimate_text_tokens(*parts: str | None) -> int:
    total_chars = sum(len(part or "") for part in parts if part)
    return max(1, math.ceil(total_chars / 4))


def _get_timeout(provider: str, token_len: int) -> int:
    profile = TIMEOUT_PROFILES[provider]
    raw_seconds = profile["base_seconds"] + (profile["seconds_per_token"] * token_len)
    rounded_seconds = int(math.ceil(raw_seconds / 30.0) * 30)
    return max(
        MIN_TIMEOUT_SECONDS,
        min(int(profile["max_seconds"]), rounded_seconds),
    )


def _resolve_timeout_seconds(
    provider: str,
    *,
    prompt: str,
    timeout_seconds: int | None,
) -> int:
    if timeout_seconds is not None:
        return timeout_seconds
    token_len = _estimate_text_tokens(prompt)
    return _get_timeout(provider, token_len)


def _display_command(command: list[str]) -> str:
    redacted = list(command)
    if not redacted:
        return ""
    if redacted[0] == "gemini" and "-p" in redacted:
        prompt_index = redacted.index("-p") + 1
        if prompt_index < len(redacted):
            redacted[prompt_index] = f"<prompt len={len(redacted[prompt_index])}>"
    elif redacted[0] == "claude" and redacted[-1] and not redacted[-1].startswith("-"):
        redacted[-1] = f"<prompt len={len(redacted[-1])}>"
    elif redacted[0] == "codex" and redacted[-1] and not redacted[-1].startswith("-"):
        redacted[-1] = f"<prompt len={len(redacted[-1])}>"
    return shlex.join(redacted)


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _ensure_state_dirs() -> None:
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    JOB_ROOT.mkdir(parents=True, exist_ok=True)


def _parse_preset_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").strip()
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        raise RuntimeError(f"Preset file is missing front matter: {path}")

    frontmatter_lines: list[str] = []
    body_start = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            body_start = index + 1
            break
        frontmatter_lines.append(lines[index])
    if body_start is None:
        raise RuntimeError(f"Preset file has unterminated front matter: {path}")

    frontmatter: dict[str, str] = {}
    for raw_line in frontmatter_lines:
        line = raw_line.strip()
        if not line:
            continue
        if ":" not in line:
            raise RuntimeError(f"Invalid front matter line in {path}: {raw_line}")
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip()

    required_keys = {"name", "category", "mode", "summary"}
    missing = sorted(required_keys.difference(frontmatter))
    if missing:
        raise RuntimeError(f"Preset file {path} is missing keys: {missing}")
    if frontmatter["mode"] not in VALID_PRESET_MODES:
        raise RuntimeError(f"Preset file {path} has invalid mode: {frontmatter['mode']}")

    sections: dict[str, str] = {}
    current_section: str | None = None
    buffer: list[str] = []
    for line in lines[body_start:]:
        if line.startswith("# "):
            if current_section is not None:
                sections[current_section] = "\n".join(buffer).strip()
            current_section = line[2:].strip().lower().replace(" ", "_")
            buffer = []
        else:
            buffer.append(line)
    if current_section is not None:
        sections[current_section] = "\n".join(buffer).strip()

    for required_section in ("goal", "when_to_use", "prompt", "output_expectations"):
        if not sections.get(required_section):
            raise RuntimeError(f"Preset file {path} is missing section: {required_section}")

    return {
        **frontmatter,
        **sections,
        "path": str(path),
    }


def _preset_registry_signature() -> tuple[tuple[str, int, int], ...]:
    if not PRESET_ROOT.exists():
        return ()
    items: list[tuple[str, int, int]] = []
    for path in sorted(PRESET_ROOT.glob("*/*.md")):
        if path.name.lower() == "readme.md":
            continue
        stat = path.stat()
        items.append((str(path), int(stat.st_mtime_ns), stat.st_size))
    return tuple(items)


def _build_preset_registry() -> dict[str, Any]:
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    for path_text, _mtime_ns, _size in _preset_registry_signature():
        path = Path(path_text)
        try:
            preset = _parse_preset_file(path)
            valid.append(preset)
        except Exception as exc:
            invalid.append(
                {
                    "path": str(path),
                    "error": str(exc),
                }
            )
    valid.sort(key=lambda item: item["name"])
    return {
        "signature": _preset_registry_signature(),
        "loaded_at": _utc_timestamp(),
        "valid": valid,
        "invalid": invalid,
    }


def _get_preset_registry(*, force_reload: bool = False) -> dict[str, Any]:
    global PRESET_REGISTRY_CACHE
    signature = _preset_registry_signature()
    if (
        force_reload
        or PRESET_REGISTRY_CACHE is None
        or PRESET_REGISTRY_CACHE.get("signature") != signature
    ):
        PRESET_REGISTRY_CACHE = _build_preset_registry()
    return PRESET_REGISTRY_CACHE


def _load_presets() -> list[dict[str, Any]]:
    return list(_get_preset_registry()["valid"])


def _load_preset(name: str) -> dict[str, Any]:
    for preset in _load_presets():
        if preset["name"] == name:
            return preset
    raise RuntimeError(f"Unknown preset: {name}")


def validate_presets(*, force_reload: bool = False) -> dict[str, Any]:
    registry = _get_preset_registry(force_reload=force_reload)
    return {
        "loaded_at": registry["loaded_at"],
        "valid_count": len(registry["valid"]),
        "invalid_count": len(registry["invalid"]),
        "valid": [
            {
                "name": preset["name"],
                "category": preset["category"],
                "mode": preset["mode"],
                "path": preset["path"],
            }
            for preset in registry["valid"]
        ],
        "invalid": list(registry["invalid"]),
    }


def reload_presets() -> dict[str, Any]:
    return validate_presets(force_reload=True)


def _session_summary(session: dict[str, Any]) -> dict[str, Any]:
    turns = session.get("turns", [])
    last_output_preview = ""
    if turns:
        last_output_preview = str(turns[-1].get("output", ""))[:200]
    return {
        "session_id": session["session_id"],
        "provider": session["provider"],
        "model": session["model"],
        "label": session.get("label"),
        "notes": session.get("notes"),
        "metadata": session.get("metadata") or {},
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "step_count": len(turns),
        "steps_remaining": MAX_SESSION_STEPS - len(turns),
        "last_output_preview": last_output_preview,
    }


def _job_path(job_id: str) -> Path:
    return JOB_ROOT / f"{job_id}.json"


def _write_job(job: dict[str, Any]) -> Path:
    _ensure_state_dirs()
    job["updated_at"] = _utc_timestamp()
    path = _job_path(job["job_id"])
    with path.open("w", encoding="utf-8") as handle:
        json.dump(job, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    return path


def _load_job(job_id: str) -> dict[str, Any]:
    path = _job_path(job_id)
    if not path.exists():
        raise RuntimeError(f"Unknown job_id: {job_id}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _job_summary(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job["job_id"],
        "job_type": job["job_type"],
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "duration_ms": job.get("duration_ms"),
        "request_summary": job.get("request_summary") or {},
        "error": job.get("error"),
    }


def _session_path(session_id: str) -> Path:
    return SESSION_ROOT / f"{session_id}.json"


def _resolve_export_path(path_text: str) -> Path:
    requested = Path(path_text).expanduser()
    if requested.is_absolute():
        return requested
    return PROJECT_ROOT / requested


def _create_session(provider: str, model: str) -> dict[str, Any]:
    session_id = f"{provider}-{uuid.uuid4().hex[:12]}"
    now = _utc_timestamp()
    return {
        "session_id": session_id,
        "provider": provider,
        "model": model,
        "label": None,
        "notes": None,
        "metadata": {},
        "created_at": now,
        "updated_at": now,
        "turns": [],
    }


def _load_session(session_id: str) -> dict[str, Any]:
    path = _session_path(session_id)
    if not path.exists():
        raise RuntimeError(f"Unknown session_id: {session_id}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_session(session: dict[str, Any]) -> Path:
    _ensure_state_dirs()
    session["updated_at"] = _utc_timestamp()
    path = _session_path(session["session_id"])
    with path.open("w", encoding="utf-8") as handle:
        json.dump(session, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    return path


def _render_turns_markdown(session: dict[str, Any]) -> str:
    lines = [
        f"# Session {session['session_id']}",
        "",
        f"- Provider: `{session['provider']}`",
        f"- Default model: `{session['model']}`",
        f"- Label: `{session.get('label')}`" if session.get("label") else "- Label: `(none)`",
        f"- Created: `{session['created_at']}`",
        f"- Updated: `{session['updated_at']}`",
        f"- Steps: `{len(session['turns'])}` / `{MAX_SESSION_STEPS}`",
        "",
    ]
    if session.get("notes"):
        lines.append("## Notes")
        lines.append("")
        lines.append(session["notes"])
        lines.append("")
    for turn in session["turns"]:
        meta = turn.get("meta") or {}
        lines.append(f"## Step {turn['step']}")
        lines.append("")
        lines.append(f"**User Prompt** ({turn['timestamp']})")
        lines.append("")
        if meta.get("preset_name"):
            lines.append(f"_Preset: `{meta['preset_name']}`_")
            lines.append("")
        if meta.get("source_session_id"):
            lines.append(f"_Source Session: `{meta['source_session_id']}`_")
            lines.append("")
        lines.append(turn["prompt"])
        lines.append("")
        if turn.get("input_text"):
            lines.append("**Attached Input**")
            lines.append("")
            lines.append("```text")
            lines.append(turn["input_text"])
            lines.append("```")
            lines.append("")
        lines.append(f"**Assistant Output** using `{turn['model']}`")
        lines.append("")
        lines.append("```text")
        lines.append(turn["output"])
        lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _export_session(session: dict[str, Any], save_path: str | None) -> str:
    _ensure_state_dirs()
    path = (
        _resolve_export_path(save_path)
        if save_path
        else EXPORT_ROOT / f"{session['session_id']}.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".json":
        with path.open("w", encoding="utf-8") as handle:
            json.dump(session, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
    else:
        with path.open("w", encoding="utf-8") as handle:
            handle.write(_render_turns_markdown(session))
    return str(path)


def _build_conversation_prompt(
    prompt: str,
    *,
    input_text: str | None,
    session: dict[str, Any] | None,
) -> str:
    current_message = _append_input(prompt, input_text)
    if not session or not session["turns"]:
        return current_message

    history_lines = [
        "You are continuing an existing conversation.",
        "Use the previous turns as context and answer the newest user message directly.",
        "",
        "<conversation_history>",
    ]
    for turn in session["turns"]:
        meta = turn.get("meta") or {}
        history_lines.append(f"Step {turn['step']} user:")
        if meta.get("preset_name"):
            history_lines.append(f"[preset={meta['preset_name']}]")
        if meta.get("source_session_id"):
            history_lines.append(f"[source_session_id={meta['source_session_id']}]")
        history_lines.append(turn["prompt"])
        if turn.get("input_text"):
            history_lines.append("<relay_input>")
            history_lines.append(turn["input_text"])
            history_lines.append("</relay_input>")
        history_lines.append("")
        history_lines.append(f"Step {turn['step']} assistant:")
        history_lines.append(turn["output"])
        history_lines.append("")
    history_lines.append("</conversation_history>")
    history_lines.append("")
    history_lines.append("<current_user_message>")
    history_lines.append(current_message)
    history_lines.append("</current_user_message>")
    return "\n".join(history_lines)


def _tagged_block(tag: str, content: str | None) -> str:
    if not content:
        return ""
    return f"<{tag}>\n{content.strip()}\n</{tag}>"


def _compose_preset_prompt(
    preset: dict[str, Any],
    prompt: str,
    *,
    input_text: str | None = None,
    extra_blocks: list[tuple[str, str]] | None = None,
) -> str:
    parts = [
        _tagged_block("preset_name", preset["name"]),
        _tagged_block("preset_category", preset["category"]),
        _tagged_block("preset_goal", preset["goal"]),
        _tagged_block("preset_instructions", preset["prompt"]),
        _tagged_block("preset_output_expectations", preset["output_expectations"]),
        _tagged_block("task_prompt", prompt),
    ]
    if input_text:
        parts.append(_tagged_block("task_input", input_text))
    for tag, content in extra_blocks or []:
        if content:
            parts.append(_tagged_block(tag, content))
    return "\n\n".join(part for part in parts if part)


def _require_command(binary: str) -> str:
    resolved = shutil.which(binary)
    if not resolved:
        raise RuntimeError(f"Required command not found on PATH: {binary}")
    return resolved


def _run_command(
    command: list[str],
    *,
    timeout_seconds: int,
    stdout_mode: str = "capture",
    stdin_mode: str = "inherit",
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    started = time.monotonic()
    APP_LOGGER.info(
        "command.start timeout=%ss stdout_mode=%s stdin_mode=%s command=%s",
        timeout_seconds,
        stdout_mode,
        stdin_mode,
        _display_command(command),
    )
    kwargs: dict[str, Any] = {
        "text": True,
        "stderr": subprocess.PIPE,
        "timeout": timeout_seconds,
        "check": False,
    }
    if stdin_mode == "devnull":
        kwargs["stdin"] = subprocess.DEVNULL
    elif stdin_mode != "inherit":
        raise RuntimeError(f"Unsupported stdin_mode: {stdin_mode}")
    if stdout_mode == "discard":
        kwargs["stdout"] = subprocess.DEVNULL
    else:
        kwargs["stdout"] = subprocess.PIPE
    if env_overrides:
        env = os.environ.copy()
        env.update(env_overrides)
        kwargs["env"] = env
    try:
        result = subprocess.run(command, **kwargs)
    except subprocess.TimeoutExpired as exc:
        APP_LOGGER.error(
            "command.timeout duration_ms=%s command=%s",
            round((time.monotonic() - started) * 1000),
            _display_command(command),
        )
        raise RuntimeError(
            f"Command timed out after {timeout_seconds}s: {' '.join(command)}"
        ) from exc
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or f"exit code {result.returncode}"
        APP_LOGGER.error(
            "command.error duration_ms=%s returncode=%s command=%s detail=%s",
            round((time.monotonic() - started) * 1000),
            result.returncode,
            _display_command(command),
            _preview_text(detail, limit=300),
        )
        raise RuntimeError(f"Command failed: {detail}")
    APP_LOGGER.info(
        "command.ok duration_ms=%s returncode=%s command=%s stdout_preview=%s",
        round((time.monotonic() - started) * 1000),
        result.returncode,
        _display_command(command),
        _preview_text(result.stdout),
    )
    return result


def run_codex(
    prompt: str,
    *,
    model: str | None = None,
    timeout_seconds: int,
) -> dict[str, Any]:
    _require_command("codex")
    chosen_model = model or DEFAULT_CODEX_MODEL
    started = time.monotonic()
    with tempfile.NamedTemporaryFile(prefix="codex-last-", suffix=".txt", delete=False) as tmp:
        out_path = tmp.name
    try:
        command = [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--model",
            chosen_model,
            "--output-last-message",
            out_path,
            prompt,
        ]
        _run_command(command, timeout_seconds=timeout_seconds, stdout_mode="discard")
        with open(out_path, "r", encoding="utf-8") as handle:
            output = handle.read().strip()
    finally:
        try:
            os.unlink(out_path)
        except FileNotFoundError:
            pass
    return {
        "provider": "codex",
        "model": chosen_model,
        "output": output,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }


def run_claude(
    prompt: str,
    *,
    model: str | None = None,
    timeout_seconds: int,
) -> dict[str, Any]:
    _require_command("claude")
    chosen_model = model or DEFAULT_CLAUDE_MODEL
    started = time.monotonic()
    command = [
        "claude",
        "-p",
        "--output-format",
        "text",
        "--permission-mode",
        "plan",
        "--model",
        chosen_model,
        prompt,
    ]
    result = _run_command(command, timeout_seconds=timeout_seconds)
    return {
        "provider": "claude",
        "model": chosen_model,
        "output": (result.stdout or "").strip(),
        "duration_ms": round((time.monotonic() - started) * 1000),
    }


def run_gemini(
    prompt: str,
    *,
    model: str | None = None,
    timeout_seconds: int,
) -> dict[str, Any]:
    _require_command("gemini")
    chosen_model = model or DEFAULT_GEMINI_MODEL
    started = time.monotonic()
    command = [
        "gemini",
        "-p",
        prompt,
        "--approval-mode",
        "plan",
        "--output-format",
        "text",
        "--model",
        chosen_model,
    ]
    result = _run_command(
        command,
        timeout_seconds=timeout_seconds,
        stdin_mode="devnull",
        env_overrides={"CI": "1"},
    )
    return {
        "provider": "gemini",
        "model": chosen_model,
        "output": (result.stdout or "").strip(),
        "duration_ms": round((time.monotonic() - started) * 1000),
    }


def run_provider_turn(
    provider: str,
    *,
    prompt: str,
    input_text: str | None = None,
    stored_input_text: str | None = None,
    model: str | None = None,
    timeout_seconds: int | None = None,
    session_id: str | None = None,
    save_session: bool = False,
    save_path: str | None = None,
    display_prompt: str | None = None,
    turn_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if provider not in PROVIDER_DEFAULT_MODELS:
        raise RuntimeError(f"Unsupported provider: {provider}")

    _ensure_state_dirs()
    started = time.monotonic()
    session = _load_session(session_id) if session_id else None
    if session and session["provider"] != provider:
        raise RuntimeError(
            f"Session {session_id} belongs to provider {session['provider']}, not {provider}"
        )
    chosen_model = model or (session["model"] if session else PROVIDER_DEFAULT_MODELS[provider])
    if session is None:
        session = _create_session(provider, chosen_model)
    if len(session["turns"]) >= MAX_SESSION_STEPS:
        raise RuntimeError(
            f"Session {session['session_id']} has reached the {MAX_SESSION_STEPS}-step limit"
        )
    APP_LOGGER.info(
        "turn.start provider=%s session_id=%s model=%s step=%s save_session=%s",
        provider,
        session["session_id"],
        chosen_model,
        len(session["turns"]) + 1,
        save_session or bool(save_path),
    )
    if VERBOSE_TOOL_LOGGING:
        APP_LOGGER.info(
            "turn.context provider=%s session_id=%s prompt_preview=%s input_preview=%s meta=%s",
            provider,
            session["session_id"],
            _preview_text(display_prompt or prompt),
            _preview_text(stored_input_text if stored_input_text is not None else input_text),
            json.dumps(turn_meta or {}, ensure_ascii=True, sort_keys=True),
        )

    full_prompt = _build_conversation_prompt(
        prompt,
        input_text=input_text,
        session=session,
    )

    resolved_timeout_seconds = _resolve_timeout_seconds(
        provider,
        prompt=full_prompt,
        timeout_seconds=timeout_seconds,
    )
    APP_LOGGER.info(
        "turn.timeout provider=%s session_id=%s token_len=%s timeout_seconds=%s",
        provider,
        session["session_id"],
        _estimate_text_tokens(full_prompt),
        resolved_timeout_seconds,
    )

    runners = {
        "codex": run_codex,
        "claude": run_claude,
        "gemini": run_gemini,
    }
    result = runners[provider](
        full_prompt,
        model=chosen_model,
        timeout_seconds=resolved_timeout_seconds,
    )

    session["model"] = chosen_model
    turn = {
        "step": len(session["turns"]) + 1,
        "timestamp": _utc_timestamp(),
        "prompt": display_prompt or prompt,
        "input_text": stored_input_text if stored_input_text is not None else input_text,
        "model": chosen_model,
        "output": result["output"],
        "duration_ms": result["duration_ms"],
    }
    if turn_meta:
        turn["meta"] = turn_meta
    session["turns"].append(turn)
    session_path = _write_session(session)
    exported_to = None
    if save_session or save_path:
        exported_to = _export_session(session, save_path)

    payload = {
        **result,
        "session_id": session["session_id"],
        "session_name": session["session_id"],
        "step": turn["step"],
        "steps_remaining": MAX_SESSION_STEPS - len(session["turns"]),
        "session_state_path": str(session_path),
        "saved_to": exported_to,
        "timeout_seconds_used": resolved_timeout_seconds,
    }
    APP_LOGGER.info(
        "turn.ok provider=%s session_id=%s step=%s duration_ms=%s output_preview=%s",
        provider,
        session["session_id"],
        turn["step"],
        round((time.monotonic() - started) * 1000),
        _preview_text(result["output"]),
    )
    return payload


def list_sessions(*, provider: str | None = None) -> list[dict[str, Any]]:
    _ensure_state_dirs()
    sessions: list[dict[str, Any]] = []
    for path in sorted(SESSION_ROOT.glob("*.json")):
        with path.open("r", encoding="utf-8") as handle:
            session = json.load(handle)
        if provider and session["provider"] != provider:
            continue
        summary = _session_summary(session)
        summary["session_state_path"] = str(path)
        sessions.append(summary)
    sessions.sort(key=lambda item: item["updated_at"], reverse=True)
    return sessions


def get_session(session_id: str) -> dict[str, Any]:
    session = _load_session(session_id)
    return {
        "summary": _session_summary(session),
        "session": session,
        "transcript_markdown": _render_turns_markdown(session),
    }


def export_session(session_id: str, *, save_path: str | None = None) -> dict[str, Any]:
    session = _load_session(session_id)
    saved_to = _export_session(session, save_path)
    return {
        "session_id": session_id,
        "saved_to": saved_to,
    }


def update_session(
    session_id: str,
    *,
    label: str | None = None,
    notes: str | None = None,
    metadata_patch: dict[str, Any] | None = None,
    clear_label: bool = False,
    clear_notes: bool = False,
) -> dict[str, Any]:
    session = _load_session(session_id)
    if clear_label:
        session["label"] = None
    elif label is not None:
        session["label"] = label

    if clear_notes:
        session["notes"] = None
    elif notes is not None:
        session["notes"] = notes

    metadata = dict(session.get("metadata") or {})
    if metadata_patch:
        metadata.update(metadata_patch)
    session["metadata"] = metadata
    path = _write_session(session)
    return {
        "session_id": session_id,
        "summary": _session_summary(session),
        "session_state_path": str(path),
    }


def delete_session(session_id: str, *, delete_exports: bool = False) -> dict[str, Any]:
    path = _session_path(session_id)
    if not path.exists():
        raise RuntimeError(f"Unknown session_id: {session_id}")
    removed_paths = [str(path)]
    path.unlink()
    if delete_exports and EXPORT_ROOT.exists():
        for export_path in sorted(EXPORT_ROOT.glob(f"{session_id}.*")):
            export_path.unlink(missing_ok=True)
            removed_paths.append(str(export_path))
    return {
        "session_id": session_id,
        "deleted": True,
        "removed_paths": removed_paths,
    }


def list_presets(*, category: str | None = None, mode: str | None = None) -> list[dict[str, Any]]:
    presets = []
    for preset in _load_presets():
        if category and preset["category"] != category:
            continue
        if mode and preset["mode"] != mode:
            continue
        presets.append(
            {
                "name": preset["name"],
                "category": preset["category"],
                "mode": preset["mode"],
                "summary": preset["summary"],
                "path": preset["path"],
            }
        )
    return presets


def get_preset(name: str) -> dict[str, Any]:
    preset = _load_preset(name)
    return {
        "name": preset["name"],
        "category": preset["category"],
        "mode": preset["mode"],
        "summary": preset["summary"],
        "goal": preset["goal"],
        "when_to_use": preset["when_to_use"],
        "prompt": preset["prompt"],
        "output_expectations": preset["output_expectations"],
        "path": preset["path"],
    }


def run_preset_review(
    provider: str,
    *,
    preset_name: str,
    prompt: str,
    input_text: str | None = None,
    model: str | None = None,
    timeout_seconds: int | None = None,
    session_id: str | None = None,
    save_session: bool = False,
    save_path: str | None = None,
) -> dict[str, Any]:
    preset = _load_preset(preset_name)
    if preset["mode"] != "single-model":
        raise RuntimeError(f"Preset {preset_name} is not a single-model preset")
    execution_prompt = _compose_preset_prompt(
        preset,
        prompt,
        input_text=input_text,
    )
    result = run_provider_turn(
        provider,
        prompt=execution_prompt,
        input_text=None,
        stored_input_text=input_text,
        model=model,
        timeout_seconds=timeout_seconds,
        session_id=session_id,
        save_session=save_session,
        save_path=save_path,
        display_prompt=prompt,
        turn_meta={
            "preset_name": preset["name"],
            "preset_category": preset["category"],
        },
    )
    result["preset_name"] = preset["name"]
    return result


def _source_session_blocks(source_session_id: str | None) -> list[tuple[str, str]]:
    if not source_session_id:
        return []
    session = _load_session(source_session_id)
    return [
        ("source_session_summary", json.dumps(_session_summary(session), indent=2, ensure_ascii=True)),
        ("source_session_transcript", _render_turns_markdown(session)),
    ]


def share_conversation_with_model(
    *,
    source_session_id: str,
    target_provider: str,
    prompt: str,
    input_text: str | None = None,
    target_session_id: str | None = None,
    model: str | None = None,
    timeout_seconds: int | None = None,
    save_session: bool = False,
    save_path: str | None = None,
    preset_name: str = "conversation_handoff",
) -> dict[str, Any]:
    preset = _load_preset(preset_name)
    if preset["mode"] != "cross-model":
        raise RuntimeError(f"Preset {preset_name} is not a cross-model preset")
    source_session = _load_session(source_session_id)
    execution_prompt = _compose_preset_prompt(
        preset,
        prompt,
        input_text=input_text,
        extra_blocks=_source_session_blocks(source_session_id),
    )
    result = run_provider_turn(
        target_provider,
        prompt=execution_prompt,
        input_text=None,
        stored_input_text=input_text,
        model=model,
        timeout_seconds=timeout_seconds,
        session_id=target_session_id,
        save_session=save_session,
        save_path=save_path,
        display_prompt=prompt,
        turn_meta={
            "preset_name": preset["name"],
            "preset_category": preset["category"],
            "source_session_id": source_session_id,
            "source_provider": source_session["provider"],
        },
    )
    result["preset_name"] = preset["name"]
    result["source_session_id"] = source_session_id
    return result


def run_cross_model_preset(
    *,
    preset_name: str,
    prompt: str,
    input_text: str | None = None,
    draft_providers: list[str] | None = None,
    judge_provider: str = "claude",
    draft_models: dict[str, str] | None = None,
    judge_model: str | None = None,
    timeout_seconds: int | None = None,
    draft_session_ids: dict[str, str] | None = None,
    judge_session_id: str | None = None,
    source_session_id: str | None = None,
    save_session: bool = False,
    save_path: str | None = None,
) -> dict[str, Any]:
    preset = _load_preset(preset_name)
    if preset["mode"] != "cross-model":
        raise RuntimeError(f"Preset {preset_name} is not a cross-model preset")

    providers = draft_providers or list(PROVIDER_DEFAULT_MODELS.keys())
    normalized_providers: list[str] = []
    for provider in providers:
        if provider not in PROVIDER_DEFAULT_MODELS:
            raise RuntimeError(f"Unsupported provider: {provider}")
        if provider not in normalized_providers:
            normalized_providers.append(provider)
    if not normalized_providers:
        raise RuntimeError("run_cross_model_preset requires at least one draft provider")

    APP_LOGGER.info(
        "cross_model_preset.start preset=%s draft_providers=%s judge_provider=%s",
        preset["name"],
        json.dumps(normalized_providers),
        judge_provider,
    )

    def _run_draft(provider: str) -> dict[str, Any]:
        return run_provider_turn(
            provider,
            prompt=prompt,
            input_text=input_text,
            model=(draft_models or {}).get(provider),
            timeout_seconds=timeout_seconds,
            session_id=(draft_session_ids or {}).get(provider),
            display_prompt=prompt,
            turn_meta={
                "phase": "draft",
                "for_preset": preset["name"],
            },
        )

    draft_results_by_provider: dict[str, dict[str, Any]] = {}
    draft_errors_by_provider: dict[str, dict[str, str]] = {}
    with ThreadPoolExecutor(
        max_workers=max(1, len(normalized_providers)),
        thread_name_prefix="model-mcp-draft",
    ) as draft_executor:
        future_to_provider = {
            draft_executor.submit(_run_draft, provider): provider
            for provider in normalized_providers
        }
        for future in as_completed(future_to_provider):
            provider = future_to_provider[future]
            try:
                draft_results_by_provider[provider] = future.result()
            except Exception as exc:
                draft_errors_by_provider[provider] = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                }

    draft_results = [
        draft_results_by_provider[provider]
        for provider in normalized_providers
        if provider in draft_results_by_provider
    ]
    draft_errors = [
        {
            "provider": provider,
            **draft_errors_by_provider[provider],
        }
        for provider in normalized_providers
        if provider in draft_errors_by_provider
    ]
    if not draft_results:
        raise RuntimeError(
            "All draft providers failed: "
            + "; ".join(f"{item['provider']}: {item['message']}" for item in draft_errors)
        )

    draft_lines: list[str] = []
    for item in draft_results:
        draft_lines.append(f"Provider: {item['provider']}")
        draft_lines.append(f"Model: {item['model']}")
        draft_lines.append(f"Session: {item['session_id']}")
        draft_lines.append("Output:")
        draft_lines.append(item["output"])
        draft_lines.append("")
    extra_blocks = _source_session_blocks(source_session_id)
    extra_blocks.append(("draft_model_outputs", "\n".join(draft_lines).strip()))
    if draft_errors:
        error_lines = []
        for item in draft_errors:
            error_lines.append(f"Provider: {item['provider']}")
            error_lines.append(f"Error Type: {item['type']}")
            error_lines.append(f"Error Message: {item['message']}")
            error_lines.append("")
        extra_blocks.append(("draft_model_failures", "\n".join(error_lines).strip()))

    review_prompt = _compose_preset_prompt(
        preset,
        prompt,
        input_text=input_text,
        extra_blocks=extra_blocks,
    )
    judge_result = run_provider_turn(
        judge_provider,
        prompt=review_prompt,
        input_text=None,
        stored_input_text=input_text,
        model=judge_model,
        timeout_seconds=timeout_seconds,
        session_id=judge_session_id,
        save_session=save_session,
        save_path=save_path,
        display_prompt=prompt,
        turn_meta={
            "preset_name": preset["name"],
            "preset_category": preset["category"],
            "phase": "judge",
            "draft_providers": normalized_providers,
            "source_session_id": source_session_id,
        },
    )
    judge_result["preset_name"] = preset["name"]
    return {
        "preset_name": preset["name"],
        "draft_results": draft_results,
        "draft_errors": draft_errors,
        "judge_result": judge_result,
    }


def run_helper(
    *,
    provider: str | None = None,
    preset_name: str | None = None,
    prompt: str,
    input_text: str | None = None,
    model: str | None = None,
    timeout_seconds: int | None = None,
    session_id: str | None = None,
    save_session: bool = False,
    save_path: str | None = None,
    source_session_id: str | None = None,
    target_provider: str | None = None,
    target_session_id: str | None = None,
    draft_providers: list[str] | None = None,
    judge_provider: str | None = None,
    draft_models: dict[str, str] | None = None,
    judge_model: str | None = None,
    draft_session_ids: dict[str, str] | None = None,
    judge_session_id: str | None = None,
) -> dict[str, Any]:
    if preset_name:
        preset = _load_preset(preset_name)
        if preset["mode"] == "single-model":
            if not provider:
                raise RuntimeError("run_helper requires provider for single-model presets")
            result = run_preset_review(
                provider,
                preset_name=preset_name,
                prompt=prompt,
                input_text=input_text,
                model=model,
                timeout_seconds=timeout_seconds,
                session_id=session_id,
                save_session=save_session,
                save_path=save_path,
            )
            return {
                "mode": "single-model-preset",
                "result": result,
            }

        if preset_name == "conversation_handoff" or source_session_id or target_provider:
            if not source_session_id:
                raise RuntimeError("run_helper requires source_session_id for conversation handoff")
            provider_name = target_provider or provider
            if not provider_name:
                raise RuntimeError("run_helper requires target_provider or provider for conversation handoff")
            result = share_conversation_with_model(
                source_session_id=source_session_id,
                target_provider=provider_name,
                prompt=prompt,
                input_text=input_text,
                target_session_id=target_session_id or session_id,
                model=model,
                timeout_seconds=timeout_seconds,
                save_session=save_session,
                save_path=save_path,
                preset_name=preset_name,
            )
            return {
                "mode": "conversation-handoff",
                "result": result,
            }

        result = run_cross_model_preset(
            preset_name=preset_name,
            prompt=prompt,
            input_text=input_text,
            draft_providers=draft_providers,
            judge_provider=judge_provider or provider or "claude",
            draft_models=draft_models,
            judge_model=judge_model or model,
            timeout_seconds=timeout_seconds,
            draft_session_ids=draft_session_ids,
            judge_session_id=judge_session_id or session_id,
            source_session_id=source_session_id,
            save_session=save_session,
            save_path=save_path,
        )
        return {
            "mode": "cross-model-preset",
            "result": result,
        }

    if not provider:
        raise RuntimeError("run_helper requires provider when preset_name is omitted")
    result = run_provider_turn(
        provider,
        prompt=prompt,
        input_text=input_text,
        model=model,
        timeout_seconds=timeout_seconds,
        session_id=session_id,
        save_session=save_session,
        save_path=save_path,
    )
    return {
        "mode": "raw-provider",
        "result": result,
    }


def _summarize_async_request(request: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "provider": request.get("provider"),
        "preset_name": request.get("preset_name"),
        "target_provider": request.get("target_provider"),
        "source_session_id": request.get("source_session_id"),
        "timeout_seconds": request.get("timeout_seconds"),
        "prompt_preview": _preview_text(str(request.get("prompt") or ""), limit=140),
        "input_preview": _preview_text(str(request.get("input_text") or ""), limit=140),
    }
    draft_providers = request.get("draft_providers")
    if draft_providers:
        summary["draft_providers"] = list(draft_providers)
    return summary


def _finalize_job(
    job_id: str,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    started: float | None = None,
) -> dict[str, Any]:
    job = _load_job(job_id)
    job["status"] = status
    job["completed_at"] = _utc_timestamp()
    if started is not None:
        job["duration_ms"] = round((time.monotonic() - started) * 1000)
    if result is not None:
        job["result"] = result
    if error is not None:
        job["error"] = error
    _write_job(job)
    return job


def _run_async_job(job_id: str, request: dict[str, Any]) -> None:
    started = time.monotonic()
    job = _load_job(job_id)
    job["status"] = "running"
    job["started_at"] = _utc_timestamp()
    _write_job(job)
    APP_LOGGER.info(
        "job.start job_id=%s job_type=%s request=%s",
        job_id,
        job["job_type"],
        json.dumps(job.get("request_summary") or {}, ensure_ascii=True, sort_keys=True),
    )
    try:
        result = run_helper(**request)
    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        failed_job = _finalize_job(
            job_id,
            status="failed",
            error=error,
            started=started,
        )
        APP_LOGGER.error(
            "job.failed job_id=%s duration_ms=%s error=%s",
            job_id,
            failed_job.get("duration_ms"),
            _preview_text(str(exc), limit=300),
        )
    else:
        completed_job = _finalize_job(
            job_id,
            status="completed",
            result=result,
            started=started,
        )
        APP_LOGGER.info(
            "job.completed job_id=%s duration_ms=%s",
            job_id,
            completed_job.get("duration_ms"),
        )
    finally:
        with JOB_LOCK:
            JOB_FUTURES.pop(job_id, None)


def start_async_job(
    *,
    provider: str | None = None,
    preset_name: str | None = None,
    prompt: str,
    input_text: str | None = None,
    model: str | None = None,
    timeout_seconds: int | None = None,
    session_id: str | None = None,
    save_session: bool = False,
    save_path: str | None = None,
    source_session_id: str | None = None,
    target_provider: str | None = None,
    target_session_id: str | None = None,
    draft_providers: list[str] | None = None,
    judge_provider: str | None = None,
    draft_models: dict[str, str] | None = None,
    judge_model: str | None = None,
    draft_session_ids: dict[str, str] | None = None,
    judge_session_id: str | None = None,
) -> dict[str, Any]:
    request = {
        "provider": provider,
        "preset_name": preset_name,
        "prompt": prompt,
        "input_text": input_text,
        "model": model,
        "timeout_seconds": timeout_seconds,
        "session_id": session_id,
        "save_session": save_session,
        "save_path": save_path,
        "source_session_id": source_session_id,
        "target_provider": target_provider,
        "target_session_id": target_session_id,
        "draft_providers": draft_providers,
        "judge_provider": judge_provider,
        "draft_models": draft_models,
        "judge_model": judge_model,
        "draft_session_ids": draft_session_ids,
        "judge_session_id": judge_session_id,
    }
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    now = _utc_timestamp()
    job = {
        "job_id": job_id,
        "job_type": "run_helper",
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
        "duration_ms": None,
        "request_summary": _summarize_async_request(request),
        "request": request,
        "result": None,
        "error": None,
    }
    path = _write_job(job)
    with JOB_LOCK:
        JOB_FUTURES[job_id] = JOB_EXECUTOR.submit(_run_async_job, job_id, request)
    return {
        "job_id": job_id,
        "status": "queued",
        "job_state_path": str(path),
        "request_summary": job["request_summary"],
    }


def get_job(job_id: str) -> dict[str, Any]:
    job = _load_job(job_id)
    return {
        "summary": _job_summary(job),
        "job": job,
    }


def list_jobs(*, status: str | None = None) -> list[dict[str, Any]]:
    _ensure_state_dirs()
    jobs: list[dict[str, Any]] = []
    for path in sorted(JOB_ROOT.glob("*.json")):
        with path.open("r", encoding="utf-8") as handle:
            job = json.load(handle)
        if status and job.get("status") != status:
            continue
        summary = _job_summary(job)
        summary["job_state_path"] = str(path)
        jobs.append(summary)
    jobs.sort(key=lambda item: item["updated_at"], reverse=True)
    return jobs


def cancel_job(job_id: str) -> dict[str, Any]:
    job = _load_job(job_id)
    if job["status"] in {"completed", "failed", "cancelled"}:
        return {
            "job_id": job_id,
            "status": job["status"],
            "cancelled": job["status"] == "cancelled",
            "message": "Job is already finished.",
        }
    with JOB_LOCK:
        future = JOB_FUTURES.get(job_id)
        cancelled = bool(future and future.cancel())
    if cancelled:
        cancelled_job = _finalize_job(job_id, status="cancelled")
        APP_LOGGER.info("job.cancelled job_id=%s", job_id)
        return {
            "job_id": job_id,
            "status": cancelled_job["status"],
            "cancelled": True,
        }
    return {
        "job_id": job_id,
        "status": job["status"],
        "cancelled": False,
        "message": "Cancellation is best-effort and only works before a job starts running.",
    }


def create_mcp_server(*, host: str, port: int) -> FastMCP:
    server = FastMCP(
        name="local-headless-models",
        instructions=(
            "Expose one-shot access to local Codex, Claude Code, and Gemini CLI "
            "installs using safe read-only defaults."
        ),
        host=host,
        port=port,
        streamable_http_path="/mcp",
        sse_path="/sse",
        message_path="/messages/",
    )

    @server.tool(description="Run or continue a Codex CLI session.")
    def ask_codex(
        prompt: str,
        input_text: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        session_id: str | None = None,
        save_session: bool = False,
        save_path: str | None = None,
    ) -> dict[str, Any]:
        return run_provider_turn(
            "codex",
            prompt=prompt,
            input_text=input_text,
            model=model,
            timeout_seconds=timeout_seconds,
            session_id=session_id,
            save_session=save_session,
            save_path=save_path,
        )

    @server.tool(description="Run or continue a Claude Code session.")
    def ask_claude(
        prompt: str,
        input_text: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        session_id: str | None = None,
        save_session: bool = False,
        save_path: str | None = None,
    ) -> dict[str, Any]:
        return run_provider_turn(
            "claude",
            prompt=prompt,
            input_text=input_text,
            model=model,
            timeout_seconds=timeout_seconds,
            session_id=session_id,
            save_session=save_session,
            save_path=save_path,
        )

    @server.tool(description="Run or continue a Gemini CLI session.")
    def ask_gemini(
        prompt: str,
        input_text: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        session_id: str | None = None,
        save_session: bool = False,
        save_path: str | None = None,
    ) -> dict[str, Any]:
        return run_provider_turn(
            "gemini",
            prompt=prompt,
            input_text=input_text,
            model=model,
            timeout_seconds=timeout_seconds,
            session_id=session_id,
            save_session=save_session,
            save_path=save_path,
        )

    @server.tool(name="list_presets", description="List the available review presets from disk.")
    def list_presets_tool(
        category: str | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        items = list_presets(category=category, mode=mode)
        return {
            "count": len(items),
            "items": items,
        }

    @server.tool(name="validate_presets", description="Validate preset files and return any load errors.")
    def validate_presets_tool(
        force_reload: bool = False,
    ) -> dict[str, Any]:
        return validate_presets(force_reload=force_reload)

    @server.tool(name="reload_presets", description="Force a preset reload from disk and return validation results.")
    def reload_presets_tool() -> dict[str, Any]:
        return reload_presets()

    @server.tool(name="get_preset", description="Return the full metadata and prompt body for a named preset.")
    def get_preset_tool(name: str) -> dict[str, Any]:
        return get_preset(name)

    @server.tool(name="run_preset_review", description="Run a single-model preset against one provider.")
    def run_preset_review_tool(
        provider: str,
        preset_name: str,
        prompt: str,
        input_text: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        session_id: str | None = None,
        save_session: bool = False,
        save_path: str | None = None,
    ) -> dict[str, Any]:
        return run_preset_review(
            provider,
            preset_name=preset_name,
            prompt=prompt,
            input_text=input_text,
            model=model,
            timeout_seconds=timeout_seconds,
            session_id=session_id,
            save_session=save_session,
            save_path=save_path,
        )

    @server.tool(name="run_cross_model_preset", description="Run a cross-model preset by collecting draft answers and judging them with another model.")
    def run_cross_model_preset_tool(
        preset_name: str,
        prompt: str,
        input_text: str | None = None,
        draft_providers: list[str] | None = None,
        judge_provider: str = "claude",
        draft_models: dict[str, str] | None = None,
        judge_model: str | None = None,
        timeout_seconds: int | None = None,
        draft_session_ids: dict[str, str] | None = None,
        judge_session_id: str | None = None,
        source_session_id: str | None = None,
        save_session: bool = False,
        save_path: str | None = None,
    ) -> dict[str, Any]:
        return run_cross_model_preset(
            preset_name=preset_name,
            prompt=prompt,
            input_text=input_text,
            draft_providers=draft_providers,
            judge_provider=judge_provider,
            draft_models=draft_models,
            judge_model=judge_model,
            timeout_seconds=timeout_seconds,
            draft_session_ids=draft_session_ids,
            judge_session_id=judge_session_id,
            source_session_id=source_session_id,
            save_session=save_session,
            save_path=save_path,
        )

    @server.tool(name="share_conversation_with_model", description="Continue a stored conversation in another provider using the conversation handoff preset.")
    def share_conversation_with_model_tool(
        source_session_id: str,
        target_provider: str,
        prompt: str,
        input_text: str | None = None,
        target_session_id: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        save_session: bool = False,
        save_path: str | None = None,
        preset_name: str = "conversation_handoff",
    ) -> dict[str, Any]:
        return share_conversation_with_model(
            source_session_id=source_session_id,
            target_provider=target_provider,
            prompt=prompt,
            input_text=input_text,
            target_session_id=target_session_id,
            model=model,
            timeout_seconds=timeout_seconds,
            save_session=save_session,
            save_path=save_path,
            preset_name=preset_name,
        )

    @server.tool(name="list_sessions", description="Return stored local sessions with metadata and step counts.")
    def list_sessions_tool(provider: str | None = None) -> dict[str, Any]:
        items = list_sessions(provider=provider)
        return {
            "count": len(items),
            "items": items,
        }

    @server.tool(name="get_session", description="Return a stored local session plus a rendered transcript.")
    def get_session_tool(session_id: str) -> dict[str, Any]:
        return get_session(session_id)

    @server.tool(name="update_session", description="Update stored session metadata such as label, notes, or custom fields.")
    def update_session_tool(
        session_id: str,
        label: str | None = None,
        notes: str | None = None,
        metadata_patch: dict[str, Any] | None = None,
        clear_label: bool = False,
        clear_notes: bool = False,
    ) -> dict[str, Any]:
        return update_session(
            session_id,
            label=label,
            notes=notes,
            metadata_patch=metadata_patch,
            clear_label=clear_label,
            clear_notes=clear_notes,
        )

    @server.tool(name="export_session", description="Export an existing stored session to markdown or JSON.")
    def export_session_tool(
        session_id: str,
        save_path: str | None = None,
    ) -> dict[str, Any]:
        return export_session(session_id, save_path=save_path)

    @server.tool(name="delete_session", description="Delete a stored local session and optionally its default exports.")
    def delete_session_tool(
        session_id: str,
        delete_exports: bool = False,
    ) -> dict[str, Any]:
        return delete_session(session_id, delete_exports=delete_exports)

    @server.tool(name="start_async_job", description="Start a background job that routes through run_helper and return immediately with a job id.")
    def start_async_job_tool(
        prompt: str,
        provider: str | None = None,
        preset_name: str | None = None,
        input_text: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        session_id: str | None = None,
        save_session: bool = False,
        save_path: str | None = None,
        source_session_id: str | None = None,
        target_provider: str | None = None,
        target_session_id: str | None = None,
        draft_providers: list[str] | None = None,
        judge_provider: str | None = None,
        draft_models: dict[str, str] | None = None,
        judge_model: str | None = None,
        draft_session_ids: dict[str, str] | None = None,
        judge_session_id: str | None = None,
    ) -> dict[str, Any]:
        return start_async_job(
            provider=provider,
            preset_name=preset_name,
            prompt=prompt,
            input_text=input_text,
            model=model,
            timeout_seconds=timeout_seconds,
            session_id=session_id,
            save_session=save_session,
            save_path=save_path,
            source_session_id=source_session_id,
            target_provider=target_provider,
            target_session_id=target_session_id,
            draft_providers=draft_providers,
            judge_provider=judge_provider,
            draft_models=draft_models,
            judge_model=judge_model,
            draft_session_ids=draft_session_ids,
            judge_session_id=judge_session_id,
        )

    @server.tool(name="get_job", description="Return the current status and result payload for a background job.")
    def get_job_tool(job_id: str) -> dict[str, Any]:
        return get_job(job_id)

    @server.tool(name="list_jobs", description="List background jobs with status, timestamps, and summaries.")
    def list_jobs_tool(status: str | None = None) -> dict[str, Any]:
        items = list_jobs(status=status)
        return {
            "count": len(items),
            "items": items,
        }

    @server.tool(name="cancel_job", description="Request cancellation for a queued background job.")
    def cancel_job_tool(job_id: str) -> dict[str, Any]:
        return cancel_job(job_id)

    @server.tool(name="run_helper", description="Unified entrypoint for raw provider calls, preset reviews, cross-model orchestration, and conversation handoff.")
    def run_helper_tool(
        prompt: str,
        provider: str | None = None,
        preset_name: str | None = None,
        input_text: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        session_id: str | None = None,
        save_session: bool = False,
        save_path: str | None = None,
        source_session_id: str | None = None,
        target_provider: str | None = None,
        target_session_id: str | None = None,
        draft_providers: list[str] | None = None,
        judge_provider: str | None = None,
        draft_models: dict[str, str] | None = None,
        judge_model: str | None = None,
        draft_session_ids: dict[str, str] | None = None,
        judge_session_id: str | None = None,
    ) -> dict[str, Any]:
        return run_helper(
            provider=provider,
            preset_name=preset_name,
            prompt=prompt,
            input_text=input_text,
            model=model,
            timeout_seconds=timeout_seconds,
            session_id=session_id,
            save_session=save_session,
            save_path=save_path,
            source_session_id=source_session_id,
            target_provider=target_provider,
            target_session_id=target_session_id,
            draft_providers=draft_providers,
            judge_provider=judge_provider,
            draft_models=draft_models,
            judge_model=judge_model,
            draft_session_ids=draft_session_ids,
            judge_session_id=judge_session_id,
        )

    return server


def create_http_app(*, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> Starlette:
    server = create_mcp_server(host=host, port=port)
    streamable_http_app = server.streamable_http_app()
    sse_app = server.sse_app()

    async def index(_request) -> JSONResponse:
        return JSONResponse(
            {
                "name": "local-headless-models",
                "streamable_http": "/mcp",
                "sse": "/sse",
                "messages": "/messages/",
                "tools": [
                    "ask_codex",
                    "ask_claude",
                    "ask_gemini",
                    "list_presets",
                    "validate_presets",
                    "reload_presets",
                    "get_preset",
                    "run_preset_review",
                    "run_cross_model_preset",
                    "share_conversation_with_model",
                    "list_sessions",
                    "get_session",
                    "update_session",
                    "export_session",
                    "delete_session",
                    "start_async_job",
                    "get_job",
                    "list_jobs",
                    "cancel_job",
                    "run_helper",
                ],
                "max_session_steps": MAX_SESSION_STEPS,
                "preset_count": len(_load_presets()),
                "invalid_preset_count": len(_get_preset_registry().get("invalid", [])),
            }
        )

    async def healthz(_request) -> JSONResponse:
        return JSONResponse({"ok": True})

    return Starlette(
        routes=[
            Route("/", endpoint=index, methods=["GET"]),
            Route("/healthz", endpoint=healthz, methods=["GET"]),
            *streamable_http_app.routes,
            *sse_app.routes,
        ],
        middleware=[*streamable_http_app.user_middleware, *sse_app.user_middleware],
        lifespan=streamable_http_app.router.lifespan_context,
    )


def main() -> None:
    global VERBOSE_TOOL_LOGGING
    parser = argparse.ArgumentParser(description="Run the local headless model MCP server.")
    parser.add_argument("--host", default=os.getenv("MODEL_MCP_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MODEL_MCP_PORT", str(DEFAULT_PORT))),
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("MODEL_MCP_LOG_LEVEL", "INFO"),
    )
    parser.add_argument(
        "--log-file",
        default=os.getenv("MODEL_MCP_LOG_FILE"),
    )
    parser.add_argument(
        "--verbose-tool-logging",
        action="store_true",
        default=VERBOSE_TOOL_LOGGING,
    )
    args = parser.parse_args()
    VERBOSE_TOOL_LOGGING = args.verbose_tool_logging
    configure_logging(log_level=args.log_level, log_file=args.log_file)
    APP_LOGGER.info(
        "server.start host=%s port=%s log_level=%s log_file=%s verbose_tool_logging=%s",
        args.host,
        args.port,
        args.log_level.upper(),
        args.log_file or "-",
        VERBOSE_TOOL_LOGGING,
    )
    uvicorn.run(
        create_http_app(host=args.host, port=args.port),
        host=args.host,
        port=args.port,
        log_level="info",
        log_config=None,
    )


if __name__ == "__main__":
    main()
