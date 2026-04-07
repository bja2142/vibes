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


def build_sse_app(app: PwnMcpApp):
    """Build a raw ASGI app that serves MCP over SSE and streamable HTTP.

    Routes:
      GET  /sse        → SSE transport (event stream)
      POST /sse        → Streamable HTTP (JSON-RPC over HTTP)
      POST /messages/  → SSE message posting endpoint
    """
    from mcp.server.sse import SseServerTransport
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

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

        # GET /sse → SSE event stream
        if path.rstrip("/") == "/sse" and method == "GET":
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

        # POST /sse → Streamable HTTP (JSON-RPC)
        if path.rstrip("/") == "/sse" and method == "POST":
            await http_session_manager.handle_request(scope, receive, send)
            return

        # POST /messages/ → SSE message posting
        if "/messages/" in path and method == "POST":
            await sse_transport.handle_post_message(scope, receive, send)
            return

        # DELETE /sse → Streamable HTTP session cleanup
        if path.rstrip("/") == "/sse" and method == "DELETE":
            await http_session_manager.handle_request(scope, receive, send)
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


def configure_logging(level_str: str) -> int:
    level = getattr(logging, level_str.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )
    return level


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{SERVER_NAME} v{SERVER_VERSION}")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
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

    asgi_app = build_sse_app(pwn_app)
    uvicorn_level = "debug" if log_level <= logging.DEBUG else logging.getLevelName(log_level).lower()
    uvicorn.run(asgi_app, host=args.host, port=args.port, log_level=uvicorn_level, lifespan="on")


if __name__ == "__main__":
    main()
