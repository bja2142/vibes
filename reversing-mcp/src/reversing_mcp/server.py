from __future__ import annotations

import argparse
import inspect
import logging
import os
import time
from contextlib import AsyncExitStack, asynccontextmanager
from functools import wraps
from typing import Annotated, Any

from pydantic import Field
from mcp.server.fastmcp import FastMCP

from .app import ReversingMCPApp
from .errors import StructuredToolError
from .result import failure
from .transport import REQUEST_RATE_LIMITER, RequestContext, load_http_transport_config, request_context

LOGGER = logging.getLogger("reversing_mcp")
APP = ReversingMCPApp()
mcp = FastMCP("reversing-mcp")


def configure_logging(level_name: str | None = None) -> int:
    level = getattr(logging, (level_name or os.environ.get("REVERSING_MCP_LOG_LEVEL", "INFO")).strip().upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    logging.getLogger("mcp").setLevel(level)
    LOGGER.setLevel(level)
    return level


def unwrap_mcp_tool_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[tuple[Any, ...], dict[str, Any]]:
    if args:
        return args, kwargs
    if set(kwargs.keys()) != {"args", "kwargs"}:
        return args, kwargs
    raw_args = kwargs.get("args", [])
    raw_kwargs = kwargs.get("kwargs", {})
    if isinstance(raw_args, str):
        raw_args = __import__("json").loads(raw_args)
    if isinstance(raw_kwargs, str):
        raw_kwargs = __import__("json").loads(raw_kwargs)
    return tuple(raw_args), raw_kwargs


def expose(fn_name: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            call_args, call_kwargs = unwrap_mcp_tool_call(args, kwargs)
            LOGGER.info("tool_request tool=%s", fn_name)
            return func(*call_args, **call_kwargs)

        wrapper.__name__ = fn_name
        wrapper.__doc__ = func.__doc__
        wrapper.__signature__ = inspect.signature(func)
        return mcp.tool(name=fn_name)(wrapper)

    return decorator


def build_network_app(transport: str):
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse

    normalized = "http" if transport == "streamable-http" else transport
    if normalized not in {"sse", "http", "both"}:
        raise ValueError(f"Unsupported network transport '{transport}'.")

    routes = []
    middleware = []
    lifespan_contexts = []

    if normalized in {"sse", "both"}:
        sse_app = mcp.sse_app("/")
        routes.extend(sse_app.routes)
        middleware.extend(sse_app.user_middleware)
        lifespan_contexts.append(sse_app.router.lifespan_context)

    if normalized in {"http", "both"}:
        http_app = mcp.streamable_http_app()
        routes.extend(http_app.routes)
        middleware.extend(http_app.user_middleware)
        lifespan_contexts.append(http_app.router.lifespan_context)

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as stack:
            for context in lifespan_contexts:
                await stack.enter_async_context(context(app))
            yield

    class RequestLoggingMiddleware:
        def __init__(self, inner_app):
            self.inner_app = inner_app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.inner_app(scope, receive, send)
                return
            started_at = time.perf_counter()
            status_code = 500

            async def send_wrapper(message):
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = message["status"]
                await send(message)

            try:
                await self.inner_app(scope, receive, send_wrapper)
            finally:
                duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
                LOGGER.info(
                    "http_request method=%s path=%s status=%s duration_ms=%s",
                    scope["method"],
                    scope["path"],
                    status_code,
                    duration_ms,
                )

    class TransportAccessMiddleware:
        def __init__(self, inner_app):
            self.inner_app = inner_app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.inner_app(scope, receive, send)
                return
            headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in scope.get("headers", [])}
            config = load_http_transport_config()
            try:
                context = _build_request_context(headers, config=config)
                if normalized in {"http", "both"} and config.auth_enabled:
                    REQUEST_RATE_LIMITER.check(
                        f"{context.tenant_id}:{context.agent_id}",
                        limit=config.requests_per_minute_per_agent,
                    )
            except StructuredToolError as exc:
                response = JSONResponse(
                    failure(
                        "http_transport",
                        {"method": scope["method"], "path": scope["path"]},
                        exc,
                    ),
                    status_code=401 if exc.code in {"http_auth_required", "http_auth_invalid"} else 429 if exc.code == "http_rate_limit_exceeded" else 403,
                )
                await response(scope, receive, send)
                return
            with request_context(context):
                await self.inner_app(scope, receive, send)

    app = Starlette(routes=routes, middleware=middleware, lifespan=lifespan)
    app.add_middleware(TransportAccessMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    return app


def _build_request_context(headers: dict[str, str], *, config) -> RequestContext:
    if not config.auth_enabled:
        agent_id = headers.get(config.agent_header, "anonymous").strip() or "anonymous"
        return RequestContext(transport="http", authenticated=False, tenant_id="anonymous", agent_id=agent_id)
    authorization = headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise StructuredToolError("authorization_failed", "http_auth_required", "HTTP transport requires a Bearer token.")
    token = authorization.split(None, 1)[1].strip()
    tenant_id = config.tokens.get(token)
    if tenant_id is None:
        raise StructuredToolError("authorization_failed", "http_auth_invalid", "HTTP Bearer token is invalid.")
    agent_id = headers.get(config.agent_header, tenant_id).strip() or tenant_id
    return RequestContext(transport="http", authenticated=True, tenant_id=tenant_id, agent_id=agent_id)


@expose("describe_tools")
def _describe_tools() -> dict[str, Any]:
    """List available foundation tools, prerequisites, and parameter summaries."""
    return APP.describe_tools()


@expose("get_capabilities")
def _get_capabilities() -> dict[str, Any]:
    """Report server, transport, ID-model, and backend capabilities for this container."""
    return APP.get_capabilities()


@expose("get_runtime_policies")
def _get_runtime_policies() -> dict[str, Any]:
    """Report workspace hardening settings, parser isolation policy, resource limits, and version information."""
    return APP.get_runtime_policies()


@expose("run_parser_probe")
def _run_parser_probe(path: str, simulate: str | None = None) -> dict[str, Any]:
    """Run a file probe in the isolated parser subprocess to validate crash containment and path policy."""
    return APP.run_parser_probe(path, simulate)


@expose("create_session")
def _create_session(name: str, description: str | None = None, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a persisted single-agent analysis session rooted in the shared workspace volume."""
    return APP.create_session(name, description, settings)


@expose("load_session")
def _load_session(session_id: str | None = None, name: str | None = None) -> dict[str, Any]:
    """Load one persisted analysis session by session_id or unique session name."""
    return APP.load_session(session_id, name)


@expose("list_sessions")
def _list_sessions(cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
    """List persisted sessions with deterministic ordering and cursor pagination."""
    return APP.list_sessions(cursor, limit)


@expose("destroy_session")
def _destroy_session(session_id: str | None = None, name: str | None = None) -> dict[str, Any]:
    """Delete persisted analysis state for one session without deleting workspace files."""
    return APP.destroy_session(session_id, name)


@expose("update_session_settings")
def _update_session_settings(session_id: str, settings_patch: dict[str, Any]) -> dict[str, Any]:
    """Persist analysis-setting changes for an existing session."""
    return APP.update_session_settings(session_id, settings_patch)


@expose("add_artifact")
def _add_artifact(session_id: str, path: str, display_name: str | None = None) -> dict[str, Any]:
    """Attach one workspace file to a session and assign a stable artifact_id."""
    return APP.add_artifact(session_id, path, display_name)


@expose("triage_artifact")
def _triage_artifact(
    session_id: str,
    artifact_id: str,
    hints: dict[str, Any] | None = None,
    string_preview_limit: int = 20,
) -> dict[str, Any]:
    """Identify a loaded artifact, summarize its headers and layout, preview strings, and report cheap static metadata."""
    return APP.triage_artifact(session_id, artifact_id, hints, string_preview_limit)


@expose("list_artifact_strings")
def _list_artifact_strings(
    session_id: str,
    artifact_id: str,
    cursor: int = 0,
    limit: int = 50,
    min_length: int = 4,
    encoding: str | None = None,
    query: str | None = None,
    hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """List extracted strings for an artifact with pagination and optional filters."""
    return APP.list_artifact_strings(session_id, artifact_id, cursor, limit, min_length, encoding, query, hints)


@expose("translate_artifact_address")
def _translate_artifact_address(
    session_id: str,
    artifact_id: str,
    input_kind: str,
    value: int | str,
    hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Translate one file offset, virtual address, or RVA within a loaded artifact."""
    return APP.translate_artifact_address(session_id, artifact_id, input_kind, value, hints)


@expose("list_artifact_children")
def _list_artifact_children(
    session_id: str,
    artifact_id: str,
    cursor: int = 0,
    limit: int = 50,
    hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """List child artifacts for container formats such as archives or fat binaries."""
    return APP.list_artifact_children(session_id, artifact_id, cursor, limit, hints)


@expose("lookup_external_enrichment")
def _lookup_external_enrichment(
    session_id: str,
    artifact_id: str,
    providers: list[str] | None = None,
    opt_in: bool = False,
) -> dict[str, Any]:
    """Query the opt-in external enrichment hook state for an artifact without requiring it for normal triage."""
    return APP.lookup_external_enrichment(session_id, artifact_id, providers, opt_in)


@expose("scan_with_yara")
def _scan_with_yara(
    session_id: str,
    artifact_id: str,
    rules_text: str | None = None,
    include_related: bool = False,
) -> dict[str, Any]:
    """Scan one artifact and optionally derived children with YARA or the built-in heuristic fallback."""
    return APP.scan_with_yara(session_id, artifact_id, rules_text, include_related)


@expose("fingerprint_compiler_toolchain")
def _fingerprint_compiler_toolchain(session_id: str, artifact_id: str) -> dict[str, Any]:
    """Return structured compiler and toolchain fingerprints for one artifact."""
    return APP.fingerprint_compiler_toolchain(session_id, artifact_id)


@expose("detect_packer")
def _detect_packer(session_id: str, artifact_id: str) -> dict[str, Any]:
    """Return signature-based and heuristic packer detections for one artifact."""
    return APP.detect_packer(session_id, artifact_id)


@expose("calculate_entropy")
def _calculate_entropy(session_id: str, artifact_id: str) -> dict[str, Any]:
    """Calculate entropy for the whole file and each parsed section of an artifact."""
    return APP.calculate_entropy(session_id, artifact_id)


@expose("deobfuscate_strings")
def _deobfuscate_strings(session_id: str, artifact_id: str, limit: int = 50) -> dict[str, Any]:
    """Return bounded deobfuscated string candidates, preferring FLARE FLOSS when supported."""
    return APP.deobfuscate_strings(session_id, artifact_id, limit)


@expose("extract_resources")
def _extract_resources(
    session_id: str,
    artifact_id: str,
    output_subdir: str | None = None,
    attach_to_session: bool = False,
    target_session_id: str | None = None,
    analyze_extracted: bool = False,
) -> dict[str, Any]:
    """Extract PE resources or archive-like container members into the workspace."""
    return APP.extract_resources(session_id, artifact_id, output_subdir, attach_to_session, target_session_id, analyze_extracted)


@expose("carve_embedded_artifacts")
def _carve_embedded_artifacts(
    session_id: str,
    artifact_id: str,
    output_subdir: str | None = None,
    attach_to_session: bool = False,
    target_session_id: str | None = None,
    analyze_extracted: bool = False,
    recurse: bool = False,
) -> dict[str, Any]:
    """Carve appended overlays and embedded child artifacts into the workspace."""
    return APP.carve_embedded_artifacts(session_id, artifact_id, output_subdir, attach_to_session, target_session_id, analyze_extracted, recurse)


@expose("get_artifact_relationships")
def _get_artifact_relationships(session_id: str, artifact_id: str, direction: str = "both") -> dict[str, Any]:
    """Return parent and child artifact relationships created through extraction and carving workflows."""
    return APP.get_artifact_relationships(session_id, artifact_id, direction)


@expose("start_artifact_analysis")
def _start_artifact_analysis(session_id: str, artifact_id: str, hints: dict[str, Any] | None = None) -> dict[str, Any]:
    """Start asynchronous headless program analysis for one loaded artifact and persist the recovered cache."""
    return APP.start_artifact_analysis(session_id, artifact_id, hints)


@expose("get_analysis_synopsis")
def _get_analysis_synopsis(session_id: str, artifact_id: str) -> dict[str, Any]:
    """Return a compact persisted synopsis of the current analysis state for one artifact."""
    return APP.get_analysis_synopsis(session_id, artifact_id)


@expose("list_artifact_symbols")
def _list_artifact_symbols(
    session_id: str,
    artifact_id: str,
    cursor: int = 0,
    limit: int = 50,
    query: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """List recovered imports, exports, thunks, and unresolved symbols with demangled-name filtering and pagination."""
    return APP.list_artifact_symbols(session_id, artifact_id, cursor, limit, query, kind)


@expose("list_artifact_functions")
def _list_artifact_functions(
    session_id: str,
    artifact_id: str,
    cursor: int = 0,
    limit: int = 50,
    query: str | None = None,
) -> dict[str, Any]:
    """List recovered functions with addresses, signatures, calling conventions, stack sizes, and analyzer confidence."""
    return APP.list_artifact_functions(session_id, artifact_id, cursor, limit, query)


@expose("get_artifact_instruction_mode")
def _get_artifact_instruction_mode(session_id: str, artifact_id: str) -> dict[str, Any]:
    """Report the supported and active instruction-set mode for the analyzed artifact."""
    return APP.get_artifact_instruction_mode(session_id, artifact_id)


@expose("set_artifact_instruction_mode")
def _set_artifact_instruction_mode(session_id: str, artifact_id: str, mode: str) -> dict[str, Any]:
    """Override the active instruction-set mode when the analyzed architecture supports multiple modes."""
    return APP.set_artifact_instruction_mode(session_id, artifact_id, mode)


@expose("disassemble_function")
def _disassemble_function(
    session_id: str,
    artifact_id: str,
    function_id: str | None = None,
    name: str | None = None,
    address: int | str | None = None,
    cursor: int = 0,
    limit: int = 200,
    instruction_mode_override: str | None = None,
) -> dict[str, Any]:
    """Retrieve structured disassembly for one recovered function with bytes, addresses, and operand hints."""
    return APP.disassemble_function(session_id, artifact_id, function_id, name, address, cursor, limit, instruction_mode_override)


@expose("disassemble_range")
def _disassemble_range(
    session_id: str,
    artifact_id: str,
    input_kind: str,
    start_value: int | str,
    size: int,
    cursor: int = 0,
    limit: int = 200,
    instruction_mode_override: str | None = None,
) -> dict[str, Any]:
    """Retrieve structured disassembly for an address or file-backed byte range within an analyzed artifact."""
    return APP.disassemble_range(session_id, artifact_id, input_kind, start_value, size, cursor, limit, instruction_mode_override)


@expose("decompile_function")
def _decompile_function(
    session_id: str,
    artifact_id: str,
    function_id: str | None = None,
    name: str | None = None,
    address: int | str | None = None,
    char_limit: int | None = None,
    line_limit: int = 200,
) -> dict[str, Any]:
    """Retrieve best-effort pseudo-C for one recovered function with explicit failure and truncation metadata."""
    return APP.decompile_function(session_id, artifact_id, function_id, name, address, char_limit, line_limit)


@expose("read_artifact_bytes")
def _read_artifact_bytes(
    session_id: str,
    artifact_id: str,
    input_kind: str,
    value: int | str,
    length: int,
    hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inspect raw bytes by file offset or virtual address with hex and ASCII views."""
    return APP.read_artifact_bytes(session_id, artifact_id, input_kind, value, length, hints)


@expose("list_artifact_xrefs")
def _list_artifact_xrefs(
    session_id: str,
    artifact_id: str,
    function_id: str | None = None,
    string_id: str | None = None,
    address: int | str | None = None,
    cursor: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """List callers and other cross-references targeting a recovered function, string, or address."""
    return APP.list_artifact_xrefs(session_id, artifact_id, function_id, string_id, address, cursor, limit)


@expose("search_artifact")
def _search_artifact(
    session_id: str,
    artifact_id: str,
    kind: str,
    query: str | None = None,
    start_address: int | str | None = None,
    end_address: int | str | None = None,
    cursor: int = 0,
    limit: int = 50,
    case_sensitive: bool = False,
) -> dict[str, Any]:
    """Search an analyzed artifact by names, strings, immediates, opcodes, byte patterns, or address ranges."""
    return APP.search_artifact(session_id, artifact_id, kind, query, start_address, end_address, cursor, limit, case_sensitive)


@expose("get_artifact_linkage")
def _get_artifact_linkage(session_id: str, artifact_id: str) -> dict[str, Any]:
    """Return relocations and linkage metadata such as PLT, GOT, IAT, thunks, and unresolved bindings."""
    return APP.get_artifact_linkage(session_id, artifact_id)


@expose("get_artifact_debug_info")
def _get_artifact_debug_info(session_id: str, artifact_id: str) -> dict[str, Any]:
    """Return parsed DWARF, PDB-derived, or embedded source-reference metadata when available."""
    return APP.get_artifact_debug_info(session_id, artifact_id)


@expose("detect_crypto_constants")
def _detect_crypto_constants(session_id: str, artifact_id: str) -> dict[str, Any]:
    """Return recovered crypto and checksum constants with per-hit evidence and confidence."""
    return APP.detect_crypto_constants(session_id, artifact_id)


@expose("recognize_library_code")
def _recognize_library_code(session_id: str, artifact_id: str) -> dict[str, Any]:
    """Return recognized runtime and library code using imports, symbols, and function metadata."""
    return APP.recognize_library_code(session_id, artifact_id)


@expose("get_call_graph")
def _get_call_graph(
    session_id: str,
    artifact_id: str,
    function_id: str | None = None,
    name: str | None = None,
    address: int | str | None = None,
    direction: str = "both",
    depth: int = 1,
    limit_nodes: int = 100,
    limit_edges: int = 200,
) -> dict[str, Any]:
    """Return bounded incoming and outgoing call-graph edges for one recovered function."""
    return APP.get_call_graph(session_id, artifact_id, function_id, name, address, direction, depth, limit_nodes, limit_edges)


@expose("get_control_flow_graph")
def _get_control_flow_graph(
    session_id: str,
    artifact_id: str,
    function_id: str | None = None,
    name: str | None = None,
    address: int | str | None = None,
) -> dict[str, Any]:
    """Return the recovered control-flow graph for one function."""
    return APP.get_control_flow_graph(session_id, artifact_id, function_id, name, address)


@expose("get_function_variables")
def _get_function_variables(
    session_id: str,
    artifact_id: str,
    function_id: str | None = None,
    name: str | None = None,
    address: int | str | None = None,
) -> dict[str, Any]:
    """Return recovered arguments, locals, globals, and register parameters for one function."""
    return APP.get_function_variables(session_id, artifact_id, function_id, name, address)


@expose("get_stack_frame")
def _get_stack_frame(
    session_id: str,
    artifact_id: str,
    function_id: str | None = None,
    name: str | None = None,
    address: int | str | None = None,
) -> dict[str, Any]:
    """Return the recovered stack-frame layout for one function."""
    return APP.get_stack_frame(session_id, artifact_id, function_id, name, address)


@expose("get_constant_propagation")
def _get_constant_propagation(
    session_id: str,
    artifact_id: str,
    function_id: str | None = None,
    name: str | None = None,
    address: int | str | None = None,
) -> dict[str, Any]:
    """Return recovered immediates and bounded call-site argument propagation for one function."""
    return APP.get_constant_propagation(session_id, artifact_id, function_id, name, address)


@expose("get_type_information")
def _get_type_information(session_id: str, artifact_id: str) -> dict[str, Any]:
    """Return recovered type summaries, function signatures, and typed-memory views for an artifact."""
    return APP.get_type_information(session_id, artifact_id)


@expose("recover_types")
def _recover_types(session_id: str, artifact_id: str) -> dict[str, Any]:
    """Return heuristic RTTI, vtable, class-hierarchy, and typed-memory recoveries."""
    return APP.recover_types(session_id, artifact_id)


@expose("inspect_data_segments")
def _inspect_data_segments(session_id: str, artifact_id: str) -> dict[str, Any]:
    """Inspect non-executable data regions for strings, pointer tables, arrays, and typed views."""
    return APP.inspect_data_segments(session_id, artifact_id)


@expose("get_indirect_flows")
def _get_indirect_flows(
    session_id: str,
    artifact_id: str,
    function_id: str | None = None,
    name: str | None = None,
    address: int | str | None = None,
) -> dict[str, Any]:
    """Return recovered indirect calls, branches, and unresolved control-flow transfers for one function."""
    return APP.get_indirect_flows(session_id, artifact_id, function_id, name, address)


@expose("get_exception_metadata")
def _get_exception_metadata(session_id: str, artifact_id: str) -> dict[str, Any]:
    """Return recovered exception, unwind, and personality metadata for an artifact."""
    return APP.get_exception_metadata(session_id, artifact_id)


@expose("get_calling_convention")
def _get_calling_convention(
    session_id: str,
    artifact_id: str,
    function_id: str | None = None,
    name: str | None = None,
    address: int | str | None = None,
) -> dict[str, Any]:
    """Return the detected or inferred calling convention for one function."""
    return APP.get_calling_convention(session_id, artifact_id, function_id, name, address)


@expose("get_intermediate_representation")
def _get_intermediate_representation(
    session_id: str,
    artifact_id: str,
    function_id: str | None = None,
    name: str | None = None,
    address: int | str | None = None,
    limit_blocks: int = 8,
    limit_statements: int = 25,
) -> dict[str, Any]:
    """Return a bounded backend IR view for one function."""
    return APP.get_intermediate_representation(session_id, artifact_id, function_id, name, address, limit_blocks, limit_statements)


@expose("get_runtime_metadata")
def _get_runtime_metadata(session_id: str, artifact_id: str) -> dict[str, Any]:
    """Return recovered language-runtime metadata for an artifact."""
    return APP.get_runtime_metadata(session_id, artifact_id)


@expose("slice_data_flow")
def _slice_data_flow(
    session_id: str,
    artifact_id: str,
    function_id: str | None = None,
    name: str | None = None,
    address: int | str | None = None,
    anchor_address: int | str | None = None,
    register: str | None = None,
    radius: int = 6,
) -> dict[str, Any]:
    """Return a bounded heuristic data-flow slice around an instruction or register use."""
    return APP.slice_data_flow(session_id, artifact_id, function_id, name, address, anchor_address, register, radius)


@expose("identify_system_calls")
def _identify_system_calls(
    session_id: str,
    artifact_id: str,
    function_id: str | None = None,
    name: str | None = None,
    address: int | str | None = None,
) -> dict[str, Any]:
    """Return recovered raw system-call instructions for one function."""
    return APP.identify_system_calls(session_id, artifact_id, function_id, name, address)


@expose("navigate_neighborhood")
def _navigate_neighborhood(
    session_id: str,
    artifact_id: str,
    function_id: str | None = None,
    name: str | None = None,
    address: int | str | None = None,
    depth: int = 1,
    radius: int = 1,
) -> dict[str, Any]:
    """Return callers, callees, nearby functions, and nearby strings around one target function."""
    return APP.navigate_neighborhood(session_id, artifact_id, function_id, name, address, depth, radius)


@expose("prioritize_functions")
def _prioritize_functions(
    session_id: str,
    artifact_id: str,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    min_score: int | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Return triaged and optionally filtered functions with explicit scores and evidence."""
    return APP.prioritize_functions(session_id, artifact_id, include_tags, exclude_tags, min_score, limit)


@expose("classify_functions")
def _classify_functions(
    session_id: str,
    artifact_id: str,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Return heuristic function-classification tags with filtering support."""
    return APP.classify_functions(session_id, artifact_id, include_tags, exclude_tags, limit)


@expose("save_workflow_item")
def _save_workflow_item(
    session_id: str,
    kind: str,
    target: dict[str, Any],
    value: dict[str, Any],
    annotation_id: str | None = None,
) -> dict[str, Any]:
    """Save a bookmark, named region, or analysis note."""
    return APP.save_workflow_item(session_id, kind, target, value, annotation_id)


@expose("list_workflow_items")
def _list_workflow_items(
    session_id: str,
    kind: str | None = None,
    artifact_id: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List saved bookmarks, named regions, and notes."""
    return APP.list_workflow_items(session_id, kind, artifact_id, cursor, limit)


@expose("export_curated_analysis")
def _export_curated_analysis(
    session_id: str,
    artifact_id: str,
    function_ids: list[str] | None = None,
    string_ids: list[str] | None = None,
    annotation_ids: list[str] | None = None,
    output_path: str | None = None,
) -> dict[str, Any]:
    """Export a curated subset of functions, strings, xrefs, and workflow items."""
    return APP.export_curated_analysis(session_id, artifact_id, function_ids, string_ids, annotation_ids, output_path)


@expose("batch_query_artifacts")
def _batch_query_artifacts(
    session_id: str,
    operation: str,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    min_score: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run one eligible semantic query across every artifact in a session."""
    return APP.batch_query_artifacts(session_id, operation, include_tags, exclude_tags, min_score, limit)


@expose("list_artifacts")
def _list_artifacts(session_id: str, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
    """List artifacts currently attached to a session."""
    return APP.list_artifacts(session_id, cursor, limit)


@expose("remove_artifact")
def _remove_artifact(session_id: str, artifact_id: str | None = None, display_name: str | None = None) -> dict[str, Any]:
    """Remove one artifact from the session and invalidate its object mappings."""
    return APP.remove_artifact(session_id, artifact_id, display_name)


@expose("register_provisional_function")
def _register_provisional_function(session_id: str, artifact_id: str, name: str, address: int | str | None = None) -> dict[str, Any]:
    """Create a provisional function handle so later calls can reference it by stable ID before a real backend exists."""
    return APP.register_provisional_function(session_id, artifact_id, name, address)


@expose("register_provisional_string")
def _register_provisional_string(session_id: str, artifact_id: str, value: str, address: int | str | None = None) -> dict[str, Any]:
    """Create a provisional string handle so later calls can reference it by stable ID before a real backend exists."""
    return APP.register_provisional_string(session_id, artifact_id, value, address)


@expose("get_object_reference")
def _get_object_reference(session_id: str, object_id: str) -> dict[str, Any]:
    """Resolve a provisional function or string object ID and fail with invalid_id if it expired."""
    return APP.get_object_reference(session_id, object_id)


@expose("put_annotation")
def _put_annotation(
    session_id: str,
    target: dict[str, Any],
    annotation_type: str,
    value: Any,
    annotation_id: str | None = None,
) -> dict[str, Any]:
    """Create or update one annotation with full per-annotation revision history."""
    return APP.put_annotation(session_id, target, annotation_type, value, annotation_id)


@expose("list_annotations")
def _list_annotations(
    session_id: str,
    artifact_id: str | None = None,
    target_kind: str | None = None,
    annotation_type: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List annotations with deterministic ordering and filters."""
    return APP.list_annotations(session_id, artifact_id, target_kind, annotation_type, cursor, limit)


@expose("get_annotation_history")
def _get_annotation_history(session_id: str, annotation_id: str) -> dict[str, Any]:
    """Return the full revision history for one annotation."""
    return APP.get_annotation_history(session_id, annotation_id)


@expose("revert_annotation")
def _revert_annotation(session_id: str, annotation_id: str, revision_id: str | None = None) -> dict[str, Any]:
    """Revert one annotation to a prior revision without affecting other annotations."""
    return APP.revert_annotation(session_id, annotation_id, revision_id)


@expose("create_session_snapshot")
def _create_session_snapshot(session_id: str, name: str, description: str | None = None) -> dict[str, Any]:
    """Capture a named whole-session snapshot that can later be restored."""
    return APP.create_session_snapshot(session_id, name, description)


@expose("list_session_snapshots")
def _list_session_snapshots(session_id: str) -> dict[str, Any]:
    """List named whole-session snapshots for one session."""
    return APP.list_session_snapshots(session_id)


@expose("restore_session_snapshot")
def _restore_session_snapshot(session_id: str, snapshot_id: str | None = None, name: str | None = None) -> dict[str, Any]:
    """Restore a named or ID-addressed whole-session snapshot in place."""
    return APP.restore_session_snapshot(session_id, snapshot_id, name)


@expose("start_artifact_reanalysis")
def _start_artifact_reanalysis(session_id: str, artifact_id: str) -> dict[str, Any]:
    """Start an asynchronous artifact re-analysis job that invalidates provisional object IDs on completion."""
    return APP.start_artifact_reanalysis(session_id, artifact_id)


@expose("get_job")
def _get_job(job_id: str) -> dict[str, Any]:
    """Read one job handle, including progress, partial results, and terminal status."""
    return APP.get_job(job_id)


@expose("list_jobs")
def _list_jobs(session_id: str | None = None, status: str | None = None, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
    """List job handles with deterministic ordering and optional filters."""
    return APP.list_jobs(session_id, status, cursor, limit)


@expose("cancel_job")
def _cancel_job(job_id: str) -> dict[str, Any]:
    """Request cancellation for a running asynchronous job."""
    return APP.cancel_job(job_id)


@expose("export_session_state")
def _export_session_state(session_id: str, output_path: str | None = None) -> dict[str, Any]:
    """Export machine-readable session state inline or to a workspace file."""
    return APP.export_session_state(session_id, output_path)


@expose("patch_artifact_bytes")
def _patch_artifact_bytes(
    session_id: str,
    artifact_id: str,
    input_kind: str,
    value: int | str,
    bytes_hex: str,
    output_path: str | None = None,
    attach_to_session: bool = True,
    display_name: str | None = None,
) -> dict[str, Any]:
    """Apply a byte patch and materialize a patched artifact in the workspace."""
    return APP.patch_artifact_bytes(session_id, artifact_id, input_kind, value, bytes_hex, output_path, attach_to_session, display_name)


@expose("patch_artifact_assembly")
def _patch_artifact_assembly(
    session_id: str,
    artifact_id: str,
    input_kind: str,
    value: int | str,
    assembly: str,
    isa: str,
    output_path: str | None = None,
    attach_to_session: bool = True,
    display_name: str | None = None,
) -> dict[str, Any]:
    """Assemble and apply a patch for a supported ISA."""
    return APP.patch_artifact_assembly(session_id, artifact_id, input_kind, value, assembly, isa, output_path, attach_to_session, display_name)


@expose("find_code_caves")
def _find_code_caves(session_id: str, artifact_id: str, min_size: int = 32) -> dict[str, Any]:
    """Discover likely code caves inside mapped sections."""
    return APP.find_code_caves(session_id, artifact_id, min_size)


@expose("edit_artifact_metadata")
def _edit_artifact_metadata(session_id: str, artifact_id: str, edit_kind: str, target: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    """Persist naming, type, and calling-convention overrides for an artifact."""
    return APP.edit_artifact_metadata(session_id, artifact_id, edit_kind, target, value)


@expose("import_type_definitions")
def _import_type_definitions(session_id: str, artifact_id: str, source_format: str, source_text: str) -> dict[str, Any]:
    """Import type definitions into artifact-local overrides."""
    return APP.import_type_definitions(session_id, artifact_id, source_format, source_text)


@expose("export_command_log")
def _export_command_log(session_id: str, format: str = "json", output_path: str | None = None) -> dict[str, Any]:
    """Export the persisted command log for Feature 07 actions."""
    return APP.export_command_log(session_id, format, output_path)


@expose("export_analysis_report")
def _export_analysis_report(session_id: str, artifact_id: str, format: str = "json", output_path: str | None = None) -> dict[str, Any]:
    """Export a structured report for one artifact."""
    return APP.export_analysis_report(session_id, artifact_id, format, output_path)


@expose("list_artifact_dependencies")
def _list_artifact_dependencies(session_id: str, artifact_id: str) -> dict[str, Any]:
    """Report dependency hints for one artifact."""
    return APP.list_artifact_dependencies(session_id, artifact_id)


@expose("correlate_session_artifacts")
def _correlate_session_artifacts(session_id: str, artifact_ids: list[str] | None = None, cursor: int = 0, limit: int = 100) -> dict[str, Any]:
    """Correlate multiple artifacts in one session with deterministic pagination."""
    return APP.correlate_session_artifacts(session_id, artifact_ids, cursor, limit)


@expose("diff_artifacts")
def _diff_artifacts(session_id: str, left_artifact_id: str, right_artifact_id: str) -> dict[str, Any]:
    """Compare two artifacts structurally and by recovered objects when available."""
    return APP.diff_artifacts(session_id, left_artifact_id, right_artifact_id)


@expose("ingest_and_triage_artifact")
def _ingest_and_triage_artifact(
    session_id: str,
    path: str,
    display_name: str | None = None,
    hints: dict[str, Any] | None = None,
    analyze: bool = False,
    verbosity: str = "brief",
    token_budget_hint: int | None = None,
    include_next_actions: bool = True,
    include_raw_sections: bool = False,
) -> dict[str, Any]:
    """Attach an artifact, triage it, and optionally queue analysis in one bounded call."""
    return APP.ingest_and_triage_artifact(
        session_id,
        path,
        display_name,
        hints,
        analyze,
        verbosity,
        token_budget_hint,
        include_next_actions,
        include_raw_sections,
    )


@expose("analyze_and_summarize")
def _analyze_and_summarize(
    session_id: str,
    artifact_id: str,
    focus: str = "general",
    wait_timeout_seconds: float = 15.0,
    verbosity: str = "brief",
    token_budget_hint: int | None = None,
    include_next_actions: bool = True,
    include_raw_sections: bool = False,
) -> dict[str, Any]:
    """Start analysis when needed, wait briefly, and return a compact artifact brief."""
    return APP.analyze_and_summarize(
        session_id,
        artifact_id,
        focus,
        wait_timeout_seconds,
        verbosity,
        token_budget_hint,
        include_next_actions,
        include_raw_sections,
    )


@expose("hunt_interesting_regions")
def _hunt_interesting_regions(
    session_id: str,
    artifact_id: str,
    objective: str = "general",
    limit: int = 8,
    verbosity: str = "brief",
    token_budget_hint: int | None = None,
    include_next_actions: bool = True,
    include_raw_sections: bool = False,
) -> dict[str, Any]:
    """Combine prioritized functions, suspicious strings, imports, and static hints into one shortlist."""
    return APP.hunt_interesting_regions(
        session_id,
        artifact_id,
        objective,
        limit,
        verbosity,
        token_budget_hint,
        include_next_actions,
        include_raw_sections,
    )


@expose("trace_capability")
def _trace_capability(
    session_id: str,
    artifact_id: str,
    target: dict[str, Any],
    depth: int = 1,
    verbosity: str = "brief",
    token_budget_hint: int | None = None,
    include_next_actions: bool = True,
    include_raw_sections: bool = False,
) -> dict[str, Any]:
    """Expand one function target into neighborhood, xrefs, variables, and instruction context."""
    return APP.trace_capability(
        session_id,
        artifact_id,
        target,
        depth,
        verbosity,
        token_budget_hint,
        include_next_actions,
        include_raw_sections,
    )


@expose("prepare_patch_plan")
def _prepare_patch_plan(
    session_id: str,
    artifact_id: str,
    objective: str,
    target: dict[str, Any] | None = None,
    min_code_cave_size: int = 32,
    verbosity: str = "brief",
    token_budget_hint: int | None = None,
    include_next_actions: bool = True,
    include_raw_sections: bool = False,
) -> dict[str, Any]:
    """Bundle patchability context, code caves, and candidate patch points into one brief."""
    return APP.prepare_patch_plan(
        session_id,
        artifact_id,
        objective,
        target,
        min_code_cave_size,
        verbosity,
        token_budget_hint,
        include_next_actions,
        include_raw_sections,
    )


@expose("artifact_relationship_brief")
def _artifact_relationship_brief(
    session_id: str,
    artifact_id: str,
    focus: str = "general",
    verbosity: str = "brief",
    token_budget_hint: int | None = None,
    include_next_actions: bool = True,
    include_raw_sections: bool = False,
) -> dict[str, Any]:
    """Summarize relationships, dependency hints, correlation hits, and likely comparison partners."""
    return APP.artifact_relationship_brief(
        session_id,
        artifact_id,
        focus,
        verbosity,
        token_budget_hint,
        include_next_actions,
        include_raw_sections,
    )


@expose("ghidra_decompile")
def _ghidra_decompile(
    session_id: str,
    artifact_id: str,
    address: int | str = 0,
    timeout_seconds: Annotated[int, Field(description="Max seconds for Ghidra decompilation. Increase for large or heavily-obfuscated binaries. Default: 300.")] = 300,
) -> dict[str, Any]:
    """Decompile a function using the Ghidra headless decompiler."""
    return APP.ghidra_decompile(session_id, artifact_id, address, timeout_seconds)


@expose("ghidra_analyze")
def _ghidra_analyze(
    session_id: str,
    artifact_id: str,
    timeout_seconds: Annotated[int, Field(description="Max seconds for full Ghidra analysis. Increase for large binaries. Default: 600.")] = 600,
) -> dict[str, Any]:
    """Run full Ghidra headless analysis and export functions, strings, imports, and sections."""
    return APP.ghidra_analyze(session_id, artifact_id, timeout_seconds)


@expose("run_ghidra_script")
def _run_ghidra_script(
    session_id: str,
    artifact_id: str,
    script: str = "",
    timeout_seconds: Annotated[int, Field(description="Max seconds before the script is killed. Increase for complex analysis scripts. Default: 300.")] = 300,
) -> dict[str, Any]:
    """Run a custom Ghidra Python script against an artifact binary."""
    return APP.run_ghidra_script(session_id, artifact_id, script, timeout_seconds)


@expose("export_dynamic_manifest")
def _export_dynamic_manifest(session_id: str, artifact_id: str, output_path: str | None = None) -> dict[str, Any]:
    """Export a JSON manifest for use by dynamic analysis tools (pwn-mcp)."""
    return APP.export_dynamic_manifest(session_id, artifact_id, output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Static binary analysis MCP foundation server")
    parser.add_argument("--transport", choices=["stdio", "sse", "http", "streamable-http", "both"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--log-level", default=os.environ.get("REVERSING_MCP_LOG_LEVEL", "INFO"))
    args = parser.parse_args()

    log_level = configure_logging(args.log_level)
    LOGGER.info("server_start transport=%s host=%s port=%s", args.transport, args.host, args.port)
    if args.transport == "stdio":
        mcp.run()
        return
    if args.transport == "sse":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="sse")
        return

    import uvicorn

    app = build_network_app(args.transport)
    uvicorn_level = "debug" if log_level <= logging.DEBUG else logging.getLevelName(log_level).lower()
    uvicorn.run(app, host=args.host, port=args.port, log_level=uvicorn_level)


if __name__ == "__main__":
    main()
