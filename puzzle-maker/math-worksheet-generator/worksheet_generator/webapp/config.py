from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class AppPaths:
    database_path: Path
    artifact_root: Path


def load_app_paths() -> AppPaths:
    database_path = Path(os.environ.get("APP_DB_PATH", "/var/lib/math-worksheet-generator/db/app.sqlite3"))
    artifact_root = Path(os.environ.get("APP_ARTIFACT_ROOT", "/var/lib/math-worksheet-generator/artifacts"))
    return AppPaths(
        database_path=database_path,
        artifact_root=artifact_root,
    )


def is_gemini_enabled() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())


def get_gemini_model() -> str:
    return os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite").strip() or "gemini-2.5-flash-lite"


def get_gemini_image_model() -> str:
    return os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image-preview").strip() or "gemini-3.1-flash-image-preview"


def get_log_verbosity() -> str:
    value = os.environ.get("APP_LOG_VERBOSITY", "normal").strip().lower()
    if value not in {"minimal", "normal", "verbose"}:
        return "normal"
    return value


def is_debug_ui_enabled() -> bool:
    return os.environ.get("APP_DEBUG_UI", "").strip().lower() in {"1", "true", "yes", "on"}


def is_job_worker_enabled() -> bool:
    raw = os.environ.get("APP_JOB_WORKER_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _get_timeout_seconds(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def get_worksheet_generation_timeout_seconds() -> float:
    return _get_timeout_seconds("APP_WORKSHEET_GENERATION_TIMEOUT_SECONDS", 180.0)


def get_worksheet_styling_timeout_seconds() -> float:
    return _get_timeout_seconds("APP_WORKSHEET_STYLING_TIMEOUT_SECONDS", 180.0)


def get_styling_confirmation_timeout_seconds() -> float:
    return _get_timeout_seconds("APP_STYLING_CONFIRMATION_TIMEOUT_SECONDS", 86400.0)
