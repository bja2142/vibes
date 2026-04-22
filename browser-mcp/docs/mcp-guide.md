# Browser Puppet MCP Guide

This document explains how agents should use the `browser-puppet` MCP server effectively inside this repository.

It is intentionally more detailed than [AGENTS.md](/home/ben/playground/browser-puppet/AGENTS.md). The root guide is optimized for quick loading. This guide is the full playbook for browser-task execution, tool selection, constraints, and expected behavior.

## What This Server Is

`browser-puppet` is a stateful browser-control MCP server built on Playwright. It exposes browser contexts, pages, discovery tools, mutation tools, diagnostics, evidence capture, and several higher-signal debugging features for frontend and auth-heavy workflows.

This is not a static analysis tool. It is intended for tasks that benefit from observing a real page at runtime.

## When Agents Should Reach For It

Use this MCP when a task requires:

- navigating a live application in a browser
- inspecting rendered DOM rather than source code alone
- verifying interactive behavior
- collecting evidence like screenshots, HAR, trace, PDF, or video
- debugging network, redirect, auth, cookie, storage, service worker, or websocket behavior
- performing browser-side checks such as coverage, CORS diagnostics, certificate details, or media state

Avoid using it when:

- code inspection already answers the question
- the task is purely backend and there is no browser-observable value
- the required outcome is faster and more reliable through direct unit or integration tests

## Connection And Transport

The server supports multiple network transports.

Endpoints:

- streamable HTTP: `http://127.0.0.1:8000/mcp`
- SSE: `http://127.0.0.1:8000/sse`

Transport modes:

- `--transport http`
  Exposes `/mcp`
- `--transport sse`
  Exposes `/sse` and `/messages`
- `--transport both`
  Exposes `/mcp`, `/sse`, and `/messages`
- `--transport stdio`
  Runs as a local stdio MCP server

Guidance:

- For Codex, prefer `/mcp`.
- For clients that explicitly need SSE, use `/sse`.
- If you are unsure and control the server startup, use `--transport both`.
- The server retries certain known transient internal tool failures once before surfacing them. The retry delay defaults to `100ms` and can be configured with `--transient-retry-delay-ms` or `BROWSER_PUPPET_TRANSIENT_RETRY_DELAY_MS`.

## Authorized Browser Parity

For testing against infrastructure you control, prefer the profile-oriented parity flow rather than scattered one-off overrides. This gives you a reproducible browser shape and a direct way to measure what the page actually sees.

Recommended flow:

1. Use `create_context` with explicit profile fields or a `preset`. Chromium contexts now inherit the `chromium_desktop` preset by default, and any explicit profile fields override those defaults.
2. If you already have known-good cookies or origin storage, load them with `profile_state`, `import_browser_profile`, or `load_storage_state`.
3. Align request headers with `set_headers` and, for Chromium contexts, runtime user agent with `set_user_agent`.
4. Open the page under test.
5. Run `get_fingerprint_report` to inspect page-visible runtime properties and the document request headers actually observed by the server.
6. Export the resulting state with `export_browser_profile` if you want to reuse it later.

Supported parity controls in this pass:

- `preset` in `create_context` for browser-specific desktop defaults; Chromium uses `chromium_desktop` unless you override fields
- `headers`, `user_agent`, `locale`, `timezone`, `viewport`, `screen`, `device_scale_factor`, `mobile`, `touch`
- `profile_state` in `create_context` to preload cookies and origin storage
- `import_browser_profile` and `export_browser_profile` for cookie plus local/session storage round-tripping
- `get_fingerprint_report` for page-visible runtime and request-header diagnostics

Limits:

- Persistent user-data-dir style browser profiles are not implemented in this pass.
- Chromium-only runtime overrides still depend on CDP.

## Automation Stealth Defaults

The server applies several measures by default to reduce the fingerprinting distance between automated and human-controlled browsers. These are active out of the box and require no configuration.

### navigator.webdriver masking

Playwright and Chromium normally expose `navigator.webdriver = true` to page JavaScript. The server neutralizes this in two layers:

1. **Launch argument**: Chromium is started with `--disable-blink-features=AutomationControlled`, which prevents the browser from setting the `webdriver` property or showing the automation infobar.
2. **Init script**: Every browser context injects an early init script that redefines `navigator.webdriver` to return `undefined`, matching the behavior of a normal user-launched browser. This fires before any page JavaScript executes.

### Realistic default profile

Chromium contexts now inherit the `chromium_desktop` preset by default, unless you override specific profile fields. That default profile avoids static fingerprint-stable values:

- **Timezone** defaults to the host system timezone (read from `/etc/timezone` or `/etc/localtime` symlink) rather than `UTC`.
- **Viewport** is selected randomly per context from common real-world desktop resolutions (1366x768, 1920x1080, 1536x864, 1440x900, 1280x720) with small random offsets to simulate browser chrome variation.
- **User-Agent** is built dynamically from the installed Playwright Chromium version and the host OS platform, rather than using a hardcoded Chrome version string.

To override any of these, pass explicit values in the `create_context` profile.

### Client Hints consistency

When `set_user_agent` applies a runtime UA override via CDP, the server now also sends matching `userAgentMetadata` in the `Emulation.setUserAgentOverride` call. This keeps `navigator.userAgentData.brands`, `navigator.userAgentData.platform`, and the high-entropy client hints consistent with the overridden UA string.

To provide your own metadata, set `user_agent_metadata` in the context config alongside `user_agent_override`.

### Non-descriptive page globals

Internal page-injected scripts (notification capture, performance timeline) use short non-descriptive global names (`__bp_n`, `__bp_tl`, `__bp_obs`) instead of names that contain the project name. This reduces the surface for naive `window` property enumeration.

### Proxy-based Notification wrapper

The notification capture init script uses a `Proxy` around the native `Notification` constructor instead of a plain function wrapper. This preserves `Notification.toString()` returning `"function Notification() { [native code] }"`, making the interception invisible to `toString()`-based detection.

### Coverage is opt-in

CSS and JS coverage instrumentation via CDP (`CSS.startRuleUsageTracking`, `Profiler.startPreciseCoverage`) is no longer started automatically on every page. Coverage adds measurable overhead that sophisticated detection could identify. To enable it, set `"enable_coverage": true` in the `create_context` profile. The `get_coverage` tool will still call `_ensure_coverage_started` on demand when invoked explicitly.

### Remaining known signals

The following are not currently masked and may still be detectable by advanced fingerprinting:

- WebGL renderer and vendor strings reflect the actual GPU or software renderer, which may differ from a real desktop Chrome.
- Canvas fingerprinting is not randomized.
- The Playwright CDP session itself is detectable via `Runtime.evaluate` timing patterns, though this is rarely checked.
- Font enumeration may differ from a typical desktop install depending on the host or container environment.

## Mental Model

The server is stateful. Most work revolves around:

- a `context_id`
- one or more `page_id` values within that context
- sometimes `element_id`, `worker_id`, `socket_id`, `request_id`, or checkpoint/handle IDs

Think of the flow like this:

1. create a browser context
2. open or discover a page
3. inspect the current state
4. perform targeted actions
5. inspect the result
6. capture artifacts if needed
7. clean up

## Recommended Agent Workflow

### 1. Create Context

Use `create_context` first. This is where you establish browser-level behavior.

Typical reasons to set profile options up front:

- mobile or touch emulation
- locale or timezone changes
- geolocation
- HAR capture
- video capture
- permissions
- initial user agent
- headers or HTTP auth

If you expect later evidence capture, configure it here rather than assuming you can enable it retroactively.

### 2. Open A Page

Use `open_page` to create and navigate a page. `open_page` and `navigate` both accept `timeout_ms`, which now defaults to `30000`. Observation is off by default on `navigate`, so it returns without the post-navigation digest unless you explicitly request observation. Page-scoped responses may also include `issue_notices` when the server has newly observed console errors or failed network requests for that page. Network notices use the form `METHOD ROUTE: CODE`.
The result usually gives you:

- `page_id`
- navigation status
- redirect chain
- a digest of the resulting state

That digest should usually drive your next step.

### 3. Inspect Before Acting

Preferred first reads:

- `get_page_digest`
- `get_page_outline`
- `find_elements`
- `find_interactive_candidates`
- `get_network_digest`
- `get_console_logs`
- `get_page_errors`

If the UI is complex, dynamic, or auth-heavy, inspect first. This reduces brittle interactions and unnecessary retries.

### 4. Interact Carefully

Preferred mutation tools:

- `click`
- `tap`
- `type_text`
- `fill_form`
- `press_key`
- `select_dropdown`
- `set_checkbox`
- `upload_file`
- `wait_for`

Use `observe` where post-action state matters. The mutation tools support an observation model that returns digest and change data after the action.

If a page-scoped wait is in progress and a fresh console error or `4xx/5xx` network response arrives, the server can interrupt the wait and return that issue instead of hanging. This applies to the blocking page tools such as `open_page`, `navigate`, `reload_page`, history navigation, `wait_for`, `click_and_wait`, and `execute_page_js`.

### 5. Capture Evidence When Needed

Use evidence tools when a task benefits from reproducibility or handoff:

- `take_screenshot`
- `get_annotated_screenshot`
- `print_to_pdf`
- `record_video`
- `export_har`
- `start_trace` / `stop_trace`
- `generate_report`

Do not capture everything by default. Artifacts are useful, but they add noise if they are not needed.

### 6. Clean Up

Use:

- `close_page`
- `close_context`
- `close_stale_contexts` when you want an explicit sweep of stale non-persistent contexts

This matters in long-running agent sessions because the server enforces resource limits and now auto-closes non-persistent contexts after one hour of inactivity by default. If a context must survive that sweep, set `persistent_context: true` in `create_context` or flip it later with `set_context_persistence`.

## Tool Families

### Lifecycle And Navigation

Use these to manage contexts and pages:

- `create_context`
- `open_page`
- `navigate`
- `reload_page`
- `go_back`
- `go_forward`
- `list_pages`
- `switch_page`
- `close_page`
- `close_context`

Important notes:

- `list_pages` includes opener metadata and origin.
- popup and multi-window flows should rely on `list_pages` plus `switch_page`.
- redirect-heavy flows should pay attention to `redirect_chain` in navigation and mutation outcomes.

### Discovery And Structural Inspection

Use these to understand the rendered page:

- `get_page_digest`
- `get_page_meta`
- `get_page_outline`
- `find_elements`
- `find_interactive_candidates`
- `get_dom_snapshot`
- `get_aom_snapshot`
- `get_dom_diff`
- `get_state_handle`
- `hydrate_state_slice`
- `list_frames`
- `switch_frame`
- `query_shadow_dom`
- `get_shadow_root`

Guidance:

- `find_elements` is usually the best starting point when you know roughly what you want.
- `find_interactive_candidates` is helpful when you know the user intent but not the exact selector.
- use `query_shadow_dom` and `get_shadow_root` when shadow DOM is involved, rather than trying random CSS selectors blindly.

### Stable Element Identity

The server tries to make discovered elements reusable through `element_id`.

What agents should do:

- prefer `element_id` returned from discovery tools for follow-up actions
- if the UI rerenders and a handle no longer works, rediscover rather than assuming the old selector still matches
- use the descriptor’s `identity` hints to understand how the element was found

Current behavior:

- normal DOM descriptors include selector and query hints
- shadow DOM descriptors include host element linkage, selector, and positional fallback
- identity is stable across minor rerenders when the underlying lookup still resolves
- identity is not guaranteed across major client rerenders or structural replacement

### Interaction

Common actions:

- `click`
- `tap`
- `type_text`
- `press_key`
- `press_key_chord`
- `hover`
- `drag_and_drop`
- `select_dropdown`
- `set_checkbox`
- `upload_file`
- `handle_dialog`
- `swipe`
- `long_press`
- `mouse_move`

`type_text` supports two useful modes:

- targeted input for standard form controls using `target` or `element_id`
- focused-element typing when no target is provided, which is useful for consoles, terminals, and editors that already own focus

For console-style surfaces, use `typing_mode="keystrokes"` so the browser emits real key events instead of using `fill()`. You can tune `keystroke_delay_ms` and `keystroke_jitter_ms` to add a base delay and random per-key variance. If you omit them, browser-puppet chooses randomized millisecond defaults per typing action.
- `mouse_click_at`
- `mouse_wheel`
- `scroll`
- `scroll_element`
- `fill_contenteditable`
- `set_input_value`
- `fill_form`
- `wait_for`

Guidance:

- prefer semantic lookup plus `click` over coordinate clicks when possible
- use `fill_form` for grouped input workflows
- use `wait_for` after transitions that depend on rendering or network completion
- use `mouse_click_at` only when semantic targeting is not realistic

### Runtime, Console, Network, And Security

Use these for debugging:

- `get_network_traffic`
- `get_request_detail`
- `get_response_body`
- `get_network_digest`
- `get_console_logs`
- `get_page_errors`
- `get_runtime_digest`
- `check_cors`
- `get_security_headers`
- `get_csp_violations`
- `get_mixed_content`
- `get_certificate_info`

Guidance:

- start with digest tools before pulling detailed bodies
- use `check_cors` when the browser-observed result matters more than static header inspection
- use `get_request_detail` and `get_response_body` when a specific request or payload is the issue

### Context-Scoped Network Control

These tools affect the context, not just one page:

- `set_host_overrides`
- `block_routes`
- `mock_routes`
- `set_headers`
- `set_http_credentials`
- `set_user_agent`
- `emulate_network`

Important:

- route blocking and route mocking are context-scoped
- host overrides are context-scoped
- advanced runtime network emulation is Chromium-only

Use cases:

- mock a backend response for deterministic UI behavior
- block analytics or third-party noise
- force a hostname to a specific IP
- simulate bandwidth and latency

### Evidence And Artifact Tools

Use these when you need proof, comparison, or handoff:

- `take_screenshot`
- `get_annotated_screenshot`
- `capture_canvas`
- `print_to_pdf`
- `record_video`
- `export_har`
- `start_trace`
- `stop_trace`
- `list_artifacts`
- `generate_report`
- `get_visual_diff`

Guidance:

- `get_visual_diff` expects equal-dimension images
- screenshots are usually enough for simple visual confirmation
- use HAR, trace, or video only when the debugging case justifies the additional noise

### Lightweight Text File Transfer

The server also exposes a narrow file-transfer interface for lightweight text-based exchange inside the context artifact directory.

Tools:

- `list_context_files`
- `upload_text_artifact`
- `download_text_artifact`

This interface is intentionally constrained:

- relative paths only
- confined to the context artifact directory
- UTF-8 text only
- size-limited
- not intended for screenshots, PDFs, videos, archives, or other binary assets

Use cases:

- upload a small fixture, note, or JSON blob for the app or task flow to consume
- download a generated JSON, log, markdown report, or other small text artifact
- inspect what files exist in a context before downloading one

Do not treat this as a general binary file transport. For screenshots and other rich artifacts, rely on the normal artifact paths and the mounted artifact volume.

### Workers, PWA, And Live Runtime Features

Use these for richer application behavior:

- `list_service_workers`
- `unregister_service_worker`
- `list_web_workers`
- `evaluate_worker`
- `get_cache_storage`
- `clear_cache_storage`
- `get_manifest`
- `list_websockets`
- `get_websocket_messages`
- `get_pending_notifications`

These are especially useful for:

- offline-capable apps
- websocket-driven dashboards
- push and notification behavior
- service-worker caching bugs

### Coverage And CDP

Higher-power Chromium-only tooling:

- `send_cdp_command`
- `subscribe_cdp_events`
- `get_cdp_events`
- `get_coverage`
- `pinch_zoom`

Use these when the normal high-level tools are insufficient. Prefer high-level tools first.

## Mutation Observation Semantics

Many mutation tools accept an `observe` parameter.

Meaning:

- `off`
  do the action without pre/post observation bundles
- `auto`
  resolve according to session defaults
- `full`
  capture a richer before-state snapshot
- light-style modes
  return lighter pre/post change information

Practical guidance:

- use `auto` by default
- use `full` for important state transitions
- use `off` only when you are intentionally minimizing overhead

Session defaults can be adjusted with `configure_session`.

## Pagination Contract

List and log style tools should be treated as paginated unless the response shape is obviously not a collection result.

Standard fields:

- `items`
- `next_cursor`
- `remaining_count`

Many tools also include a domain-specific field like:

- `artifacts`
- `websockets`
- `workers`
- `service_workers`
- `notifications`
- `pages`

Guidance for agents:

- prefer `items` when writing generic pagination logic
- use the domain-specific field when writing task-specific logic or presenting results
- if `next_cursor` is non-null, the result is partial

## Credentials And Secrets

Use the built-in credential aliasing instead of embedding secrets in normal text.

Relevant tools:

- `store_credential`
- `list_credentials`
- `delete_credential`

Reference syntax:

- `{{cred:alias_name}}`

Use this in places like:

- `type_text`
- `fill_form`
- `clipboard_write`

Guidance:

- prefer storing credentials once per context
- refer to them by alias in actions
- avoid emitting raw secret values in agent narration or notes

## Shorthand Input Compatibility

The server accepts a compatibility shorthand layer for common structured tool inputs.

Supported patterns:

- legacy wrapped calls using top-level `args` and `kwargs`
- raw selector strings for `target` and `query`, such as `target="#submit"`
- loose locator fields lifted into `target` or `query`, such as `click(page_id=..., text="Submit")`
- `wait_for` calls that omit `state` when the intent is clear

Examples:

```python
find_elements(page_id=page_id, text="Submit")
click(page_id=page_id, target="#submit")
click_and_wait(page_id=page_id, target="#submit", wait_for="navigation")
wait_for(page_id=page_id, text="Saved", timeout_ms=10000)
drag_and_drop(page_id=page_id, source_target="#src", dest_target={"text": "Drop here"})
find_interactive_candidates(page_id=page_id, intent="submit button", text="Submit")
fill_form(page_id=page_id, text="Username", value="alice")
fill_and_click(page_id=page_id, text="Username", value="alice", click_target="#submit")
submit_form(page_id=page_id, target="form.form-stack")
run_action_and_describe(tool="click", page_id=page_id, target="#submit")
press_key_chord(page_id=page_id, keys="Control+K")
```

Guidance:

- prefer the documented full shapes in deterministic integrations
- use the shorthand layer when working with LLM-driven or legacy MCP clients that frequently flatten structured inputs
- if both top-level fields and nested fields are present, the nested object wins

## Local Network Access

By default, browser-puppet allows navigation and requests to local targets from browser contexts.

Allowed by default:

- `localhost`
- loopback addresses such as `127.0.0.1`
- private network ranges such as `10.x.x.x`, `172.16-31.x.x`, and `192.168.x.x`
- link-local addresses

To restore the older restrictive behavior for a context, set `allow_local_network: false` in the `create_context` profile.

Example:

```python
create_context(
  browser="chromium",
  profile={
    "allow_local_network": false,
    "ignore_https_errors": true
  }
)
```

Notes:

- when running in Docker, `localhost` refers to the container, not your host machine
- for host apps, prefer the host LAN IP or explicit `set_host_overrides` mappings

For Chromium-only local app testing on plain `http://` origins that are not already treated as secure by the browser, you can opt a context into the launch flag below:

```python
create_context(
  browser="chromium",
  profile={
    "treat_insecure_origins_as_secure": ["http://10.0.2.15:3000"]
  }
)
```

You can also update an existing Chromium context in place:

```python
set_insecure_origins_as_secure(
  context_id=context_id,
  origins=["http://10.0.2.15:3000"]
)
```

This recreates the Playwright context under the hood, so existing pages are closed and should be reopened.

## Headed Browser Default

Browser contexts launch headed by default.

Guidance:

- this server is intended to run under a valid X session so websites do not see Playwright's headless-specific behavior changes
- in Docker and Compose, browser-puppet starts under Xvfb for that reason
- only set `headless: true` in `create_context(profile=...)` when you explicitly want headless mode

## Browser And Capability Constraints

Several advanced features are capability-gated.

Chromium-only or Chromium-preferred:

- `print_to_pdf`
- `set_user_agent` runtime override
- `emulate_network`
- `pinch_zoom`
- CDP tools
- `get_coverage`
- richer certificate inspection

Limited by Playwright or current implementation:

- denied and prompt permission simulation are limited
- custom CA bundle support is validated and tracked, but not fully wired as a transport trust override
- media device mocking is partially declarative
- identity stability is best-effort, not absolute

Agents should surface these limits clearly rather than pretending success means full fidelity.

## Popup And Multi-Window Guidance

When a flow opens another window or popup:

1. use `list_pages`
2. identify the new page by URL, title, origin, or opener metadata
3. use `switch_page`
4. continue the flow on the correct `page_id`

Relevant page metadata includes:

- `page_id`
- `url`
- `origin`
- `opener_page_id`
- `opener_url`
- `is_active`

This is the preferred way to handle OAuth, SSO, external auth, payment popups, or preview windows.

## Suggested Task Patterns

### Basic Login Flow

1. `create_context`
2. `store_credential`
3. `open_page`
4. `find_elements` or `find_interactive_candidates`
5. `type_text` or `fill_form` with `{{cred:...}}`
6. `click` submit button
7. `wait_for`
8. `get_page_digest` or `get_network_digest`

### Auth Redirect Investigation

1. `create_context`
2. `open_page`
3. perform action that triggers auth
4. inspect `redirect_chain`
5. use `list_pages` if popup-based
6. inspect cookies, headers, console, network, and final digest

### Visual Regression Spot Check

1. `open_page`
2. `take_screenshot`
3. capture second screenshot after change
4. `get_visual_diff`
5. review artifact path and changed ratio

### Websocket Or Live App Debugging

1. `open_page`
2. exercise the app
3. `list_websockets`
4. `get_websocket_messages`
5. correlate with `get_console_logs` and `get_network_traffic`

### PWA And Cache Investigation

1. `open_page`
2. `list_service_workers`
3. `get_manifest`
4. `get_cache_storage`
5. `clear_cache_storage` or `unregister_service_worker` if needed

## Anti-Patterns

Avoid:

- blind clicking without a prior digest or discovery step
- storing raw secrets directly in action calls
- assuming every browser supports every advanced feature
- taking screenshots, HAR, trace, and video all at once without a reason
- using coordinate-based interaction when semantic targeting is available
- treating stale `element_id` handles as permanent truth after major rerenders

## If Something Fails

When a tool fails:

1. inspect the semantic error code
2. check whether the feature is browser-gated
3. re-run discovery to confirm page state
4. confirm whether context-scoped configuration was set early enough
5. capture a screenshot or digest if the page state may have diverged

Useful fallback reads:

- `get_page_digest`
- `get_page_meta`
- `get_console_logs`
- `get_page_errors`
- `get_network_digest`
- `list_pages`

## Minimal Agent Prompt Snippet

If you want to instruct another agent to use this MCP, this is a good compact prompt:

`Use the browser-puppet MCP at http://127.0.0.1:8000/mcp for browser runtime tasks. Start with create_context and open_page, inspect before acting, prefer element_id handles from discovery tools, use observe-capable mutation tools for important state changes, and capture artifacts only when they materially help verify or explain behavior.`

## Page JavaScript Guidance

- `execute_page_js` is for DOM reads and in-page state changes that should finish in the current execution context.
- It now has an explicit `timeout_ms` and returns a structured timeout or script error instead of waiting indefinitely.
- Use `navigate` or `reload_page` instead of causing navigation from `execute_page_js` with calls like `location.reload()` or `location.href = ...`.
