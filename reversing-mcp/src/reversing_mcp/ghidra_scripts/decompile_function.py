# Ghidra headless script: decompile a function at a given address.
# Usage: analyzeHeadless ... -postScript decompile_function.py <hex_address>
# Outputs JSON to stdout.
#
# Runs inside Ghidra's Jython environment — uses Ghidra Java API.
# @category reversing-mcp

import json
import sys

from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor


def main():
    args = getScriptArgs()  # noqa: F821 — Ghidra injects this
    if not args:
        print(json.dumps({"ok": False, "error": "No address argument provided"}))
        return

    address_str = args[0]
    addr = currentProgram.getAddressFactory().getAddress(address_str)  # noqa: F821
    if addr is None:
        print(json.dumps({"ok": False, "error": "Invalid address: " + address_str}))
        return

    func = getFunctionAt(addr)  # noqa: F821
    if func is None:
        func = getFunctionContaining(addr)  # noqa: F821
    if func is None:
        print(json.dumps({"ok": False, "error": "No function at address: " + address_str}))
        return

    monitor = ConsoleTaskMonitor()
    decomp = DecompInterface()
    decomp.openProgram(currentProgram)  # noqa: F821

    results = decomp.decompileFunction(func, 120, monitor)
    if not results.depiledFunction():
        c_code = ""
        warnings = ["Decompilation failed: " + (results.getErrorMessage() or "unknown error")]
    else:
        c_code = results.getDecompiledFunction().getC()
        warnings = []

    output = {
        "ok": True,
        "result": {
            "function_name": func.getName(),
            "entry_point": str(func.getEntryPoint()),
            "source": c_code,
            "line_count": len(c_code.splitlines()) if c_code else 0,
            "char_count": len(c_code) if c_code else 0,
            "warnings": warnings,
        },
    }
    print(json.dumps(output))


main()
