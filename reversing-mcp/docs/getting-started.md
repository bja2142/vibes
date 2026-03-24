# Getting Started

## Starting The Server

### Stdio

```bash
reversing-mcp --transport stdio
```

### Docker Compose

```bash
cp .env.example .env
docker compose up -d --build
```

Default network endpoints:

- Streamable HTTP MCP: `http://127.0.0.1:6767/mcp`
- SSE MCP: `http://127.0.0.1:6767/sse`

Optional HTTP auth and quota environment variables:

- `REVERSING_MCP_HTTP_REQUIRE_AUTH=true`
- `REVERSING_MCP_HTTP_TOKENS=tenant-a=secret-a,tenant-b=secret-b`
- `REVERSING_MCP_HTTP_AGENT_HEADER=x-reversing-agent-id`
- `REVERSING_MCP_HTTP_REQUESTS_PER_MINUTE_PER_AGENT=120`
- `REVERSING_MCP_HTTP_MAX_SESSIONS_PER_TENANT=32`
- `REVERSING_MCP_HTTP_MAX_ACTIVE_JOBS_PER_TENANT=4`

HTTP auth is optional and defaults to off. Tokens are only enforced when `REVERSING_MCP_HTTP_REQUIRE_AUTH=true`.

When HTTP mode is in use, sessions are leased to one agent header at a time and are isolated per tenant when bearer auth is configured.

## Workspace Expectations

All sample paths and output paths must live under the configured workspace root.

Defaults:

- Runtime workspace root: `/workspace`
- Persisted state root: `/workspace/.reversing-mcp`

## First Session

Minimal flow:

1. `create_session`
2. `add_artifact`
3. `triage_artifact`
4. `start_artifact_analysis`
5. `get_job`
6. query functions, symbols, disassembly, or semantic views

Example call sequence:

```json
{"tool":"create_session","arguments":{"name":"demo"}}
{"tool":"add_artifact","arguments":{"session_id":"sess_...","path":"/workspace/samples/a.out"}}
{"tool":"triage_artifact","arguments":{"session_id":"sess_...","artifact_id":"art_..."}}
{"tool":"start_artifact_analysis","arguments":{"session_id":"sess_...","artifact_id":"art_..."}}
{"tool":"get_job","arguments":{"job_id":"job_..."}}
```

Composite shortcut:

```json
{"tool":"create_session","arguments":{"name":"demo"}}
{"tool":"ingest_and_triage_artifact","arguments":{"session_id":"sess_...","path":"/workspace/samples/a.out","analyze":false}}
{"tool":"analyze_and_summarize","arguments":{"session_id":"sess_...","artifact_id":"art_...","focus":"general","wait_timeout_seconds":30.0}}
```

Use the composite tools when you want to reduce back-and-forth:

- `ingest_and_triage_artifact`: attach plus triage in one call
- `analyze_and_summarize`: queue analysis if needed and return a bounded artifact brief
- `hunt_interesting_regions`: produce a ranked shortlist of likely-interesting targets
- `trace_capability`: expand one function target into neighborhood and variable context
- `prepare_patch_plan`: combine code-caves, patch points, and instruction-mode context
- `artifact_relationship_brief`: summarize relationships, dependencies, and likely diff candidates

## Result Envelope

Every tool returns the same top-level shape:

- `ok`: success flag
- `result`: tool payload on success
- `error`: normalized error object on failure
- `partial`: true when the result is intentionally incomplete
- `confidence`: confidence wrapper for the response
- `provenance`: tool name and parameters used
- `suggested_next_actions`: optional recommended follow-up calls

## Common Patterns

### Find A Function And Disassemble It

```json
{"tool":"list_artifact_functions","arguments":{"session_id":"sess_...","artifact_id":"art_...","query":"main"}}
{"tool":"disassemble_function","arguments":{"session_id":"sess_...","artifact_id":"art_...","function_id":"fn_..."}}
```

### Get Semantic Context For A Function

```json
{"tool":"get_call_graph","arguments":{"session_id":"sess_...","artifact_id":"art_...","function_id":"fn_...","depth":2}}
{"tool":"get_control_flow_graph","arguments":{"session_id":"sess_...","artifact_id":"art_...","function_id":"fn_..."}}
{"tool":"get_function_variables","arguments":{"session_id":"sess_...","artifact_id":"art_...","function_id":"fn_..."}}
```

### Save Analyst State

```json
{"tool":"save_workflow_item","arguments":{"session_id":"sess_...","kind":"note","target":{"kind":"function","object_id":"fn_..."},"value":{"text":"interesting edge case"}}}
```

### Extract And Reattach Derived Artifacts

```json
{"tool":"carve_embedded_artifacts","arguments":{"session_id":"sess_...","artifact_id":"art_...","attach_to_session":true,"analyze_extracted":true}}
```

### Prepare A Patch Plan With Fewer Calls

```json
{"tool":"analyze_and_summarize","arguments":{"session_id":"sess_...","artifact_id":"art_...","focus":"patching"}}
{"tool":"prepare_patch_plan","arguments":{"session_id":"sess_...","artifact_id":"art_...","objective":"bypass_guard","target":{"function_id":"fn_..."}}}
```

### Compare A Parent Artifact And Its Derived Children

```json
{"tool":"artifact_relationship_brief","arguments":{"session_id":"sess_...","artifact_id":"art_...","focus":"diffing"}}
{"tool":"diff_artifacts","arguments":{"session_id":"sess_...","left_artifact_id":"art_...","right_artifact_id":"art_..."}}
```

## Important Operational Notes

- Use `list_artifact_functions(query=...)` before relying on a function name match.
- Prefer `function_id` or `string_id` over name-based lookup once you have them.
- Reanalysis invalidates old function and string IDs.
- Extraction and carving can return partial results when safety limits are reached.
- `scan_with_yara` works with custom YARA source if `yara-python` is installed, otherwise it falls back to built-in heuristics.
- HTTP auth is optional and off by default; tokens only take effect when `REVERSING_MCP_HTTP_REQUIRE_AUTH=true`.
- Composite brief tools intentionally return bounded previews; use the lower-level tools when you need complete tables or wider raw context.
- `token_budget_hint` can clamp a requested `verbosity` level down to a smaller response profile.
