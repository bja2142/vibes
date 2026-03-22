Here is the revised master Technical Design Document. It expands the original blueprint into a more complete MCP specification for application testing, human-experience validation, and adversarial CTF challenge verification across desktop and mobile environments.

***

# Technical Design Document: Agentic Browser-Control MCP Server (Master Blueprint)

## 1. System Overview
**Objective:** Build a highly secure, asynchronous, and containerized Model Context Protocol (MCP) server in Python 3 that exposes deep, stateful browser automation capabilities to an autonomous LLM agent.

**Primary Use Cases:**
- End-to-end functional application testing
- Human-experience validation for desktop and mobile web flows
- Accessibility, responsiveness, and UI-state verification
- Security challenge and CTF interaction, exploitation validation, and evidence capture
- Deterministic reproduction of browser behavior for regression analysis

**Design Goals:**
- Give the agent enough page awareness to act like a careful human tester without flooding context windows
- Support both desktop and mobile form factors, including touch-first interactions
- Provide strong observability over DOM, network, console, storage, screenshots, downloads, dialogs, and traces
- Preserve strict execution boundaries for hostile targets
- Prefer semantic, low-token summaries first, with selective escalation to DOM, trace, or vision artifacts

## 2. High-Level Architecture
The system uses a multi-container, fully asynchronous client-server model to isolate browser execution, keep the MCP runtime stable, and support hostile web targets.

- **Server Runtime:** Python 3.11+ using the official `mcp` SDK on `asyncio`
- **Automation Engine:** `playwright.async_api`
- **Supported Browsers:** `chromium` and `firefox`, with architecture left open for `webkit` support where feasible
- **Execution Split:**
  1. **MCP Python Server:** Tool dispatch, state tracking, diffing, evidence packaging, policy enforcement
  2. **Browser Node:** Runs browser engines and exposes CDP / Playwright connection endpoints
- **Transport:** SSE over HTTP (`/sse`) and `stdio`
- **Artifact Volume:** Shared writable directory for screenshots, downloads, traces, HAR files, videos, and exported reports

## 3. Core Design Principles

### 3.1. Agent-Oriented Control Model
- Tools must be composable and narrowly scoped
- Every actionable element should have a stable MCP-facing identifier
- The server should maintain per-context state so the agent can ask follow-up questions cheaply
- The server should favor summaries and diffs before full snapshots

### 3.2. Human-Experience Fidelity
- The system must support realistic desktop and mobile layouts
- The agent must be able to inspect what a user would actually perceive: viewport, overlays, dialogs, focus order, accessibility tree, scroll state, and rendered screenshots
- Interaction tools must distinguish between synthetic success and user-visible success

### 3.3. Determinism and Replayability
- Context creation must support explicit seeds/configuration where possible
- Important runs should be traceable and exportable
- Each test session should emit durable artifacts sufficient for replay and postmortem analysis

### 3.4. Token-Efficient Server-Side Reduction
- The MCP must do the expensive interpretation work server-side and return only decision-useful context to the agent
- Every read-oriented tool should support tiered output such as `compact`, `standard`, and `full`
- Default tool responses should prefer handles, summaries, ranked candidates, and semantic diffs over raw DOM, raw logs, or base64 blobs
- Large artifacts should be stored server-side and referenced by stable IDs so the agent can selectively hydrate only needed slices
- Server responses should prioritize what changed, what is actionable, what failed, and what is user-visible
- The MCP should maintain short-lived per-page caches for DOM state, accessibility state, screenshots, console buffers, and network buffers to avoid resending unchanged context

## 4. Browser Context and Emulation Model
Contexts must be strictly isolated and ephemeral unless explicitly configured for a multi-step authenticated workflow.

Each context should support:
- Browser engine selection
- Desktop or mobile mode
- Viewport width/height
- Device scale factor
- Touch enabled / disabled
- User agent override
- Locale
- Timezone
- Color scheme
- Reduced motion preference
- Geolocation
- Permission grants and denials
- Proxy configuration if allowed by policy
- HTTP authentication
- Optional persistent evidence capture settings

The system should expose standard presets for:
- Generic desktop
- Generic mobile
- Common responsive breakpoints
- Named device profiles where Playwright supports them

## 4b. Authentication State and Session Persistence
Agents frequently need to authenticate, then reuse that session across multiple pages or even across context restarts (e.g., logging in once and then testing 20 protected routes).

- **Storage state save/restore:** The server must support exporting and importing Playwright `storageState` (cookies + localStorage) so that an authenticated session can be snapshotted and rehydrated without re-logging in.
- **Credential vault:** An in-memory, per-session credential store that tools like `type_text` can reference by alias (e.g., `{{cred:admin_password}}`) so that raw secrets never appear in agent tool-call logs or MCP message history.
- **OAuth / redirect-chain support:** Navigation tools must gracefully follow multi-hop OAuth redirects (302 chains, IdP login pages, consent screens) and report each hop so the agent can interact at any step.
- **MFA / TOTP helper:** A utility tool to generate TOTP codes from a shared secret, enabling automated login to MFA-protected applications in test environments.

## 4c. Certificate and TLS Configuration
Testing and CTF environments frequently use self-signed or internally-signed certificates.

- Contexts must support `ignore_https_errors` for self-signed certificate acceptance.
- The server should support providing a custom CA certificate bundle per context for corporate PKI environments.
- TLS version and cipher information should be observable in network request details.

## 4d. Session-Level Response Configuration
Agents should not need to pass `mode: compact` on every single tool call. The server must support a session-level response policy that applies defaults globally.

- **`configure_session`** sets default verbosity (`compact`, `standard`, `full`), maximum list lengths, excluded response fields, and whether binary artifacts are returned inline or as handles.
- Per-tool `mode` parameters override the session default when present.
- The session config should also control **proactive event attachment**: when enabled, the server appends a `_events_since_last_call` field to every tool response containing any buffered console errors, unhandled exceptions, dialog appearances, navigation events, or download triggers that occurred since the previous tool call. This eliminates the need for the agent to poll event buffers after every action.
- The session config should support a `checkpoint_auto` flag: when enabled, the server automatically captures a lightweight state checkpoint before every mutation tool, enabling cheap diffs without the agent explicitly calling `get_state_handle`.

## 4e. Handle and Cache Lifecycle
State handles, cached snapshots, and server-side ring buffers accumulate over long sessions and must have defined lifecycle semantics.

- Handles have a configurable TTL (default: 5 minutes) and are evicted LRU when the per-context cache exceeds a configurable cap.
- Named checkpoints (see §9.3) are exempt from TTL until explicitly released or the context is destroyed.
- The server must expose a `get_cache_stats` diagnostic showing active handles, memory usage, and eviction counts.
- Evicted handles return a clear `handle_expired` error with guidance to recapture, not a generic failure.

## 5. Tool Interface Definitions
The toolset is designed around tiered observability and precise manipulation. The agent should be able to orient quickly, act safely, and escalate only when needed.

### 5.0. Token-Reduction Design Principles for Tool Calls
The single largest source of token waste in agent-driven browser automation is **round-trip overhead**: the agent must generate a tool call, receive a response, reason over it, and generate the next call. Each round-trip costs input tokens (prior context re-read), output tokens (tool call generation), and response tokens. The tool interface must therefore minimize the number of round-trips required for common workflows.

**Key patterns enforced across all tool sections:**

1. **Inline element resolution:** Every action tool that accepts `element_id` must also accept an optional `target` object with fields `{selector?, text?, role?, label?, nth?}`. When `target` is provided instead of `element_id`, the server resolves the element server-side, eliminating the prior `find_elements` round-trip. If resolution is ambiguous, the server returns ranked candidates instead of failing silently.

2. **Post-action observation bundling:** Every mutation tool (click, type, navigate, etc.) accepts an optional `observe` parameter (default: `auto`). When enabled, the response includes a compact post-action digest: URL change, new errors, DOM delta summary, dialog/overlay appearance, and navigation events — the same data the agent would otherwise request in a follow-up `get_page_digest` call.

3. **Batch execution:** The `run_steps` tool (§5.14) executes a sequence of actions server-side in a single round-trip, returning only the final state plus any intermediate failures. This collapses multi-step flows from N round-trips to 1.

4. **Composite operations:** High-frequency multi-step patterns (form filling, table reading, multi-viewport comparison) have dedicated single-call tools that do server-side what the agent would otherwise orchestrate across many calls.

5. **Cursor-based pagination:** Any tool that returns lists (network logs, console entries, DOM nodes, artifacts) must support `cursor` and `limit` parameters. The initial response includes a `next_cursor` token; subsequent calls resume from that point without re-transmitting prior entries or metadata.

### 5.1. Session, Navigation, and Viewport Management

| Tool Name | Parameters | Description | Returns |
| :--- | :--- | :--- | :--- |
| `create_context` | `browser` (enum: chromium, firefox), `profile?` (obj) | Creates a fresh isolated browser context with optional desktop/mobile emulation and permissions. | `context_id` plus effective config |
| `open_page` | `context_id` (str), `url` (str), `wait_until?` (enum: domcontentloaded, load, networkidle) | Opens a page in the target context. | `page_id`, final URL, status |
| `navigate` | `page_id` (str), `url` (str), `wait_until?` (enum) | Navigates the active page. | Final URL, status, timing summary |
| `reload_page` | `page_id` (str), `ignore_cache?` (bool) | Reloads the current page. | Status and timing summary |
| `go_back` | `page_id` (str) | Navigates backward in history. | Final URL and status |
| `go_forward` | `page_id` (str) | Navigates forward in history. | Final URL and status |
| `list_pages` | `context_id` (str) | Lists all open tabs, popups, and their metadata. | JSON array |
| `switch_page` | `page_id` (str) | Switches operational focus to another tab or popup. | Page title and URL |
| `resize_viewport` | `page_id` (str), `width` (int), `height` (int) | Changes viewport size for responsive testing. | Effective viewport info |
| `set_emulation` | `page_id` (str), `settings` (obj) | Updates viewport/device/touch/locale/color-scheme/reduced-motion settings where supported. | Effective emulation config |
| `scroll` | `page_id` (str), `direction` (enum: up, down, top, bottom), `amount_px?` (int) | Scrolls the active viewport or page. | Updated scroll metrics |
| `close_page` | `page_id` (str) | Closes a single page or popup. | Success boolean |
| `close_context` | `context_id` (str) | Destroys the context and clears session data. | Success boolean |
| `save_storage_state` | `context_id` (str), `path?` (str) | Exports cookies + localStorage for session reuse. | File path or JSON blob |
| `load_storage_state` | `context_id` (str), `state` (str or obj) | Restores a previously saved storage state into the context. | Success boolean |
| `set_extra_http_headers` | `page_id` (str), `headers` (obj) | Sets persistent extra HTTP headers on a specific page (complementing context-level `set_headers`). | Success boolean |
| `set_http_credentials` | `context_id` (str), `username` (str), `password` (str) | Configures HTTP Basic/Digest auth credentials for the context. | Success boolean |
| `generate_totp` | `secret` (str), `algorithm?` (enum: SHA1, SHA256, SHA512), `digits?` (int), `period?` (int) | Generates a current TOTP code for MFA login flows. | TOTP code string |

### 5.2. Page Discovery and Structural Introspection

| Tool Name | Parameters | Description | Returns |
| :--- | :--- | :--- | :--- |
| `get_page_meta` | `page_id` (str) | Returns current URL, title, readiness, status, and top-level page facts. | JSON object |
| `get_viewport_state` | `page_id` (str) | Returns viewport size, scroll position, DPR, orientation, and touch mode. | JSON object |
| `get_page_outline` | `page_id` (str) | Returns a low-token summary of landmarks, headings, forms, buttons, links, dialogs, and iframes. | JSON object |
| `get_page_digest` | `page_id` (str), `mode?` (enum: compact, standard, full) | Returns a single prioritized state digest: URL/title, primary landmarks, active blockers, pending async work, top actionable elements, and recent page changes. | JSON object |
| `find_elements` | `page_id` (str), `query` (obj) | Finds elements by role, text, label, placeholder, CSS, XPath, or semantic filters. | Matching element descriptors |
| `find_interactive_candidates` | `page_id` (str), `intent` (str), `filters?` (obj), `limit?` (int) | Ranks likely target elements for an agent intent such as login, submit, search, close modal, next step, or open menu. | Ranked element descriptors |
| `get_element_state` | `element_id` (str), `attribute?` (str) | Returns visibility, enabled state, checked state, text, value, or a specific attribute. | JSON value |
| `get_element_box` | `element_id` (str) | Returns bounding box, z-index context, occlusion hints, and viewport intersection. | JSON object |
| `get_computed_style` | `element_id` (str), `properties?` (array) | Returns selected computed style values for layout/visibility debugging. | JSON object |
| `get_aom_snapshot` | `page_id` (str), `include_hidden?` (bool) | Extracts the accessibility tree using deterministic stable IDs where possible. | JSON array |
| `get_dom_snapshot` | `page_id` (str), `scope?` (enum: minimal, interactive, full) | Returns serialized DOM or a focused subset. | JSON/tree payload |
| `get_dom_diff` | `page_id` (str), `previous_state_id` (str) | Returns nodes and attributes mutated since a prior snapshot. | JSON array |
| `get_state_handle` | `page_id` (str), `kinds?` (array: dom, aom, visual, network, console) | Captures and caches current state server-side, returning opaque handles for later diff or retrieval. | Handle bundle |
| `hydrate_state_slice` | `handle_id` (str), `slice` (obj) | Materializes only a requested subset of a cached state object such as one frame, one node subtree, one issue cluster, or one viewport region. | JSON payload |
| `list_frames` | `page_id` (str) | Lists iframes, origins, names, and frame IDs. | JSON array |
| `switch_frame` | `frame_id` (str) | Switches tool focus to an iframe. | Frame metadata |
| `query_shadow_dom` | `host_element_id` (str), `selector` (str) | Queries elements inside a shadow root. | Matching element descriptors |
| `get_shadow_root` | `element_id` (str) | Returns the shadow root tree of a web component host element. | JSON tree or null |
| `extract_text` | `page_id` (str), `scope?` (enum: visible, full, element), `element_id?` (str) | Extracts readable text content from the page or a specific element (stripping markup). | Text string |
| `extract_table_data` | `element_id` (str) | Extracts structured data from an HTML table element. | JSON array of rows |
| `get_selection` | `page_id` (str) | Returns the current text selection range and content. | JSON object |

### 5.3. Visual and Accessibility Telemetry

| Tool Name | Parameters | Description | Returns |
| :--- | :--- | :--- | :--- |
| `take_screenshot` | `target` (enum: viewport, full_page, element), `page_id?` (str), `element_id?` (str), `return_method` (enum: base64, disk) | Captures rendered output. | Base64 string or absolute path |
| `get_annotated_screenshot` | `page_id` (str), `viewport_only?` (bool) | Captures a screenshot with numbered overlays for actionable elements. | Base64 image string |
| `get_visual_digest` | `page_id` (str), `mode?` (enum: compact, standard, full) | Returns a low-token visual summary: occlusions, sticky UI, off-screen actionable controls, modal presence, and screenshot handle references. | JSON object |
| `record_video` | `context_id` (str), `action` (enum: start, stop) | Starts or stops video recording for user-flow evidence. | Status plus artifact path if stopped |
| `start_trace` | `context_id` (str), `options?` (obj) | Starts Playwright tracing with screenshots/snapshots/sources as configured. | Trace session ID |
| `stop_trace` | `context_id` (str) | Stops trace capture and exports artifact. | Absolute trace path |
| `get_visual_diff` | `baseline_path` (str), `candidate_path` (str), `threshold?` (float) | Computes a visual diff for regression detection. | Diff summary and artifact path |
| `compare_viewports` | `page_id` (str), `profiles` (array), `mode?` (enum: compact, standard, full) | Runs the same page through multiple desktop/mobile viewport profiles and returns only material layout/interaction deltas. | JSON object |
| `run_accessibility_audit` | `page_id` (str), `scope?` (enum: page, element) | Runs accessibility checks and returns violations with affected nodes. | JSON report |
| `get_issue_digest` | `page_id` (str), `sources` (array: visual, accessibility, console, network, performance), `limit?` (int) | Collapses raw findings into deduplicated, severity-ranked issues with evidence handles. | JSON array |
| `get_focus_order` | `page_id` (str) | Returns the current tab order and focusable elements. | JSON array |
| `get_live_regions` | `page_id` (str) | Summarizes active ARIA live regions and recent announcements where observable. | JSON array |

### 5.4. Interaction and Input Simulation

**Note:** All tools in this section that accept `element_id` also accept an optional `target` object (`{selector?, text?, role?, label?, nth?}`) for inline server-side element resolution, eliminating the need for a prior `find_elements` call. All mutation tools accept an optional `observe` parameter (default: `auto`) that bundles a compact post-action digest into the response.

| Tool Name | Parameters | Description | Returns |
| :--- | :--- | :--- | :--- |
| `click` | `element_id` or `target` (obj), `button?` (enum: left, middle, right), `click_count?` (int), `timeout_ms?` (int), `observe?` (enum: off, auto, full) | Clicks a target and surfaces user-relevant failure reasons. | Action result bundle with semantic outcome + optional post-action digest |
| `tap` | `element_id` or `target` (obj), `observe?` | Performs a touch interaction for mobile-specific flows. | Action result + optional digest |
| `type_text` | `element_id` or `target` (obj), `text` (str), `clear_first?` (bool), `observe?` | Focuses an input and types text. | Action result + optional digest |
| `press_key` | `page_id` (str), `key` (str), `observe?` | Simulates a raw keyboard press. | Action result + optional digest |
| `press_key_chord` | `page_id` (str), `keys` (array of str), `observe?` | Sends multi-key shortcuts like `Ctrl+L` or `Shift+Tab`. | Action result + optional digest |
| `hover` | `element_id` or `target` (obj) | Triggers hover states. | Success boolean |
| `drag_and_drop` | `source_element_id` or `source_target` (obj), `target_element_id` or `dest_target` (obj), `observe?` | Handles drag/drop workflows. | Action result + optional digest |
| `select_dropdown` | `element_id` or `target` (obj), `value` (str), `observe?` | Selects an option in a native `<select>`. | Action result + optional digest |
| `set_checkbox` | `element_id` or `target` (obj), `checked` (bool), `observe?` | Sets a checkbox/radio to a target state. | Action result + optional digest |
| `upload_file` | `element_id` or `target` (obj), `file_path` (str), `observe?` | Uploads one or more files. | Action result + optional digest |
| `handle_dialog` | `action` (enum: accept, dismiss), `prompt_text?` (str) | Handles alert/confirm/prompt dialogs. | Dialog metadata and result |
| `swipe` | `page_id` (str), `start` (obj: x, y), `end` (obj: x, y), `duration_ms?` (int), `observe?` | Performs a touch swipe gesture for mobile carousels, drawers, pull-to-refresh. | Action result + optional digest |
| `long_press` | `element_id` or `target` (obj), `duration_ms?` (int), `observe?` | Performs a touch long-press for context menus and mobile interactions. | Action result + optional digest |
| `pinch_zoom` | `page_id` (str), `center` (obj: x, y), `scale_factor` (float), `observe?` | Simulates pinch-to-zoom gesture where supported. | Action result + optional digest |
| `mouse_move` | `page_id` (str), `x` (int), `y` (int) | Moves the mouse to absolute viewport coordinates. | Success boolean |
| `mouse_click_at` | `page_id` (str), `x` (int), `y` (int), `button?` (enum), `observe?` | Clicks at absolute coordinates (for canvas, SVGs, custom widgets without DOM targets). | Action result + optional digest |
| `mouse_wheel` | `page_id` (str), `delta_x` (int), `delta_y` (int) | Dispatches wheel events for custom scroll containers and zoom UIs. | Success boolean |
| `scroll_element` | `element_id` or `target` (obj), `direction` (enum: up, down, left, right), `amount_px?` (int) | Scrolls within an overflow container (not the page viewport). | Updated scroll position |
| `clipboard_read` | `page_id` (str) | Reads current clipboard contents (requires permission grant in context). | Clipboard text/data |
| `clipboard_write` | `page_id` (str), `text` (str) | Writes text to clipboard for paste-driven workflows. | Success boolean |
| `fill_contenteditable` | `element_id` or `target` (obj), `html` (str) | Inserts HTML into contenteditable/rich-text-editor elements. | Success boolean |
| `select_date` | `element_id` or `target` (obj), `value` (str: ISO date) | Sets the value of native date/datetime inputs. | Success boolean |
| `set_input_value` | `element_id` or `target` (obj), `value` (str) | Programmatically sets input value and dispatches change/input events (for sliders, color pickers, date inputs). | Success boolean |
| `fill_form` | `page_id` (str), `form_target?` (element_id or target obj), `fields` (array of `{target: obj, value: str, action?: enum}`), `submit?` (bool), `observe?` | Fills multiple form fields in a single call. Each field entry identifies the input and provides the value. Optionally submits the form after filling. Server resolves each field, fills them in order, and returns per-field success plus optional post-submit digest. | JSON object with per-field results + optional digest |
| `wait_for` | `target` (obj), `state` (enum: attached, visible, hidden, enabled, disabled, url, networkidle, download, dialog, text_content, response, element_count), `observe?` | Waits for a condition without forcing sleeps. | Success boolean, observed state, + optional digest |
| `run_action_and_describe` | `action` (obj), `expect?` (obj), `mode?` (enum: compact, standard, full) | Executes one interaction and returns the minimal useful post-action summary: navigation change, DOM delta, blockers, new errors, and evidence handles. | JSON object |

### 5.5. Network, Storage, and Runtime Instrumentation

**Note:** All list/log tools in this section support cursor-based pagination via optional `cursor` and `limit` parameters. Initial responses include a `next_cursor` token when results are truncated. Tools also support a `since` parameter (checkpoint name or timestamp) to scope results to a time window, eliminating full-log re-reads.

| Tool Name | Parameters | Description | Returns |
| :--- | :--- | :--- | :--- |
| `get_network_traffic` | `page_id` (str), `filters?` (obj), `since?` (str), `cursor?` (str), `limit?` (int) | Returns intercepted request/response metadata with optional filtering. | JSON array with `next_cursor` |
| `get_request_detail` | `request_id` (str) | Returns headers, body summary, initiator, timing, redirect chain, and response metadata. | JSON object |
| `get_response_body` | `request_id` (str), `encoding?` (enum: text, base64) | Retrieves response bodies for selected requests subject to size limits. | Body payload |
| `get_network_digest` | `page_id` (str), `window?` (obj), `mode?` (enum: compact, standard, full) | Returns a condensed summary of network behavior: failures, redirects, auth events, API calls, polling, websocket activity, and unusual payloads. | JSON object |
| `list_websockets` | `page_id` (str) | Lists active WebSocket connections. | JSON array |
| `get_websocket_messages` | `socket_id` (str), `limit?` (int) | Returns captured WebSocket frames. | JSON array |
| `set_headers` | `context_id` (str), `headers` (obj) | Injects custom HTTP headers for subsequent requests. | Success boolean |
| `set_user_agent` | `context_id` (str), `user_agent` (str) | Overrides user agent for device/browser simulation. | Success boolean |
| `emulate_network` | `context_id` (str), `profile` (enum or obj) | Simulates offline/slow network conditions. | Effective throttling config |
| `block_routes` | `context_id` (str), `rules` (array) | Blocks or aborts matching requests. | Success boolean |
| `mock_routes` | `context_id` (str), `rules` (array) | Fulfills matching requests with synthetic responses. | Success boolean |
| `get_cookies` | `context_id` (str), `urls?` (array of str) | Retrieves all cookies, including relevant flags. | JSON array |
| `set_cookie` | `context_id` (str), `cookie` (obj) | Injects or modifies a cookie. | Success boolean |
| `clear_cookies` | `context_id` (str) | Clears cookies for the context. | Success boolean |
| `manage_storage` | `page_id` (str), `action` (enum: get, set, remove, clear), `type` (enum: local, session), `key?`, `value?` | Reads or manipulates Web Storage. | JSON or boolean |
| `get_indexeddb_summary` | `page_id` (str) | Returns database/store names and lightweight entry counts. | JSON object |
| `get_console_logs` | `page_id` (str), `level?` (enum), `since?` (str), `cursor?` (str), `limit?` (int) | Returns browser console messages and stack locations. | JSON array with `next_cursor` |
| `get_page_errors` | `page_id` (str), `since?` (str), `cursor?` (str), `limit?` (int) | Returns uncaught exceptions and unhandled promise rejections. | JSON array with `next_cursor` |
| `get_runtime_digest` | `page_id` (str), `since?` (str), `mode?` (enum: compact, standard, full) | Returns deduplicated client-side runtime issues, grouped by root cause and linked to affected actions or routes. | JSON object |
| `execute_page_js` | `page_id` (str), `script` (str) | Evaluates JavaScript in page context. | Structured result or JS error |
| `execute_local_python` | `script` (str), `context_id` (str) | Executes local Python for heavy parsing or analysis under strict sandbox controls. | String or JSON output |

### 5.6. Performance, Metrics, and Resource Analysis

| Tool Name | Parameters | Description | Returns |
| :--- | :--- | :--- | :--- |
| `get_performance_metrics` | `page_id` (str) | Returns Core Web Vitals (LCP, FID, CLS, INP), navigation timing, and resource timing summaries. | JSON object |
| `get_memory_usage` | `page_id` (str) | Returns JS heap size and usage from `performance.measureUserAgentSpecificMemory()` where available. | JSON object |
| `get_resource_summary` | `page_id` (str) | Summarizes loaded resources by type (script, stylesheet, image, font, xhr, fetch) with sizes and timing. | JSON array |
| `get_coverage` | `page_id` (str), `type` (enum: js, css) | Returns JS/CSS coverage data showing used vs unused bytes. | JSON array |
| `capture_performance_timeline` | `page_id` (str), `duration_ms` (int) | Captures performance observer entries (long tasks, layout shifts, paints) over a time window. | JSON array |

### 5.7. Service Workers, Web Workers, and PWA

| Tool Name | Parameters | Description | Returns |
| :--- | :--- | :--- | :--- |
| `list_service_workers` | `context_id` (str) | Lists registered service workers, their status, and scope. | JSON array |
| `unregister_service_worker` | `context_id` (str), `scope` (str) | Unregisters a service worker to test uncached behavior. | Success boolean |
| `list_web_workers` | `page_id` (str) | Lists active web workers and shared workers. | JSON array |
| `evaluate_worker` | `worker_id` (str), `script` (str) | Evaluates JavaScript inside a web/service worker context. | Structured result |
| `get_cache_storage` | `page_id` (str) | Lists Cache API entries and their metadata. | JSON array |
| `clear_cache_storage` | `page_id` (str), `cache_name?` (str) | Clears Cache API storage. | Success boolean |
| `get_manifest` | `page_id` (str) | Reads and parses the web app manifest. | JSON object or null |

### 5.8. Security Header and Policy Inspection

| Tool Name | Parameters | Description | Returns |
| :--- | :--- | :--- | :--- |
| `get_security_headers` | `page_id` (str) | Returns security-relevant response headers (CSP, HSTS, X-Frame-Options, CORS, Permissions-Policy, etc.) for the main document. | JSON object |
| `get_csp_violations` | `page_id` (str) | Returns Content Security Policy violation reports captured during the session. | JSON array |
| `get_mixed_content` | `page_id` (str) | Identifies mixed-content (HTTP resources loaded on HTTPS pages) issues. | JSON array |
| `get_certificate_info` | `page_id` (str) | Returns TLS certificate details for the current page (subject, issuer, validity, protocol version). | JSON object |
| `check_cors` | `page_id` (str), `url` (str), `method?` (str) | Performs a preflight check and reports CORS policy for a target URL. | JSON object |

### 5.9. Canvas, Media, and Embedded Content

| Tool Name | Parameters | Description | Returns |
| :--- | :--- | :--- | :--- |
| `capture_canvas` | `element_id` (str), `format?` (enum: png, jpeg) | Extracts pixel data from a `<canvas>` element as an image. | Base64 image string or file path |
| `get_media_state` | `element_id` (str) | Returns playback state, duration, current time, volume, muted, buffered ranges for audio/video elements. | JSON object |
| `control_media` | `element_id` (str), `action` (enum: play, pause, seek, mute, unmute), `value?` (float) | Controls audio/video playback. | Success boolean |
| `mock_media_devices` | `context_id` (str), `config` (obj) | Configures fake camera/microphone streams for WebRTC or getUserMedia testing. | Success boolean |
| `get_pdf_content` | `page_id` (str) | Extracts text content from an embedded PDF viewer or generates a PDF of the current page. | Text string or file path |
| `print_to_pdf` | `page_id` (str), `options?` (obj) | Generates a PDF rendering of the page. | Absolute file path |

### 5.10. Browser Notifications and Permissions

| Tool Name | Parameters | Description | Returns |
| :--- | :--- | :--- | :--- |
| `get_pending_notifications` | `page_id` (str) | Returns browser notifications triggered via the Notifications API during the session. | JSON array |
| `set_permission` | `context_id` (str), `permission` (str), `state` (enum: granted, denied, prompt) | Dynamically sets permission state (geolocation, notifications, clipboard-read, camera, microphone, etc.). | Success boolean |
| `update_geolocation` | `context_id` (str), `latitude` (float), `longitude` (float), `accuracy?` (float) | Dynamically updates geolocation mid-session (complementing initial context config). | Success boolean |

### 5.11. CDP / Low-Level Protocol Access

| Tool Name | Parameters | Description | Returns |
| :--- | :--- | :--- | :--- |
| `send_cdp_command` | `page_id` (str), `method` (str), `params?` (obj) | Sends a raw Chrome DevTools Protocol command for capabilities not covered by higher-level tools. | CDP response object |
| `subscribe_cdp_events` | `page_id` (str), `events` (array of str) | Subscribes to CDP event domains and buffers events for later retrieval. | Subscription ID |
| `get_cdp_events` | `subscription_id` (str), `limit?` (int) | Retrieves buffered CDP events from a subscription. | JSON array |

### 5.12. Session Configuration and Batch Execution

| Tool Name | Parameters | Description | Returns |
| :--- | :--- | :--- | :--- |
| `configure_session` | `defaults?` (obj: `{mode?, max_list_length?, exclude_fields?, binary_mode?, observe_default?, checkpoint_auto?}`), `proactive_events?` (bool) | Sets session-level response defaults. `mode` sets default verbosity for all tools. `max_list_length` caps ranked lists globally. `binary_mode` controls inline vs handle for binary data. `observe_default` sets the default `observe` behavior for all mutation tools. `proactive_events` enables automatic event attachment on every response. `checkpoint_auto` enables automatic pre-mutation state snapshots. | Effective session config |
| `create_checkpoint` | `page_id` (str), `name` (str), `kinds?` (array: dom, aom, visual, network, console) | Creates a named state checkpoint that persists until explicitly released or the context is destroyed. Named checkpoints are exempt from handle TTL eviction. | Checkpoint ID |
| `release_checkpoint` | `checkpoint_id` (str) | Releases a named checkpoint and frees associated cached state. | Success boolean |
| `diff_since_checkpoint` | `page_id` (str), `checkpoint_name` (str), `kinds?` (array), `mode?` (enum: compact, standard, full) | Returns all changes across the requested signal kinds since the named checkpoint was taken. | JSON diff object |
| `run_steps` | `page_id` (str), `steps` (array of action objects), `stop_on_failure?` (bool, default: true), `observe?` (enum: final_only, each, off) | Executes a sequence of actions server-side in a single round-trip. Each step is an action object matching an interaction tool's parameters (e.g., `{tool: "click", target: {role: "button", text: "Submit"}}`). Returns per-step success/failure plus the post-sequence digest. With `observe: each`, includes a mini-digest per step for debugging; with `observe: final_only` (default), only the final state. Collapses common multi-step flows (navigate → fill form → submit → verify) from N round-trips to 1. | JSON object with step results + digest |
| `get_cache_stats` | `context_id` (str) | Returns diagnostic information about active handles, named checkpoints, memory usage, and eviction counts. | JSON object |

### 5.13. DNS, Hosts, and Network Identity

| Tool Name | Parameters | Description | Returns |
| :--- | :--- | :--- | :--- |
| `set_host_overrides` | `context_id` (str), `mappings` (obj: hostname→IP) | Maps hostnames to specific IPs for testing multi-tenant apps, split DNS, or CTF challenges on local infra. | Success boolean |
| `get_dns_resolution` | `page_id` (str), `hostname` (str) | Reports how a hostname was resolved during page load (useful for SSRF and DNS rebinding CTF challenges). | JSON object |

### 5.14. Downloads, Evidence, and Reporting

| Tool Name | Parameters | Description | Returns |
| :--- | :--- | :--- | :--- |
| `intercept_download` | `page_id` (str), `trigger?` (obj) | Waits for and captures a file download. | Absolute file path plus metadata |
| `list_artifacts` | `context_id` (str) | Lists screenshots, downloads, traces, videos, and reports tied to the session. | JSON array |
| `export_har` | `context_id` (str) | Exports captured HTTP traffic in HAR format. | Absolute file path |
| `generate_report` | `context_id` (str), `format` (enum: json, markdown) | Produces a compact run report with actions, errors, and artifacts. | Report payload or file path |

## 6. Required Behavioral Semantics

### 6.1. Stable Element Identity
- Element IDs returned by discovery tools should remain stable across minor rerenders whenever feasible
- If stability cannot be maintained, responses must include re-identification hints such as role, text, label, and selector candidates

### 6.2. Human-Relevant Action Validation
After interactions, the server should optionally verify:
- URL change
- DOM mutation
- focus change
- dialog appearance
- network activity
- toast/error banner appearance
- form validation state
- download trigger

This avoids reporting false positives where a low-level click occurred but the UI did not actually advance.

Action-oriented tools should default to returning a compact result bundle containing:
- whether the action succeeded at the browser level
- whether the UI outcome matched the likely human expectation
- the top 1-5 material changes since the prior state
- any newly introduced blockers, errors, dialogs, or overlays
- handles to deeper artifacts rather than raw payloads

### 6.3. Mobile-First Interaction Support
The system must handle:
- touch interactions including tap, swipe, long-press, and pinch-zoom
- responsive menus and hamburger navigation
- virtual keyboard-sensitive layouts where observable
- fixed headers/footers overlapping controls
- mobile-specific navigation drawers and gesture-driven surfaces where Playwright support exists
- orientation changes (portrait ↔ landscape)
- safe area insets for notched device emulation
- bottom sheet and app-banner dismissal patterns

### 6.4. Shadow DOM and Web Component Traversal
- Discovery and interaction tools must pierce open shadow roots transparently
- `query_shadow_dom` provides explicit access when needed for targeted queries
- Element IDs must work consistently for elements inside shadow trees
- Nested shadow DOM (shadow root inside shadow root) must be traversable

### 6.5. Multi-Window and Popup Coordination
- The server must automatically detect and track `window.open()` popups, `target="_blank"` navigations, and auth redirect windows
- `list_pages` must include popup origin information and opener relationships
- The agent must be able to switch between windows and coordinate cross-window flows (e.g., OAuth popups)
- `beforeunload` dialogs must be interceptable and handleable

### 6.6. Navigation Interception and Redirect Awareness
- Navigation tools must report the full redirect chain (all intermediate URLs, status codes, and headers)
- The server should support intercepting navigations before they execute (for testing link targets, form actions, etc.)
- `wait_for` with `state: navigation` should support matching on URL patterns, not just exact URLs

## 7. Security, Sandbox, and CTF-Specific Constraints
The platform must assume hostile pages, malicious scripts, SSRF attempts, prompt-injection attempts, and intentional resource abuse.

### 7.1. Container Segregation
- `mcp-server`: non-root, minimal Linux capabilities, read-only base image where practical
- `browser-node`: isolated browser execution container exposing only the required automation port
- Shared artifact volume only for evidence output

### 7.2. Egress Policy
- Deny access to localhost, RFC1918 ranges, cloud metadata endpoints, and other sensitive internal targets by default
- Support explicit allowlists for approved test ranges and challenge infrastructure
- Log blocked requests as security-relevant telemetry

### 7.3. Dangerous Capability Controls
- `execute_local_python` must run in a subprocess with strict timeout, memory, CPU, and filesystem restrictions
- `execute_page_js` should be allowed, but the server must clearly distinguish page-context execution from host-context execution
- Request interception and mocking must be isolated to the current context
- File upload paths must be restricted to approved directories

### 7.4. CTF-Oriented Support
The MCP must support workflows common in web exploitation labs:
- manual cookie and token injection
- custom header manipulation
- response body capture (including binary payloads)
- hidden form and DOM inspection (hidden inputs, `display:none` elements, HTML comments)
- iframe and cross-origin challenge navigation visibility where browser policy permits
- download interception for recovered flags/files
- artifact export for proof of exploit
- DNS rebinding detection and host override for split-horizon challenges
- WebSocket message inspection and injection for real-time challenge protocols
- JavaScript deobfuscation support via `execute_page_js` for analyzing packed/obfuscated client-side code
- CSP and CORS policy inspection for identifying misconfigurations
- Certificate pinning bypass through `ignore_https_errors` for testing against lab infrastructure
- Request replay and modification through route mocking for injection testing
- Source map detection and retrieval for analyzing minified application code
- HTML comment extraction for hidden flags and information disclosure
- Base64/encoding utility tools or delegation to `execute_page_js` for decoding challenge payloads

### 7.5. Resource and Abuse Limits
- Maximum concurrent contexts per session (configurable, default 5)
- Maximum pages per context (configurable, default 20)
- Maximum total memory per browser node (enforced via container limits)
- Per-tool timeout defaults with agent-overridable maximums
- Rate limiting on high-frequency tools (`take_screenshot`, `execute_page_js`) to prevent runaway loops
- Automatic context cleanup after idle timeout

## 8. Error Handling Protocol (Semantic Reporting)
The server must translate raw Playwright and browser exceptions into actionable, semantic context for the agent.

### 8.1. Response Contract
Errors should be structured JSON with:
- machine-readable error code
- concise human-readable explanation
- target entity (`page_id`, `element_id`, `request_id`) where applicable
- retryability hint
- likely causes
- suggested next diagnostic tools

### 8.2. Example
```json
{
  "error_code": "element_obscured",
  "message": "Click failed after 10000ms because element_id '42' is covered by a fixed-position modal.",
  "target": {
    "element_id": "42",
    "page_id": "page-1"
  },
  "retryable": true,
  "likely_causes": [
    "cookie banner",
    "modal dialog",
    "sticky header overlap"
  ],
  "next_steps": [
    "get_element_box",
    "get_annotated_screenshot",
    "handle_dialog"
  ]
}
```

## 9. Performance and Token-Economy Requirements
- Prefer summaries over full payloads by default
- Enforce payload size caps with truncation metadata
- Allow explicit pagination for network logs, console logs, and DOM snapshots
- Cache recent snapshots per page to enable cheap diffs
- Use background event capture for console, request, response, dialog, and download events
- Avoid forcing screenshots or full DOM dumps unless requested or needed for an error path

### 9.1. Default Response Discipline
- Every high-volume tool must define a default compact schema rather than returning arbitrary JSON
- Default responses should cap candidate lists, issue lists, log entries, frame lists, and DOM nodes to a small ranked set with explicit `remaining_count`
- Binary data should never be inlined by default; return artifact handles or file paths unless the caller explicitly requests inline content
- Repeated fields such as long selectors, stack traces, headers, and HTML snippets should be normalized, deduplicated, and referenced by ID inside a response

### 9.2. Server-Side Ranking and Compression
- Discovery tools should rank likely actionable controls by intent, visibility, enabled state, viewport presence, semantic role, and recency of interaction
- Log tools should cluster duplicates and return counts, first/last occurrence, representative sample, and severity
- DOM and accessibility tools should collapse unchanged siblings, boilerplate navigation, repetitive list items, and hidden implementation detail unless explicitly requested
- Network tools should summarize requests by route pattern, status class, initiator type, and anomaly score before exposing individual entries

### 9.3. Delta-First Operation Model
- Tool responses should prefer change sets against the most recent cached state, not complete re-serialization
- The MCP should support comparing current state to named checkpoints via `create_checkpoint` / `diff_since_checkpoint` (§5.12). Checkpoint names like `before_login`, `after_submit`, or `mobile_landscape` allow the agent to reference meaningful points in a test flow
- When `checkpoint_auto` is enabled in session config, the server creates anonymous checkpoints before every mutation, so `observe: auto` responses always contain a diff rather than absolute state
- For viewport comparisons, the default output should be only actionable differences: missing controls, overlap, clipped content, changed focus order, or divergent network/runtime behavior
- The server should automatically suppress "no meaningful change" payloads into a tiny acknowledgement with a checkpoint reference

### 9.4. Observation Bundles
- The MCP should support compound observation passes that gather visual, DOM, accessibility, console, and network signals in one server-side operation and return one compact digest
- Bundle responses should include cross-signal correlation, such as mapping a console error to a broken button or a 401 response to a blocked post-click transition
- The server should maintain ring buffers for recent events and expose summary windows like "since last action" or "since checkpoint" to eliminate full-log polling

### 9.5. Selector and Element Token Efficiency
- Every element descriptor should use a compact canonical schema with short field names or fixed-order tuples if transport cost matters
- Selector generation should be server-side and scored, returning only the best stable selectors plus fallback references
- The server should prefer persistent `element_id`s and `frame_id`s over repeatedly restating selector text
- Annotated screenshots and page digests should share the same numeric element labels so the agent can refer to elements cheaply across tools

### 9.6. Report Generation as Offloaded Summarization
- Final reports, issue lists, and evidence packages should be assembled server-side from stored handles, not reconstructed by the agent from prior tool outputs
- The MCP should provide one-call generation of compact "what happened" summaries for a session, a page, a checkpoint range, or a failed action
- Generated reports should support audience modes such as `agent`, `tester`, and `ctf-proof`, each with different default verbosity and artifact linking

### 9.7. Round-Trip Reduction Targets
The following table quantifies the expected token savings from the batch and composite patterns. These are design targets, not guarantees.

| Common Flow | Without Optimization | With Optimization | Tool Used |
| :--- | :--- | :--- | :--- |
| Click a button | `find_elements` → `click` → `get_page_digest` (3 calls) | `click` with inline `target` + `observe: auto` (1 call) | Inline resolution + observe |
| Fill and submit a 5-field form | 5× `find_elements` + 5× `type_text` + `click` (11 calls) | `fill_form` with `submit: true` + `observe: auto` (1 call) | `fill_form` composite |
| Login → navigate → verify 3 pages | ~15 calls | `run_steps` with 5-7 steps (1 call) | `run_steps` batch |
| Check for errors after action | Explicit `get_console_logs` + `get_page_errors` (2 calls) | Automatic in `_events_since_last_call` (0 calls) | Proactive event attachment |
| Compare pre/post state | `get_state_handle` before + `get_dom_diff` after (2+ calls) | `checkpoint_auto` + `observe: auto` (0 extra calls) | Auto-checkpointing |

The design should target a **3-5x reduction** in average round-trips for standard testing workflows compared to a naive tool-per-action model.

## 10. Evidence and Auditability Requirements
Each session should be able to produce enough evidence for test verification or challenge submission:
- screenshots before and after key actions
- downloadable traces/HAR/videos when enabled
- structured action log with timestamps
- exported errors and console output
- captured downloads and generated reports

Artifacts should be associated with:
- `context_id`
- `page_id`
- timestamp
- action/tool origin

## 11. Minimum Definition of Done
An implementation is not complete unless it can:
- test a normal desktop web login flow end-to-end, including form fills, redirects, and post-login state verification
- test the same flow in a mobile viewport with touch enabled, including swipe and tap interactions
- save and restore authenticated session state across contexts
- inspect and manipulate cookies, headers, local/session storage, and network requests
- handle dialogs, popups, frames (including shadow DOM), uploads, and downloads
- traverse shadow DOM and interact with web component internals
- capture console errors, page exceptions, screenshots, and traces
- extract performance metrics including Core Web Vitals
- inspect security headers and CSP violations
- interact with canvas elements and capture their visual state
- handle service workers and Cache API for PWA testing
- send raw CDP commands for edge cases not covered by higher-level tools
- perform mobile gesture simulation (swipe, pinch-zoom, long-press)
- read and write clipboard contents
- capture and inspect WebSocket traffic
- export reproducible evidence artifacts (screenshots, HAR, traces, videos, reports)
- run safely against intentionally hostile CTF targets under default-deny network policy
- enforce resource limits and automatic cleanup under sustained agent use
- produce compact state digests and post-action summaries that let an agent test flows without repeatedly requesting full DOM, screenshot, network, or console payloads
- execute a multi-step form fill and submit flow in a single `fill_form` call with inline element resolution
- execute a multi-action test sequence via `run_steps` and receive a single consolidated result
- complete a standard 5-field login flow in 1-2 tool calls, not 11+
- resolve elements inline on action tools without requiring prior `find_elements` calls
- configure session-level response defaults so the agent does not pass `mode: compact` on every call
- automatically attach relevant events (errors, dialogs, navigations) to tool responses without agent polling
- create named checkpoints and diff against them across DOM, accessibility, network, and console signals
- paginate all list/log endpoints via cursor tokens without re-reading prior entries

***

This revision provides comprehensive coverage for agent-driven browser automation including: session persistence and authentication flows, full mobile gesture support, shadow DOM traversal, service worker and PWA inspection, security header and CSP analysis, performance metrics and Core Web Vitals, canvas/media introspection, raw CDP access for edge cases, clipboard operations, DNS/host overrides for CTF infrastructure, resource abuse limits, and structured evidence capture for both application testing and CTF challenge verification. The token-optimization layer adds inline element resolution on all action tools, post-action observation bundling, batch step execution, composite form filling, session-level response configuration, proactive event attachment, named checkpoints with diffing, cursor-based pagination, and handle lifecycle management — targeting a 3-5x reduction in agent round-trips for standard testing workflows.
