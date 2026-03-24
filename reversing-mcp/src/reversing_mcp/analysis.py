from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:  # pragma: no cover - install-time dependency
    import angr
except ImportError:  # pragma: no cover
    angr = None

try:  # pragma: no cover - install-time dependency
    import cxxfilt
except ImportError:  # pragma: no cover
    cxxfilt = None

try:  # pragma: no cover - install-time dependency
    import pefile
except ImportError:  # pragma: no cover
    pefile = None

try:  # pragma: no cover - install-time dependency
    from elftools.elf.elffile import ELFFile
    from elftools.elf.relocation import RelocationSection
except ImportError:  # pragma: no cover
    ELFFile = None
    RelocationSection = None

from .semantic import build_semantic_views
from .triage import context_for_offset, extract_strings, parse_artifact, translate_value

IMMEDIATE_RE = re.compile(r"(?:^|[^A-Za-z0-9_])(0x[0-9a-fA-F]+|\d+)(?:$|[^A-Za-z0-9_])")
CALL_MNEMONICS = {"bl", "blr", "call", "callq", "jal", "jalr"}
BRANCH_MNEMONICS = {
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
}
PRINTABLE_ASCII = set(range(0x20, 0x7F))


def analysis_backend_status() -> dict[str, Any]:
    return {
        "name": "angr-cfgfast",
        "available": angr is not None,
        "exact": False,
        "notes": "Headless analysis backend using angr CFGFast plus structured capstone disassembly and best-effort decompilation.",
        "supported_formats": ["ELF", "PE", "Mach-O"],
    }


def analyze_program(
    path: str | Path,
    *,
    hints: dict[str, Any] | None = None,
    resource_limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    project = _load_project(path)
    target = Path(path)
    data = target.read_bytes()
    parsed = parse_artifact(target, data, hints or {}, resource_limits or {})
    file_format = parsed["file_type"]["format"]
    if file_format not in {"ELF", "PE", "Mach-O"}:
        raise ValueError(f"Analysis backend does not support format '{file_format}'.")

    cfg = project.analyses.CFGFast(normalize=True, data_references=True, cross_references=True)
    functions, kb_functions = _collect_functions(project)
    symbols = _collect_symbols(project, target, file_format)
    strings = extract_strings(
        data,
        parsed,
        min_length=4,
        max_strings=int((resource_limits or {}).get("string_count_limit", 50000)),
    )["items"]
    xrefs = _collect_xrefs(project, kb_functions, functions, symbols)
    linkage = _collect_linkage(project, target, parsed, symbols)
    debug_info = _collect_debug_info(target, parsed)
    semantics = build_semantic_views(project, target, parsed, functions, kb_functions, symbols, strings, xrefs, debug_info)
    instruction_modes = _instruction_mode_details(project.arch.name)
    capabilities = {
        "format": file_format,
        "architecture": project.arch.name,
        "bitness": project.arch.bits,
        "endianness": "little" if project.arch.memory_endness.endswith("LE") else "big",
        "features": {
            "functions": True,
            "symbols": True,
            "disassembly": True,
            "decompilation": True,
            "raw_bytes": True,
            "xrefs": True,
            "search": True,
            "linkage_analysis": True,
            "debug_info": bool(debug_info["available"]),
            "instruction_set_mode_query": True,
            "instruction_set_mode_override": len(instruction_modes["supported"]) > 1,
        },
        "instruction_set_modes": instruction_modes,
    }
    summary = {
        "entry_point": int(project.entry),
        "image_base": int(getattr(project.loader.main_object, "mapped_base", 0) or 0),
        "function_count": len(functions),
        "symbol_count": len(symbols),
        "string_count": len(strings),
        "xref_count": len(xrefs),
        "import_count": len([item for item in symbols if item["kind"] == "import"]),
        "export_count": len([item for item in symbols if item["kind"] == "export"]),
        "thunk_count": len([item for item in symbols if item["kind"] == "thunk"]),
        "unresolved_symbol_count": len([item for item in symbols if item["kind"] == "unresolved"]),
        "debug_formats": list(debug_info["formats"]),
    }
    return {
        "backend": analysis_backend_status(),
        "capabilities": capabilities,
        "summary": summary,
        "functions": semantics["functions"],
        "symbols": symbols,
        "strings": semantics["strings"],
        "xrefs": xrefs,
        "linkage": linkage,
        "debug_info": debug_info,
        "call_graph": semantics["call_graph"],
        "function_details": semantics["function_details"],
        "type_information": semantics["type_information"],
        "recovered_types": semantics["recovered_types"],
        "data_segments": semantics["data_segments"],
        "exception_metadata": semantics["exception_metadata"],
        "runtime_metadata": semantics["runtime_metadata"],
    }


def disassemble_function(
    path: str | Path,
    *,
    analysis: dict[str, Any],
    function_address: int,
    cursor: int = 0,
    limit: int = 200,
    instruction_mode_override: str | None = None,
) -> dict[str, Any]:
    project = _load_project(path)
    function = next((item for item in analysis["functions"] if int(item["address"]) == int(function_address)), None)
    if function is None:
        raise ValueError(f"Function at address 0x{function_address:x} is not present in the analysis cache.")
    current_mode = _resolve_instruction_mode(project.arch.name, analysis, instruction_mode_override)
    address_names = _address_name_map(analysis)
    items = _disassemble_blocks(
        project,
        path,
        blocks=function["blocks"],
        mode=current_mode,
        address_names=address_names,
    )
    page = _slice_page(items, cursor=cursor, limit=limit)
    return {
        "function": function,
        "instruction_set_mode": {
            "supported": analysis["capabilities"]["instruction_set_modes"]["supported"],
            "default": analysis["capabilities"]["instruction_set_modes"]["default"],
            "current": current_mode,
            "override_applied": instruction_mode_override is not None,
        },
        "items": page["items"],
        "page": page["page"],
        "warnings": _disassembly_warnings(function),
    }


def disassemble_range(
    path: str | Path,
    *,
    analysis: dict[str, Any],
    input_kind: str,
    start_value: int,
    size: int,
    cursor: int = 0,
    limit: int = 200,
    instruction_mode_override: str | None = None,
) -> dict[str, Any]:
    target = Path(path)
    project = _load_project(target)
    parsed = parse_artifact(target, target.read_bytes(), {}, {})
    translation = _resolve_runtime_location(project, parsed, input_kind=input_kind, value=int(start_value))
    if input_kind == "file_offset":
        file_offset = int(start_value)
        virtual_address = translation.get("execution_address")
    else:
        file_offset = translation.get("file_offset")
        virtual_address = translation.get("execution_address")
    if virtual_address is None:
        raise ValueError("The requested disassembly range does not map to an executable virtual address.")
    block_range = [{"address": int(virtual_address), "size": max(1, int(size))}]
    current_mode = _resolve_instruction_mode(project.arch.name, analysis, instruction_mode_override)
    items = _disassemble_blocks(
        project,
        target,
        blocks=block_range,
        mode=current_mode,
        address_names=_address_name_map(analysis),
    )
    page = _slice_page(items, cursor=cursor, limit=limit)
    return {
        "range": {
            "input_kind": input_kind,
            "start_value": int(start_value),
            "file_offset": int(file_offset),
            "virtual_address": int(virtual_address),
            "size": max(1, int(size)),
        },
        "instruction_set_mode": {
            "supported": analysis["capabilities"]["instruction_set_modes"]["supported"],
            "default": analysis["capabilities"]["instruction_set_modes"]["default"],
            "current": current_mode,
            "override_applied": instruction_mode_override is not None,
        },
        "items": page["items"],
        "page": page["page"],
        "warnings": [],
    }


def decompile_function(
    path: str | Path,
    *,
    function_address: int,
    char_limit: int,
    line_limit: int,
) -> dict[str, Any]:
    project = _load_project(path)
    cfg = project.analyses.CFGFast(normalize=True, data_references=True, cross_references=True)
    function = project.kb.functions.get(function_address)
    if function is None:
        raise ValueError(f"Function at address 0x{function_address:x} was not recovered by the analysis backend.")
    warnings: list[str] = []
    try:
        decompilation = project.analyses.Decompiler(function, cfg=cfg.model)
    except Exception as exc:
        return {
            "status": "failed",
            "warnings": [str(exc)],
            "source": "",
            "truncated": False,
            "line_count": 0,
            "char_count": 0,
        }
    if decompilation.codegen is None:
        return {
            "status": "failed",
            "warnings": ["Decompiler returned no structured code output for the requested function."],
            "source": "",
            "truncated": False,
            "line_count": 0,
            "char_count": 0,
        }
    text = decompilation.codegen.text or ""
    limited_text, truncated = _limit_text(text, char_limit=max(256, int(char_limit)), line_limit=max(10, int(line_limit)))
    if truncated:
        warnings.append("Decompilation output was truncated to respect output limits.")
    return {
        "status": "completed",
        "warnings": warnings,
        "source": limited_text,
        "truncated": truncated,
        "line_count": len(text.splitlines()),
        "char_count": len(text),
    }


def read_bytes(
    path: str | Path,
    *,
    input_kind: str,
    value: int,
    length: int,
    hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = Path(path)
    data = target.read_bytes()
    parsed = parse_artifact(target, data, hints or {}, {})
    normalized_value = int(value)
    if input_kind == "file_offset":
        if normalized_value < 0 or normalized_value >= len(data):
            raise ValueError(f"Unable to translate file_offset value 0x{normalized_value:x}.")
        context = context_for_offset(parsed, normalized_value)
        file_offset = normalized_value
        virtual_address = context.get("virtual_address")
    else:
        project = _load_project(target)
        resolved = _resolve_runtime_location(project, parsed, input_kind=input_kind, value=normalized_value)
        file_offset = int(resolved["file_offset"])
        virtual_address = resolved.get("execution_address")
    end = min(len(data), file_offset + max(1, int(length)))
    chunk = data[file_offset:end]
    return {
        "input": {"kind": input_kind, "value": normalized_value},
        "resolved": {
            "file_offset": file_offset,
            "virtual_address": virtual_address,
            "length": len(chunk),
        },
        "bytes_hex": chunk.hex(),
        "bytes_ascii": "".join(chr(byte) if byte in PRINTABLE_ASCII else "." for byte in chunk),
        "truncated": len(chunk) < max(1, int(length)),
    }


def search_program(
    path: str | Path,
    *,
    analysis: dict[str, Any],
    kind: str,
    query: str | None = None,
    start_address: int | None = None,
    end_address: int | None = None,
    cursor: int = 0,
    limit: int = 50,
    case_sensitive: bool = False,
) -> dict[str, Any]:
    normalized_kind = kind.strip().lower()
    if normalized_kind == "name":
        items = _search_names(analysis, query or "", case_sensitive=case_sensitive)
    elif normalized_kind == "string":
        items = _search_strings(analysis, query or "", case_sensitive=case_sensitive)
    elif normalized_kind == "address_range":
        if start_address is None or end_address is None:
            raise ValueError("address_range searches require start_address and end_address.")
        items = _search_address_range(analysis, int(start_address), int(end_address))
    elif normalized_kind == "byte_pattern":
        items = _search_byte_pattern(Path(path), query or "")
    elif normalized_kind == "immediate":
        items = _search_immediate(Path(path), analysis, query or "")
    elif normalized_kind == "opcode":
        items = _search_opcode(Path(path), analysis, query or "", case_sensitive=case_sensitive)
    else:
        raise ValueError(f"Unsupported search kind '{kind}'.")
    page = _slice_page(items, cursor=cursor, limit=limit)
    return {
        "kind": normalized_kind,
        "items": page["items"],
        "page": page["page"],
    }


def build_analysis_synopsis(
    analysis: dict[str, Any],
    *,
    artifact_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    unknowns = []
    if not analysis["debug_info"]["available"]:
        unknowns.append("No debug metadata was recovered for the current artifact.")
    if not any(item["kind"] == "export" for item in analysis["symbols"]):
        unknowns.append("No exported symbols were recovered.")
    if not any(item.get("calling_convention") for item in analysis["functions"]):
        unknowns.append("Calling convention recovery is incomplete for the current function set.")
    return {
        "artifact": artifact_summary,
        "backend": analysis["backend"],
        "capabilities": analysis["capabilities"],
        "summary": analysis["summary"],
        "highlights": {
            "top_functions": [
                {
                    "function_id": item.get("function_id"),
                    "name": item.get("demangled_name") or item.get("name"),
                    "address": item["address"],
                    "size": item.get("size"),
                }
                for item in analysis["functions"][:5]
            ],
            "interesting_strings": [
                {
                    "string_id": item.get("string_id"),
                    "value": item["value"][:80],
                    "address": item.get("address"),
                }
                for item in analysis["strings"][:5]
            ],
        },
        "outstanding_unknowns": unknowns,
    }


def _load_project(path: str | Path):
    if angr is None:  # pragma: no cover - install-time dependency
        raise RuntimeError("angr is not installed.")
    return angr.Project(str(path), auto_load_libs=False)


def _collect_functions(project,) -> tuple[list[dict[str, Any]], dict[int, Any]]:
    main_object = project.loader.main_object
    contains_addr = getattr(main_object, "contains_addr", None)
    functions = []
    kb_functions: dict[int, Any] = {}
    for function in sorted(project.kb.functions.values(), key=lambda item: item.addr):
        if callable(contains_addr) and not contains_addr(function.addr):
            continue
        blocks = []
        for block in sorted(function.blocks, key=lambda item: item.addr):
            blocks.append({"address": int(block.addr), "size": int(block.size)})
        if not blocks:
            continue
        end_address = max(block["address"] + block["size"] for block in blocks)
        mangled_name = function.name or f"sub_{function.addr:x}"
        demangled_name = _demangle_name(mangled_name)
        stack_size = _infer_stack_size(project, blocks[0]["address"], blocks[0]["size"])
        calling_convention = getattr(getattr(function, "calling_convention", None), "name", None)
        prototype = str(function.prototype) if getattr(function, "prototype", None) is not None else None
        functions.append(
            {
                "address": int(function.addr),
                "end_address": int(end_address),
                "size": int(end_address - function.addr),
                "blocks": blocks,
                "name": mangled_name,
                "mangled_name": mangled_name,
                "demangled_name": demangled_name if demangled_name != mangled_name else None,
                "signature": prototype or f"{demangled_name if demangled_name != mangled_name else mangled_name}()",
                "calling_convention": calling_convention,
                "stack_size": stack_size,
                "analyzer_confidence": {
                    "level": "medium" if mangled_name.startswith("sub_") else "high",
                    "method": "angr CFGFast",
                },
                "is_plt": bool(getattr(function, "is_plt", False)),
            }
        )
        kb_functions[int(function.addr)] = function
    return functions, kb_functions


def _collect_symbols(project, path: Path, file_format: str) -> list[dict[str, Any]]:
    main_object = project.loader.main_object
    symbols: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for symbol in getattr(main_object, "symbols", []):
        name = getattr(symbol, "name", None)
        addr = getattr(symbol, "rebased_addr", None)
        key = (name, int(addr) if addr else None, bool(getattr(symbol, "is_import", False)), bool(getattr(symbol, "is_export", False)))
        if key in seen:
            continue
        seen.add(key)
        kind = "symbol"
        if getattr(symbol, "is_import", False):
            kind = "import"
        elif getattr(symbol, "is_export", False):
            kind = "export"
        if kind == "import" and not addr:
            kind = "unresolved"
        mangled_name = name or f"sym_{len(symbols):04d}"
        demangled_name = _demangle_name(mangled_name)
        owner = getattr(getattr(symbol, "owner", None), "binary", None)
        symbols.append(
            {
                "name": mangled_name,
                "mangled_name": mangled_name,
                "demangled_name": demangled_name if demangled_name != mangled_name else None,
                "kind": kind,
                "address": int(addr) if addr else None,
                "library": owner,
                "ordinal": None,
                "is_function": bool(getattr(symbol, "is_function", False)),
            }
        )
    for thunk_name, thunk_addr in sorted(getattr(main_object, "plt", {}).items(), key=lambda item: item[1]):
        symbols.append(
            {
                "name": thunk_name,
                "mangled_name": thunk_name,
                "demangled_name": None,
                "kind": "thunk",
                "address": int(thunk_addr),
                "library": None,
                "ordinal": None,
                "is_function": True,
            }
        )
    if file_format == "PE" and pefile is not None:
        try:
            pe = pefile.PE(str(path), fast_load=True)
            pe.parse_data_directories(
                directories=[
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"],
                ]
            )
            for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
                for imp in entry.imports:
                    name = imp.name.decode("utf-8", errors="replace") if imp.name else f"ordinal_{imp.ordinal}"
                    symbols.append(
                        {
                            "name": name,
                            "mangled_name": name,
                            "demangled_name": None,
                            "kind": "import",
                            "address": int(imp.address) if imp.address else None,
                            "library": entry.dll.decode("utf-8", errors="replace"),
                            "ordinal": int(imp.ordinal) if imp.ordinal else None,
                            "is_function": True,
                        }
                    )
        except Exception:
            pass
    symbols.sort(key=lambda item: (item["address"] if item["address"] is not None else 1 << 63, item["kind"], item["name"]))
    return symbols


def _collect_xrefs(project, kb_functions: dict[int, Any], functions: list[dict[str, Any]], symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    function_names = {item["address"]: item.get("demangled_name") or item["name"] for item in functions}
    symbol_names = {item["address"]: item.get("demangled_name") or item["name"] for item in symbols if item.get("address") is not None}
    xrefs: list[dict[str, Any]] = []
    for function in functions:
        kb_function = kb_functions.get(int(function["address"]))
        if kb_function is None:
            continue
        try:
            for callsite in sorted(kb_function.get_call_sites()):
                target = kb_function.get_call_target(callsite)
                if target is None:
                    continue
                xrefs.append(
                    {
                        "source_address": int(callsite),
                        "source_function_address": int(function["address"]),
                        "target_kind": "function",
                        "target_address": int(target),
                        "target_name": function_names.get(int(target)) or symbol_names.get(int(target)),
                        "xref_type": "call",
                    }
                )
        except Exception:
            continue
    xrefs.sort(key=lambda item: (item["target_kind"], item["target_address"], item["source_address"]))
    return xrefs


def _collect_linkage(project, path: Path, parsed: dict[str, Any], symbols: list[dict[str, Any]]) -> dict[str, Any]:
    file_format = parsed["file_type"]["format"]
    main_object = project.loader.main_object
    linkage = {
        "imports": [item for item in symbols if item["kind"] == "import"],
        "exports": [item for item in symbols if item["kind"] == "export"],
        "thunks": [item for item in symbols if item["kind"] == "thunk"],
        "unresolved": [item for item in symbols if item["kind"] == "unresolved"],
        "plt": [{"name": name, "address": int(addr)} for name, addr in sorted(getattr(main_object, "plt", {}).items(), key=lambda item: item[1])],
        "got": [],
        "iat": [],
        "relocations": [],
    }
    if file_format == "ELF" and ELFFile is not None and RelocationSection is not None:
        with path.open("rb") as handle:
            elf = ELFFile(handle)
            for section in elf.iter_sections():
                if section.name in {".got", ".got.plt"}:
                    linkage["got"].append(
                        {
                            "name": section.name,
                            "virtual_address": int(section["sh_addr"]),
                            "size": int(section["sh_size"]),
                        }
                    )
                if isinstance(section, RelocationSection):
                    symtab = elf.get_section(section["sh_link"])
                    for relocation in section.iter_relocations():
                        symbol = symtab.get_symbol(relocation["r_info_sym"]) if symtab is not None else None
                        linkage["relocations"].append(
                            {
                                "section": section.name,
                                "offset": int(relocation["r_offset"]),
                                "type": relocation["r_info_type"],
                                "symbol": symbol.name if symbol is not None else None,
                            }
                        )
    elif file_format == "PE" and pefile is not None:
        try:
            pe = pefile.PE(str(path), fast_load=True)
            pe.parse_data_directories(
                directories=[
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_BASERELOC"],
                ]
            )
            for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
                for imp in entry.imports:
                    linkage["iat"].append(
                        {
                            "library": entry.dll.decode("utf-8", errors="replace"),
                            "name": imp.name.decode("utf-8", errors="replace") if imp.name else None,
                            "ordinal": int(imp.ordinal) if imp.ordinal else None,
                            "address": int(imp.address) if imp.address else None,
                        }
                    )
            for block in getattr(pe, "DIRECTORY_ENTRY_BASERELOC", []):
                linkage["relocations"].append(
                    {
                        "section": None,
                        "offset": int(block.struct.VirtualAddress),
                        "type": "base_relocation_block",
                        "symbol": None,
                    }
                )
        except Exception:
            pass
    return linkage


def _collect_debug_info(path: Path, parsed: dict[str, Any]) -> dict[str, Any]:
    file_format = parsed["file_type"]["format"]
    if file_format == "ELF" and ELFFile is not None:
        try:
            with path.open("rb") as handle:
                elf = ELFFile(handle)
                if not elf.has_dwarf_info():
                    return {"available": False, "formats": [], "summary": {}, "source_files": [], "function_names": []}
                dwarf = elf.get_dwarf_info()
                source_files: set[str] = set()
                function_names: list[str] = []
                for cu in dwarf.iter_CUs():
                    top_die = cu.get_top_DIE()
                    comp_dir = top_die.attributes.get("DW_AT_comp_dir")
                    comp_dir_value = comp_dir.value.decode("utf-8", errors="replace") if comp_dir is not None else None
                    line_program = dwarf.line_program_for_CU(cu)
                    if line_program is not None:
                        for entry in line_program.header.file_entry:
                            file_name = entry.name.decode("utf-8", errors="replace")
                            source_files.add(f"{comp_dir_value}/{file_name}" if comp_dir_value else file_name)
                    for die in cu.iter_DIEs():
                        if die.tag != "DW_TAG_subprogram":
                            continue
                        attr = die.attributes.get("DW_AT_name")
                        if attr is None:
                            continue
                        function_names.append(attr.value.decode("utf-8", errors="replace"))
                function_names = sorted(dict.fromkeys(function_names))
                return {
                    "available": True,
                    "formats": ["DWARF"],
                    "summary": {
                        "compilation_unit_count": sum(1 for _ in dwarf.iter_CUs()),
                        "source_file_count": len(source_files),
                        "function_name_count": len(function_names),
                    },
                    "source_files": sorted(source_files),
                    "function_names": function_names,
                }
        except Exception:
            return {"available": False, "formats": [], "summary": {}, "source_files": [], "function_names": []}
    if file_format == "PE" and pefile is not None:
        try:
            pe = pefile.PE(str(path), fast_load=True)
            pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DEBUG"]])
            pdb_paths = []
            for entry in getattr(pe, "DIRECTORY_ENTRY_DEBUG", []):
                raw = pe.get_data(entry.struct.AddressOfRawData, entry.struct.SizeOfData)
                if b"RSDS" in raw:
                    pdb_paths.append(raw.split(b"\x00")[-2].decode("utf-8", errors="replace"))
            return {
                "available": bool(pdb_paths),
                "formats": ["PDB"] if pdb_paths else [],
                "summary": {"pdb_path_count": len(pdb_paths)},
                "source_files": [],
                "function_names": [],
                "pdb_paths": pdb_paths,
            }
        except Exception:
            return {"available": False, "formats": [], "summary": {}, "source_files": [], "function_names": []}
    return {"available": False, "formats": [], "summary": {}, "source_files": [], "function_names": []}


def _disassemble_blocks(project, path: str | Path, *, blocks: list[dict[str, Any]], mode: str, address_names: dict[int, str]) -> list[dict[str, Any]]:
    target = Path(path)
    data = target.read_bytes()
    parsed = parse_artifact(target, data, {}, {})
    items = []
    for block in sorted(blocks, key=lambda item: item["address"]):
        angr_block = project.factory.block(block["address"], size=block["size"], thumb=mode == "thumb")
        for instruction in angr_block.capstone.insns:
            virtual_address = int(instruction.address)
            translations = translate_value(parsed, "virtual_address", virtual_address)
            file_offset = translations[0]["file_offset"] if translations else None
            resolved_operands = []
            comments = []
            for immediate in _extract_immediates(instruction):
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
                    "mixed_code_data": False,
                }
            )
    return items


def _search_names(analysis: dict[str, Any], query: str, *, case_sensitive: bool) -> list[dict[str, Any]]:
    matcher = _matcher(query, case_sensitive=case_sensitive)
    items = []
    for function in analysis["functions"]:
        if matcher(function["name"]) or matcher(function.get("demangled_name") or ""):
            items.append(
                {
                    "result_kind": "function",
                    "function_id": function.get("function_id"),
                    "name": function.get("demangled_name") or function["name"],
                    "address": function["address"],
                }
            )
    for symbol in analysis["symbols"]:
        if matcher(symbol["name"]) or matcher(symbol.get("demangled_name") or ""):
            items.append(
                {
                    "result_kind": "symbol",
                    "name": symbol.get("demangled_name") or symbol["name"],
                    "address": symbol.get("address"),
                    "symbol_kind": symbol["kind"],
                }
            )
    items.sort(key=lambda item: (item["result_kind"], item.get("address") or -1, item["name"]))
    return items


def _search_strings(analysis: dict[str, Any], query: str, *, case_sensitive: bool) -> list[dict[str, Any]]:
    matcher = _matcher(query, case_sensitive=case_sensitive)
    items = []
    for string_item in analysis["strings"]:
        if matcher(string_item["value"]):
            items.append(
                {
                    "result_kind": "string",
                    "string_id": string_item.get("string_id"),
                    "value": string_item["value"],
                    "address": string_item.get("address"),
                    "encoding": string_item["encoding"],
                }
            )
    return items


def _search_address_range(analysis: dict[str, Any], start_address: int, end_address: int) -> list[dict[str, Any]]:
    lo = min(start_address, end_address)
    hi = max(start_address, end_address)
    items = []
    for function in analysis["functions"]:
        if int(function["end_address"]) < lo or int(function["address"]) > hi:
            continue
        items.append(
            {
                "result_kind": "function",
                "function_id": function.get("function_id"),
                "name": function.get("demangled_name") or function["name"],
                "address": function["address"],
                "end_address": function["end_address"],
            }
        )
    for symbol in analysis["symbols"]:
        address = symbol.get("address")
        if address is None or not (lo <= int(address) <= hi):
            continue
        items.append(
            {
                "result_kind": "symbol",
                "name": symbol.get("demangled_name") or symbol["name"],
                "address": int(address),
                "symbol_kind": symbol["kind"],
            }
        )
    items.sort(key=lambda item: (item.get("address") or -1, item["result_kind"], item.get("name") or ""))
    return items


def _search_byte_pattern(path: Path, pattern: str) -> list[dict[str, Any]]:
    normalized = re.sub(r"[^0-9a-fA-F]", "", pattern)
    if not normalized or len(normalized) % 2:
        raise ValueError("byte_pattern searches require an even-length hex string.")
    needle = bytes.fromhex(normalized)
    data = path.read_bytes()
    items = []
    offset = data.find(needle)
    while offset != -1:
        items.append({"result_kind": "byte_pattern", "file_offset": offset, "length": len(needle)})
        offset = data.find(needle, offset + 1)
    return items


def _search_immediate(path: Path, analysis: dict[str, Any], query: str) -> list[dict[str, Any]]:
    target_value = int(query, 16 if query.lower().startswith("0x") else 10)
    project = _load_project(path)
    items = []
    for function in analysis["functions"]:
        for instruction in _disassemble_blocks(project, path, blocks=function["blocks"], mode=analysis["capabilities"]["instruction_set_modes"]["default"], address_names={}):
            operands = f"{instruction['mnemonic']} {instruction['operand_text']}"
            for raw in IMMEDIATE_RE.findall(operands):
                value = int(raw, 16 if raw.lower().startswith("0x") else 10)
                if value == target_value:
                    items.append(
                        {
                            "result_kind": "instruction",
                            "function_id": function.get("function_id"),
                            "function_name": function.get("demangled_name") or function["name"],
                            "address": instruction["address"],
                            "mnemonic": instruction["mnemonic"],
                            "operand_text": instruction["operand_text"],
                        }
                    )
                    break
    items.sort(key=lambda item: (item["address"], item["function_name"]))
    return items


def _search_opcode(path: Path, analysis: dict[str, Any], query: str, *, case_sensitive: bool) -> list[dict[str, Any]]:
    matcher = _matcher(query, case_sensitive=case_sensitive)
    project = _load_project(path)
    items = []
    for function in analysis["functions"]:
        for instruction in _disassemble_blocks(project, path, blocks=function["blocks"], mode=analysis["capabilities"]["instruction_set_modes"]["default"], address_names={}):
            if matcher(instruction["mnemonic"]):
                items.append(
                    {
                        "result_kind": "instruction",
                        "function_id": function.get("function_id"),
                        "function_name": function.get("demangled_name") or function["name"],
                        "address": instruction["address"],
                        "mnemonic": instruction["mnemonic"],
                        "operand_text": instruction["operand_text"],
                    }
                )
    items.sort(key=lambda item: (item["address"], item["function_name"]))
    return items


def _instruction_mode_details(arch_name: str) -> dict[str, Any]:
    normalized = arch_name.upper()
    if normalized.startswith("ARM") and normalized != "AARCH64":
        return {"supported": ["arm", "thumb"], "default": "arm"}
    if normalized in {"X86", "AMD64"}:
        default_mode = "64" if normalized == "AMD64" else "32"
        return {"supported": [default_mode], "default": default_mode}
    return {"supported": [normalized.lower()], "default": normalized.lower()}


def _resolve_instruction_mode(arch_name: str, analysis: dict[str, Any], instruction_mode_override: str | None) -> str:
    supported = analysis["capabilities"]["instruction_set_modes"]["supported"]
    current = instruction_mode_override or analysis["capabilities"]["instruction_set_modes"].get("override") or analysis["capabilities"]["instruction_set_modes"]["default"]
    if current not in supported:
        raise ValueError(f"Instruction-set mode '{current}' is not supported for architecture '{arch_name}'.")
    return current


def _address_name_map(analysis: dict[str, Any]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for function in analysis["functions"]:
        mapping[int(function["address"])] = function.get("demangled_name") or function["name"]
    for symbol in analysis["symbols"]:
        if symbol.get("address") is not None:
            mapping[int(symbol["address"])] = symbol.get("demangled_name") or symbol["name"]
    for string_item in analysis["strings"]:
        address = string_item.get("address")
        if address is not None:
            mapping[int(address)] = f"string:{string_item['value'][:32]}"
    return mapping


def _extract_immediates(instruction) -> list[int]:
    immediates = []
    for raw in IMMEDIATE_RE.findall(f"{instruction.mnemonic} {instruction.op_str}"):
        immediates.append(int(raw, 16 if raw.lower().startswith("0x") else 10))
    return immediates


def _demangle_name(name: str) -> str:
    if not name:
        return name
    if cxxfilt is None:
        return name
    try:
        return cxxfilt.demangle(name, external_only=False)
    except Exception:
        return name


def _infer_stack_size(project, address: int, size: int) -> int | None:
    try:
        block = project.factory.block(address, size=size)
    except Exception:
        return None
    for instruction in block.capstone.insns[:6]:
        text = f"{instruction.mnemonic} {instruction.op_str}"
        if instruction.mnemonic == "sub" and "sp" in instruction.op_str:
            immediate = _extract_immediates(instruction)
            if immediate:
                return int(immediate[-1])
        if instruction.mnemonic == "stp" and "[sp, #-" in instruction.op_str:
            immediate = _extract_immediates(instruction)
            if immediate:
                return int(immediate[-1])
        if instruction.mnemonic == "push":
            return 8
    return None


def _limit_text(text: str, *, char_limit: int, line_limit: int) -> tuple[str, bool]:
    truncated = False
    lines = text.splitlines()
    if len(lines) > line_limit:
        lines = lines[:line_limit]
        truncated = True
    output = "\n".join(lines)
    if len(output) > char_limit:
        output = output[:char_limit]
        truncated = True
    return output, truncated


def _disassembly_warnings(function: dict[str, Any]) -> list[str]:
    warnings = []
    if function["is_plt"]:
        warnings.append("This function appears to be a thunk or PLT entry.")
    if len(function["blocks"]) > 1:
        warnings.append("Disassembly is emitted in recovered basic-block order instead of a single linear byte range.")
    return warnings


def _slice_page(items: list[dict[str, Any]], *, cursor: int, limit: int) -> dict[str, Any]:
    start = max(0, int(cursor))
    size = max(1, int(limit))
    page_items = items[start : start + size]
    next_cursor = start + size if start + size < len(items) else None
    return {
        "items": page_items,
        "page": {
            "cursor": start,
            "limit": size,
            "returned": len(page_items),
            "total": len(items),
            "next_cursor": next_cursor,
            "truncated": next_cursor is not None,
        },
    }


def _matcher(query: str, *, case_sensitive: bool):
    needle = query if case_sensitive else query.lower()

    def match(value: str) -> bool:
        haystack = value if case_sensitive else value.lower()
        return needle in haystack

    return match


def _resolve_runtime_location(project, parsed: dict[str, Any], *, input_kind: str, value: int) -> dict[str, Any]:
    base_address = int(getattr(project.loader.main_object, "mapped_base", 0) or 0)
    if input_kind == "file_offset":
        mappings = translate_value(parsed, "file_offset", value)
        if not mappings:
            raise ValueError(f"Unable to translate file_offset value 0x{value:x}.")
        linked_virtual_address = mappings[0].get("virtual_address")
        execution_address = linked_virtual_address + base_address if linked_virtual_address is not None else None
        return {
            "file_offset": value,
            "linked_virtual_address": linked_virtual_address,
            "execution_address": execution_address,
        }

    direct_mappings = translate_value(parsed, input_kind, value)
    if direct_mappings:
        linked_virtual_address = direct_mappings[0].get("virtual_address")
        execution_address = linked_virtual_address + base_address if linked_virtual_address is not None and linked_virtual_address < base_address else linked_virtual_address
        return {
            "file_offset": int(direct_mappings[0]["file_offset"]),
            "linked_virtual_address": linked_virtual_address,
            "execution_address": execution_address,
        }

    if input_kind == "virtual_address" and value >= base_address:
        rebased_value = value - base_address
        rebased_mappings = translate_value(parsed, "virtual_address", rebased_value)
        if rebased_mappings:
            return {
                "file_offset": int(rebased_mappings[0]["file_offset"]),
                "linked_virtual_address": rebased_mappings[0].get("virtual_address"),
                "execution_address": value,
            }

    raise ValueError(f"Unable to translate {input_kind} value 0x{value:x}.")
