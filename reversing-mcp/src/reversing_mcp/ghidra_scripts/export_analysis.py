# Ghidra headless script: export full program analysis as JSON.
# Usage: analyzeHeadless ... -postScript export_analysis.py [output_path]
# If output_path is provided, writes JSON there; otherwise writes to stdout.
#
# @category reversing-mcp

import json

from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor


def main():
    args = getScriptArgs()  # noqa: F821
    output_path = args[0] if args else None

    program = currentProgram  # noqa: F821
    listing = program.getListing()
    mem = program.getMemory()
    sym_table = program.getSymbolTable()

    # Collect functions
    functions = []
    fm = program.getFunctionManager()
    for func in fm.getFunctions(True):
        entry = func.getEntryPoint()
        body = func.getBody()
        functions.append({
            "name": func.getName(),
            "address": str(entry),
            "address_int": entry.getOffset(),
            "size": body.getNumAddresses() if body else 0,
            "is_external": func.isExternal(),
            "is_thunk": func.isThunk(),
            "calling_convention": str(func.getCallingConventionName()) if func.getCallingConventionName() else None,
            "signature": str(func.getSignature()),
        })

    # Collect imports
    imports = []
    for sym in sym_table.getExternalSymbols():
        imports.append({
            "name": sym.getName(),
            "address": str(sym.getAddress()),
            "library": str(sym.getParentNamespace().getName()) if sym.getParentNamespace() else None,
        })

    # Collect defined strings
    strings = []
    for data in listing.getDefinedData(True):
        dt = data.getDataType()
        if dt and ("string" in dt.getName().lower() or "char" in dt.getName().lower()):
            try:
                val = data.getValue()
                if val is not None:
                    strings.append({
                        "value": str(val),
                        "address": str(data.getAddress()),
                        "address_int": data.getAddress().getOffset(),
                    })
            except Exception:
                pass

    # Collect sections/memory blocks
    sections = []
    for block in mem.getBlocks():
        sections.append({
            "name": block.getName(),
            "start": str(block.getStart()),
            "end": str(block.getEnd()),
            "size": block.getSize(),
            "permissions": {
                "read": block.isRead(),
                "write": block.isWrite(),
                "execute": block.isExecute(),
            },
        })

    result = {
        "ok": True,
        "result": {
            "program_name": program.getName(),
            "language": str(program.getLanguageID()),
            "compiler": str(program.getCompilerSpec().getCompilerSpecID()),
            "image_base": str(program.getImageBase()),
            "entry_point": str(program.getSymbolTable().getSymbol("entry").getAddress()) if program.getSymbolTable().getSymbol("entry") else None,
            "function_count": len(functions),
            "string_count": len(strings),
            "import_count": len(imports),
            "functions": functions,
            "strings": strings[:5000],
            "imports": imports,
            "sections": sections,
        },
    }

    output = json.dumps(result, indent=2)
    if output_path:
        with open(output_path, "w") as f:
            f.write(output)
        print(json.dumps({"ok": True, "result": {"written_to": output_path}}))
    else:
        print(output)


main()
