from __future__ import annotations

from typing import Any

from .config import SERVER_NAME, SERVER_VERSION
from .errors import StructuredToolError
from .utils import json_clone, utc_now


def confidence(level: str = "exact", method: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"level": level}
    if method:
        payload["method"] = method
    return payload


def provenance(
    tool_name: str,
    parameters: dict[str, Any],
    *,
    backend: str = "session-core",
    exact: bool = True,
    artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "backend": backend,
        "tool": tool_name,
        "parameters": json_clone(parameters),
        "exact": exact,
        "inferred": not exact,
    }
    if artifact is not None:
        payload["artifact"] = json_clone(artifact)
    return payload


def success(
    tool_name: str,
    parameters: dict[str, Any],
    result: dict[str, Any],
    *,
    confidence_value: dict[str, Any] | None = None,
    provenance_value: dict[str, Any] | None = None,
    partial: bool = False,
    suggested_next_actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "server": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "timestamp": utc_now(),
        "ok": True,
        "partial": partial,
        "confidence": json_clone(confidence_value or confidence()),
        "provenance": json_clone(provenance_value or provenance(tool_name, parameters)),
        "result": json_clone(result),
        "error": None,
        "suggested_next_actions": json_clone(suggested_next_actions or []),
    }


def failure(
    tool_name: str,
    parameters: dict[str, Any],
    error: StructuredToolError,
    *,
    confidence_value: dict[str, Any] | None = None,
    provenance_value: dict[str, Any] | None = None,
    suggested_next_actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "server": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "timestamp": utc_now(),
        "ok": False,
        "partial": error.partial,
        "confidence": json_clone(confidence_value or confidence()),
        "provenance": json_clone(provenance_value or provenance(tool_name, parameters)),
        "result": None,
        "error": error.to_dict(),
        "suggested_next_actions": json_clone(suggested_next_actions or []),
    }
