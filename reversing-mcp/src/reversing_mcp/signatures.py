from __future__ import annotations

import base64
import binascii
import json
import math
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

try:  # pragma: no cover - install-time dependency
    import pefile
except ImportError:  # pragma: no cover
    pefile = None

try:  # pragma: no cover - optional dependency
    import yara
except ImportError:  # pragma: no cover
    yara = None

from .triage import detect_format, extract_strings, parse_artifact

PRINTABLE_ASCII = set(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}
KNOWN_CRYPTO_CONSTANTS = {
    0x9E3779B9: {"name": "tea_delta", "family": "TEA/XTEA"},
    0x9E3779B97F4A7C15: {"name": "golden_ratio_64", "family": "TEA/SplitMix"},
    0xEDB88320: {"name": "crc32_poly_le", "family": "CRC32"},
    0x04C11DB7: {"name": "crc32_poly_be", "family": "CRC32"},
    0x67452301: {"name": "md5_a", "family": "MD5"},
    0xEFCDAB89: {"name": "md5_b", "family": "MD5"},
    0x98BADCFE: {"name": "md5_c", "family": "MD5"},
    0x10325476: {"name": "md5_d", "family": "MD5"},
    0x6A09E667: {"name": "sha256_h0", "family": "SHA-256"},
    0xBB67AE85: {"name": "sha256_h1", "family": "SHA-256"},
}
PACKER_SECTION_NAMES = {"upx0", "upx1", "aspack", ".aspack", "mpress1", "mpress2", "petite", ".packed"}
MAGIC_PATTERNS = [
    (b"PK\x03\x04", "zip", ".zip"),
    (b"\x7fELF", "elf", ".elf"),
    (b"MZ", "pe", ".exe"),
    (b"\x1f\x8b\x08", "gzip", ".gz"),
    (b"\xfd7zXZ\x00", "xz", ".xz"),
]
FLOSS_METHOD_MAP = {
    "decoded_strings": ("floss-decoded", "high", "decoded-string analysis"),
    "stack_strings": ("floss-stack", "medium", "stack-string recovery"),
    "tight_strings": ("floss-tight", "medium", "tight-string recovery"),
}


def parse_artifact_context(path: str | Path, *, resource_limits: dict[str, Any], hints: dict[str, Any] | None = None) -> dict[str, Any]:
    target = Path(path)
    data = target.read_bytes()
    parsed = parse_artifact(target, data, hints or {}, resource_limits)
    strings = extract_strings(
        data,
        parsed,
        min_length=4,
        max_strings=int(resource_limits.get("string_count_limit", 50000)),
    )["items"]
    return {
        "path": target,
        "data": data,
        "parsed": parsed,
        "strings": strings,
    }


def run_yara_scan(
    *,
    path: str | Path,
    data: bytes,
    parsed: dict[str, Any],
    strings: list[dict[str, Any]],
    analysis: dict[str, Any] | None = None,
    rules_text: str | None = None,
) -> dict[str, Any]:
    if yara is not None and rules_text:
        compiled = yara.compile(source=rules_text)
        matches = []
        for match in compiled.match(data=data):
            matched_strings = []
            for offset, identifier, value in getattr(match, "strings", []):
                preview = value.decode("utf-8", errors="replace")[:64] if isinstance(value, bytes) else str(value)[:64]
                matched_strings.append(
                    {
                        "offset": int(offset),
                        "identifier": identifier,
                        "preview": preview,
                    }
                )
            matches.append(
                {
                    "rule": match.rule,
                    "namespace": getattr(match, "namespace", "default"),
                    "tags": list(getattr(match, "tags", [])),
                    "meta": dict(getattr(match, "meta", {})),
                    "strings": matched_strings,
                    "confidence": {"level": "high", "method": "yara-python"},
                    "evidence": [f"YARA rule '{match.rule}' matched {len(matched_strings)} string(s)."],
                }
            )
        return {"backend": "yara-python", "matches": matches}

    lower_strings = [item["value"].lower() for item in strings]
    symbol_names = []
    if analysis is not None:
        symbol_names = [
            (item.get("demangled_name") or item.get("name") or "").lower()
            for item in analysis.get("symbols", [])
        ]
    compiler = fingerprint_compiler_toolchain(path=path, parsed=parsed, strings=strings)
    overlay = detect_overlay(path=path, data=data, parsed=parsed)
    builtin_matches = []
    if parsed.get("signatures", {}).get("elf_build_id", {}).get("present"):
        builtin_matches.append(
            {
                "rule": "elf_build_id_present",
                "namespace": "builtin",
                "tags": ["elf", "metadata"],
                "meta": {"family": "build-id"},
                "strings": [],
                "confidence": {"level": "high", "method": "triage signature"},
                "evidence": ["ELF build ID note is present in parsed signatures."],
            }
        )
    if any("__cxa_" in item or "_ztv" in item for item in lower_strings + symbol_names):
        builtin_matches.append(
            {
                "rule": "cpp_runtime_markers",
                "namespace": "builtin",
                "tags": ["cpp", "runtime"],
                "meta": {"family": "itanium-abi"},
                "strings": [],
                "confidence": {"level": "medium", "method": "symbol and string heuristic"},
                "evidence": ["Recovered C++ ABI marker strings or symbols."],
            }
        )
    if compiler["matches"]:
        builtin_matches.append(
            {
                "rule": "compiler_fingerprint_present",
                "namespace": "builtin",
                "tags": ["toolchain"],
                "meta": {"compiler": compiler["matches"][0]["compiler"]},
                "strings": [],
                "confidence": {"level": "medium", "method": "toolchain fingerprint reuse"},
                "evidence": compiler["matches"][0]["evidence"],
            }
        )
    if overlay["present"] and overlay.get("detected_format"):
        builtin_matches.append(
            {
                "rule": "overlay_embedded_artifact",
                "namespace": "builtin",
                "tags": ["overlay", "embedded"],
                "meta": {"detected_format": overlay["detected_format"]},
                "strings": [],
                "confidence": {"level": "medium", "method": "overlay magic heuristic"},
                "evidence": [f"Detected appended {overlay['detected_format']} payload at offset 0x{overlay['offset']:x}."],
            }
        )
    return {"backend": "heuristic-fallback", "matches": builtin_matches}


def detect_crypto_constants(analysis: dict[str, Any], data: bytes | None = None) -> dict[str, Any]:
    items = []
    seen: set[tuple[str, int, int]] = set()
    for function in analysis.get("functions", []):
        detail = analysis.get("function_details", {}).get(str(int(function["address"])), {})
        for immediate in detail.get("constant_propagation", {}).get("immediates", []):
            value = int(immediate["value"])
            candidate = KNOWN_CRYPTO_CONSTANTS.get(value)
            if candidate is None:
                continue
            key = (candidate["name"], int(function["address"]), int(immediate["instruction_address"]))
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "family": candidate["family"],
                    "name": candidate["name"],
                    "value": value,
                    "representation": hex(value),
                    "function_id": function.get("function_id"),
                    "function_name": function.get("demangled_name") or function["name"],
                    "instruction_address": int(immediate["instruction_address"]),
                    "confidence": {"level": "high", "method": "known constant table"},
                    "evidence": [f"Immediate {hex(value)} recovered at 0x{int(immediate['instruction_address']):x}."],
                }
            )
    if data:
        for value, candidate in KNOWN_CRYPTO_CONSTANTS.items():
            width = max(4, (value.bit_length() + 7) // 8)
            for endianness in ("little", "big"):
                needle = int(value).to_bytes(width, endianness, signed=False)
                offset = data.find(needle)
                if offset < 0:
                    continue
                items.append(
                    {
                        "family": candidate["family"],
                        "name": candidate["name"],
                        "value": value,
                        "representation": hex(value),
                        "function_id": None,
                        "function_name": None,
                        "instruction_address": None,
                        "file_offset": offset,
                        "confidence": {"level": "medium", "method": f"raw-byte {endianness}-endian scan"},
                        "evidence": [f"Matched {hex(value)} at file offset 0x{offset:x} as {endianness}-endian raw bytes."],
                    }
                )
                break
    return {"items": items}


def recognize_library_code(parsed: dict[str, Any], analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    imports = [item.lower() for item in parsed.get("taxonomy_inputs", {}).get("imports", [])]
    symbol_names = []
    if analysis is not None:
        symbol_names = [(item.get("demangled_name") or item.get("name") or "").lower() for item in analysis.get("symbols", [])]
    text = " ".join(imports + symbol_names)
    libraries = []
    for name, needles, confidence in [
        ("libc", ("libc.so", "puts", "__libc_start_main", "msvcrt"), "high"),
        ("libstdc++", ("libstdc++.so", "__cxa_", "operator new", "std::"), "medium"),
        ("libgcc", ("libgcc_s", "_unwind_", "__gxx_personality"), "medium"),
    ]:
        evidence = [needle for needle in needles if needle in text]
        if not evidence:
            continue
        libraries.append(
            {
                "library": name,
                "confidence": {"level": confidence, "method": "import and symbol heuristic"},
                "evidence": [f"Matched marker '{marker}'." for marker in evidence[:5]],
            }
        )
    recognized_functions = []
    if analysis is not None:
        for function in analysis.get("functions", []):
            name = (function.get("demangled_name") or function["name"]).lower()
            if any(marker in name for marker in ("__libc_", "__cxa_", "_init", "_fini")) or function.get("is_plt"):
                recognized_functions.append(
                    {
                        "function_id": function.get("function_id"),
                        "name": function.get("demangled_name") or function["name"],
                        "library_hint": "runtime",
                    }
                )
    return {"libraries": libraries, "recognized_functions": recognized_functions[:50]}


def fingerprint_compiler_toolchain(*, path: str | Path, parsed: dict[str, Any], strings: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = []
    for string_item in strings:
        value = string_item["value"]
        lowered = value.lower()
        if "gcc:" in lowered:
            candidates.append(
                {
                    "compiler": "gcc",
                    "toolchain": "gnu",
                    "confidence": {"level": "high", "method": "embedded comment string"},
                    "evidence": [f"Recovered compiler banner '{value[:80]}'."],
                }
            )
        elif "clang version" in lowered:
            candidates.append(
                {
                    "compiler": "clang",
                    "toolchain": "llvm",
                    "confidence": {"level": "high", "method": "embedded comment string"},
                    "evidence": [f"Recovered compiler banner '{value[:80]}'."],
                }
            )
        elif "msvc" in lowered or "microsoft visual c++" in lowered:
            candidates.append(
                {
                    "compiler": "msvc",
                    "toolchain": "visual-studio",
                    "confidence": {"level": "medium", "method": "embedded string heuristic"},
                    "evidence": [f"Recovered toolchain string '{value[:80]}'."],
                }
            )
    if not candidates and parsed.get("file_type", {}).get("format") == "PE":
        imports = " ".join(parsed.get("taxonomy_inputs", {}).get("imports", [])).lower()
        if "msvcrt" in imports or "kernel32" in imports:
            candidates.append(
                {
                    "compiler": "msvc-or-compatible",
                    "toolchain": "windows-pe",
                    "confidence": {"level": "low", "method": "import heuristic"},
                    "evidence": ["PE imports are consistent with a Windows user-space toolchain."],
                }
            )
    if not candidates:
        candidates.append(
            {
                "compiler": "unknown",
                "toolchain": "unknown",
                "confidence": {"level": "low", "method": "fallback"},
                "evidence": [f"No explicit compiler fingerprint recovered from '{Path(path).name}'."],
            }
        )
    return {"matches": candidates}


def calculate_entropy(path: str | Path, data: bytes, parsed: dict[str, Any]) -> dict[str, Any]:
    sections = []
    for section in parsed.get("sections", []):
        offset = int(section.get("file_offset", 0))
        size = int(section.get("size", 0))
        chunk = data[offset : offset + size] if size > 0 else b""
        sections.append(
            {
                "name": section.get("name"),
                "file_offset": offset,
                "size": size,
                "entropy": round(_shannon_entropy(chunk), 4),
                "classification": _entropy_label(chunk),
            }
        )
    return {
        "path": str(Path(path)),
        "whole_file": {
            "size": len(data),
            "entropy": round(_shannon_entropy(data), 4),
            "classification": _entropy_label(data),
        },
        "sections": sections,
    }


def detect_packer(*, path: str | Path, data: bytes, parsed: dict[str, Any], strings: list[dict[str, Any]]) -> dict[str, Any]:
    entropy = calculate_entropy(path, data, parsed)
    evidence = []
    section_names = {str(item.get("name", "")).lower() for item in parsed.get("sections", [])}
    if section_names.intersection(PACKER_SECTION_NAMES):
        evidence.append("Section names match known packer conventions.")
    lowered_strings = [item["value"].lower() for item in strings]
    if any("upx!" in item or "mpress" in item or "aspack" in item for item in lowered_strings):
        evidence.append("Recovered packer marker strings in the artifact.")
    overlay = detect_overlay(path=path, data=data, parsed=parsed)
    if overlay["present"]:
        evidence.append(f"Appended overlay of {overlay['size']} bytes starts at file offset 0x{overlay['offset']:x}.")
    if any(section["entropy"] >= 7.2 and "x" in str(_section_permissions(parsed, section["name"])) for section in entropy["sections"]):
        evidence.append("Executable section entropy exceeds 7.2 bits/byte.")
    import_count = len(parsed.get("taxonomy_inputs", {}).get("imports", []))
    if parsed.get("file_type", {}).get("kind") == "executable" and import_count <= 4:
        evidence.append("Import surface is unusually small for an executable.")
    likely_packed = bool(evidence)
    return {
        "likely_packed": likely_packed,
        "confidence": {"level": "medium" if likely_packed else "low", "method": "signature and heuristic aggregation"},
        "evidence": evidence,
        "overlay": overlay,
    }


def deobfuscate_strings(
    *,
    path: str | Path,
    parsed: dict[str, Any],
    strings: list[dict[str, Any]],
    limit: int = 50,
) -> dict[str, Any]:
    items = []
    seen_values: set[str] = set()
    backends: list[str] = []
    errors: list[dict[str, str]] = []
    target_limit = max(1, int(limit))

    floss_result = _run_floss(path, min_length=4) if _supports_floss(parsed) else None
    if floss_result is not None:
        if floss_result["ok"]:
            backends.append("flare-floss")
            for item in _decode_floss_strings(floss_result["document"]):
                decoded = item["decoded_value"]
                if decoded in seen_values:
                    continue
                seen_values.add(decoded)
                items.append(item)
                if len(items) >= target_limit:
                    return {
                        "backend": "+".join(backends),
                        "fallback_used": False,
                        "supported_by_floss": True,
                        "items": items,
                        "truncated": True,
                        "errors": errors,
                    }
        else:
            errors.append(
                {
                    "backend": "flare-floss",
                    "code": floss_result["code"],
                    "message": floss_result["message"],
                }
            )

    heuristic_used = False
    for string_item in strings:
        raw_value = string_item["value"]
        for method, decoded in _decode_candidates(raw_value):
            if decoded in seen_values:
                continue
            seen_values.add(decoded)
            heuristic_used = True
            items.append(
                {
                    "source_value": raw_value,
                    "decoded_value": decoded,
                    "method": method,
                    "string_id": string_item.get("string_id"),
                    "address": string_item.get("address"),
                    "confidence": {"level": "medium", "method": f"{method} heuristic"},
                    "evidence": [f"Recovered printable {method} decode candidate from '{raw_value[:48]}'."],
                }
            )
            if len(items) >= target_limit:
                break
        if len(items) >= target_limit:
            break

    if heuristic_used:
        backends.append("heuristic-fallback")
    if not backends:
        backends.append("heuristic-fallback")
    return {
        "backend": "+".join(backends),
        "fallback_used": heuristic_used,
        "supported_by_floss": _supports_floss(parsed),
        "items": items,
        "truncated": len(items) >= target_limit and len(strings) > 0,
        "errors": errors,
    }


def detect_overlay(*, path: str | Path, data: bytes, parsed: dict[str, Any]) -> dict[str, Any]:
    max_end = 0
    for item in parsed.get("sections", []) + parsed.get("segments", []):
        start = int(item.get("file_offset", 0))
        size = int(item.get("size", 0))
        max_end = max(max_end, start + size)
    if max_end >= len(data):
        return {"present": False, "offset": None, "size": 0, "detected_format": None, "suggested_extension": None}
    overlay = data[max_end:]
    detected_format = None
    suggested_extension = None
    for magic, label, extension in MAGIC_PATTERNS:
        if overlay.startswith(magic):
            detected_format = label
            suggested_extension = extension
            break
    return {
        "present": True,
        "offset": max_end,
        "size": len(overlay),
        "detected_format": detected_format,
        "suggested_extension": suggested_extension,
    }


def extract_pe_resources(path: str | Path, data: bytes) -> list[dict[str, Any]]:
    if pefile is None:
        return []
    try:
        pe = pefile.PE(str(path), fast_load=False)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
    except Exception:
        return []
    directory = getattr(pe, "DIRECTORY_ENTRY_RESOURCE", None)
    if directory is None:
        return []
    items = []
    for type_entry in directory.entries:
        type_name = str(getattr(type_entry, "name", None) or getattr(type_entry.struct, "Id", "unknown"))
        for id_entry in getattr(type_entry.directory, "entries", []):
            resource_name = str(getattr(id_entry, "name", None) or getattr(id_entry.struct, "Id", "unknown"))
            for lang_entry in getattr(id_entry.directory, "entries", []):
                data_entry = getattr(lang_entry, "data", None)
                if data_entry is None:
                    continue
                offset = pe.get_offset_from_rva(data_entry.struct.OffsetToData)
                size = int(data_entry.struct.Size)
                payload = data[offset : offset + size]
                items.append(
                    {
                        "name": f"{type_name}_{resource_name}_{int(lang_entry.struct.Id)}.bin",
                        "container_path": f"pe.resources/{type_name}/{resource_name}/{int(lang_entry.struct.Id)}",
                        "offset": int(offset),
                        "size": size,
                        "bytes": payload,
                        "provenance": {
                            "container_path": f"pe.resources/{type_name}/{resource_name}/{int(lang_entry.struct.Id)}",
                            "offset": int(offset),
                            "size": size,
                            "extraction_method": "pe-resource",
                        },
                    }
                )
    return items


def extract_archive_members(path: str | Path, *, max_items: int, max_bytes: int) -> dict[str, Any]:
    target = Path(path)
    format_name = detect_format(target, target.read_bytes(), {})
    extracted = []
    skipped = []
    bytes_written = 0
    if format_name == "zip":
        with zipfile.ZipFile(target) as archive:
            for index, info in enumerate(sorted(archive.infolist(), key=lambda item: (item.header_offset, item.filename))):
                if index >= max_items:
                    skipped.append({"name": info.filename, "reason": "artifact_count_limit"})
                    continue
                if info.is_dir():
                    continue
                if _looks_like_decompression_bomb(info.file_size, info.compress_size):
                    skipped.append({"name": info.filename, "reason": "decompression_bomb_detected"})
                    continue
                if bytes_written + int(info.file_size) > max_bytes:
                    skipped.append({"name": info.filename, "reason": "carved_byte_budget_exceeded"})
                    continue
                payload = archive.read(info)
                bytes_written += len(payload)
                extracted.append(
                    {
                        "name": info.filename,
                        "container_path": info.filename,
                        "offset": int(info.header_offset),
                        "size": len(payload),
                        "bytes": payload,
                        "provenance": {
                            "container_path": info.filename,
                            "offset": int(info.header_offset),
                            "size": len(payload),
                            "compressed_size": int(info.compress_size),
                            "extraction_method": "zip",
                        },
                    }
                )
    return {"items": extracted, "skipped": skipped, "format": format_name}


def materialize_output_file(base_path: Path, payload: bytes) -> Path:
    candidate = base_path
    counter = 1
    while candidate.exists():
        candidate = candidate.with_name(f"{base_path.stem}_{counter}{base_path.suffix}")
        counter += 1
    candidate.write_bytes(payload)
    return candidate


def _supports_floss(parsed: dict[str, Any]) -> bool:
    return parsed.get("file_type", {}).get("format") == "PE"


def _run_floss(path: str | Path, *, min_length: int) -> dict[str, Any]:
    env = os.environ.copy()
    env["HOME"] = str(Path(path).resolve().parent)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "floss.main",
                "--json",
                "--minimum-length",
                str(max(1, int(min_length))),
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            env=env,
        )
    except FileNotFoundError:
        return {"ok": False, "code": "floss_not_installed", "message": "FLARE FLOSS is not installed in this environment."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "floss_timeout", "message": "FLARE FLOSS did not finish before the timeout."}

    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        return {
            "ok": False,
            "code": "floss_failed",
            "message": stderr or f"FLARE FLOSS exited with status {completed.returncode}.",
        }

    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "code": "floss_invalid_json",
            "message": f"FLARE FLOSS returned invalid JSON: {exc.msg}.",
        }
    return {"ok": True, "document": document}


def _decode_floss_strings(document: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    strings = document.get("strings", {})
    for field_name, (method_name, confidence_level, evidence_label) in FLOSS_METHOD_MAP.items():
        for candidate in strings.get(field_name, []):
            decoded_value = str(candidate.get("string") or "")
            if not decoded_value:
                continue
            address = (
                candidate.get("decoded_at")
                or candidate.get("program_counter")
                or candidate.get("address")
            )
            items.append(
                {
                    "source_value": None,
                    "decoded_value": decoded_value,
                    "method": method_name,
                    "string_id": None,
                    "address": int(address) if isinstance(address, int) else None,
                    "encoding": candidate.get("encoding"),
                    "confidence": {"level": confidence_level, "method": "FLARE FLOSS"},
                    "evidence": [f"Recovered by FLARE FLOSS {evidence_label}."],
                }
            )
    return items


def _decode_candidates(value: str) -> list[tuple[str, str]]:
    candidates = []
    compact = value.strip()
    if len(compact) >= 8 and len(compact) % 4 == 0:
        try:
            decoded = base64.b64decode(compact, validate=True)
        except binascii.Error:
            decoded = None
        if decoded:
            printable = _printable_ratio(decoded)
            if printable >= 0.85:
                candidates.append(("base64", decoded.decode("utf-8", errors="replace")))
    if len(compact) >= 8 and len(compact) % 2 == 0 and all(ch in "0123456789abcdefABCDEF" for ch in compact):
        try:
            decoded = bytes.fromhex(compact)
        except ValueError:
            decoded = None
        if decoded and _printable_ratio(decoded) >= 0.85:
            candidates.append(("hex", decoded.decode("utf-8", errors="replace")))
    return candidates


def _printable_ratio(data: bytes) -> float:
    if not data:
        return 0.0
    return sum(1 for byte in data if byte in PRINTABLE_ASCII) / len(data)


def _shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    buckets = [0] * 256
    for byte in data:
        buckets[byte] += 1
    entropy = 0.0
    length = len(data)
    for count in buckets:
        if not count:
            continue
        probability = count / length
        entropy -= probability * math.log2(probability)
    return entropy


def _entropy_label(data: bytes) -> str:
    entropy = _shannon_entropy(data)
    if entropy >= 7.2:
        return "high"
    if entropy >= 5.0:
        return "medium"
    return "low"


def _section_permissions(parsed: dict[str, Any], section_name: str | None) -> str:
    for section in parsed.get("sections", []):
        if section.get("name") == section_name:
            return str(section.get("permissions", ""))
    return ""


def _looks_like_decompression_bomb(size: int, compressed_size: int) -> bool:
    if size <= 0:
        return False
    effective_compressed = max(1, compressed_size)
    return size > 1_000_000 and size / effective_compressed > 1000
