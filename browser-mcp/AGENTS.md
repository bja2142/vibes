# Agent Guide

This repository contains `browser-puppet`, a Playwright-backed MCP server for browser automation, inspection, debugging, and evidence capture.

Use this file as the short operational contract. For the full reference, examples, and tool-by-tool guidance, read [docs/mcp-guide.md](./docs/mcp-guide.md).

## When To Use This MCP

Use `browser-puppet` when a task requires any of the following:

- opening and navigating a real browser
- inspecting page structure, accessibility, console, network, storage, cookies, or runtime behavior
- interacting with UI elements
- capturing screenshots, PDFs, traces, HAR, video, or other artifacts
- diagnosing frontend regressions, auth flows, redirects, CORS, service workers, or websocket behavior

Do not use it for tasks that can be solved more cheaply with static code inspection alone.

## Connection

Preferred endpoint for Codex:

- streamable HTTP: `http://127.0.0.1:8000/mcp`

Also available:

- SSE: `http://127.0.0.1:8000/sse`

Current transport modes:

- `--transport http` exposes `/mcp`
- `--transport sse` exposes `/sse` and `/messages`
- `--transport both` exposes `/mcp`, `/sse`, and `/messages`

For Codex, prefer `/mcp` unless a client explicitly requires SSE.

## Default Workflow

Use this sequence unless the task clearly needs something different:

1. `create_context`
2. `open_page`
3. inspect with `get_page_digest`, `get_page_outline`, `find_elements`, `get_network_digest`, or other read tools
4. interact with `click`, `type_text`, `fill_form`, `press_key`, `wait_for`, and related tools
5. capture artifacts only when they add value
6. close with `close_page` and `close_context` when finished

For multi-step browser tasks, inspect before acting. Do not blindly click.

## Operating Rules

- Prefer `element_id` handles from discovery tools when possible.
- If an action changes page state, use the mutation tools that support `observe`.
- Treat Chromium-only tools as capability-gated. Do not assume Firefox/WebKit parity.
- Use credential aliases like `{{cred:alias}}` instead of embedding secrets directly.
- Route blocking, route mocking, host overrides, and many runtime settings are context-scoped.
- HAR and video capture depend on context creation configuration.
- If a task needs evidence, register artifacts rather than relying on transient console output.
- The text transfer tools are only for lightweight UTF-8 text files in the context artifact directory. Do not use them for screenshots, PDFs, video, or other binary assets.
- For authorized parity testing against your own infrastructure, prefer `create_context` profile inputs, `import_browser_profile`, `export_browser_profile`, `set_headers`, `set_user_agent`, and `get_fingerprint_report` over ad hoc page scripting.

## Key Tool Groups

Core lifecycle:

- `create_context`
- `open_page`
- `list_pages`
- `switch_page`
- `close_page`
- `close_context`

Inspection:

- `get_page_digest`
- `get_page_meta`
- `get_page_outline`
- `find_elements`
- `find_interactive_candidates`
- `get_dom_snapshot`
- `get_aom_snapshot`
- `get_console_logs`
- `get_page_errors`
- `get_network_traffic`

Interaction:

- `click`
- `tap`
- `type_text`
- `fill_form`
- `press_key`
- `select_dropdown`
- `set_checkbox`
- `upload_file`
- `wait_for`

Artifacts and evidence:

- `take_screenshot`
- `get_annotated_screenshot`
- `print_to_pdf`
- `record_video`
- `export_har`
- `start_trace`
- `stop_trace`
- `generate_report`

Advanced diagnostics:

- `send_cdp_command`
- `subscribe_cdp_events`
- `get_cdp_events`
- `list_websockets`
- `get_websocket_messages`
- `list_service_workers`
- `list_web_workers`
- `check_cors`
- `get_certificate_info`
- `get_coverage`
- `get_fingerprint_report`

Authorized profile parity:

- `import_browser_profile`
- `export_browser_profile`
- `save_storage_state`
- `load_storage_state`
- `set_headers`
- `set_user_agent`

Lightweight text transfer:

- `list_context_files`
- `upload_text_artifact`
- `download_text_artifact`

## Common Constraints

- `set_user_agent`, `emulate_network`, `pinch_zoom`, `get_coverage`, and most CDP work are Chromium-only.
- `print_to_pdf` is Chromium-only.
- Permission denial and prompt simulation are limited by Playwright APIs.
- Video capture is configured at context creation time.
- Custom CA bundle handling currently validates and records configuration state, but does not fully rewire Playwright transport trust.

## Good Habits For Agents

- Start broad, then narrow. Use digest and discovery tools before precise interactions.
- Prefer the smallest context or page change needed to verify a behavior.
- If you need a reproducible artifact, take it explicitly.
- If a flow spans redirects, auth, popups, or multiple windows, use `list_pages`, `switch_page`, and the returned opener metadata.
- If a page rerenders, fall back to fresh discovery rather than assuming an old selector is still valid.

## Read Next

The complete guide is here:

- [docs/mcp-guide.md](./docs/mcp-guide.md)
