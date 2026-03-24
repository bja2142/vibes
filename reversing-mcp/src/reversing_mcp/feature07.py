from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

try:  # pragma: no cover - install-time dependency
    from pycparser import c_ast, c_parser
except ImportError:  # pragma: no cover
    c_ast = None
    c_parser = None

from .errors import StructuredToolError
from .triage import context_for_offset, parse_artifact, translate_value
from .utils import stable_json_dumps

SENSITIVE_SECTION_NAMES = {
    ".dynamic",
    ".dynsym",
    ".dynstr",
    ".got",
    ".got.plt",
    ".plt",
    ".idata",
    ".edata",
    ".reloc",
    ".rsrc",
    ".eh_frame",
    ".eh_frame_hdr",
    ".pdata",
    ".xdata",
}
ZERO_FILL_BYTES = {0x00, 0x90, 0xCC}
TEXT_EXPORT_LIMIT = 80
X86_ISA_ALIASES = {"x86", "x86_64", "amd64"}
ARM_ISA_ALIASES = {"arm", "arm32"}
THUMB_ISA_ALIASES = {"thumb", "thumb2"}
AARCH64_ISA_ALIASES = {"aarch64", "arm64"}


def parse_artifact_context(path: str | Path, *, hints: dict[str, Any] | None = None) -> dict[str, Any]:
    target = Path(path)
    data = target.read_bytes()
    parsed = parse_artifact(target, data, hints or {}, {})
    return {"path": target, "data": data, "parsed": parsed}


def decode_patch_bytes(bytes_hex: str) -> bytes:
    if not isinstance(bytes_hex, str) or not bytes_hex.strip():
        raise StructuredToolError("invalid_request", "patch_bytes_required", "bytes_hex must be a non-empty hexadecimal string.")
    normalized = re.sub(r"[^0-9a-fA-F]", "", bytes_hex)
    if not normalized or len(normalized) % 2:
        raise StructuredToolError("invalid_request", "patch_bytes_invalid", "bytes_hex must contain an even number of hexadecimal nybbles.")
    return bytes.fromhex(normalized)


def resolve_patch_location(path: str | Path, *, input_kind: str, value: int, hints: dict[str, Any] | None = None) -> dict[str, Any]:
    context = parse_artifact_context(path, hints=hints)
    data = context["data"]
    parsed = context["parsed"]
    normalized_value = int(value)
    if input_kind == "file_offset":
        if normalized_value < 0 or normalized_value >= len(data):
            raise StructuredToolError("not_found", "patch_offset_out_of_range", f"File offset 0x{normalized_value:x} is outside the artifact.")
        mapping = context_for_offset(parsed, normalized_value)
        return {
            **context,
            "file_offset": normalized_value,
            "virtual_address": mapping.get("virtual_address"),
            "rva": mapping.get("rva"),
            "section": mapping.get("section"),
            "segment": mapping.get("segment"),
        }
    matches = translate_value(parsed, input_kind, normalized_value)
    if not matches:
        raise StructuredToolError("not_found", "patch_location_not_mapped", f"Unable to translate {input_kind} value 0x{normalized_value:x}.")
    match = matches[0]
    return {
        **context,
        "file_offset": int(match["file_offset"]),
        "virtual_address": match.get("virtual_address"),
        "rva": match.get("rva"),
        "section": match.get("name") if match.get("mapping_kind") == "section" else None,
        "segment": match.get("name") if match.get("mapping_kind") == "segment" else None,
    }


def build_patch_report(
    path: str | Path,
    *,
    input_kind: str,
    value: int,
    patch_bytes: bytes,
    analysis: dict[str, Any] | None = None,
    hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = resolve_patch_location(path, input_kind=input_kind, value=value, hints=hints)
    file_offset = int(resolved["file_offset"])
    data = resolved["data"]
    if file_offset + len(patch_bytes) > len(data):
        raise StructuredToolError(
            "invalid_request",
            "patch_range_out_of_bounds",
            "The requested patch would extend past the end of the artifact.",
            details={"file_offset": file_offset, "patch_size": len(patch_bytes), "size_bytes": len(data)},
        )
    before = data[file_offset : file_offset + len(patch_bytes)]
    warnings = collect_patch_warnings(
        parsed=resolved["parsed"],
        analysis=analysis,
        file_offset=file_offset,
        patch_size=len(patch_bytes),
        virtual_address=resolved.get("virtual_address"),
        rva=resolved.get("rva"),
    )
    return {
        "resolved": {
            "file_offset": file_offset,
            "virtual_address": resolved.get("virtual_address"),
            "rva": resolved.get("rva"),
            "section": resolved.get("section"),
            "segment": resolved.get("segment"),
        },
        "before": {"bytes_hex": before.hex()},
        "after": {"bytes_hex": patch_bytes.hex()},
        "warnings": warnings,
    }


def apply_patch_bytes(path: str | Path, *, file_offset: int, patch_bytes: bytes, output_path: str | Path) -> dict[str, Any]:
    source = Path(path)
    output = Path(output_path)
    data = bytearray(source.read_bytes())
    data[file_offset : file_offset + len(patch_bytes)] = patch_bytes
    output.write_bytes(data)
    return {
        "path": str(output),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def collect_patch_warnings(
    *,
    parsed: dict[str, Any],
    analysis: dict[str, Any] | None,
    file_offset: int,
    patch_size: int,
    virtual_address: int | None,
    rva: int | None,
) -> list[str]:
    warnings: list[str] = []
    patch_end = file_offset + max(1, int(patch_size))
    section = next(
        (item for item in parsed.get("sections", []) if int(item.get("file_offset", -1)) <= file_offset < int(item.get("file_offset", -1)) + max(int(item.get("size", 0)), 1)),
        None,
    )
    if section is not None:
        permissions = str(section.get("permissions") or "")
        name = str(section.get("name") or "")
        if "x" in permissions:
            warnings.append(f"Patch overlaps executable section '{name}' and may split decoded instructions.")
        if name in SENSITIVE_SECTION_NAMES:
            warnings.append(f"Patch touches structurally sensitive section '{name}'.")
    minimum_section_offset = min((int(item.get("file_offset", 0)) for item in parsed.get("sections", []) if int(item.get("size", 0)) > 0), default=0)
    if file_offset < minimum_section_offset:
        warnings.append("Patch falls inside file header or pre-section metadata.")
    if analysis is not None:
        for relocation in analysis.get("linkage", {}).get("relocations", []):
            location = relocation.get("offset")
            if location is None:
                continue
            if virtual_address is not None and int(location) == int(virtual_address):
                warnings.append(f"Patch overlaps relocation entry at address 0x{int(location):x}.")
                break
            if rva is not None and int(location) == int(rva):
                warnings.append(f"Patch overlaps relocation entry at RVA 0x{int(location):x}.")
                break
        for detail in analysis.get("function_details", {}).values():
            for instruction in detail.get("instructions", []):
                instruction_offset = instruction.get("file_offset")
                if instruction_offset is None:
                    continue
                size = max(1, len(bytes.fromhex(instruction.get("bytes", ""))))
                if int(instruction_offset) < patch_end and int(instruction_offset) + size > file_offset:
                    warnings.append(f"Patch overlaps decoded instruction at 0x{int(instruction['address']):x}.")
                    return warnings
    return warnings


def assemble_patch(isa: str, assembly: str, *, origin_virtual_address: int | None = None) -> dict[str, Any]:
    normalized_isa = _normalize_patch_isa(isa)
    instructions = [item.strip() for item in re.split(r"[;\n]+", assembly) if item.strip()]
    if not instructions:
        raise StructuredToolError("invalid_request", "assembly_required", "assembly must contain at least one instruction.")
    current = int(origin_virtual_address or 0)
    payload = bytearray()
    rendered = []
    for instruction in instructions:
        encoded = _encode_instruction(normalized_isa, instruction, current=current)
        payload.extend(encoded)
        rendered.append({"instruction": instruction, "bytes_hex": encoded.hex()})
        current += len(encoded)
    return {
        "isa": normalized_isa,
        "bytes": bytes(payload),
        "items": rendered,
        "supported_examples": _supported_examples_for_isa(normalized_isa),
    }


def discover_code_caves(path: str | Path, *, min_size: int = 32, hints: dict[str, Any] | None = None) -> dict[str, Any]:
    context = parse_artifact_context(path, hints=hints)
    data = context["data"]
    parsed = context["parsed"]
    caves = []
    mappings = parsed.get("sections") or parsed.get("segments") or []
    threshold = max(4, int(min_size))
    for mapping in mappings:
        offset = int(mapping.get("file_offset", 0))
        size = int(mapping.get("size", 0))
        if size <= 0 or offset < 0 or offset >= len(data):
            continue
        chunk = data[offset : min(len(data), offset + size)]
        run_start = None
        run_byte = None
        for index, byte in enumerate(chunk):
            if byte in ZERO_FILL_BYTES:
                if run_start is None:
                    run_start = index
                    run_byte = byte
                elif byte != run_byte:
                    if index - run_start >= threshold:
                        caves.append(_code_cave_record(mapping, offset + run_start, index - run_start, run_byte))
                    run_start = index
                    run_byte = byte
                continue
            if run_start is not None and index - run_start >= threshold:
                caves.append(_code_cave_record(mapping, offset + run_start, index - run_start, run_byte))
            run_start = None
            run_byte = None
        if run_start is not None and len(chunk) - run_start >= threshold:
            caves.append(_code_cave_record(mapping, offset + run_start, len(chunk) - run_start, run_byte))
    caves.sort(key=lambda item: (-item["size"], item["file_offset"]))
    return {
        "items": caves,
        "summary": {
            "total": len(caves),
            "largest_size": max((item["size"] for item in caves), default=0),
        },
    }


def import_type_definitions(source_text: str, *, source_format: str) -> dict[str, Any]:
    normalized_format = (source_format or "").strip().lower()
    if normalized_format == "structured_json":
        try:
            payload = json.loads(source_text)
        except json.JSONDecodeError as exc:
            raise StructuredToolError("invalid_request", "type_import_json_invalid", "structured_json payload could not be parsed.") from exc
        if not isinstance(payload, dict):
            raise StructuredToolError("invalid_request", "type_import_json_invalid", "structured_json payload must decode to a JSON object.")
        return {
            "source_format": normalized_format,
            "named_types": payload.get("named_types", {}),
            "function_signatures": payload.get("function_signatures", []),
            "typedefs": payload.get("typedefs", []),
        }
    if normalized_format != "c_header":
        raise StructuredToolError("unsupported_format", "type_import_format_unsupported", f"Unsupported type import format '{source_format}'.")
    if c_parser is None or c_ast is None:  # pragma: no cover - dependency availability
        raise StructuredToolError("backend_failure", "type_import_backend_missing", "pycparser is not available.")
    sanitized = "\n".join(line for line in source_text.splitlines() if not line.lstrip().startswith("#"))
    parser = c_parser.CParser()
    try:
        translation_unit = parser.parse(sanitized)
    except Exception as exc:
        raise StructuredToolError("invalid_request", "type_import_parse_failed", str(exc)) from exc
    collector = _TypeCollector()
    collector.visit(translation_unit)
    return {
        "source_format": normalized_format,
        "named_types": collector.named_types,
        "function_signatures": collector.function_signatures,
        "typedefs": collector.typedefs,
    }


def build_analysis_report(
    artifact: dict[str, Any],
    *,
    parsed: dict[str, Any],
    analysis: dict[str, Any] | None,
    feature07: dict[str, Any],
    format_name: str,
) -> dict[str, Any]:
    payload = {
        "artifact": {
            "artifact_id": artifact["artifact_id"],
            "display_name": artifact["display_name"],
            "relative_path": artifact["relative_path"],
            "size_bytes": artifact["size_bytes"],
        },
        "file_type": parsed.get("file_type", {}),
        "header": parsed.get("header", {}),
        "dependencies": parsed.get("taxonomy_inputs", {}).get("imports", []),
        "analysis_summary": analysis.get("summary") if analysis else None,
        "patch_history": feature07.get("patch_history", []),
        "edits": feature07.get("edits", {}),
    }
    normalized = (format_name or "json").strip().lower()
    if normalized == "text":
        lines = [
            f"Artifact: {artifact['display_name']} ({artifact['artifact_id']})",
            f"Path: {artifact['relative_path']}",
            f"Format: {parsed.get('file_type', {}).get('format', 'unknown')}",
            f"Architecture: {parsed.get('file_type', {}).get('architecture', 'unknown')}",
            f"Imports: {', '.join(parsed.get('taxonomy_inputs', {}).get('imports', [])[:TEXT_EXPORT_LIMIT]) or 'none'}",
            f"Patch count: {len(feature07.get('patch_history', []))}",
            f"Type import count: {len(feature07.get('edits', {}).get('type_imports', []))}",
        ]
        if analysis:
            summary = analysis.get("summary", {})
            lines.extend(
                [
                    f"Functions: {summary.get('function_count', 0)}",
                    f"Strings: {summary.get('string_count', 0)}",
                    f"Imports (analysis): {summary.get('import_count', 0)}",
                ]
            )
        return {
            "format": normalized,
            "text": "\n".join(lines),
            "json": payload,
            "truncated": False,
        }
    return {"format": "json", "json": payload, "truncated": False}


def list_dependencies(
    artifact: dict[str, Any],
    *,
    parsed: dict[str, Any],
    analysis: dict[str, Any] | None,
    relationships: dict[str, Any],
) -> dict[str, Any]:
    imports = []
    for item in parsed.get("taxonomy_inputs", {}).get("imports", []):
        imports.append({"name": item, "kind": "declared_import"})
    for item in (analysis or {}).get("linkage", {}).get("imports", []):
        name = item.get("name")
        if name and not any(existing["name"] == name for existing in imports):
            imports.append({"name": name, "kind": "linked_symbol"})
    return {
        "artifact": {
            "artifact_id": artifact["artifact_id"],
            "display_name": artifact["display_name"],
        },
        "imports": imports,
        "related_artifacts": relationships.get("parents", []) + relationships.get("children", []),
    }


def correlate_artifacts(records: list[dict[str, Any]]) -> dict[str, Any]:
    shared_imports: dict[str, list[str]] = {}
    shared_strings: dict[str, list[str]] = {}
    shared_functions: dict[str, list[str]] = {}
    payload_by_artifact = {item["artifact"]["artifact_id"]: item for item in records}
    for record in records:
        artifact_id = record["artifact"]["artifact_id"]
        for import_name in record["parsed"].get("taxonomy_inputs", {}).get("imports", []):
            shared_imports.setdefault(import_name, []).append(artifact_id)
        for string_item in (record.get("analysis") or {}).get("strings", [])[:200]:
            value = string_item.get("value")
            if value and len(value) >= 6:
                shared_strings.setdefault(value, []).append(artifact_id)
        for function in (record.get("analysis") or {}).get("functions", []):
            name = (function.get("demangled_name") or function.get("name") or "").strip()
            if name:
                shared_functions.setdefault(name, []).append(artifact_id)
    correlations = []
    for kind, collection in (("import", shared_imports), ("string", shared_strings), ("function", shared_functions)):
        for value, artifact_ids in collection.items():
            unique = sorted(set(artifact_ids))
            if len(unique) < 2:
                continue
            correlations.append(
                {
                    "kind": kind,
                    "value": value,
                    "artifacts": [payload_by_artifact[item]["artifact"] for item in unique],
                    "count": len(unique),
                }
            )
    correlations.sort(key=lambda item: (-item["count"], item["kind"], item["value"]))
    return {"items": correlations}


def diff_artifacts(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_data = Path(left["artifact"]["canonical_path"]).read_bytes()
    right_data = Path(right["artifact"]["canonical_path"]).read_bytes()
    left_imports = sorted(set(left["parsed"].get("taxonomy_inputs", {}).get("imports", [])))
    right_imports = sorted(set(right["parsed"].get("taxonomy_inputs", {}).get("imports", [])))
    left_functions = sorted({_normalized_function_name(item) for item in (left.get("analysis") or {}).get("functions", []) if _normalized_function_name(item)})
    right_functions = sorted({_normalized_function_name(item) for item in (right.get("analysis") or {}).get("functions", []) if _normalized_function_name(item)})
    left_strings = sorted({item.get("value") for item in (left.get("analysis") or {}).get("strings", []) if item.get("value")})
    right_strings = sorted({item.get("value") for item in (right.get("analysis") or {}).get("strings", []) if item.get("value")})
    shared_strings = sorted(set(left_strings).intersection(right_strings))
    left_only_strings = sorted(set(left_strings).difference(right_strings))
    right_only_strings = sorted(set(right_strings).difference(left_strings))
    return {
        "artifacts": {
            "left": {"artifact_id": left["artifact"]["artifact_id"], "display_name": left["artifact"]["display_name"]},
            "right": {"artifact_id": right["artifact"]["artifact_id"], "display_name": right["artifact"]["display_name"]},
        },
        "available_levels": {
            "structural": True,
            "functions": bool(left.get("analysis") and right.get("analysis")),
            "strings": bool(left.get("analysis") and right.get("analysis")),
            "semantic": False,
        },
        "structural": {
            "size_delta": len(right_data) - len(left_data),
            "sha256_changed": hashlib.sha256(left_data).hexdigest() != hashlib.sha256(right_data).hexdigest(),
            "format_changed": left["parsed"].get("file_type", {}).get("format") != right["parsed"].get("file_type", {}).get("format"),
        },
        "imports": {
            "shared": sorted(set(left_imports).intersection(right_imports)),
            "left_only": sorted(set(left_imports).difference(right_imports)),
            "right_only": sorted(set(right_imports).difference(left_imports)),
        },
        "functions": {
            "shared": sorted(set(left_functions).intersection(right_functions)),
            "left_only": sorted(set(left_functions).difference(right_functions)),
            "right_only": sorted(set(right_functions).difference(left_functions)),
        },
        "strings": {
            "shared": shared_strings[:TEXT_EXPORT_LIMIT],
            "left_only": left_only_strings[:TEXT_EXPORT_LIMIT],
            "right_only": right_only_strings[:TEXT_EXPORT_LIMIT],
            "limits": {
                "max_items_per_bucket": TEXT_EXPORT_LIMIT,
                "truncated": any(len(items) > TEXT_EXPORT_LIMIT for items in (shared_strings, left_only_strings, right_only_strings)),
                "totals": {
                    "shared": len(shared_strings),
                    "left_only": len(left_only_strings),
                    "right_only": len(right_only_strings),
                },
            },
        },
        "notes": ["Semantic diffing is not available in this MVP slice; only structural and recovered-object comparisons are reported."],
    }


def render_command_log(entries: list[dict[str, Any]], *, format_name: str) -> dict[str, Any]:
    normalized = (format_name or "json").strip().lower()
    if normalized == "text":
        lines = []
        for entry in entries:
            artifact_ref = f" artifact={entry['artifact_id']}" if entry.get("artifact_id") else ""
            details = stable_json_dumps(entry.get("details", {})).replace("\n", " ")
            lines.append(f"{entry['created_at']} {entry['tool_name']} {entry['action']}{artifact_ref} {details}".rstrip())
        return {"format": normalized, "text": "\n".join(lines), "line_count": len(lines), "truncated": False}
    return {"format": "json", "json": {"items": entries}, "line_count": len(entries), "truncated": False}


def _parse_numeric_operand(raw: str) -> int:
    value = raw.strip().lower()
    try:
        return int(value, 16 if value.startswith("0x") else 10)
    except ValueError as exc:
        raise StructuredToolError("invalid_request", "assembly_operand_invalid", f"Unable to parse numeric operand '{raw}'.") from exc


def _normalize_patch_isa(isa: str) -> str:
    normalized = (isa or "").strip().lower()
    if normalized in X86_ISA_ALIASES:
        return "x86_64" if normalized in {"x86_64", "amd64"} else "x86"
    if normalized in AARCH64_ISA_ALIASES:
        return "aarch64"
    if normalized in ARM_ISA_ALIASES:
        return "arm"
    if normalized in THUMB_ISA_ALIASES:
        return "thumb"
    raise StructuredToolError("unsupported_format", "assembly_isa_unsupported", f"Assembly backend does not support ISA '{isa}'.")


def _supported_examples_for_isa(isa: str) -> list[str]:
    if isa in {"x86", "x86_64"}:
        return ["nop", "ret", "int3", "jmp 0x401000", "call 0x401050"]
    if isa == "aarch64":
        return ["nop", "ret", "brk 0", "b 0x401000", "bl 0x401050"]
    if isa == "arm":
        return ["nop", "ret", "bkpt 0", "b 0x401000", "bl 0x401050"]
    if isa == "thumb":
        return ["nop", "ret", "bkpt 0", "b 0x401000"]
    return []


def _encode_instruction(isa: str, instruction: str, *, current: int) -> bytes:
    if isa in {"x86", "x86_64"}:
        return _encode_x86_instruction(instruction, current=current)
    if isa == "aarch64":
        return _encode_aarch64_instruction(instruction, current=current)
    if isa == "arm":
        return _encode_arm_instruction(instruction, current=current)
    if isa == "thumb":
        return _encode_thumb_instruction(instruction, current=current)
    raise StructuredToolError("unsupported_format", "assembly_isa_unsupported", f"Assembly backend does not support ISA '{isa}'.")


def _split_instruction(instruction: str) -> tuple[str, str | None]:
    parts = instruction.strip().split(None, 1)
    mnemonic = parts[0].lower()
    operand = parts[1].strip() if len(parts) > 1 else None
    return mnemonic, operand


def _unsupported_instruction(isa: str, instruction: str) -> StructuredToolError:
    return StructuredToolError(
        "unsupported_format",
        "assembly_instruction_unsupported",
        f"Assembly backend could not encode instruction '{instruction}' for ISA '{isa}'.",
        details={"supported_examples": _supported_examples_for_isa(isa)},
    )


def _encode_u32(word: int) -> bytes:
    return int(word & 0xFFFFFFFF).to_bytes(4, "little", signed=False)


def _encode_u16(word: int) -> bytes:
    return int(word & 0xFFFF).to_bytes(2, "little", signed=False)


def _require_aligned_delta(delta: int, *, alignment: int, instruction: str) -> None:
    if delta % alignment != 0:
        raise StructuredToolError(
            "invalid_request",
            "assembly_target_alignment_invalid",
            f"Instruction '{instruction}' requires a target aligned to {alignment} bytes relative to the current origin.",
        )


def _encode_signed_immediate(delta: int, *, shift: int, bits: int, instruction: str) -> int:
    _require_aligned_delta(delta, alignment=1 << shift, instruction=instruction)
    scaled = delta >> shift
    minimum = -(1 << (bits - 1))
    maximum = (1 << (bits - 1)) - 1
    if scaled < minimum or scaled > maximum:
        raise StructuredToolError(
            "invalid_request",
            "assembly_target_out_of_range",
            f"Instruction '{instruction}' target is outside the encodable range.",
            details={"delta": delta, "scaled_delta": scaled, "bits": bits},
        )
    return scaled


def _encode_x86_instruction(instruction: str, *, current: int) -> bytes:
    mnemonic, operand = _split_instruction(instruction)
    if mnemonic == "nop" and operand is None:
        return b"\x90"
    if mnemonic == "ret" and operand is None:
        return b"\xC3"
    if mnemonic == "int3" and operand is None:
        return b"\xCC"
    if mnemonic == "jmp" and operand is not None:
        target = _parse_numeric_operand(operand)
        return b"\xE9" + int(target - (current + 5)).to_bytes(4, "little", signed=True)
    if mnemonic == "call" and operand is not None:
        target = _parse_numeric_operand(operand)
        return b"\xE8" + int(target - (current + 5)).to_bytes(4, "little", signed=True)
    raise _unsupported_instruction("x86_64", instruction)


def _encode_aarch64_instruction(instruction: str, *, current: int) -> bytes:
    mnemonic, operand = _split_instruction(instruction)
    if mnemonic == "nop" and operand is None:
        return _encode_u32(0xD503201F)
    if mnemonic == "ret" and operand is None:
        return _encode_u32(0xD65F03C0)
    if mnemonic == "brk":
        immediate = 0 if operand is None else _parse_numeric_operand(operand)
        if immediate < 0 or immediate > 0xFFFF:
            raise StructuredToolError("invalid_request", "assembly_operand_out_of_range", "brk immediate must fit in 16 bits.")
        return _encode_u32(0xD4200000 | ((immediate & 0xFFFF) << 5))
    if mnemonic in {"b", "bl"} and operand is not None:
        target = _parse_numeric_operand(operand)
        scaled = _encode_signed_immediate(target - current, shift=2, bits=26, instruction=instruction)
        base = 0x14000000 if mnemonic == "b" else 0x94000000
        return _encode_u32(base | (scaled & 0x03FFFFFF))
    raise _unsupported_instruction("aarch64", instruction)


def _encode_arm_instruction(instruction: str, *, current: int) -> bytes:
    mnemonic, operand = _split_instruction(instruction)
    if mnemonic == "nop" and operand is None:
        return _encode_u32(0xE320F000)
    if mnemonic == "ret" and operand is None:
        return _encode_u32(0xE12FFF1E)
    if mnemonic == "bkpt":
        immediate = 0 if operand is None else _parse_numeric_operand(operand)
        if immediate < 0 or immediate > 0xFFFF:
            raise StructuredToolError("invalid_request", "assembly_operand_out_of_range", "bkpt immediate must fit in 16 bits.")
        return _encode_u32(0xE1200070 | ((immediate & 0xFFF0) << 4) | (immediate & 0xF))
    if mnemonic in {"b", "bl"} and operand is not None:
        target = _parse_numeric_operand(operand)
        scaled = _encode_signed_immediate(target - (current + 8), shift=2, bits=24, instruction=instruction)
        base = 0xEA000000 if mnemonic == "b" else 0xEB000000
        return _encode_u32(base | (scaled & 0x00FFFFFF))
    raise _unsupported_instruction("arm", instruction)


def _encode_thumb_instruction(instruction: str, *, current: int) -> bytes:
    mnemonic, operand = _split_instruction(instruction)
    if mnemonic == "nop" and operand is None:
        return _encode_u16(0xBF00)
    if mnemonic == "ret" and operand is None:
        return _encode_u16(0x4770)
    if mnemonic == "bkpt":
        immediate = 0 if operand is None else _parse_numeric_operand(operand)
        if immediate < 0 or immediate > 0xFF:
            raise StructuredToolError("invalid_request", "assembly_operand_out_of_range", "thumb bkpt immediate must fit in 8 bits.")
        return _encode_u16(0xBE00 | (immediate & 0xFF))
    if mnemonic == "b" and operand is not None:
        target = _parse_numeric_operand(operand)
        scaled = _encode_signed_immediate(target - (current + 4), shift=1, bits=11, instruction=instruction)
        return _encode_u16(0xE000 | (scaled & 0x07FF))
    raise _unsupported_instruction("thumb", instruction)


def _code_cave_record(mapping: dict[str, Any], file_offset: int, size: int, fill_byte: int | None) -> dict[str, Any]:
    delta = file_offset - int(mapping.get("file_offset", 0))
    virtual_address = mapping.get("virtual_address")
    rva = mapping.get("rva")
    return {
        "section": mapping.get("name"),
        "file_offset": file_offset,
        "virtual_address": int(virtual_address) + delta if virtual_address is not None else None,
        "rva": int(rva) + delta if rva is not None else None,
        "size": size,
        "permissions": mapping.get("permissions"),
        "fill_byte": None if fill_byte is None else f"0x{fill_byte:02x}",
    }


def _normalized_function_name(function: dict[str, Any]) -> str:
    return str(function.get("demangled_name") or function.get("name") or "").strip()


if c_ast is not None:

    class _TypeCollector(c_ast.NodeVisitor):  # type: ignore[misc]
        def __init__(self) -> None:
            self.named_types: dict[str, dict[str, Any]] = {"structs": {}, "enums": {}, "typedefs": {}}
            self.function_signatures: list[dict[str, Any]] = []
            self.typedefs: list[dict[str, Any]] = []

        def visit_Typedef(self, node):  # noqa: N802
            rendered = _render_decl(node.type)
            entry = {"name": node.name, "definition": rendered}
            self.named_types["typedefs"][node.name] = entry
            self.typedefs.append(entry)
            self.generic_visit(node)

        def visit_Decl(self, node):  # noqa: N802
            if isinstance(node.type, c_ast.FuncDecl):
                signature = _render_decl(node)
                self.function_signatures.append({"name": node.name, "signature": signature})
            self.generic_visit(node)

        def visit_Struct(self, node):  # noqa: N802
            if node.name:
                self.named_types["structs"][node.name] = {
                    "name": node.name,
                    "fields": [
                        {"name": decl.name, "type": _render_decl(decl.type)}
                        for decl in (node.decls or [])
                    ],
                }
            self.generic_visit(node)

        def visit_Enum(self, node):  # noqa: N802
            if node.name:
                self.named_types["enums"][node.name] = {
                    "name": node.name,
                    "values": [
                        {"name": enumerator.name, "value": _render_expr(getattr(enumerator, "value", None))}
                        for enumerator in (node.values.enumerators if node.values is not None else [])
                    ],
                }
            self.generic_visit(node)


    def _render_decl(node: Any) -> str:
        if node is None:
            return "void"
        if isinstance(node, c_ast.TypeDecl):
            return _render_decl(node.type)
        if isinstance(node, c_ast.IdentifierType):
            return " ".join(node.names)
        if isinstance(node, c_ast.PtrDecl):
            return f"{_render_decl(node.type)} *"
        if isinstance(node, c_ast.ArrayDecl):
            return f"{_render_decl(node.type)}[]"
        if isinstance(node, c_ast.FuncDecl):
            args = []
            if node.args is not None:
                for param in node.args.params:
                    args.append(_render_decl(param))
            return f"{_render_decl(node.type)} ({', '.join(args) if args else 'void'})"
        if isinstance(node, c_ast.Struct):
            return f"struct {node.name or '<anonymous>'}"
        if isinstance(node, c_ast.Enum):
            return f"enum {node.name or '<anonymous>'}"
        if isinstance(node, c_ast.Typename):
            return _render_decl(node.type)
        if isinstance(node, c_ast.Decl):
            if isinstance(node.type, c_ast.FuncDecl):
                params = []
                if node.type.args is not None:
                    for param in node.type.args.params:
                        params.append(f"{_render_decl(param.type)} {param.name or ''}".strip())
                return f"{_render_decl(node.type.type)} {node.name}({', '.join(params) if params else 'void'})"
            return f"{_render_decl(node.type)} {node.name}".strip()
        return type(node).__name__


    def _render_expr(node: Any) -> str | None:
        if node is None:
            return None
        if isinstance(node, c_ast.Constant):
            return str(node.value)
        return type(node).__name__

else:

    class _TypeCollector:  # pragma: no cover - guarded by import_type_definitions()
        def __init__(self) -> None:
            raise StructuredToolError("backend_failure", "type_import_backend_missing", "pycparser is not available.")


    def _render_decl(node: Any) -> str:
        raise StructuredToolError("backend_failure", "type_import_backend_missing", "pycparser is not available.")


    def _render_expr(node: Any) -> str | None:
        raise StructuredToolError("backend_failure", "type_import_backend_missing", "pycparser is not available.")
