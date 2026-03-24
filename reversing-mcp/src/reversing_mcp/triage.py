from __future__ import annotations

import binascii
import hashlib
import io
import os
import re
import struct
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Any

try:  # pragma: no cover - import availability depends on install
    import pefile
except ImportError:  # pragma: no cover
    pefile = None

try:  # pragma: no cover - import availability depends on install
    from elftools.elf.dynamic import DynamicSection
    from elftools.elf.elffile import ELFFile
    from elftools.elf.enums import ENUM_E_MACHINE
except ImportError:  # pragma: no cover
    DynamicSection = None
    ELFFile = None
    ENUM_E_MACHINE = {}

try:  # pragma: no cover - import availability depends on install
    from macholib.MachO import MachO
    from macholib.mach_o import (
        CPU_TYPE_NAMES,
        LC_CODE_SIGNATURE,
        LC_LOAD_DYLIB,
        LC_LOAD_WEAK_DYLIB,
        LC_MAIN,
        LC_REEXPORT_DYLIB,
        LC_SEGMENT,
        LC_SEGMENT_64,
        LC_UNIXTHREAD,
        MH_BUNDLE,
        MH_DYLIB,
        MH_EXECUTE,
        MH_PIE,
        MH_PRELOAD,
    )
except ImportError:  # pragma: no cover
    MachO = None
    CPU_TYPE_NAMES = {}
    LC_CODE_SIGNATURE = 0x1D
    LC_LOAD_DYLIB = 0xC
    LC_LOAD_WEAK_DYLIB = 0x18 | 0x80000000
    LC_MAIN = 0x80000028
    LC_REEXPORT_DYLIB = 0x1F | 0x80000000
    LC_SEGMENT = 0x1
    LC_SEGMENT_64 = 0x19
    LC_UNIXTHREAD = 0x5
    MH_BUNDLE = 0x8
    MH_DYLIB = 0x6
    MH_EXECUTE = 0x2
    MH_PIE = 0x200000
    MH_PRELOAD = 0x5


ASCII_RE = re.compile(rb"[\x20-\x7e]{4,}")
UTF16LE_RE = re.compile(rb"(?:[\x20-\x7e]\x00){4,}")
UTF16BE_RE = re.compile(rb"(?:\x00[\x20-\x7e]){4,}")
UTF8_RE = re.compile(
    rb"(?:[\x20-\x7e]|[\xc2-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2}|[\xf0-\xf4][\x80-\xbf]{3}){4,}"
)
PE_DLLCHAR_DYNAMIC_BASE = 0x0040
PE_DLLCHAR_NX_COMPAT = 0x0100
PE_DLLCHAR_GUARD_CF = 0x4000
PE_CHARACTERISTICS_DLL = 0x2000
PE_CHARACTERISTICS_RELOCS_STRIPPED = 0x0001
PE_DIRECTORY_SECURITY = 4
PE_DIRECTORY_BASERELOC = 5
PE_DIRECTORY_TLS = 9
MACHO_MAGIC = {0xFEEDFACE, 0xCEFAEDFE, 0xFEEDFACF, 0xCFFAEDFE, 0xCAFEBABE, 0xBEBAFECA, 0xCAFEBABF, 0xBFBAFECA}


def analyze_artifact(
    path: str | Path,
    *,
    hints: dict[str, Any] | None = None,
    resource_limits: dict[str, Any] | None = None,
    string_preview_limit: int = 20,
) -> dict[str, Any]:
    target = Path(path)
    data = target.read_bytes()
    resource_limits = resource_limits or {}
    parsed = parse_artifact(target, data, hints or {}, resource_limits)
    strings = extract_strings(
        data,
        parsed,
        min_length=4,
        max_strings=int(resource_limits.get("string_count_limit", 50000)),
    )
    preview_limit = max(0, int(string_preview_limit))
    child_items = parsed.get("children", [])
    return {
        "file_type": parsed["file_type"],
        "taxonomy": classify_taxonomy(parsed, strings["items"]),
        "hashes": compute_hashes(target, data, parsed),
        "header": parsed["header"],
        "layout": {
            "sections": parsed["sections"],
            "segments": parsed["segments"],
        },
        "discrepancies": parsed["discrepancies"],
        "security_mitigations": parsed["mitigations"],
        "signatures": parsed["signatures"],
        "deep_inspection": parsed["deep_inspection"],
        "children_preview": {
            "items": child_items[:preview_limit],
            "total": len(child_items),
            "next_cursor": str(preview_limit) if preview_limit < len(child_items) else None,
        },
        "strings_preview": {
            "items": strings["items"][:preview_limit],
            "total": strings["total"],
            "truncated": strings["truncated"],
            "next_cursor": str(preview_limit) if preview_limit < strings["total"] else None,
        },
        "external_enrichment": lookup_external_enrichment(target, opt_in=False),
        "hints_applied": parsed.get("hints_applied", {}),
    }


def list_strings(
    path: str | Path,
    *,
    hints: dict[str, Any] | None = None,
    resource_limits: dict[str, Any] | None = None,
    cursor: int = 0,
    limit: int = 50,
    min_length: int = 4,
    encoding: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    target = Path(path)
    data = target.read_bytes()
    parsed = parse_artifact(target, data, hints or {}, resource_limits or {})
    extracted = extract_strings(
        data,
        parsed,
        min_length=min_length,
        max_strings=int((resource_limits or {}).get("string_count_limit", 50000)),
    )
    items = extracted["items"]
    if encoding:
        items = [item for item in items if item["encoding"] == encoding]
    if query:
        lowered = query.lower()
        items = [item for item in items if lowered in item["value"].lower()]
    items.sort(key=lambda item: (item["file_offset"], item["encoding"], item["value"]))
    start = max(0, int(cursor))
    size = max(1, int(limit))
    page = items[start : start + size]
    next_cursor = start + size if start + size < len(items) else None
    return {
        "items": page,
        "page": {
            "cursor": start,
            "limit": size,
            "returned": len(page),
            "total": len(items),
            "next_cursor": next_cursor,
            "truncated": extracted["truncated"],
        },
    }


def translate_artifact_address(
    path: str | Path,
    *,
    input_kind: str,
    value: int,
    hints: dict[str, Any] | None = None,
    resource_limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = Path(path)
    data = target.read_bytes()
    parsed = parse_artifact(target, data, hints or {}, resource_limits or {})
    mappings = translate_value(parsed, input_kind, value)
    return {
        "input": {"kind": input_kind, "value": value},
        "matches": mappings,
    }


def list_child_artifacts(
    path: str | Path,
    *,
    hints: dict[str, Any] | None = None,
    resource_limits: dict[str, Any] | None = None,
    cursor: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    target = Path(path)
    data = target.read_bytes()
    parsed = parse_artifact(target, data, hints or {}, resource_limits or {})
    children = sorted(parsed.get("children", []), key=lambda item: (item.get("offset", -1), item.get("name", "")))
    start = max(0, int(cursor))
    size = max(1, int(limit))
    page = children[start : start + size]
    next_cursor = start + size if start + size < len(children) else None
    return {
        "items": page,
        "page": {
            "cursor": start,
            "limit": size,
            "returned": len(page),
            "total": len(children),
            "next_cursor": next_cursor,
        },
    }


def lookup_external_enrichment(
    path: str | Path,
    *,
    providers: list[str] | None = None,
    opt_in: bool = False,
) -> dict[str, Any]:
    target = Path(path)
    available_providers = ["virustotal", "nsrl", "symbol_server"]
    requested = providers or available_providers
    return {
        "path": str(target),
        "requested_providers": requested,
        "supported_providers": available_providers,
        "enabled": False,
        "opt_in_requested": bool(opt_in),
        "results": [],
        "status": "disabled",
        "reason": "External enrichment is disabled by default and no network backend is configured.",
        "source": "external",
    }


def parse_artifact(path: Path, data: bytes, hints: dict[str, Any], resource_limits: dict[str, Any]) -> dict[str, Any]:
    detected = detect_format(path, data, hints)
    if detected == "elf":
        return analyze_elf(path, hints)
    if detected == "pe":
        return analyze_pe(path, hints)
    if detected == "macho":
        return analyze_macho(path, data, hints)
    if detected == "zip":
        return analyze_zip(path, data, hints)
    if detected == "tar":
        return analyze_tar(path, data, hints)
    if detected == "intel_hex":
        return analyze_intel_hex(path, data, hints)
    if detected == "srec":
        return analyze_srec(path, data, hints)
    return analyze_raw(path, data, hints)


def detect_format(path: Path, data: bytes, hints: dict[str, Any]) -> str:
    if data.startswith(b"\x7fELF"):
        return "elf"
    if len(data) >= 0x40 and data[:2] == b"MZ":
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        if pe_offset + 4 <= len(data) and data[pe_offset : pe_offset + 4] == b"PE\0\0":
            return "pe"
    if len(data) >= 4 and struct.unpack(">I", data[:4])[0] in MACHO_MAGIC:
        return "macho"
    if zipfile.is_zipfile(path):
        return "zip"
    if tarfile.is_tarfile(path):
        return "tar"
    if looks_like_intel_hex(data):
        return "intel_hex"
    if looks_like_srec(data):
        return "srec"
    if hints:
        return "raw"
    return "raw"


def looks_like_intel_hex(data: bytes) -> bool:
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeDecodeError:
        return False
    meaningful = [line.strip() for line in lines if line.strip()]
    return bool(meaningful) and all(line.startswith(":") and len(line) >= 11 for line in meaningful[:5])


def looks_like_srec(data: bytes) -> bool:
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeDecodeError:
        return False
    meaningful = [line.strip() for line in lines if line.strip()]
    return bool(meaningful) and all(line.startswith("S") and len(line) >= 10 for line in meaningful[:5])


def analyze_elf(path: Path, hints: dict[str, Any]) -> dict[str, Any]:
    if ELFFile is None:  # pragma: no cover - install-time dependency
        raise RuntimeError("pyelftools is not available.")
    with path.open("rb") as handle:
        elf = ELFFile(handle)
        header = elf.header
        e_type = header["e_type"]
        osabi = header["e_ident"]["EI_OSABI"]
        sections = []
        for index, section in enumerate(elf.iter_sections()):
            flags = section["sh_flags"]
            sections.append(
                {
                    "index": index,
                    "name": section.name or f"section_{index}",
                    "type": section["sh_type"],
                    "file_offset": int(section["sh_offset"]),
                    "size": int(section["sh_size"]),
                    "virtual_address": int(section["sh_addr"]),
                    "rva": int(section["sh_addr"]),
                    "virtual_size": int(section["sh_size"]),
                    "alignment": int(section["sh_addralign"]),
                    "permissions": permissions_from_elf_flags(flags),
                }
            )
        segments = []
        for index, segment in enumerate(elf.iter_segments()):
            flags = segment["p_flags"]
            segments.append(
                {
                    "index": index,
                    "type": segment["p_type"],
                    "file_offset": int(segment["p_offset"]),
                    "size": int(segment["p_filesz"]),
                    "virtual_address": int(segment["p_vaddr"]),
                    "rva": int(segment["p_vaddr"]),
                    "virtual_size": int(segment["p_memsz"]),
                    "alignment": int(segment["p_align"]),
                    "permissions": permissions_from_program_flags(flags),
                }
            )
        image_base = min((segment["virtual_address"] for segment in segments if segment["type"] == "PT_LOAD"), default=0)
        needed_libraries = []
        dynamic_flags = []
        undefined_symbols = []
        for section in elf.iter_sections():
            if DynamicSection is not None and isinstance(section, DynamicSection):
                for tag in section.iter_tags():
                    if tag.entry.d_tag == "DT_NEEDED":
                        needed_libraries.append(tag.needed)
                    if tag.entry.d_tag in {"DT_BIND_NOW", "DT_FLAGS", "DT_FLAGS_1"}:
                        dynamic_flags.append(str(getattr(tag, "entry", {}).d_val if hasattr(tag, "entry") else tag.entry))
            if section.name == ".dynsym":
                for symbol in section.iter_symbols():
                    if symbol["st_shndx"] == "SHN_UNDEF" and symbol.name:
                        undefined_symbols.append(symbol.name)
        build_id = None
        notes = []
        for section in elf.iter_sections():
            if section.header["sh_type"] == "SHT_NOTE":
                for note in section.iter_notes():
                    description = note["n_desc"]
                    if isinstance(description, bytes):
                        description_text = description.hex()
                    else:
                        description_text = str(description)
                    note_entry = {
                        "section": section.name,
                        "name": note["n_name"],
                        "type": note["n_type"],
                        "description": description_text[:200],
                    }
                    notes.append(note_entry)
                    if note["n_name"] == "GNU" and note["n_type"] == "NT_GNU_BUILD_ID":
                        build_id = description.hex() if isinstance(description, bytes) else str(description)
        discrepancies = detect_range_discrepancies(sections, segments)
        if header["e_shoff"] == 0 or header["e_shnum"] == 0:
            discrepancies.append(
                {
                    "kind": "missing_section_table",
                    "severity": "high",
                    "message": "ELF file has no section table entries.",
                }
            )
        if not any(section["name"] == ".symtab" for section in sections):
            discrepancies.append(
                {
                    "kind": "stripped_symbols",
                    "severity": "medium",
                    "message": "ELF file appears stripped because .symtab is absent.",
                }
            )
        mitigations = elf_mitigations(elf, segments, undefined_symbols, e_type)
        return {
            "file_type": {
                "format": "ELF",
                "architecture": elf.get_machine_arch(),
                "endianness": "little" if elf.little_endian else "big",
                "bitness": elf.elfclass,
                "platform": "linux" if "GNU" in osabi or osabi == "ELFOSABI_SYSV" else str(osabi),
                "kind": elf_file_kind(e_type),
                "mime_type": file_mime_type(path),
                "description": file_description(path),
            },
            "header": {
                "entry_point": int(header["e_entry"]),
                "image_base": image_base,
                "file_type": e_type,
                "machine": elf.get_machine_arch(),
                "osabi": osabi,
                "interpreter": find_elf_interpreter(elf),
                "needed_libraries": sorted(set(needed_libraries)),
                "undefined_symbol_count": len(undefined_symbols),
                "undefined_symbols_preview": sorted(undefined_symbols)[:20],
                "relocation_section_count": sum(1 for section in sections if "REL" in str(section["type"])),
                "is_relocatable_object": e_type == "ET_REL",
            },
            "sections": normalize_rva_fields(sections, image_base),
            "segments": normalize_rva_fields(segments, image_base),
            "discrepancies": discrepancies,
            "mitigations": mitigations,
            "signatures": {
                "elf_build_id": {
                    "present": build_id is not None,
                    "build_id": build_id,
                }
            },
            "deep_inspection": {
                "elf": {
                    "init_arrays": [section for section in sections if section["name"] in {".init_array", ".preinit_array"}],
                    "fini_arrays": [section for section in sections if section["name"] == ".fini_array"],
                    "gnu_notes": notes,
                }
            },
            "children": [],
            "taxonomy_inputs": {
                "imports": sorted(set(needed_libraries)),
                "undefined_symbols": sorted(undefined_symbols),
                "subsystem": None,
                "format_kind": elf_file_kind(e_type),
                "stripped": not any(section["name"] == ".symtab" for section in sections),
            },
            "hints_applied": hints,
        }


def analyze_pe(path: Path, hints: dict[str, Any]) -> dict[str, Any]:
    if pefile is None:  # pragma: no cover - install-time dependency
        raise RuntimeError("pefile is not available.")
    pe = pefile.PE(str(path), fast_load=False)
    pe.parse_data_directories()
    image_base = int(pe.OPTIONAL_HEADER.ImageBase)
    bitness = 64 if pe.PE_TYPE == pefile.OPTIONAL_HEADER_MAGIC_PE_PLUS else 32
    sections = []
    for index, section in enumerate(pe.sections):
        section_name = section.Name.rstrip(b"\x00").decode("ascii", errors="replace")
        permissions = "".join(
            [
                "r" if section.Characteristics & 0x40000000 else "-",
                "w" if section.Characteristics & 0x80000000 else "-",
                "x" if section.Characteristics & 0x20000000 else "-",
            ]
        )
        sections.append(
            {
                "index": index,
                "name": section_name or f"section_{index}",
                "type": "section",
                "file_offset": int(section.PointerToRawData),
                "size": int(section.SizeOfRawData),
                "virtual_address": image_base + int(section.VirtualAddress),
                "rva": int(section.VirtualAddress),
                "virtual_size": int(section.Misc_VirtualSize),
                "alignment": int(pe.OPTIONAL_HEADER.SectionAlignment),
                "permissions": permissions,
            }
        )
    imports = []
    if hasattr(pe, "DIRECTORY_ENTRY_IMPORT"):
        for entry in pe.DIRECTORY_ENTRY_IMPORT:
            dll_name = entry.dll.decode("ascii", errors="replace").lower()
            for imported in entry.imports:
                import_name = imported.name.decode("ascii", errors="replace").lower() if imported.name else f"ord{imported.ordinal}"
                imports.append(f"{dll_name}.{import_name}")
    discrepancies = detect_range_discrepancies(sections, [])
    if not sections:
        discrepancies.append(
            {
                "kind": "missing_section_table",
                "severity": "high",
                "message": "PE file has no section entries.",
            }
        )
    security_dir = pe.OPTIONAL_HEADER.DATA_DIRECTORY[PE_DIRECTORY_SECURITY]
    reloc_dir = pe.OPTIONAL_HEADER.DATA_DIRECTORY[PE_DIRECTORY_BASERELOC]
    tls_callbacks = []
    if hasattr(pe, "DIRECTORY_ENTRY_TLS"):
        tls_struct = pe.DIRECTORY_ENTRY_TLS.struct
        callbacks = getattr(tls_struct, "AddressOfCallBacks", 0)
        if callbacks:
            tls_callbacks.append({"address_of_callbacks": int(callbacks)})
    delay_imports = []
    if hasattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT"):
        for entry in pe.DIRECTORY_ENTRY_DELAY_IMPORT:
            delay_imports.append({"dll": entry.dll.decode("ascii", errors="replace")})
    dll_characteristics = int(pe.OPTIONAL_HEADER.DllCharacteristics)
    subsystem = pefile.SUBSYSTEM_TYPE.get(pe.OPTIONAL_HEADER.Subsystem, str(pe.OPTIONAL_HEADER.Subsystem))
    mitigations = {
        "nx": {"enabled": bool(dll_characteristics & PE_DLLCHAR_NX_COMPAT), "evidence": "IMAGE_DLLCHARACTERISTICS_NX_COMPAT"},
        "aslr": {"enabled": bool(dll_characteristics & PE_DLLCHAR_DYNAMIC_BASE), "evidence": "IMAGE_DLLCHARACTERISTICS_DYNAMIC_BASE"},
        "pie": {"enabled": bool(dll_characteristics & PE_DLLCHAR_DYNAMIC_BASE), "evidence": "PE relocation-based image randomization"},
        "relro": {"enabled": False, "evidence": "RELRO is not a PE mitigation"},
        "canary": {
            "enabled": any("__security_cookie" in item or "__security_check_cookie" in item for item in imports),
            "evidence": "import heuristic",
        },
        "cfg": {"enabled": bool(dll_characteristics & PE_DLLCHAR_GUARD_CF), "evidence": "IMAGE_DLLCHARACTERISTICS_GUARD_CF"},
    }
    return {
        "file_type": {
            "format": "PE",
            "architecture": pefile.MACHINE_TYPE.get(pe.FILE_HEADER.Machine, hex(pe.FILE_HEADER.Machine)),
            "endianness": "little",
            "bitness": bitness,
            "platform": "windows",
            "kind": pe_file_kind(pe, subsystem),
            "mime_type": file_mime_type(path),
            "description": file_description(path),
        },
        "header": {
            "entry_point": image_base + int(pe.OPTIONAL_HEADER.AddressOfEntryPoint),
            "image_base": image_base,
            "subsystem": subsystem,
            "machine": pefile.MACHINE_TYPE.get(pe.FILE_HEADER.Machine, hex(pe.FILE_HEADER.Machine)),
            "timestamp": int(pe.FILE_HEADER.TimeDateStamp),
            "is_relocatable_object": False,
            "relocations_present": bool(reloc_dir.VirtualAddress and reloc_dir.Size),
            "section_alignment": int(pe.OPTIONAL_HEADER.SectionAlignment),
            "file_alignment": int(pe.OPTIONAL_HEADER.FileAlignment),
        },
        "sections": sections,
        "segments": [],
        "discrepancies": discrepancies,
        "mitigations": mitigations,
        "signatures": {
            "pe_authenticode": {
                "present": bool(security_dir.VirtualAddress and security_dir.Size),
                "directory_offset": int(security_dir.VirtualAddress),
                "directory_size": int(security_dir.Size),
                "coverage": "unknown",
            }
        },
        "deep_inspection": {
            "pe": {
                "tls_callbacks": tls_callbacks,
                "delay_imports": delay_imports,
            }
        },
        "children": [],
        "taxonomy_inputs": {
            "imports": sorted(set(imports)),
            "subsystem": subsystem,
            "format_kind": pe_file_kind(pe, subsystem),
            "stripped": False,
        },
        "hints_applied": hints,
    }


def analyze_macho(path: Path, data: bytes, hints: dict[str, Any]) -> dict[str, Any]:
    if MachO is None:  # pragma: no cover - install-time dependency
        raise RuntimeError("macholib is not available.")
    macho = MachO(str(path))
    sections = []
    segments = []
    load_dylibs = []
    code_signature = None
    entry_point = None
    children = []
    image_base = None
    architecture = "unknown"
    bitness = 64
    platform = "macos"
    kind = "mach-o"
    for header_index, macho_header in enumerate(macho.headers):
        header = macho_header.header
        architecture = CPU_TYPE_NAMES.get(getattr(header, "cputype", 0), str(getattr(header, "cputype", "unknown")))
        bitness = 64 if getattr(header, "magic", 0) in {0xFEEDFACF, 0xCFFAEDFE} else 32
        header_type = getattr(header, "filetype", 0)
        if header_type == MH_EXECUTE:
            kind = "executable"
        elif header_type == MH_DYLIB:
            kind = "shared_library"
        elif header_type == MH_BUNDLE:
            kind = "bundle"
        elif header_type == MH_PRELOAD:
            kind = "preload"
        children.append(
            {
                "index": header_index,
                "name": f"macho_header_{header_index}",
                "container_path": f"header[{header_index}]",
                "offset": getattr(macho_header, "offset", 0),
                "size": getattr(macho_header, "sizediff", 0) or 0,
                "architecture": architecture,
            }
        )
        for load_command, command_data, command_sections in macho_header.commands:
            if load_command.cmd in {LC_SEGMENT, LC_SEGMENT_64}:
                segname = getattr(command_data, "segname", b"").rstrip(b"\x00").decode("ascii", errors="replace")
                seg_entry = {
                    "name": segname,
                    "type": "segment",
                    "file_offset": int(getattr(command_data, "fileoff", 0)),
                    "size": int(getattr(command_data, "filesize", 0)),
                    "virtual_address": int(getattr(command_data, "vmaddr", 0)),
                    "rva": int(getattr(command_data, "vmaddr", 0)),
                    "virtual_size": int(getattr(command_data, "vmsize", 0)),
                    "alignment": 0,
                    "permissions": permissions_from_macho_prot(int(getattr(command_data, "initprot", 0))),
                }
                segments.append(seg_entry)
                if image_base is None and segname != "__PAGEZERO":
                    image_base = seg_entry["virtual_address"]
                for section_index, section in enumerate(command_sections or []):
                    sectname = getattr(section, "sectname", b"").rstrip(b"\x00").decode("ascii", errors="replace")
                    sections.append(
                        {
                            "index": section_index,
                            "name": sectname,
                            "type": "section",
                            "file_offset": int(getattr(section, "offset", 0)),
                            "size": int(getattr(section, "size", 0)),
                            "virtual_address": int(getattr(section, "addr", 0)),
                            "rva": int(getattr(section, "addr", 0)),
                            "virtual_size": int(getattr(section, "size", 0)),
                            "alignment": int(getattr(section, "align", 0)),
                            "permissions": seg_entry["permissions"],
                        }
                    )
            elif load_command.cmd in {LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB}:
                dylib_name = getattr(command_data, "name", None)
                if isinstance(dylib_name, bytes):
                    dylib_name = dylib_name.rstrip(b"\x00").decode("utf-8", errors="replace")
                load_dylibs.append({"name": dylib_name or "unknown", "kind": hex(load_command.cmd)})
            elif load_command.cmd == LC_CODE_SIGNATURE:
                code_signature = {
                    "present": True,
                    "data_offset": int(getattr(command_data, "dataoff", 0)),
                    "data_size": int(getattr(command_data, "datasize", 0)),
                }
            elif load_command.cmd == LC_MAIN:
                entry_point = int(getattr(command_data, "entryoff", 0))
            elif load_command.cmd == LC_UNIXTHREAD and entry_point is None:
                entry_point = 0
    flags = getattr(macho.headers[0].header, "flags", 0) if macho.headers else 0
    image_base = image_base or 0
    return {
        "file_type": {
            "format": "Mach-O",
            "architecture": architecture,
            "endianness": "little",
            "bitness": bitness,
            "platform": platform,
            "kind": kind,
            "mime_type": file_mime_type(path),
            "description": file_description(path),
        },
        "header": {
            "entry_point": (image_base + entry_point) if entry_point is not None else None,
            "image_base": image_base,
            "is_relocatable_object": False,
        },
        "sections": normalize_rva_fields(sections, image_base),
        "segments": normalize_rva_fields(segments, image_base),
        "discrepancies": detect_range_discrepancies(sections, segments),
        "mitigations": {
            "nx": {"enabled": any(segment["name"] == "__PAGEZERO" for segment in segments), "evidence": "__PAGEZERO segment heuristic"},
            "aslr": {"enabled": bool(flags & MH_PIE), "evidence": "MH_PIE"},
            "pie": {"enabled": bool(flags & MH_PIE), "evidence": "MH_PIE"},
            "relro": {"enabled": False, "evidence": "RELRO is ELF-specific"},
            "canary": {"enabled": False, "evidence": "not determined"},
            "cfg": {"enabled": False, "evidence": "CFG is not a Mach-O mitigation"},
        },
        "signatures": {
            "mach_o_code_signature": code_signature or {"present": False},
        },
        "deep_inspection": {
            "mach_o": {
                "load_dylibs": load_dylibs,
                "code_signature": code_signature or {"present": False},
            }
        },
        "children": children if len(children) > 1 else [],
        "taxonomy_inputs": {
            "imports": [item["name"] for item in load_dylibs],
            "subsystem": None,
            "format_kind": kind,
            "stripped": False,
        },
        "hints_applied": hints,
    }


def analyze_zip(path: Path, data: bytes, hints: dict[str, Any]) -> dict[str, Any]:
    children = []
    with zipfile.ZipFile(path) as archive:
        for index, info in enumerate(sorted(archive.infolist(), key=lambda item: (item.header_offset, item.filename))):
            children.append(
                {
                    "index": index,
                    "name": info.filename,
                    "container_path": info.filename,
                    "offset": int(info.header_offset),
                    "compressed_size": int(info.compress_size),
                    "size": int(info.file_size),
                    "compression": str(info.compress_type),
                    "is_directory": info.is_dir(),
                    "provenance": {
                        "source_artifact": str(path),
                        "container_path": info.filename,
                        "extraction_method": "zip",
                    },
                }
            )
    return {
        "file_type": {
            "format": "ZIP",
            "architecture": "n/a",
            "endianness": "n/a",
            "bitness": None,
            "platform": "container",
            "kind": "archive",
            "mime_type": file_mime_type(path),
            "description": file_description(path),
        },
        "header": {
            "entry_point": None,
            "image_base": None,
            "is_relocatable_object": False,
        },
        "sections": [],
        "segments": [],
        "discrepancies": [],
        "mitigations": {},
        "signatures": {},
        "deep_inspection": {"archive": {"member_count": len(children)}},
        "children": children,
        "taxonomy_inputs": {
            "imports": [],
            "subsystem": None,
            "format_kind": "archive",
            "stripped": False,
        },
        "hints_applied": hints,
    }


def analyze_tar(path: Path, data: bytes, hints: dict[str, Any]) -> dict[str, Any]:
    children = []
    with tarfile.open(path) as archive:
        for index, member in enumerate(sorted(archive.getmembers(), key=lambda item: (item.offset_data, item.name))):
            children.append(
                {
                    "index": index,
                    "name": member.name,
                    "container_path": member.name,
                    "offset": int(member.offset_data),
                    "size": int(member.size),
                    "type": member.type.decode("ascii", errors="replace") if isinstance(member.type, bytes) else str(member.type),
                    "provenance": {
                        "source_artifact": str(path),
                        "container_path": member.name,
                        "extraction_method": "tar",
                    },
                }
            )
    return {
        "file_type": {
            "format": "TAR",
            "architecture": "n/a",
            "endianness": "n/a",
            "bitness": None,
            "platform": "container",
            "kind": "archive",
            "mime_type": file_mime_type(path),
            "description": file_description(path),
        },
        "header": {
            "entry_point": None,
            "image_base": None,
            "is_relocatable_object": False,
        },
        "sections": [],
        "segments": [],
        "discrepancies": [],
        "mitigations": {},
        "signatures": {},
        "deep_inspection": {"archive": {"member_count": len(children)}},
        "children": children,
        "taxonomy_inputs": {
            "imports": [],
            "subsystem": None,
            "format_kind": "archive",
            "stripped": False,
        },
        "hints_applied": hints,
    }


def analyze_intel_hex(path: Path, data: bytes, hints: dict[str, Any]) -> dict[str, Any]:
    text = data.decode("ascii", errors="replace").splitlines()
    base = 0
    ranges = []
    for line in text:
        line = line.strip()
        if not line or not line.startswith(":") or len(line) < 11:
            continue
        count = int(line[1:3], 16)
        address = int(line[3:7], 16)
        record_type = int(line[7:9], 16)
        payload = line[9 : 9 + count * 2]
        if record_type == 0x00:
            absolute = base + address
            ranges.append((absolute, count))
        elif record_type == 0x04:
            base = int(line[9:13], 16) << 16
    segments = contiguous_ranges(ranges)
    return hinted_text_blob_result(path, "Intel HEX", segments, hints)


def analyze_srec(path: Path, data: bytes, hints: dict[str, Any]) -> dict[str, Any]:
    text = data.decode("ascii", errors="replace").splitlines()
    ranges = []
    for line in text:
        line = line.strip()
        if not line.startswith("S") or len(line) < 10:
            continue
        record_type = line[1]
        if record_type not in {"1", "2", "3"}:
            continue
        count = int(line[2:4], 16)
        addr_len = {"1": 4, "2": 6, "3": 8}[record_type]
        address = int(line[4 : 4 + addr_len], 16)
        data_len = max(0, count - (addr_len // 2) - 1)
        ranges.append((address, data_len))
    segments = contiguous_ranges(ranges)
    return hinted_text_blob_result(path, "SREC", segments, hints)


def hinted_text_blob_result(path: Path, format_name: str, segments: list[dict[str, Any]], hints: dict[str, Any]) -> dict[str, Any]:
    image_base = min((segment["virtual_address"] for segment in segments), default=parse_int(hints.get("base_address"), 0))
    return {
        "file_type": {
            "format": format_name,
            "architecture": hints.get("architecture", "unknown"),
            "endianness": hints.get("endianness", "unknown"),
            "bitness": hints.get("bitness"),
            "platform": hints.get("platform", "firmware"),
            "kind": "firmware_image",
            "mime_type": file_mime_type(path),
            "description": file_description(path),
        },
        "header": {
            "entry_point": parse_int(hints.get("entry_point")),
            "image_base": image_base,
            "is_relocatable_object": False,
        },
        "sections": [],
        "segments": normalize_rva_fields(segments, image_base),
        "discrepancies": [],
        "mitigations": {},
        "signatures": {},
        "deep_inspection": {},
        "children": [],
        "taxonomy_inputs": {
            "imports": [],
            "subsystem": None,
            "format_kind": "firmware_image",
            "stripped": True,
        },
        "hints_applied": hints,
    }


def analyze_raw(path: Path, data: bytes, hints: dict[str, Any]) -> dict[str, Any]:
    base_address = parse_int(hints.get("base_address"), 0)
    memory_map = []
    if hints.get("memory_map"):
        for index, entry in enumerate(hints["memory_map"]):
            memory_map.append(
                {
                    "index": index,
                    "name": entry.get("name", f"range_{index}"),
                    "type": "segment",
                    "file_offset": int(parse_int(entry.get("file_offset"), 0) or 0),
                    "size": int(parse_int(entry.get("size"), len(data)) or len(data)),
                    "virtual_address": int(parse_int(entry.get("virtual_address"), base_address) or base_address),
                    "rva": int(parse_int(entry.get("virtual_address"), base_address) or base_address),
                    "virtual_size": int(parse_int(entry.get("virtual_size"), entry.get("size"), len(data)) or len(data)),
                    "alignment": int(parse_int(entry.get("alignment"), 1) or 1),
                    "permissions": entry.get("permissions", "r-x"),
                }
            )
    else:
        memory_map.append(
            {
                "index": 0,
                "name": "raw_image",
                "type": "segment",
                "file_offset": 0,
                "size": len(data),
                "virtual_address": base_address,
                "rva": base_address,
                "virtual_size": len(data),
                "alignment": 1,
                "permissions": hints.get("permissions", "r-x"),
            }
        )
    return {
        "file_type": {
            "format": hints.get("format", "RAW"),
            "architecture": hints.get("architecture", "unknown"),
            "endianness": hints.get("endianness", "unknown"),
            "bitness": hints.get("bitness"),
            "platform": hints.get("platform", "firmware"),
            "kind": "firmware_image" if hints else "raw_blob",
            "mime_type": file_mime_type(path),
            "description": file_description(path),
        },
        "header": {
            "entry_point": parse_int(hints.get("entry_point")),
            "image_base": base_address if hints else None,
            "is_relocatable_object": False,
            "memory_map_hint_applied": bool(hints),
        },
        "sections": [],
        "segments": normalize_rva_fields(memory_map, base_address),
        "discrepancies": [],
        "mitigations": {},
        "signatures": {},
        "deep_inspection": {},
        "children": [],
        "taxonomy_inputs": {
            "imports": [],
            "subsystem": None,
            "format_kind": "firmware_image" if hints else "raw_blob",
            "stripped": True,
        },
        "hints_applied": hints,
    }


def compute_hashes(path: Path, data: bytes, parsed: dict[str, Any]) -> dict[str, Any]:
    hashes = {
        "md5": hashlib.md5(data).hexdigest(),
        "sha1": hashlib.sha1(data).hexdigest(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "ssdeep": compute_ssdeep(path),
    }
    if parsed["file_type"]["format"] == "PE" and pefile is not None:
        try:
            pe = pefile.PE(str(path), fast_load=True)
            hashes["imphash"] = {"applicable": True, "value": pe.get_imphash()}
        except Exception:
            hashes["imphash"] = {"applicable": True, "value": None}
    else:
        hashes["imphash"] = {"applicable": False, "value": None}
    return hashes


def compute_ssdeep(path: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["ssdeep", "-b", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except FileNotFoundError:
        return {"available": False, "value": None}
    if completed.returncode != 0:
        return {"available": False, "value": None}
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return {"available": True, "value": lines[-1] if lines else None}


def classify_taxonomy(parsed: dict[str, Any], strings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inputs = parsed.get("taxonomy_inputs", {})
    imports = [item.lower() for item in inputs.get("imports", [])]
    string_values = [item["value"].lower() for item in strings]
    categories = []
    kind = inputs.get("format_kind")
    subsystem = (inputs.get("subsystem") or "").lower()
    stripped = bool(inputs.get("stripped"))
    if kind in {"shared_library", "bundle"}:
        categories.append(taxonomy_entry("shared_library", "exact", ["format metadata marks the artifact as a shared library"]))
    if kind == "relocatable_object":
        categories.append(taxonomy_entry("shared_library", "medium", ["relocatable object files often provide reusable library code"]))
    if "gui" in subsystem or any(lib for lib in imports if any(token in lib for token in ("gtk", "qt", "appkit", "uikit"))):
        categories.append(taxonomy_entry("gui_application", "high", ["GUI subsystem or UI framework imports were detected"]))
    if "console" in subsystem or kind == "executable":
        categories.append(taxonomy_entry("cli_tool", "high" if not stripped else "medium", ["Executable metadata indicates an interactive program"]))
    if any("service" in item or "daemon" in item for item in imports + string_values):
        categories.append(taxonomy_entry("service_or_daemon", "medium", ["Service- or daemon-related imports/strings were detected"]))
    if kind == "archive" and any("setup" in child.get("name", "").lower() or "install" in child.get("name", "").lower() for child in parsed.get("children", [])):
        categories.append(taxonomy_entry("installer", "medium", ["Archive member names look installer-related"]))
    if kind == "firmware_image":
        categories.append(taxonomy_entry("firmware_image", "high" if inputs.get("subsystem") is None else "medium", ["Raw or record-based image analysis used firmware-style memory mapping"]))
    if kind == "raw_blob" and parsed["file_type"]["architecture"] == "unknown":
        categories.append(taxonomy_entry("shellcode", "speculative", ["Headerless raw bytes without format metadata may be shellcode or firmware"]))
    if not categories:
        categories.append(taxonomy_entry("cli_tool", "speculative" if stripped else "low", ["No stronger taxonomy signal was detected"]))
    ranking = {"exact": 0, "high": 1, "medium": 2, "low": 3, "speculative": 4}
    categories.sort(key=lambda item: (ranking.get(item["confidence"]["level"], 99), item["category"]))
    return categories


def taxonomy_entry(category: str, confidence: str, evidence: list[str]) -> dict[str, Any]:
    return {
        "category": category,
        "confidence": {"level": confidence, "method": "heuristic classification"},
        "evidence": evidence,
    }


def extract_strings(data: bytes, parsed: dict[str, Any], *, min_length: int, max_strings: int) -> dict[str, Any]:
    strings = []
    seen = set()

    def _append(file_offset: int, raw: bytes, encoding: str, value: str) -> None:
        fingerprint = (file_offset, encoding, value)
        if fingerprint in seen:
            return
        seen.add(fingerprint)
        context = context_for_offset(parsed, file_offset)
        virtual_address = context.get("virtual_address")
        strings.append(
            {
                "file_offset": file_offset,
                "virtual_address": virtual_address,
                "relative_virtual_address": context.get("rva"),
                "encoding": encoding,
                "length": len(value),
                "value": value,
                "section": context.get("section"),
                "segment": context.get("segment"),
            }
        )

    for match in ASCII_RE.finditer(data):
        if len(strings) >= max_strings:
            break
        raw = match.group()
        if len(raw) >= min_length:
            _append(match.start(), raw, "ascii", raw.decode("ascii", errors="replace"))
    for match in UTF16LE_RE.finditer(data):
        if len(strings) >= max_strings:
            break
        raw = match.group()
        value = raw.decode("utf-16le", errors="ignore")
        if len(value) >= min_length:
            _append(match.start(), raw, "utf-16le", value)
    for match in UTF16BE_RE.finditer(data):
        if len(strings) >= max_strings:
            break
        raw = match.group()
        value = raw.decode("utf-16be", errors="ignore")
        if len(value) >= min_length:
            _append(match.start(), raw, "utf-16be", value)
    for match in UTF8_RE.finditer(data):
        if len(strings) >= max_strings:
            break
        raw = match.group()
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if len(value) >= min_length and any(ord(ch) > 127 for ch in value):
            _append(match.start(), raw, "utf-8", value)
    strings.sort(key=lambda item: (item["file_offset"], item["encoding"], item["value"]))
    return {
        "items": strings,
        "total": len(strings),
        "truncated": len(strings) >= max_strings,
    }


def context_for_offset(parsed: dict[str, Any], file_offset: int) -> dict[str, Any]:
    best_section = next((item for item in parsed.get("sections", []) if item["file_offset"] <= file_offset < item["file_offset"] + max(item["size"], 1)), None)
    best_segment = next((item for item in parsed.get("segments", []) if item["file_offset"] <= file_offset < item["file_offset"] + max(item["size"], 1)), None)
    result = {
        "section": best_section["name"] if best_section else None,
        "segment": best_segment.get("name") if best_segment else None,
        "virtual_address": None,
        "rva": None,
    }
    mapping = best_section or best_segment
    if mapping and mapping.get("virtual_address") is not None:
        delta = file_offset - mapping["file_offset"]
        result["virtual_address"] = mapping["virtual_address"] + delta
        if mapping.get("rva") is not None:
            result["rva"] = mapping["rva"] + delta
    return result


def translate_value(parsed: dict[str, Any], input_kind: str, value: int) -> list[dict[str, Any]]:
    mappings = []
    for mapping_kind, items in (("section", parsed.get("sections", [])), ("segment", parsed.get("segments", []))):
        for item in items:
            match = translate_against_mapping(mapping_kind, item, input_kind, value)
            if match:
                mappings.append(match)
    mappings.sort(key=lambda item: (item["mapping_kind"], item["name"]))
    return mappings


def translate_against_mapping(mapping_kind: str, item: dict[str, Any], input_kind: str, value: int) -> dict[str, Any] | None:
    size = max(item.get("virtual_size") or item.get("size") or 0, item.get("size") or 0)
    file_size = max(item.get("size") or 0, 0)
    file_offset = item.get("file_offset")
    virtual_address = item.get("virtual_address")
    rva = item.get("rva")
    if input_kind == "file_offset" and file_offset is not None and file_offset <= value < file_offset + max(file_size, 1):
        delta = value - file_offset
    elif input_kind == "virtual_address" and virtual_address is not None and virtual_address <= value < virtual_address + max(size, 1):
        delta = value - virtual_address
    elif input_kind == "rva" and rva is not None and rva <= value < rva + max(size, 1):
        delta = value - rva
    else:
        return None
    return {
        "mapping_kind": mapping_kind,
        "name": item.get("name"),
        "file_offset": file_offset + delta if file_offset is not None else None,
        "virtual_address": virtual_address + delta if virtual_address is not None else None,
        "rva": rva + delta if rva is not None else None,
        "permissions": item.get("permissions"),
    }


def detect_range_discrepancies(sections: list[dict[str, Any]], segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    discrepancies = []
    for label, items in (("section", sections), ("segment", segments)):
        ordered = sorted(items, key=lambda item: (item.get("file_offset", -1), item.get("size", 0), item.get("name", "")))
        for previous, current in zip(ordered, ordered[1:]):
            prev_end = previous.get("file_offset", 0) + previous.get("size", 0)
            if current.get("file_offset", 0) < prev_end:
                discrepancies.append(
                    {
                        "kind": f"overlapping_{label}s",
                        "severity": "medium",
                        "message": f"{label.title()} '{current.get('name')}' overlaps the preceding {label}.",
                    }
                )
            alignment = current.get("alignment")
            if alignment not in {None, 0, 1} and alignment & (alignment - 1):
                discrepancies.append(
                    {
                        "kind": f"non_power_of_two_{label}_alignment",
                        "severity": "low",
                        "message": f"{label.title()} '{current.get('name')}' uses a non power-of-two alignment.",
                    }
                )
    return discrepancies


def elf_mitigations(elf: Any, segments: list[dict[str, Any]], undefined_symbols: list[str], e_type: str) -> dict[str, Any]:
    gnu_stack = next((segment for segment in segments if segment["type"] == "PT_GNU_STACK"), None)
    gnu_relro = next((segment for segment in segments if segment["type"] == "PT_GNU_RELRO"), None)
    has_bind_now = False
    for section in elf.iter_sections():
        if DynamicSection is not None and isinstance(section, DynamicSection):
            for tag in section.iter_tags():
                if tag.entry.d_tag == "DT_BIND_NOW":
                    has_bind_now = True
    canary = any(symbol == "__stack_chk_fail" for symbol in undefined_symbols)
    return {
        "nx": {
            "enabled": bool(gnu_stack is None or "x" not in gnu_stack["permissions"]),
            "evidence": "PT_GNU_STACK executable flag",
        },
        "aslr": {"enabled": e_type == "ET_DYN", "evidence": "ET_DYN indicates PIE-compatible main executable"},
        "pie": {"enabled": e_type == "ET_DYN", "evidence": "ET_DYN"},
        "relro": {
            "enabled": bool(gnu_relro),
            "mode": "full" if gnu_relro and has_bind_now else "partial" if gnu_relro else "none",
            "evidence": "PT_GNU_RELRO plus DT_BIND_NOW",
        },
        "canary": {"enabled": canary, "evidence": "__stack_chk_fail import heuristic"},
        "cfg": {"enabled": False, "evidence": "CFG is not a standard ELF mitigation"},
    }


def find_elf_interpreter(elf: Any) -> str | None:
    for segment in elf.iter_segments():
        if segment["p_type"] == "PT_INTERP":
            data = segment.data()
            return data.rstrip(b"\x00").decode("utf-8", errors="replace")
    return None


def permissions_from_elf_flags(flags: int) -> str:
    return "".join(
        [
            "r" if flags & 0x2 else "-",
            "w" if flags & 0x1 else "-",
            "x" if flags & 0x4 else "-",
        ]
    )


def permissions_from_program_flags(flags: int) -> str:
    return "".join(
        [
            "r" if flags & 0x4 else "-",
            "w" if flags & 0x2 else "-",
            "x" if flags & 0x1 else "-",
        ]
    )


def permissions_from_macho_prot(prot: int) -> str:
    return "".join(
        [
            "r" if prot & 0x1 else "-",
            "w" if prot & 0x2 else "-",
            "x" if prot & 0x4 else "-",
        ]
    )


def normalize_rva_fields(items: list[dict[str, Any]], image_base: int) -> list[dict[str, Any]]:
    normalized = []
    for item in items:
        entry = dict(item)
        if entry.get("virtual_address") is not None and entry.get("rva") is None:
            entry["rva"] = entry["virtual_address"] - image_base if image_base is not None else entry["virtual_address"]
        normalized.append(entry)
    return normalized


def contiguous_ranges(ranges: list[tuple[int, int]]) -> list[dict[str, Any]]:
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged: list[tuple[int, int]] = []
    start, size = ordered[0]
    end = start + size
    for current_start, current_size in ordered[1:]:
        current_end = current_start + current_size
        if current_start <= end:
            end = max(end, current_end)
        else:
            merged.append((start, end - start))
            start, end = current_start, current_end
    merged.append((start, end - start))
    return [
        {
            "index": index,
            "name": f"range_{index}",
            "type": "segment",
            "file_offset": 0,
            "size": size,
            "virtual_address": start,
            "rva": start,
            "virtual_size": size,
            "alignment": 1,
            "permissions": "r-x",
        }
        for index, (start, size) in enumerate(merged)
    ]


def elf_file_kind(e_type: str) -> str:
    return {
        "ET_EXEC": "executable",
        "ET_DYN": "shared_library",
        "ET_REL": "relocatable_object",
        "ET_CORE": "core_dump",
    }.get(e_type, str(e_type).lower())


def pe_file_kind(pe: Any, subsystem: str) -> str:
    if pe.FILE_HEADER.Characteristics & PE_CHARACTERISTICS_DLL:
        return "shared_library"
    if subsystem.lower().endswith("native"):
        return "kernel_driver"
    return "executable"


def parse_int(*values: Any) -> int | None:
    for value in values:
        if value is None or value == "":
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            raw = value.strip().lower()
            try:
                return int(raw, 16 if raw.startswith("0x") else 10)
            except ValueError:
                continue
    return None


def file_description(path: Path) -> str | None:
    return run_file_command(path, [])


def file_mime_type(path: Path) -> str | None:
    return run_file_command(path, ["--mime-type"])


def run_file_command(path: Path, extra_args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["file", "-b", *extra_args, str(path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except FileNotFoundError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None
