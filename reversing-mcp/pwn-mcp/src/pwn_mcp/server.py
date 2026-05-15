from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .app import PwnMcpApp
from .config import SERVER_NAME, SERVER_VERSION
from .errors import PwnMcpError

LOGGER = logging.getLogger("pwn_mcp")


def _build_server(app: PwnMcpApp) -> Server:
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return app.tool_definitions()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if "kwargs" in arguments and len(arguments) == 1:
            raw_kwargs = arguments["kwargs"]
            if isinstance(raw_kwargs, str):
                try:
                    arguments = json.loads(raw_kwargs)
                except json.JSONDecodeError:
                    pass

        try:
            result = app.dispatch(name, arguments)
        except PwnMcpError as exc:
            result = {"ok": False, "error": exc.to_dict()}
        except Exception as exc:
            result = {
                "ok": False,
                "error": {
                    "category": "backend_failure",
                    "code": "unexpected_error",
                    "message": str(exc),
                    "details": {},
                    "retryable": False,
                },
            }
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    return server


def build_network_app(app: PwnMcpApp, transport: str = "both"):
    """Build a raw ASGI app that serves MCP over SSE and streamable HTTP.

    Routes:
      GET  /sse        → SSE transport (event stream)
      POST /messages/  → SSE message posting endpoint
      POST /mcp        → Streamable HTTP (JSON-RPC over HTTP)

    For backward compatibility with the original pwn-mcp container, POST and
    DELETE to /sse are also accepted as streamable HTTP when SSE is enabled.
    """
    from mcp.server.sse import SseServerTransport
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    normalized = "http" if transport == "streamable-http" else transport
    if normalized not in {"sse", "http", "both"}:
        raise ValueError(f"Unsupported network transport '{transport}'.")

    sse_transport = SseServerTransport("/messages/")
    http_session_manager = StreamableHTTPSessionManager(
        app=_build_server(app),
        json_response=False,
        stateless=True,
    )

    _manager_cm = None
    _manager_task = None

    async def _run_manager():
        nonlocal _manager_cm
        _manager_cm = http_session_manager.run()
        await _manager_cm.__aenter__()

    async def asgi_app(scope, receive, send):
        nonlocal _manager_task

        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await _run_manager()
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    if _manager_cm:
                        await _manager_cm.__aexit__(None, None, None)
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return

        if scope["type"] != "http":
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET")
        route_path = path.rstrip("/") or "/"

        # GET /sse → SSE event stream
        if normalized in {"sse", "both"} and route_path == "/sse" and method == "GET":
            server = _build_server(app)
            async with sse_transport.connect_sse(scope, receive, send) as (
                read_stream,
                write_stream,
            ):
                await server.run(
                    read_stream,
                    write_stream,
                    server.create_initialization_options(),
                )
            return

        # POST/DELETE /mcp → Streamable HTTP (JSON-RPC)
        if normalized in {"http", "both"} and route_path == "/mcp" and method in {"POST", "DELETE", "GET"}:
            await http_session_manager.handle_request(scope, receive, send)
            return

        # POST /messages/ → SSE message posting
        if normalized in {"sse", "both"} and "/messages/" in path and method == "POST":
            await sse_transport.handle_post_message(scope, receive, send)
            return

        # Backward-compatible streamable HTTP on /sse.
        if normalized in {"sse", "both"} and route_path == "/sse" and method in {"POST", "DELETE"}:
            await http_session_manager.handle_request(scope, receive, send)
            return

        if route_path == "/" and method == "GET":
            endpoints = {}
            if normalized in {"http", "both"}:
                endpoints["streamable_http"] = "/mcp"
            if normalized in {"sse", "both"}:
                endpoints["sse"] = "/sse"
            body = json.dumps({
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
                "endpoints": endpoints,
                "auth": "none",
            }).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [[b"content-type", b"application/json"], [b"content-length", str(len(body)).encode("ascii")]],
            })
            await send({"type": "http.response.body", "body": body})
            return

        # 404 for everything else
        await send({
            "type": "http.response.start",
            "status": 404,
            "headers": [[b"content-type", b"text/plain"]],
        })
        await send({
            "type": "http.response.body",
            "body": b"Not Found",
        })

    return asgi_app


def build_sse_app(app: PwnMcpApp):
    """Backward-compatible wrapper for the original pwn-mcp SSE app builder."""
    return build_network_app(app, "both")


def configure_logging(level_str: str) -> int:
    level = getattr(logging, level_str.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )
    return level


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{SERVER_NAME} v{SERVER_VERSION}")
    parser.add_argument("--transport", choices=["stdio", "sse", "http", "streamable-http", "both"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=6768)
    parser.add_argument("--workspace", default=None, help="Workspace root (overrides PWN_MCP_WORKSPACE_ROOT)")
    parser.add_argument("--log-level", default=os.environ.get("PWN_MCP_LOG_LEVEL", "INFO"))
    args = parser.parse_args()

    pwn_app = PwnMcpApp()
    if args.workspace:
        from pathlib import Path
        pwn_app.security._workspace_root = Path(args.workspace)

    log_level = configure_logging(args.log_level)
    LOGGER.info("server_start transport=%s host=%s port=%s", args.transport, args.host, args.port)

    if args.transport == "stdio":
        server = _build_server(pwn_app)

        async def _run():
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())

        asyncio.run(_run())
        return

    import uvicorn

    asgi_app = build_network_app(pwn_app, args.transport)
    uvicorn_level = "debug" if log_level <= logging.DEBUG else logging.getLevelName(log_level).lower()
    uvicorn.run(asgi_app, host=args.host, port=args.port, log_level=uvicorn_level, lifespan="on")


if __name__ == "__main__":
    main()
