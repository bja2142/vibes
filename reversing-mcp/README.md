# Reversing MCP

`reversing-mcp` is a static binary analysis MCP server for workspace-scoped reverse engineering. It provides persistent sessions, artifact management, triage, disassembly, decompilation, semantic recovery, signatures and extraction workflows, analyst notes, exports, and batch operations through a structured MCP tool surface.

## What It Provides

Current implemented scope:

- Persistent analysis sessions rooted in the shared workspace.
- Stable artifact, function, and string identifiers with analysis-generation invalidation.
- File triage for ELF, PE, Mach-O, ZIP, TAR, and raw hinted blobs.
- Structured strings, address translation, child-artifact listing, and linkage/debug metadata.
- Async analysis with function enumeration, disassembly, decompilation, xrefs, and search.
- Semantic recovery including call graphs, CFGs, variables, stack frames, constants, types, runtime metadata, slices, system calls, and triage scoring.
- Signature and extraction workflows including YARA-style scanning, compiler/toolchain fingerprints, crypto constants, packer heuristics, entropy, string deobfuscation, extraction, carving, and relationship tracking.
- Analyst workflow features including notes, bookmarks, named regions, snapshots, curated exports, and batch artifact queries.
- Feature 07 workflows including byte and assembly patching, code-cave discovery, artifact-local naming and type overrides, type import, command/report export, dependency views, cross-artifact correlation, and structural diffing.
- Feature 08 operational polish including stdio and streamable HTTP transport, HTTP auth and quota controls, single-agent session leasing for HTTP, enriched analysis synopses, and a requirements traceability matrix.
- Feature 09 composite brief workflows including one-shot intake, compact analysis summaries, ranked hunting shortlists, function traces, patch plans, relationship briefs, and response-budget controls.

## Tool Surface

The MCP currently exposes these tool groups:

- Discovery and runtime policy
- Session and artifact lifecycle
- Triage and file-intake metadata
- Analysis, disassembly, and decompilation
- Semantic recovery and analyst workflow
- Signatures, extraction, and obfuscation handling
- Annotations, snapshots, jobs, and exports
- Patching, overrides, reports, and multi-artifact interoperability
- Composite token-efficient workflow briefs

Use `describe_tools` or `get_capabilities` from the MCP for machine-readable discovery.

## Run

```bash
reversing-mcp --transport stdio
```

## Docker Compose

```bash
cp .env.example .env
docker compose up -d --build
```

The compose deployment exposes the MCP on port `6767` by default and can be overridden with `REVERSING_MCP_PORT` in `.env`.

If your host user is not `1000:1000`, set `REVERSING_MCP_UID` and `REVERSING_MCP_GID` in `.env` to match your local UID/GID so the mounted workspace stays writable.

Endpoints:

- Streamable HTTP MCP: `http://127.0.0.1:${REVERSING_MCP_PORT:-6767}/mcp`
- SSE MCP: `http://127.0.0.1:${REVERSING_MCP_PORT:-6767}/sse`

Important environment variables:

- `REVERSING_MCP_WORKSPACE_ROOT`: shared workspace root used for persisted session state. Defaults to `/workspace`.
- `REVERSING_MCP_LOG_LEVEL`: optional log level for the server process.
- `REVERSING_MCP_MAX_INPUT_SIZE_BYTES`: maximum accepted input size.
- `REVERSING_MCP_MAX_ARTIFACTS_PER_SESSION`: session artifact-count limit.
- `REVERSING_MCP_PARSER_TIMEOUT_SECONDS`: isolated parser timeout.
- `REVERSING_MCP_PARSER_MEMORY_MB`: isolated parser memory limit.
- `REVERSING_MCP_PARSER_CPU_SECONDS`: isolated parser CPU budget.
- `REVERSING_MCP_RECURSION_DEPTH_LIMIT`: recursive extraction depth limit.
- `REVERSING_MCP_STRING_COUNT_LIMIT`: string extraction cap.
- `REVERSING_MCP_CARVED_BYTE_BUDGET`: total carved-byte budget for extraction workflows.
- `REVERSING_MCP_HTTP_REQUIRE_AUTH`: require bearer auth on the streamable HTTP transport. Defaults to `false`.
- `REVERSING_MCP_HTTP_TOKENS`: comma-separated `tenant=token` pairs for HTTP auth. Tokens are only enforced when `REVERSING_MCP_HTTP_REQUIRE_AUTH=true`.
- `REVERSING_MCP_HTTP_AGENT_HEADER`: request header used to identify the current HTTP agent. Defaults to `x-reversing-agent-id`.
- `REVERSING_MCP_HTTP_REQUESTS_PER_MINUTE_PER_AGENT`: per-agent HTTP request quota.
- `REVERSING_MCP_HTTP_MAX_SESSIONS_PER_TENANT`: per-tenant HTTP session quota.
- `REVERSING_MCP_HTTP_MAX_ACTIVE_JOBS_PER_TENANT`: per-tenant active-job quota.

## Security Model

`reversing-mcp` is a static-only analysis server.

- Samples must live inside the configured workspace root.
- The parser and analysis worker run in an isolated subprocess.
- Shell execution against sample-controlled input is not part of the product model.
- Extraction workflows preserve sanitized filenames and stay inside the configured resource limits.

## Documentation

The complete user guide is in `docs/`.

- [Overview](docs/overview.md)
- [Getting Started](docs/getting-started.md)
- [Workflows](docs/workflows.md)
- [Tool Reference](docs/tool-reference.md)
- [Requirements Matrix](docs/requirements-matrix.md)

## Test

Run tests inside the container:

```bash
docker exec -w /app reversing-mcp-compose pytest -q
```

Or locally in a suitable environment:

```bash
pytest
```
