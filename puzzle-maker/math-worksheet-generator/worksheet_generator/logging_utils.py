from __future__ import annotations

import json
import logging
import os


_VERBOSITY_ORDER = {
    "minimal": 0,
    "normal": 1,
    "verbose": 2,
}


def get_log_verbosity() -> str:
    value = os.environ.get("APP_LOG_VERBOSITY", "normal").strip().lower()
    if value not in _VERBOSITY_ORDER:
        return "normal"
    return value


def configure_application_logging() -> str:
    verbosity = get_log_verbosity()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=False,
    )
    return verbosity


def log_event(logger: logging.Logger, event: str, *, verbosity: str = "minimal", **fields: object) -> None:
    if _VERBOSITY_ORDER[get_log_verbosity()] < _VERBOSITY_ORDER[verbosity]:
        return
    payload = {
        "event": event,
        **fields,
    }
    logger.info(json.dumps(payload, sort_keys=True, default=str))
