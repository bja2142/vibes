"""
Frida dynamic instrumentation tools.

Provides function hooking, memory inspection, and script injection.
"""
from __future__ import annotations

import subprocess
import uuid
from typing import Any, TYPE_CHECKING

from mcp.types import Tool

from ..config import DEFAULT_EXEC_TIMEOUT_SECONDS
from ..errors import PwnMcpError
from ..store import FridaSession
from ..utils import which_tool

if TYPE_CHECKING:
    from ..app import PwnMcpApp


def _require_frida() -> None:
    try:
        import frida  # noqa: F401
    except ImportError:
        raise PwnMcpError("tool_not_found", "frida_missing", "Frida Python bindings are not installed.")


def start_frida_session(
    app: "PwnMcpApp",
    session_id: str,
    binary_path: str,
    args: list[str] | None = None,
) -> dict[str, Any]:
    _require_frida()
    import frida

    session = app.sessions.get(session_id)
    binary = app.security.resolve_binary(binary_path)

    frida_id = f"frida_{uuid.uuid4().hex[:8]}"

    # Spawn the binary under Frida
    device = frida.get_local_device()
    pid = device.spawn([str(binary)] + (args or []))
    frida_session = device.attach(pid)

    fs = FridaSession(
        frida_id=frida_id,
        session_id=session_id,
        target=str(binary),
        pid=pid,
        device=device,
        session=frida_session,
    )

    with session._lock:
        session.frida_sessions[frida_id] = fs

    # Resume the process (Frida spawns it paused)
    device.resume(pid)

    return {
        "ok": True,
        "result": {
            "frida_id": frida_id,
            "pid": pid,
            "binary": str(binary),
        },
    }


def inject_script(
    app: "PwnMcpApp",
    session_id: str,
    frida_id: str,
    script_source: str,
    script_name: str | None = None,
) -> dict[str, Any]:
    _require_frida()
    session = app.sessions.get(session_id)
    fs = session.frida_sessions.get(frida_id)
    if fs is None:
        raise PwnMcpError("not_found", "frida_session_not_found", f"Frida session '{frida_id}' not found.")

    name = script_name or f"script_{uuid.uuid4().hex[:6]}"
    messages: list[dict] = []

    def on_message(msg, data):
        messages.append({"type": msg.get("type"), "payload": msg.get("payload"), "description": msg.get("description")})

    script = fs.session.create_script(script_source, name=name)
    script.on("message", on_message)
    script.load()
    fs.scripts[name] = {"script": script, "messages": messages}

    return {
        "ok": True,
        "result": {
            "frida_id": frida_id,
            "script_name": name,
            "loaded": True,
        },
    }


def hook_function(
    app: "PwnMcpApp",
    session_id: str,
    frida_id: str,
    function_name: str | None = None,
    address: str | None = None,
    on_enter: str | None = None,
    on_leave: str | None = None,
) -> dict[str, Any]:
    """Hook a function by name or address using Interceptor.attach."""
    _require_frida()

    if not function_name and not address:
        raise PwnMcpError("invalid_request", "hook_target_required", "Provide function_name or address.")

    if function_name:
        target = f"Module.findExportByName(null, '{function_name}')"
    else:
        target = f"ptr('{address}')"

    enter_body = on_enter or "console.log('[+] ' + this.returnAddress + ' called ' + this.context.pc);"
    leave_body = on_leave or "console.log('[-] retval: ' + retval);"

    script_source = f"""
Interceptor.attach({target}, {{
    onEnter: function(args) {{
        {enter_body}
    }},
    onLeave: function(retval) {{
        {leave_body}
    }}
}});
"""
    return inject_script(app, session_id, frida_id, script_source, script_name=f"hook_{function_name or 'addr'}")


def trace_calls(
    app: "PwnMcpApp",
    session_id: str,
    frida_id: str,
    module_name: str | None = None,
    function_pattern: str = "*",
) -> dict[str, Any]:
    """Trace function calls in a module using Frida Stalker or Interceptor."""
    _require_frida()

    if module_name:
        script_source = f"""
var mod = Process.findModuleByName('{module_name}');
if (mod) {{
    mod.enumerateExports().forEach(function(exp) {{
        if (exp.type === 'function' && exp.name.match(/{function_pattern}/)) {{
            try {{
                Interceptor.attach(exp.address, {{
                    onEnter: function(args) {{
                        send({{type: 'call', name: exp.name, address: exp.address.toString()}});
                    }}
                }});
            }} catch(e) {{}}
        }}
    }});
    send({{type: 'status', message: 'Tracing ' + mod.name}});
}} else {{
    send({{type: 'error', message: 'Module not found: {module_name}'}});
}}
"""
    else:
        script_source = """
Process.enumerateModules().forEach(function(mod) {
    if (mod.path.indexOf('/lib') === -1) {
        mod.enumerateExports().forEach(function(exp) {
            if (exp.type === 'function') {
                try {
                    Interceptor.attach(exp.address, {
                        onEnter: function(args) {
                            send({type: 'call', name: exp.name, module: mod.name, address: exp.address.toString()});
                        }
                    });
                } catch(e) {}
            }
        });
    }
});
send({type: 'status', message: 'Tracing active'});
"""
    return inject_script(app, session_id, frida_id, script_source, script_name="trace_calls")


def get_exports(
    app: "PwnMcpApp",
    session_id: str,
    frida_id: str,
    module_name: str | None = None,
) -> dict[str, Any]:
    """List exports of a loaded module."""
    _require_frida()
    session = app.sessions.get(session_id)
    fs = session.frida_sessions.get(frida_id)
    if fs is None:
        raise PwnMcpError("not_found", "frida_session_not_found", f"Frida session '{frida_id}' not found.")

    if module_name:
        script_src = f"""
var mod = Process.findModuleByName('{module_name}');
if (mod) {{
    send(mod.enumerateExports().slice(0, 200));
}} else {{
    send([]);
}}
"""
    else:
        script_src = """
send(Process.enumerateModules().map(function(m) {
    return {name: m.name, base: m.base.toString(), size: m.size, path: m.path};
}));
"""

    messages: list = []

    def on_msg(msg, data):
        if msg.get("type") == "send":
            messages.append(msg.get("payload"))

    script = fs.session.create_script(script_src)
    script.on("message", on_msg)
    script.load()

    import time
    time.sleep(0.3)
    script.unload()

    return {"ok": True, "result": {"exports": messages[0] if messages else []}}


def get_memory_ranges(
    app: "PwnMcpApp",
    session_id: str,
    frida_id: str,
    protection: str = "r--",
) -> dict[str, Any]:
    """List memory ranges with given protection."""
    _require_frida()
    session = app.sessions.get(session_id)
    fs = session.frida_sessions.get(frida_id)
    if fs is None:
        raise PwnMcpError("not_found", "frida_session_not_found", f"Frida session '{frida_id}' not found.")

    messages: list = []

    def on_msg(msg, data):
        if msg.get("type") == "send":
            messages.append(msg.get("payload"))

    script_src = f"""
send(Process.enumerateRanges('{protection}').map(function(r) {{
    return {{base: r.base.toString(), size: r.size, protection: r.protection, file: r.file ? r.file.path : null}};
}}).slice(0, 200));
"""
    script = fs.session.create_script(script_src)
    script.on("message", on_msg)
    script.load()

    import time
    time.sleep(0.3)
    script.unload()

    return {"ok": True, "result": {"ranges": messages[0] if messages else []}}


def dump_memory(
    app: "PwnMcpApp",
    session_id: str,
    frida_id: str,
    address: str,
    length: int = 256,
) -> dict[str, Any]:
    """Dump raw bytes from a memory address."""
    _require_frida()
    session = app.sessions.get(session_id)
    fs = session.frida_sessions.get(frida_id)
    if fs is None:
        raise PwnMcpError("not_found", "frida_session_not_found", f"Frida session '{frida_id}' not found.")

    if length > 65536:
        raise PwnMcpError("invalid_request", "dump_too_large", "Max dump size is 64KB.")

    messages: list = []

    def on_msg(msg, data):
        if msg.get("type") == "send":
            messages.append(data)

    script_src = f"""
var buf = Memory.readByteArray(ptr('{address}'), {length});
send(null, buf);
"""
    script = fs.session.create_script(script_src)
    script.on("message", on_msg)
    script.load()

    import time
    time.sleep(0.3)
    script.unload()

    raw = messages[0] if messages else b""
    return {
        "ok": True,
        "result": {
            "address": address,
            "length": len(raw),
            "hex": raw.hex() if raw else "",
        },
    }


def stop_frida_session(
    app: "PwnMcpApp",
    session_id: str,
    frida_id: str,
) -> dict[str, Any]:
    """Stop a Frida session: detach, kill spawned process, clean up."""
    session = app.sessions.get(session_id)
    if session is None:
        raise PwnMcpError("not_found", "session_not_found", f"Session '{session_id}' not found.")

    fs = session.frida_sessions.get(frida_id)
    if fs is None:
        raise PwnMcpError("not_found", "frida_session_not_found", f"Frida session '{frida_id}' not found.")

    errors = []

    # Unload all scripts
    for name, script in list(fs.scripts.items()):
        try:
            script.unload()
        except Exception as exc:
            errors.append(f"Script unload '{name}': {exc}")

    # Detach session
    try:
        fs.session.detach()
    except Exception as exc:
        errors.append(f"Detach: {exc}")

    # Kill spawned process
    try:
        if fs.device is not None and fs.pid is not None:
            fs.device.kill(fs.pid)
    except Exception as exc:
        errors.append(f"Kill: {exc}")

    with session._lock:
        del session.frida_sessions[frida_id]

    return {
        "ok": True,
        "result": {
            "frida_id": frida_id,
            "stopped": True,
            "errors": errors if errors else None,
        },
    }


# ── Registration ──────────────────────────────────────────────────────────────

def register(app: "PwnMcpApp") -> dict[str, dict]:
    def _h(fn):
        def wrapper(**kwargs):
            return fn(app, **kwargs)
        return wrapper

    _sid = {"type": "string", "description": "Session ID."}
    _fid = {"type": "string", "description": "Frida session ID from start_frida_session."}

    return {
        "start_frida_session": {
            "handler": _h(start_frida_session),
            "schema": Tool(
                name="start_frida_session",
                description="Spawn a binary under Frida for dynamic instrumentation.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "binary_path": {"type": "string"},
                        "args": {"type": "array", "items": {"type": "string"}, "default": []},
                    },
                    "required": ["session_id", "binary_path"],
                    "additionalProperties": False,
                },
            ),
        },
        "inject_script": {
            "handler": _h(inject_script),
            "schema": Tool(
                name="inject_script",
                description="Inject a Frida JavaScript script into the target process.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid, "frida_id": _fid,
                        "script_source": {"type": "string", "description": "Frida JS source code."},
                        "script_name": {"type": "string"},
                    },
                    "required": ["session_id", "frida_id", "script_source"],
                    "additionalProperties": False,
                },
            ),
        },
        "hook_function": {
            "handler": _h(hook_function),
            "schema": Tool(
                name="hook_function",
                description="Hook a function by name or address using Frida Interceptor.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid, "frida_id": _fid,
                        "function_name": {"type": "string"},
                        "address": {"type": "string", "description": "Hex address (0x...)."},
                        "on_enter": {"type": "string", "description": "JS body for onEnter handler."},
                        "on_leave": {"type": "string", "description": "JS body for onLeave handler."},
                    },
                    "required": ["session_id", "frida_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "trace_calls": {
            "handler": _h(trace_calls),
            "schema": Tool(
                name="trace_calls",
                description="Trace function calls in a module or the main binary using Frida Interceptor.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid, "frida_id": _fid,
                        "module_name": {"type": "string"},
                        "function_pattern": {"type": "string", "default": "*"},
                    },
                    "required": ["session_id", "frida_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "get_exports": {
            "handler": _h(get_exports),
            "schema": Tool(
                name="get_exports",
                description="List exports of a loaded module, or list all loaded modules.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid, "frida_id": _fid,
                        "module_name": {"type": "string", "description": "Module name. Omit to list all modules."},
                    },
                    "required": ["session_id", "frida_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "get_memory_ranges": {
            "handler": _h(get_memory_ranges),
            "schema": Tool(
                name="get_memory_ranges",
                description="List memory ranges with specific protection (e.g. 'rwx', 'r-x').",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid, "frida_id": _fid,
                        "protection": {"type": "string", "default": "r--", "description": "Protection filter (e.g. 'rwx')."},
                    },
                    "required": ["session_id", "frida_id"],
                    "additionalProperties": False,
                },
            ),
        },
        "dump_memory": {
            "handler": _h(dump_memory),
            "schema": Tool(
                name="dump_memory",
                description="Dump raw bytes from a process memory address via Frida.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid, "frida_id": _fid,
                        "address": {"type": "string", "description": "Hex address (0x...)."},
                        "length": {"type": "integer", "default": 256, "description": "Bytes to read (max 65536)."},
                    },
                    "required": ["session_id", "frida_id", "address"],
                    "additionalProperties": False,
                },
            ),
        },
        "stop_frida_session": {
            "handler": _h(stop_frida_session),
            "schema": Tool(
                name="stop_frida_session",
                description="Stop a Frida session: detach from process, unload scripts, and clean up.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": _sid,
                        "frida_id": _fid,
                    },
                    "required": ["session_id", "frida_id"],
                    "additionalProperties": False,
                },
            ),
        },
    }
