from __future__ import annotations

from typing import Any

from .utils import json_clone

VERBOSITY_PROFILES: dict[str, dict[str, int | bool]] = {
    "brief": {
        "function_limit": 3,
        "string_limit": 4,
        "import_limit": 5,
        "match_limit": 4,
        "xref_limit": 4,
        "code_cave_limit": 3,
        "instruction_limit": 6,
        "correlation_limit": 4,
        "char_limit": 72,
        "include_evidence": False,
    },
    "normal": {
        "function_limit": 5,
        "string_limit": 6,
        "import_limit": 8,
        "match_limit": 6,
        "xref_limit": 6,
        "code_cave_limit": 5,
        "instruction_limit": 10,
        "correlation_limit": 6,
        "char_limit": 96,
        "include_evidence": True,
    },
    "deep": {
        "function_limit": 8,
        "string_limit": 10,
        "import_limit": 12,
        "match_limit": 10,
        "xref_limit": 10,
        "code_cave_limit": 8,
        "instruction_limit": 16,
        "correlation_limit": 10,
        "char_limit": 140,
        "include_evidence": True,
    },
}

_BUDGET_CAPS = (
    (600, "brief"),
    (1200, "normal"),
    (2200, "deep"),
)


def normalize_brief_options(
    *,
    verbosity: str = "brief",
    token_budget_hint: int | None = None,
    include_next_actions: bool = True,
    include_raw_sections: bool = False,
) -> dict[str, Any]:
    normalized = verbosity.strip().lower()
    if normalized not in VERBOSITY_PROFILES:
        raise ValueError("verbosity must be one of: brief, normal, deep.")
    profile = json_clone(VERBOSITY_PROFILES[normalized])
    effective = normalized
    if token_budget_hint is not None:
        budget = max(128, int(token_budget_hint))
        for threshold, capped in _BUDGET_CAPS:
            if budget <= threshold and _profile_rank(capped) < _profile_rank(effective):
                effective = capped
                profile = json_clone(VERBOSITY_PROFILES[capped])
                break
        profile["token_budget_hint"] = budget
    else:
        profile["token_budget_hint"] = None
    profile["verbosity"] = effective
    profile["requested_verbosity"] = normalized
    profile["include_next_actions"] = bool(include_next_actions)
    profile["include_raw_sections"] = bool(include_raw_sections)
    return profile


def truncate_text(value: str | None, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    clipped = max(1, limit - 3)
    return f"{text[:clipped]}..."


def compact_page(items: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    bounded = items[: max(1, int(limit))]
    return {
        "items": bounded,
        "truncated": len(items) > len(bounded),
        "total": len(items),
    }


def compact_function(item: dict[str, Any], *, profile: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "function_id": item.get("function_id"),
        "name": item.get("demangled_name") or item.get("name"),
        "address": item.get("address"),
        "size": item.get("size"),
    }
    if item.get("triage_score") is not None:
        payload["triage_score"] = item["triage_score"].get("score")
    tags = item.get("classification_tags") or []
    if tags:
        payload["tags"] = tags[:3]
    if profile.get("include_evidence") and item.get("triage_score", {}).get("evidence"):
        payload["evidence"] = item["triage_score"]["evidence"][:2]
    return payload


def compact_string(item: dict[str, Any], *, profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "string_id": item.get("string_id"),
        "value": truncate_text(item.get("value"), int(profile["char_limit"])),
        "address": item.get("address"),
        "encoding": item.get("encoding"),
    }


def compact_symbol(item: dict[str, Any], *, profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": truncate_text(item.get("demangled_name") or item.get("name"), int(profile["char_limit"])),
        "kind": item.get("kind"),
        "address": item.get("address"),
    }


def compact_xref(item: dict[str, Any], *, profile: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "source_function_id": item.get("source_function_id"),
        "source_function_name": truncate_text(item.get("source_function_name"), int(profile["char_limit"])),
        "source_address": item.get("source_function_address"),
        "target_address": item.get("target_address"),
        "kind": item.get("kind"),
    }
    if profile.get("include_evidence") and item.get("evidence"):
        payload["evidence"] = item["evidence"][:2]
    return payload


def compact_instruction(item: dict[str, Any], *, profile: dict[str, Any]) -> dict[str, Any]:
    operand_text = item.get("operand_text") or ""
    text = f"{item.get('mnemonic', '')} {operand_text}".strip()
    return {
        "address": item.get("address"),
        "text": truncate_text(text, int(profile["char_limit"])),
    }


def compact_code_cave(item: dict[str, Any], *, profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_offset": item.get("file_offset"),
        "virtual_address": item.get("virtual_address"),
        "size": item.get("size"),
        "section": truncate_text(item.get("section_name"), int(profile["char_limit"])),
    }


def profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "requested_verbosity": profile["requested_verbosity"],
        "effective_verbosity": profile["verbosity"],
        "token_budget_hint": profile["token_budget_hint"],
        "include_next_actions": profile["include_next_actions"],
        "include_raw_sections": profile["include_raw_sections"],
    }


def _profile_rank(name: str) -> int:
    order = {"brief": 0, "normal": 1, "deep": 2}
    return order[name]
