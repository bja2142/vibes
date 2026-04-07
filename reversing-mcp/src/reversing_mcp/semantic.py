from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .triage import translate_value

IMMEDIATE_RE = re.compile(r"(?:^|[^A-Za-z0-9_])(0x[0-9a-fA-F]+|\d+)(?:$|[^A-Za-z0-9_])")
_GENERIC_NAME_RE = re.compile(r"^sub_[0-9A-Fa-f]+$")
STACK_ACCESS_RE = re.compile(r"\[(sp|x29|fp|rbp|ebp),\s*#?(-?0x[0-9a-fA-F]+|-?\d+)")
REGISTER_RE = re.compile(r"\b([wxvsdq][0-9]+|x29|x30|sp|fp|lr|r(?:ax|bx|cx|dx|si|di|bp|sp|8|9|10|11|12|13|14|15)|e(?:ax|bx|cx|dx|si|di|bp|sp)|[abcd]l)\b")
PRINTABLE_ASCII = set(range(0x20, 0x7F))
CONTROL_TRANSFER = {
    "b",
    "ba",
    "beq",
    "bne",
    "bge",
    "bgt",
    "ble",
    "blt",
    "bx",
    "br",
    "jmp",
    "je",
    "jne",
    "ja",
    "jb",
    "jg",
    "jl",
    "ret",
}
INDIRECT_FLOW_MNEMONICS = {"blr", "br", "call", "callq", "jmp", "jalr"}
SYSCALL_MNEMONICS = {"svc", "syscall", "int"}


def build_semantic_views(
    project: Any,
    path: str | Path,
    parsed: dict[str, Any],
    functions: list[dict[str, Any]],
    kb_functions: dict[int, Any],
    symbols: list[dict[str, Any]],
    strings: list[dict[str, Any]],
    xrefs: list[dict[str, Any]],
    debug_info: dict[str, Any],
) -> dict[str, Any]:
    normalized_strings = [_normalize_string_record(item) for item in strings]
    address_names = _address_name_map(functions, symbols, normalized_strings)
    function_details: dict[str, Any] = {}
    enriched_functions: list[dict[str, Any]] = []
    for function in functions:
        kb_function = kb_functions.get(int(function["address"]))
        instructions = _disassemble_blocks(project, path, parsed=parsed, blocks=function["blocks"], address_names=address_names)
        cfg = _recover_control_flow_graph(function, kb_function, instructions)
        variables = _recover_variables(project.arch.name, parsed, function, instructions)
        stack_frame = _recover_stack_frame(function, variables)
        constant_propagation = _recover_constant_propagation(project.arch.name, function, instructions)
        indirect_flows = _recover_indirect_flows(function, instructions)
        calling_convention = _recover_calling_convention(project.arch.name, function)
        intermediate_representation = _recover_intermediate_representation(project, function)
        system_calls = _identify_system_calls(project.arch.name, instructions)
        tags = _classify_function(function, instructions, indirect_flows)
        triage_score = _score_function(function, cfg, indirect_flows, tags, system_calls)
        detail = {
            "control_flow_graph": cfg,
            "variables": variables,
            "stack_frame": stack_frame,
            "constant_propagation": constant_propagation,
            "indirect_flows": indirect_flows,
            "calling_convention": calling_convention,
            "intermediate_representation": intermediate_representation,
            "system_calls": system_calls,
            "classification_tags": tags,
            "triage_score": triage_score,
            "instructions": instructions,
        }
        function_details[str(int(function["address"]))] = detail
        enriched_functions.append(
            {
                **function,
                "classification_tags": [item["tag"] for item in tags],
                "triage_score": triage_score,
            }
        )
    data_segments = _inspect_data_segments(path, parsed, normalized_strings, functions)
    runtime_metadata = _recover_runtime_metadata(parsed, enriched_functions, symbols, normalized_strings)
    recovered_types = _recover_types(enriched_functions, symbols, runtime_metadata, data_segments)
    type_information = _build_type_information(enriched_functions, recovered_types, data_segments)
    exception_metadata = _recover_exception_metadata(parsed, symbols)
    call_graph = _build_call_graph(enriched_functions, symbols, xrefs)
    return {
        "functions": enriched_functions,
        "strings": normalized_strings,
        "function_details": function_details,
        "call_graph": call_graph,
        "type_information": type_information,
        "recovered_types": recovered_types,
        "data_segments": data_segments,
        "exception_metadata": exception_metadata,
        "runtime_metadata": runtime_metadata,
    }


def _normalize_string_record(item: dict[str, Any]) -> dict[str, Any]:
    record = dict(item)
    if record.get("address") is None:
        for key in ("virtual_address", "relative_virtual_address"):
            if record.get(key) is not None:
                record["address"] = int(record[key])
                break
    return record


def _address_name_map(functions: list[dict[str, Any]], symbols: list[dict[str, Any]], strings: list[dict[str, Any]]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for function in functions:
        mapping[int(function["address"])] = function.get("demangled_name") or function["name"]
    for symbol in symbols:
        if symbol.get("address") is not None:
            mapping[int(symbol["address"])] = symbol.get("demangled_name") or symbol["name"]
    for string_item in strings:
        if string_item.get("address") is not None:
            mapping[int(string_item["address"])] = f"string:{string_item['value'][:32]}"
    return mapping


def _disassemble_blocks(project: Any, path: str | Path, *, parsed: dict[str, Any], blocks: list[dict[str, Any]], address_names: dict[int, str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    target = Path(path)
    for block in sorted(blocks, key=lambda item: item["address"]):
        angr_block = project.factory.block(block["address"], size=block["size"])
        for instruction in angr_block.capstone.insns:
            virtual_address = int(instruction.address)
            translations = translate_value(parsed, "virtual_address", virtual_address)
            file_offset = translations[0]["file_offset"] if translations else None
            resolved_operands = []
            comments = []
            for immediate in _extract_immediates(f"{instruction.mnemonic} {instruction.op_str}"):
                name = address_names.get(int(immediate))
                if name:
                    resolved_operands.append({"kind": "address", "value": int(immediate), "symbolic_name": name})
                    comments.append(f"references {name}")
            items.append(
                {
                    "address": virtual_address,
                    "file_offset": file_offset,
                    "bytes": instruction.bytes.hex(),
                    "mnemonic": instruction.mnemonic,
                    "operand_text": instruction.op_str,
                    "resolved_operands": resolved_operands,
                    "comments": comments,
                    "registers": sorted(set(_extract_registers(instruction.op_str))),
                }
            )
    return items


def _recover_control_flow_graph(function: dict[str, Any], kb_function: Any | None, instructions: list[dict[str, Any]]) -> dict[str, Any]:
    nodes = [
        {
            "address": int(block["address"]),
            "size": int(block["size"]),
            "instruction_count": len([item for item in instructions if int(block["address"]) <= int(item["address"]) < int(block["address"]) + int(block["size"])]),
        }
        for block in function["blocks"]
    ]
    edges: list[dict[str, Any]] = []
    loop_edges: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    if kb_function is not None:
        graph = getattr(kb_function, "transition_graph", None)
        if graph is not None:
            for src, dst in graph.edges():
                src_addr = getattr(src, "addr", None)
                dst_addr = getattr(dst, "addr", None)
                if src_addr is None or dst_addr is None:
                    continue
                edge_key = (int(src_addr), int(dst_addr))
                if edge_key in seen:
                    continue
                seen.add(edge_key)
                src_size = getattr(src, "size", 0) or 0
                fallthrough = int(dst_addr) == int(src_addr) + int(src_size)
                kind = "fallthrough" if fallthrough else "branch"
                record = {
                    "source": int(src_addr),
                    "target": int(dst_addr),
                    "kind": kind,
                    "is_back_edge": int(dst_addr) <= int(src_addr),
                }
                edges.append(record)
                if record["is_back_edge"]:
                    loop_edges.append(record)
    if not edges:
        blocks = sorted(function["blocks"], key=lambda item: item["address"])
        for left, right in zip(blocks, blocks[1:]):
            record = {
                "source": int(left["address"]),
                "target": int(right["address"]),
                "kind": "fallthrough",
                "is_back_edge": int(right["address"]) <= int(left["address"]),
            }
            edges.append(record)
            if record["is_back_edge"]:
                loop_edges.append(record)
    branch_targets = sorted({edge["target"] for edge in edges if edge["kind"] == "branch"})
    return {
        "nodes": nodes,
        "edges": edges,
        "branch_targets": branch_targets,
        "loops": loop_edges,
        "fallthrough_edges": [edge for edge in edges if edge["kind"] == "fallthrough"],
        "warnings": [] if edges else ["Control-flow graph was synthesized from linear block order."],
    }


def _recover_variables(arch_name: str, parsed: dict[str, Any], function: dict[str, Any], instructions: list[dict[str, Any]]) -> dict[str, Any]:
    args = []
    seen_args: set[str] = set()
    for index, register in enumerate(_argument_registers(arch_name)):
        if any(register in item["registers"] for item in instructions[:8]):
            if register in seen_args:
                continue
            seen_args.add(register)
            args.append(
                {
                    "name": f"arg_{index}",
                    "storage": {"kind": "register", "register": register},
                    "confidence": {"level": "medium", "method": "entry-block register usage heuristic"},
                    "evidence": [f"Register {register} is read in the first basic block."],
                }
            )
    locals_: list[dict[str, Any]] = []
    globals_: list[dict[str, Any]] = []
    seen_slots: set[tuple[str, int]] = set()
    seen_globals: set[int] = set()
    for instruction in instructions:
        for base, offset in _extract_stack_accesses(instruction["operand_text"]):
            slot_key = (base, offset)
            if slot_key in seen_slots:
                continue
            seen_slots.add(slot_key)
            locals_.append(
                {
                    "name": f"{base}_{offset:+#x}",
                    "kind": "stack_slot",
                    "base_register": base,
                    "offset": offset,
                    "size_hint": _operand_size_hint(instruction["operand_text"]),
                    "confidence": {"level": "medium", "method": "stack-memory operand heuristic"},
                    "evidence": [f"Instruction 0x{instruction['address']:x} accesses [{base}, {offset:+#x}]."],
                }
            )
        for resolved in instruction["resolved_operands"]:
            address = int(resolved["value"])
            if address in seen_globals or _address_in_function(address, function):
                continue
            section = _section_for_address(parsed, address)
            if section and "x" not in section.get("permissions", ""):
                seen_globals.add(address)
                globals_.append(
                    {
                        "name": resolved.get("symbolic_name") or f"global_0x{address:x}",
                        "address": address,
                        "section": section.get("name"),
                        "confidence": {"level": "medium", "method": "resolved data reference heuristic"},
                        "evidence": [f"Instruction 0x{instruction['address']:x} references data address 0x{address:x}."],
                    }
                )
    return {
        "arguments": args,
        "locals": locals_,
        "globals": globals_,
        "register_parameters": [item["storage"]["register"] for item in args],
        "confidence": {"level": "medium", "method": "instruction-pattern heuristics"},
    }


def _recover_stack_frame(function: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
    saved_registers = []
    for local in variables["locals"]:
        if local["base_register"] in {"sp", "rbp", "ebp", "x29", "fp"} and local["offset"] <= 0:
            saved_registers.append({"slot": local["name"], "offset": local["offset"]})
    return {
        "stack_size": function.get("stack_size"),
        "slots": variables["locals"],
        "saved_registers": saved_registers,
        "warnings": [] if function.get("stack_size") is not None else ["Stack size could not be inferred from the function prologue."],
    }


def _recover_constant_propagation(arch_name: str, function: dict[str, Any], instructions: list[dict[str, Any]]) -> dict[str, Any]:
    immediates = []
    for instruction in instructions:
        for value in _extract_immediates(f"{instruction['mnemonic']} {instruction['operand_text']}"):
            immediates.append(
                {
                    "instruction_address": instruction["address"],
                    "value": value,
                    "representation": hex(value),
                    "confidence": {"level": "high", "method": "decoded immediate operand"},
                }
            )
    call_sites = []
    arg_registers = _argument_registers(arch_name)
    for index, instruction in enumerate(instructions):
        if instruction["mnemonic"] not in {"bl", "call", "callq"}:
            continue
        arguments = []
        for register in arg_registers[:4]:
            argument = _trace_register_constant(register, instructions, index)
            if argument is not None:
                arguments.append(argument)
        call_sites.append(
            {
                "call_address": instruction["address"],
                "target": instruction["resolved_operands"][0]["symbolic_name"] if instruction["resolved_operands"] else instruction["operand_text"],
                "arguments": arguments,
                "state": "partial" if len(arguments) < 4 else "exact",
            }
        )
    return {
        "immediates": immediates,
        "call_sites": call_sites,
        "confidence": {"level": "medium", "method": "bounded backward register-trace heuristic"},
    }


def _recover_indirect_flows(function: dict[str, Any], instructions: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    for instruction in instructions:
        if instruction["mnemonic"] not in INDIRECT_FLOW_MNEMONICS:
            continue
        if instruction["resolved_operands"]:
            continue
        if not instruction["registers"]:
            continue
        items.append(
            {
                "address": instruction["address"],
                "mnemonic": instruction["mnemonic"],
                "operand_text": instruction["operand_text"],
                "kind": "indirect_call" if instruction["mnemonic"] in {"blr", "call", "callq", "jalr"} else "indirect_branch",
                "confidence": {"level": "medium", "method": "register-target control-transfer heuristic"},
                "evidence": [f"Instruction 0x{instruction['address']:x} transfers control through {instruction['operand_text']}."],
            }
        )
    return {
        "items": items,
        "unresolved_count": len(items),
        "warnings": [] if items else ["No indirect control-flow transfers were identified."],
    }


def _recover_calling_convention(arch_name: str, function: dict[str, Any]) -> dict[str, Any]:
    name = function.get("calling_convention") or _default_calling_convention(arch_name)
    return {
        "name": name,
        "source": "backend" if function.get("calling_convention") else "architecture_default",
        "register_parameters": _argument_registers(arch_name),
        "stack_pointer_register": "sp" if arch_name.upper().startswith("AARCH64") or arch_name.upper().startswith("ARM") else "rsp",
    }


def _recover_intermediate_representation(project: Any, function: dict[str, Any]) -> dict[str, Any]:
    blocks = []
    for block in function["blocks"][:8]:
        irsb = project.factory.block(block["address"], size=block["size"]).vex
        blocks.append(
            {
                "address": int(block["address"]),
                "jumpkind": irsb.jumpkind,
                "statements": [str(statement) for statement in irsb.statements[:25]],
                "next": str(irsb.next),
            }
        )
    return {
        "backend": "vex",
        "blocks": blocks,
        "truncated": len(function["blocks"]) > 8 or any(len(item["statements"]) == 25 for item in blocks),
    }


def _identify_system_calls(arch_name: str, instructions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    syscall_register = "x8" if arch_name.upper().startswith("AARCH64") else "rax"
    items = []
    for index, instruction in enumerate(instructions):
        if instruction["mnemonic"] not in SYSCALL_MNEMONICS:
            continue
        candidate = _trace_register_constant(syscall_register, instructions, index)
        items.append(
            {
                "address": instruction["address"],
                "mnemonic": instruction["mnemonic"],
                "operand_text": instruction["operand_text"],
                "syscall_number": candidate.get("value") if candidate is not None else None,
                "confidence": {"level": "medium" if candidate is not None else "low", "method": "bounded syscall-number backtrace"},
            }
        )
    return items


def _classify_function(function: dict[str, Any], instructions: list[dict[str, Any]], indirect_flows: dict[str, Any]) -> list[dict[str, Any]]:
    text = " ".join(f"{item['mnemonic']} {item['operand_text']} {' '.join(comment for comment in item['comments'])}" for item in instructions).lower()
    tags = []
    heuristics = [
        ("logging", ("puts", "printf", "fprintf", "string:")),
        ("memory_allocation", ("malloc", "free", "operator new", "operator delete")),
        ("runtime_init", ("_start", "__libc_start_main", "register_tm_clones", "__do_global_dtors_aux")),
        ("string_processing", ("str", "mem", "puts", "printf")),
        ("control_flow", ("switch", "jmp", "br ", "blr", "call")),
    ]
    name_text = f"{function['name']} {function.get('demangled_name') or ''}".lower()
    for tag, needles in heuristics:
        if any(needle in name_text or needle in text for needle in needles):
            tags.append(
                {
                    "tag": tag,
                    "confidence": {"level": "medium", "method": "symbol and instruction heuristic"},
                    "evidence": [f"Matched one of: {', '.join(needles)}"],
                }
            )
    if indirect_flows["items"]:
        tags.append(
            {
                "tag": "indirect_control_flow",
                "confidence": {"level": "high", "method": "indirect branch detection"},
                "evidence": [f"Recovered {len(indirect_flows['items'])} indirect control-transfer sites."],
            }
        )
    if not tags:
        tags.append(
            {
                "tag": "general",
                "confidence": {"level": "low", "method": "fallback classification"},
                "evidence": ["No stronger classification heuristic matched this function."],
            }
        )
    return tags


def _score_function(
    function: dict[str, Any],
    cfg: dict[str, Any],
    indirect_flows: dict[str, Any],
    tags: list[dict[str, Any]],
    system_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    score = 10
    evidence = []
    instruction_count = sum(node["instruction_count"] for node in cfg["nodes"])
    if instruction_count >= 10:
        score += min(20, instruction_count)
        evidence.append(f"Instruction count contributes {min(20, instruction_count)} points.")
    if len(cfg["edges"]) > len(cfg["nodes"]):
        score += 10
        evidence.append("Branch density suggests non-trivial control flow.")
    if indirect_flows["items"]:
        score += 15
        evidence.append("Indirect control-flow raises triage priority.")
    if system_calls:
        score += 15
        evidence.append("System-call instructions raise triage priority.")
    noisy_tags = {item["tag"] for item in tags}
    if "runtime_init" in noisy_tags:
        score -= 10
        evidence.append("Runtime initialization code is deprioritized.")
    name = function.get("name", "")
    if not function.get("is_plt") and name and not _GENERIC_NAME_RE.match(name):
        score += 25
        evidence.append("Non-generic symbol name raises triage priority.")
    score = max(0, min(100, score))
    return {
        "score": score,
        "evidence": evidence,
        "thresholds": {"high": 70, "medium": 40, "low": 15},
        "priority": "high" if score >= 70 else "medium" if score >= 40 else "low",
    }


def _build_call_graph(functions: list[dict[str, Any]], symbols: list[dict[str, Any]], xrefs: list[dict[str, Any]]) -> dict[str, Any]:
    nodes = []
    for function in functions:
        nodes.append(
            {
                "node_kind": "function",
                "function_id": function.get("function_id"),
                "address": function["address"],
                "name": function.get("demangled_name") or function["name"],
            }
        )
    seen_external: set[tuple[str, int | None]] = set()
    for symbol in symbols:
        if symbol["kind"] != "import":
            continue
        key = (symbol["name"], symbol.get("address"))
        if key in seen_external:
            continue
        seen_external.add(key)
        nodes.append(
            {
                "node_kind": "external",
                "address": symbol.get("address"),
                "name": symbol.get("demangled_name") or symbol["name"],
                "symbol_kind": symbol["kind"],
            }
        )
    edges = []
    for xref in xrefs:
        edges.append(
            {
                "source_function_id": xref.get("source_function_id"),
                "source_address": xref["source_function_address"],
                "target_function_id": xref.get("target_function_id"),
                "target_address": xref.get("target_address"),
                "target_name": xref.get("target_name"),
                "kind": xref.get("xref_type", "call"),
            }
        )
    return {"nodes": nodes, "edges": edges}


def _inspect_data_segments(path: str | Path, parsed: dict[str, Any], strings: list[dict[str, Any]], functions: list[dict[str, Any]]) -> dict[str, Any]:
    data = Path(path).read_bytes()
    pointer_size = 8 if parsed["file_type"]["bitness"] == 64 else 4
    function_addresses = {int(item["address"]) for item in functions}
    sections = []
    typed_views = []
    for section in _parsed_sections(parsed):
        permissions = section.get("permissions", "")
        if "x" in permissions:
            continue
        strings_in_section = [item for item in strings if item.get("section") == section.get("name")]
        pointer_table = _detect_pointer_table(data, section, pointer_size, function_addresses)
        section_record = {
            "name": section.get("name"),
            "virtual_address": section.get("virtual_address"),
            "size": section.get("size"),
            "permissions": permissions,
            "string_count": len(strings_in_section),
            "typed_views": [],
        }
        if strings_in_section:
            view = {
                "kind": "string_pool",
                "section": section.get("name"),
                "item_count": len(strings_in_section),
                "preview": [item["value"][:48] for item in strings_in_section[:5]],
                "confidence": {"level": "high", "method": "string extraction"},
            }
            section_record["typed_views"].append(view)
            typed_views.append(view)
        if pointer_table is not None:
            view = {
                "kind": "pointer_table",
                "section": section.get("name"),
                "item_count": pointer_table["item_count"],
                "target_kind": pointer_table["target_kind"],
                "confidence": {"level": "medium", "method": "contiguous pointer-scan heuristic"},
            }
            section_record["typed_views"].append(view)
            typed_views.append(view)
        sections.append(section_record)
    return {"sections": sections, "typed_views": typed_views}


def _recover_runtime_metadata(
    parsed: dict[str, Any],
    functions: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    strings: list[dict[str, Any]],
) -> dict[str, Any]:
    section_names = {item.get("name", "") for item in _parsed_sections(parsed)}
    names = " ".join(item["name"] for item in functions + symbols if item.get("name"))
    languages = []
    if any(item.get("demangled_name") for item in functions) or "_z" in names.lower():
        languages.append(
            {
                "language": "c++",
                "confidence": {"level": "high", "method": "demangled-symbol heuristic"},
                "evidence": ["Recovered C++-mangled symbols or demangled names in the analysis cache."],
            }
        )
    if ".gopclntab" in section_names or "runtime." in names:
        languages.append(
            {
                "language": "go",
                "confidence": {"level": "medium", "method": "Go runtime section heuristic"},
                "evidence": ["Found Go-specific section or runtime symbol names."],
            }
        )
    if any(name.startswith("__swift") for name in section_names) or "$s" in names:
        languages.append(
            {
                "language": "swift",
                "confidence": {"level": "medium", "method": "Swift metadata heuristic"},
                "evidence": ["Found Swift sections or mangled symbol prefixes."],
            }
        )
    if "rust_begin_unwind" in names or "_rnv" in names.lower():
        languages.append(
            {
                "language": "rust",
                "confidence": {"level": "medium", "method": "Rust runtime heuristic"},
                "evidence": ["Found Rust-specific symbol names."],
            }
        )
    if any("__objc" in name for name in section_names):
        languages.append(
            {
                "language": "objective-c",
                "confidence": {"level": "medium", "method": "Objective-C section heuristic"},
                "evidence": ["Found Objective-C metadata sections."],
            }
        )
    if not languages:
        languages.append(
            {
                "language": "c",
                "confidence": {"level": "low", "method": "fallback classification"},
                "evidence": ["No richer language-runtime metadata was recovered."],
            }
        )
    return {
        "languages": languages,
        "string_evidence": [item["value"][:64] for item in strings[:10]],
    }


def _recover_types(
    functions: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    runtime_metadata: dict[str, Any],
    data_segments: dict[str, Any],
) -> dict[str, Any]:
    items = []
    for symbol in symbols:
        name = symbol.get("demangled_name") or symbol["name"]
        if "vtable for" in name.lower():
            items.append(
                {
                    "kind": "vtable",
                    "name": name,
                    "confidence": {"level": "high", "method": "demangled vtable symbol"},
                    "evidence": [f"Symbol '{name}' matches a vtable naming pattern."],
                }
            )
        if "typeinfo for" in name.lower():
            items.append(
                {
                    "kind": "rtti",
                    "name": name,
                    "confidence": {"level": "high", "method": "demangled RTTI symbol"},
                    "evidence": [f"Symbol '{name}' matches RTTI naming."],
                }
            )
    for language in runtime_metadata["languages"]:
        if language["language"] == "c++":
            items.append(
                {
                    "kind": "language_runtime",
                    "name": "c++ class model",
                    "confidence": {"level": "medium", "method": "language metadata aggregation"},
                    "evidence": language["evidence"],
                }
            )
    for view in data_segments["typed_views"]:
        items.append(
            {
                "kind": view["kind"],
                "name": f"{view['section']}:{view['kind']}",
                "confidence": view["confidence"],
                "evidence": [f"Recovered {view['kind']} in {view['section']}."],
            }
        )
    return {"items": items}


def _build_type_information(functions: list[dict[str, Any]], recovered_types: dict[str, Any], data_segments: dict[str, Any]) -> dict[str, Any]:
    return {
        "function_signatures": [
            {
                "function_id": function.get("function_id"),
                "name": function.get("demangled_name") or function["name"],
                "signature": function.get("signature"),
            }
            for function in functions
        ],
        "named_types": recovered_types["items"],
        "typed_memory": data_segments["typed_views"],
    }


def _recover_exception_metadata(parsed: dict[str, Any], symbols: list[dict[str, Any]]) -> dict[str, Any]:
    sections = [item.get("name") for item in _parsed_sections(parsed) if item.get("name") in {".eh_frame", ".eh_frame_hdr", ".gcc_except_table", ".pdata", ".xdata"}]
    personalities = [item["name"] for item in symbols if "personality" in item["name"]]
    return {
        "available": bool(sections or personalities),
        "sections": sections,
        "personality_routines": personalities,
        "confidence": {"level": "medium" if sections or personalities else "low", "method": "section and symbol heuristics"},
    }


def slice_function_data_flow(detail: dict[str, Any], *, anchor_address: int | None = None, register: str | None = None, radius: int = 6) -> dict[str, Any]:
    instructions = detail.get("instructions", [])
    if not instructions:
        return {"anchor": None, "registers": [], "items": [], "confidence": {"level": "low", "method": "no instruction cache"}}
    if anchor_address is None:
        anchor_index = 0
    else:
        anchor_index = next((index for index, item in enumerate(instructions) if int(item["address"]) == int(anchor_address)), None)
        if anchor_index is None:
            raise ValueError(f"Anchor address 0x{int(anchor_address):x} is not present in the cached instruction list.")
    anchor = instructions[anchor_index]
    tracked_registers = [register] if register else anchor["registers"][:2]
    start = max(0, anchor_index - max(1, int(radius)))
    end = min(len(instructions), anchor_index + max(1, int(radius)) + 1)
    items = []
    for candidate in instructions[start:end]:
        if tracked_registers and not any(item in candidate["registers"] for item in tracked_registers):
            continue
        items.append(candidate)
    return {
        "anchor": anchor,
        "registers": tracked_registers,
        "items": items,
        "confidence": {"level": "low", "method": "bounded register-neighborhood heuristic"},
    }


def navigate_function_neighborhood(
    analysis: dict[str, Any],
    function: dict[str, Any],
    *,
    depth: int = 1,
    radius: int = 1,
) -> dict[str, Any]:
    current_ids = {function.get("function_id")} if function.get("function_id") else set()
    current_addresses = {int(function["address"])}
    visited_ids = set(current_ids)
    visited_addresses = set(current_addresses)
    incoming = []
    outgoing = []
    seen_incoming: set[tuple[int | None, int | None, str | None, str | None]] = set()
    seen_outgoing: set[tuple[int | None, int | None, str | None, str | None]] = set()
    edges = analysis.get("call_graph", {}).get("edges", [])
    for _ in range(max(1, int(depth))):
        next_ids: set[str] = set()
        next_addresses: set[int] = set()
        for edge in edges:
            source_address = edge.get("source_function_address") or edge.get("source_address")
            target_address = edge.get("target_address")
            edge_key = (
                int(source_address) if source_address is not None else None,
                int(target_address) if target_address is not None else None,
                edge.get("source_function_id"),
                edge.get("target_function_id"),
            )
            if edge.get("source_function_id") in current_ids or source_address in current_addresses:
                if edge_key not in seen_outgoing:
                    seen_outgoing.add(edge_key)
                    outgoing.append(edge)
                if edge.get("target_function_id") and edge["target_function_id"] not in visited_ids:
                    next_ids.add(edge["target_function_id"])
                if target_address is not None and int(target_address) not in visited_addresses:
                    next_addresses.add(int(target_address))
            if edge.get("target_function_id") in current_ids or target_address in current_addresses:
                if edge_key not in seen_incoming:
                    seen_incoming.add(edge_key)
                    incoming.append(edge)
                if edge.get("source_function_id") and edge["source_function_id"] not in visited_ids:
                    next_ids.add(edge["source_function_id"])
                if source_address is not None and int(source_address) not in visited_addresses:
                    next_addresses.add(int(source_address))
        visited_ids.update(next_ids)
        visited_addresses.update(next_addresses)
        current_ids = next_ids
        current_addresses = next_addresses
        if not current_ids and not current_addresses:
            break
    nearby_functions = [
        item
        for item in analysis["functions"]
        if abs(int(item["address"]) - int(function["address"])) <= max(1, int(radius)) * 0x80 and item["function_id"] != function.get("function_id")
    ]
    nearby_strings = [
        item
        for item in analysis["strings"]
        if item.get("address") is not None and abs(int(item["address"]) - int(function["address"])) <= max(1, int(radius)) * 0x200
    ]
    return {
        "target_function": function,
        "callers": incoming,
        "callees": outgoing,
        "nearby_functions": nearby_functions,
        "nearby_strings": nearby_strings,
    }


def filter_and_prioritize_functions(
    analysis: dict[str, Any],
    *,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    min_score: int | None = None,
    max_items: int = 50,
) -> list[dict[str, Any]]:
    include = {item.lower() for item in include_tags or []}
    exclude = {item.lower() for item in exclude_tags or []}
    items = []
    for function in analysis["functions"]:
        tags = {item.lower() for item in function.get("classification_tags", [])}
        if include and not tags.intersection(include):
            continue
        if exclude and tags.intersection(exclude):
            continue
        score = function.get("triage_score", {}).get("score", 0)
        if min_score is not None and score < int(min_score):
            continue
        items.append(function)
    items.sort(key=lambda item: (-item.get("triage_score", {}).get("score", 0), item["address"]))
    return items[: max(1, int(max_items))]


def _section_for_address(parsed: dict[str, Any], address: int) -> dict[str, Any] | None:
    for section in _parsed_sections(parsed):
        start = int(section.get("virtual_address", 0))
        end = start + int(section.get("virtual_size", section.get("size", 0)) or section.get("size", 0))
        if start <= int(address) < end:
            return section
    return None


def _parsed_sections(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    sections = parsed.get("sections")
    if isinstance(sections, list):
        return sections
    return parsed.get("layout", {}).get("sections", [])


def _address_in_function(address: int, function: dict[str, Any]) -> bool:
    return int(function["address"]) <= int(address) < int(function["end_address"])


def _extract_registers(text: str) -> list[str]:
    return [item.lower() for item in REGISTER_RE.findall(text)]


def _extract_stack_accesses(text: str) -> list[tuple[str, int]]:
    accesses = []
    for base, raw_offset in STACK_ACCESS_RE.findall(text):
        accesses.append((base.lower(), int(raw_offset, 16 if raw_offset.lower().startswith("0x") or raw_offset.lower().startswith("-0x") else 10)))
    return accesses


def _operand_size_hint(text: str) -> int | None:
    lowered = text.lower()
    if re.search(r"\b[wse]\d+\b", lowered):
        return 4
    if re.search(r"\b[xdq]\d+\b", lowered):
        return 8
    return None


def _extract_immediates(text: str) -> list[int]:
    return [int(raw, 16 if raw.lower().startswith("0x") else 10) for raw in IMMEDIATE_RE.findall(text)]


def _argument_registers(arch_name: str) -> list[str]:
    normalized = arch_name.upper()
    if normalized == "AARCH64":
        return [f"x{i}" for i in range(8)] + [f"w{i}" for i in range(8)]
    if normalized == "AMD64":
        return ["rdi", "rsi", "rdx", "rcx", "r8", "r9"]
    if normalized == "X86":
        return ["ecx", "edx", "eax"]
    return []


def _default_calling_convention(arch_name: str) -> str:
    normalized = arch_name.upper()
    if normalized == "AARCH64":
        return "aarch64-aapcs"
    if normalized == "AMD64":
        return "sysv-amd64"
    if normalized == "X86":
        return "cdecl"
    return normalized.lower()


def _trace_register_constant(register: str, instructions: list[dict[str, Any]], stop_index: int) -> dict[str, Any] | None:
    lowered = register.lower()
    for candidate in reversed(instructions[max(0, stop_index - 6) : stop_index]):
        operand = candidate["operand_text"].lower()
        if not operand.startswith(lowered):
            continue
        immediates = _extract_immediates(f"{candidate['mnemonic']} {candidate['operand_text']}")
        if immediates:
            return {
                "register": lowered,
                "state": "exact",
                "value": immediates[-1],
                "instruction_address": candidate["address"],
            }
        if candidate["resolved_operands"]:
            return {
                "register": lowered,
                "state": "symbolic",
                "symbolic_name": candidate["resolved_operands"][0]["symbolic_name"],
                "instruction_address": candidate["address"],
            }
        return {"register": lowered, "state": "unknown", "instruction_address": candidate["address"]}
    return None


def _detect_pointer_table(data: bytes, section: dict[str, Any], pointer_size: int, function_addresses: set[int]) -> dict[str, Any] | None:
    file_offset = int(section.get("file_offset", 0))
    size = int(section.get("size", 0))
    if size < pointer_size * 3:
        return None
    chunk = data[file_offset : file_offset + size]
    consecutive = 0
    max_consecutive = 0
    target_kind = "unknown"
    for cursor in range(0, len(chunk) - pointer_size + 1, pointer_size):
        value = int.from_bytes(chunk[cursor : cursor + pointer_size], "little")
        if value in function_addresses:
            consecutive += 1
            target_kind = "function"
        elif value != 0:
            consecutive = 0
        max_consecutive = max(max_consecutive, consecutive)
    if max_consecutive >= 3:
        return {"item_count": max_consecutive, "target_kind": target_kind}
    return None
