---
name: browser-puppet
description: "Use this skill whenever a task involves browser automation, web scraping, navigating URLs, clicking elements, filling forms, taking screenshots, testing web UIs, extracting page data, inspecting network traffic, debugging frontend behavior, handling auth flows, capturing evidence from web pages, or any other task that requires controlling or observing a real browser. Triggers on: open a browser, go to a URL, navigate to, click on, fill out, submit form, screenshot, scrape, extract text, web automation, page inspection, DOM, login flow, test UI, check page, capture PDF, record video, accessibility audit, cookie management, network debugging, websocket, service worker, CORS, redirect, popup, iframe, shadow DOM, SPA testing, visual regression, HAR capture, browser trace, page digest, console logs, page errors, fingerprint, stealth browsing."
---

# Browser Puppet MCP Skill

Browser Puppet is a stateful Playwright-backed MCP server for real browser automation, inspection, debugging, and evidence capture. It manages browser contexts, pages, and element handles across a full lifecycle.

**Connection**: `http://127.0.0.1:8000/mcp` (streamable HTTP) or `http://127.0.0.1:8000/sse` (SSE)

## Core Workflow

Every browser task follows this pattern:

1. **`create_context`** — create a browser context (configure profile, emulation, permissions, HAR/video)
2. **`open_page`** — open a URL in that context
3. **Inspect** — use digest/discovery tools before acting
4. **Interact** — click, type, fill, wait
5. **Capture** — screenshots, PDFs, traces, HAR only when needed
6. **Clean up** — `close_page`, `close_context`

**Golden rule**: inspect before acting. Never blindly click.

---

## Complete Tool Reference

### Lifecycle & Navigation

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `create_context` | `browser: str`, `profile?: dict` | Create a browser context. Profile supports: `viewport`, `user_agent`, `locale`, `timezone`, `geolocation`, `mobile`, `touch`, `headers`, `device_scale_factor`, `permissions`, `allow_local_network`, `enable_coverage`, `preset`, `profile_state`, `record_har`, `record_video` |
| `open_page` | `context_id: str`, `url: str`, `wait_until?: str = "load"`, `timeout_ms?: int = 30000` | Open a new page at a URL. Returns `page_id`, navigation status, timeout, redirect chain, and page digest |
| `navigate` | `page_id: str`, `url: str`, `wait_until?: str = "load"`, `timeout_ms?: int = 30000`, `observe?: str = "off"` | Navigate an existing page to a new URL with an explicit navigation timeout. Observation is off by default so navigation returns without the post-navigation digest unless you explicitly request it |
| `reload_page` | `page_id: str`, `ignore_cache?: bool = false` | Reload the current page |
| `go_back` | `page_id: str` | Navigate back in history |
| `go_forward` | `page_id: str` | Navigate forward in history |
| `list_pages` | `context_id: str`, `cursor?: str`, `limit?: int` | List all pages in a context. Returns `page_id`, `url`, `origin`, `opener_page_id`, `is_active` |
| `switch_page` | `page_id: str` | Switch active page (for popups, multi-tab flows) |
| `close_page` | `page_id: str` | Close a page |
| `close_context` | `context_id: str` | Close a browser context and all its pages |

### Discovery & Inspection

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `get_page_digest` | `page_id: str`, `mode?: str = "compact"` | High-level page summary: title, URL, meta, key content. Start here |
| `get_page_meta` | `page_id: str` | Page metadata: title, URL, status, headers |
| `get_page_outline` | `page_id: str` | Structural outline of headings, landmarks, and sections |
| `find_elements` | `page_id: str`, `query: dict` | Find elements by CSS selector, text, role, or other query. Returns `element_id` handles for follow-up actions |
| `find_interactive_candidates` | `page_id: str`, `intent: str`, `filters?: dict`, `limit?: int = 10` | Find interactive elements matching a natural-language intent (e.g., "login button", "email input") |
| `get_element_state` | `element_id: str`, `attribute?: str` | Get element visibility, enabled state, checked state, value, or a specific attribute |
| `get_element_box` | `element_id: str` | Get bounding box (x, y, width, height) of an element |
| `get_computed_style` | `element_id: str`, `properties?: list[str]` | Get computed CSS properties for an element |
| `get_dom_snapshot` | `page_id: str`, `scope?: str = "interactive"` | DOM tree snapshot. Scope: `"interactive"`, `"full"`, etc. |
| `get_aom_snapshot` | `page_id: str`, `include_hidden?: bool = false` | Accessibility Object Model snapshot |
| `get_dom_diff` | `page_id: str`, `previous_state_id: str` | Diff DOM against a previous state handle |
| `get_state_handle` | `page_id: str`, `kinds?: list[str]` | Capture a state snapshot handle for later diffing |
| `hydrate_state_slice` | `handle_id: str`, `slice: dict` | Retrieve a specific slice from a state handle |
| `list_frames` | `page_id: str` | List all frames (iframes) in a page |
| `switch_frame` | `frame_id: str` | Switch to a specific frame for subsequent operations |
| `query_shadow_dom` | `host_element_id: str`, `selector: str` | Query inside a shadow DOM root |
| `get_shadow_root` | `element_id: str` | Get the shadow root of an element |
| `extract_text` | `page_id: str`, `scope?: str = "visible"`, `element_id?: str` | Extract text content from a page or element |
| `extract_table_data` | `element_id: str` | Extract structured table data from a `<table>` element |
| `get_selection` | `page_id: str` | Get current text selection |
| `get_visual_digest` | `page_id: str`, `mode?: str = "compact"` | Visual layout summary of the page |
| `get_viewport_state` | `page_id: str` | Current viewport dimensions and scroll position |

### Interaction

All interaction tools that accept `element_id` or `target` use **inline target resolution**: provide either an `element_id` from a prior discovery call, or a `target` dict (e.g. `{"css": "button.submit"}`, `{"text": "Log in"}`, `{"role": "button", "name": "Submit"}`).

Many interaction tools accept `observe: str = "auto"` which controls post-action observation:
- `"auto"` — use session defaults (recommended)
- `"full"` — capture rich before/after state
- `"off"` — skip observation for speed

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `click` | `page_id?: str`, `element_id?: str`, `target?: dict`, `button?: str = "left"`, `click_count?: int = 1`, `timeout_ms?: int`, `observe?: str = "off"` | Click an element |
| `tap` | `page_id?: str`, `element_id?: str`, `target?: dict`, `observe?: str = "off"` | Tap (touch) an element |
| `type_text` | `text: str`, `page_id?: str`, `element_id?: str`, `target?: dict`, `clear_first?: bool = true`, `typing_mode?: str = "auto"`, `keystroke_delay_ms?: int`, `keystroke_jitter_ms?: int`, `observe?: str = "off"` | Type text into a targeted element or, if no target is supplied, into the currently focused element. Use `typing_mode="keystrokes"` for consoles, terminals, and editors that need real key events. If timing values are omitted, browser-puppet picks random millisecond defaults for delay and jitter. Supports `{{cred:alias}}` for secrets |
| `press_key` | `page_id: str`, `key: str`, `observe?: str = "off"` | Press a keyboard key (e.g., `"Enter"`, `"Tab"`, `"Escape"`) |
| `press_key_chord` | `page_id: str`, `keys: list[str]`, `observe?: str = "off"` | Press a key combination (e.g., `["Control", "a"]`) |
| `hover` | `page_id?: str`, `element_id?: str`, `target?: dict` | Hover over an element |
| `drag_and_drop` | `page_id: str`, `source_element_id?: str`, `source_target?: dict`, `target_element_id?: str`, `dest_target?: dict`, `observe?: str = "off"` | Drag from source to destination |
| `select_dropdown` | `value: str`, `page_id?: str`, `element_id?: str`, `target?: dict`, `observe?: str = "off"` | Select an option from a `<select>` dropdown |
| `set_checkbox` | `checked: bool`, `page_id?: str`, `element_id?: str`, `target?: dict`, `observe?: str = "off"` | Set a checkbox to checked or unchecked |
| `upload_file` | `file_path: str`, `page_id?: str`, `element_id?: str`, `target?: dict`, `observe?: str = "off"` | Upload a file to a file input |
| `fill_form` | `page_id: str`, `fields: list[dict]`, `form_target?: dict`, `submit?: bool = false`, `observe?: str = "off"` | Fill multiple form fields at once. Each field is `{"target": ..., "value": ...}`. Set `submit: true` to auto-submit |
| `fill_and_click` | `page_id: str`, `fields: list[dict]`, `click_target: dict`, `observe?: str = "off"` | Fill one or more fields, then click a submit or continuation target |
| `submit_form` | `page_id?: str`, `element_id?: str`, `target?: dict`, `observe?: str = "off"` | Submit a form via `requestSubmit()` using a form element or an element inside a form |
| `click_and_wait` | `page_id?: str`, `element_id?: str`, `target?: dict`, `wait_for?: str = "navigation"`, `wait_target?: dict` | Click a target and then wait for navigation, network idle, URL, or element state |
| `fill_contenteditable` | `html: str`, `page_id?: str`, `element_id?: str`, `target?: dict` | Set HTML content in a contenteditable element |
| `set_input_value` | `value: str`, `page_id?: str`, `element_id?: str`, `target?: dict` | Programmatically set an input value (no keystrokes) |
| `select_date` | `value: str`, `page_id?: str`, `element_id?: str`, `target?: dict` | Set a date input value |
| `handle_dialog` | `action: str`, `prompt_text?: str` | Accept or dismiss a browser dialog (alert, confirm, prompt) |
| `long_press` | `page_id?: str`, `element_id?: str`, `target?: dict`, `duration_ms?: int = 800`, `observe?: str = "off"` | Long press (touch hold) |
| `swipe` | `page_id: str`, `start: dict`, `end: dict`, `duration_ms?: int = 300`, `observe?: str = "off"` | Swipe gesture. `start`/`end` are `{"x": int, "y": int}` |
| `mouse_move` | `page_id: str`, `x: int`, `y: int` | Move mouse to coordinates |
| `mouse_click_at` | `page_id: str`, `x: int`, `y: int`, `button?: str = "left"`, `observe?: str = "off"` | Click at specific coordinates (use only when semantic targeting fails) |
| `mouse_wheel` | `page_id: str`, `delta_x: int`, `delta_y: int` | Scroll via mouse wheel |
| `scroll` | `page_id: str`, `direction: str`, `amount_px?: int` | Scroll the page in a direction |
| `scroll_element` | `direction: str`, `amount_px?: int = 200`, `page_id?: str`, `element_id?: str`, `target?: dict` | Scroll a specific scrollable element |
| `clipboard_read` | `page_id: str` | Read clipboard contents |
| `clipboard_write` | `page_id: str`, `text: str` | Write to clipboard. Supports `{{cred:alias}}` |
| `wait_for` | `target: dict`, `state: str`, `page_id?: str`, `observe?: str = "off"` | Wait for an element to reach a state (`"visible"`, `"hidden"`, `"attached"`, `"detached"`) |
| `run_steps` | `page_id: str`, `steps: list[dict]`, `stop_on_failure?: bool = true`, `observe?: str = "final_only"` | Run a batch of actions sequentially |
| `run_action_and_describe` | `action: dict`, `expect?: dict`, `mode?: str = "compact"` | Run an action and get a structured description of what changed |

Compatibility shorthand:

- many tools that take `target` or `query` also accept flattened forms such as `text="Submit"` or `target="#submit"`
- wrapped legacy payloads using top-level `args` and `kwargs` are also accepted
- for `wait_for`, `state` is inferred when the target clearly implies an element wait or URL-pattern wait
- `fill_form` accepts a single-field shorthand such as `text="Username", value="alice"`
- `fill_and_click` accepts the same single-field shorthand plus `click_target="#submit"`
- `submit_form` accepts the same target shorthand, such as `target="form.form-stack"` or `text="Save"`
- `click_and_wait` accepts common payloads like `target={"selector": ...}, wait_for="navigation"`
- `run_action_and_describe` accepts a flattened step such as `tool="click", target="#submit"`
- `press_key_chord` accepts string chords such as `keys="Control+K"`

Browser mode:

- contexts launch headed by default
- set `profile={"headless": true}` only when you explicitly want headless mode
- containerized deployments are expected to provide a valid X session via Xvfb

Page issue notices:

- page-scoped tool responses may include `issue_notices` when new console errors or failed network requests were observed on that page
- network notices use the form `METHOD ROUTE: CODE`, for example `GET /api/items: 500`
- console notices are intentionally high level; use `get_console_logs`, `get_page_errors`, or `get_network_traffic` if you need detail
- long-running page waits such as `navigate`, `open_page`, `reload_page`, `wait_for`, `click_and_wait`, and `execute_page_js` can interrupt early when a new console or network issue appears

### Viewport & Emulation

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `resize_viewport` | `page_id: str`, `width: int`, `height: int` | Resize the viewport |
| `set_emulation` | `page_id: str`, `settings: dict` | Apply emulation settings |
| `compare_viewports` | `page_id: str`, `profiles: list[dict]`, `mode?: str = "compact"` | Compare page rendering across different viewport profiles |
| `pinch_zoom` | `page_id: str`, `scale_factor: float`, `x?: int`, `y?: int` | Pinch zoom (Chromium-only) |

### Network & Security

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `get_network_traffic` | `page_id: str`, `filters?: dict`, `since?: str`, `cursor?: str`, `limit?: int` | Get network request log. Paginated |
| `get_network_digest` | `page_id: str`, `window?: dict`, `mode?: str = "compact"` | Summarized network activity |
| `get_request_detail` | `request_id: str` | Full details of a specific request |
| `get_response_body` | `request_id: str`, `encoding?: str = "text"` | Get a response body |
| `get_resource_summary` | `page_id: str` | Summary of loaded resources by type |
| `check_cors` | `page_id: str`, `url: str`, `method?: str = "GET"` | Test CORS for a URL from the page's origin |
| `get_security_headers` | `page_id: str` | Inspect security headers |
| `get_csp_violations` | `page_id: str` | Get Content Security Policy violations |
| `get_mixed_content` | `page_id: str` | Detect mixed HTTP/HTTPS content |
| `get_certificate_info` | `page_id: str` | TLS certificate details |
| `get_dns_resolution` | `page_id: str`, `hostname: str` | DNS resolution result |

### Context-Scoped Network Control

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `set_headers` | `context_id: str`, `headers: dict[str, str]` | Set extra HTTP headers for all requests in the context |
| `set_extra_http_headers` | `page_id: str`, `headers: dict[str, str]` | Set extra HTTP headers for a specific page |
| `set_http_credentials` | `context_id: str`, `username: str`, `password: str` | Set HTTP Basic Auth credentials |
| `set_user_agent` | `context_id: str`, `user_agent: str` | Override user agent (Chromium-only runtime override) |
| `set_host_overrides` | `context_id: str`, `mappings: dict[str, str]` | Map hostnames to IPs |
| `block_routes` | `context_id: str`, `patterns: list[str]` | Block requests matching URL patterns |
| `mock_routes` | `context_id: str`, `routes: list[dict]` | Mock responses for matched routes |
| `emulate_network` | `context_id: str`, `profile?: dict`, `preset?: str` | Simulate network conditions (Chromium-only) |

### Console & Runtime

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `get_console_logs` | `page_id: str`, `level?: str`, `since?: str`, `cursor?: str`, `limit?: int` | Get console log entries. Paginated |
| `get_page_errors` | `page_id: str`, `since?: str`, `cursor?: str`, `limit?: int` | Get uncaught page errors. Paginated |
| `get_runtime_digest` | `page_id: str`, `since?: str`, `mode?: str = "compact"` | Combined runtime summary (console + errors + key events) |
| `execute_page_js` | `page_id: str`, `script: str`, `timeout_ms?: int = 10000` | Execute JavaScript in the page context with an explicit timeout. Use `navigate` or `reload_page` instead of navigation-causing scripts like `location.reload()` |
| `execute_local_python` | `script: str`, `context_id: str`, `timeout_ms?: int = 10000` | Execute Python in the server process. Increase `timeout_ms` for slow scripts (default 10s) |

### Cookies & Storage

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `get_cookies` | `context_id: str`, `urls?: list[str]` | Get cookies, optionally filtered by URLs |
| `set_cookie` | `context_id: str`, `cookie: dict` | Set a cookie |
| `clear_cookies` | `context_id: str` | Clear all cookies |
| `manage_storage` | `page_id: str`, `action: str`, `type: str`, `key?: str`, `value?: str` | Manage localStorage/sessionStorage (get, set, remove, clear) |
| `get_indexeddb_summary` | `page_id: str` | Summarize IndexedDB databases |
| `save_storage_state` | `context_id: str`, `path?: str` | Export cookies + storage to a file |
| `load_storage_state` | `context_id: str`, `state: str | dict` | Import cookies + storage from a file or dict |

### Browser Profile Parity

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `export_browser_profile` | `context_id: str`, `path?: str`, `include_session_storage?: bool = true` | Export full browser profile (cookies + local/session storage) |
| `import_browser_profile` | `context_id: str`, `profile: str | dict` | Import a saved browser profile |
| `get_fingerprint_report` | `page_id: str` | Report page-visible fingerprint: UA, platform, client hints, headers |

### Credentials

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `store_credential` | `context_id: str`, `alias: str`, `value: str` | Store a secret by alias |
| `delete_credential` | `context_id: str`, `alias: str` | Delete a stored credential |
| `list_credentials` | `context_id: str` | List stored credential aliases |
| `generate_totp` | `secret: str`, `algorithm?: str = "SHA1"`, `digits?: int = 6`, `period?: int = 30` | Generate a TOTP code |

### Evidence & Artifacts

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `take_screenshot` | `target: str`, `page_id?: str`, `element_id?: str`, `return_method?: str = "disk"` | Screenshot. Target: `"page"`, `"viewport"`, or element. `return_method`: `"disk"` or `"inline"` |
| `get_annotated_screenshot` | `page_id: str`, `viewport_only?: bool = true` | Screenshot with interactive elements annotated |
| `capture_canvas` | `element_id: str`, `format?: str = "png"`, `return_method?: str = "disk"` | Capture a `<canvas>` element |
| `print_to_pdf` | `page_id: str`, `options?: dict` | Export page as PDF (Chromium-only) |
| `record_video` | `context_id: str`, `action: str` | Control video recording (configured at context creation) |
| `start_trace` | `context_id: str`, `options?: dict` | Start a Playwright trace |
| `stop_trace` | `context_id: str` | Stop trace and save to disk |
| `export_har` | `context_id: str` | Export HAR network archive |
| `get_visual_diff` | `context_id: str`, `baseline_path: str`, `candidate_path: str` | Pixel-diff two equal-dimension images |
| `list_artifacts` | `context_id: str`, `cursor?: str`, `limit?: int` | List all captured artifacts |
| `generate_report` | `context_id: str`, `format?: str = "json"` | Generate a summary report of the session |

### Lightweight Text Transfer

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `list_context_files` | `context_id: str`, `cursor?: str`, `limit?: int`, `subdir?: str` | List files in the context artifact directory |
| `upload_text_artifact` | `context_id: str`, `relative_path: str`, `content: str`, `overwrite?: bool = false` | Upload a small UTF-8 text file |
| `download_text_artifact` | `context_id: str`, `relative_path: str`, `max_bytes?: int` | Download a small text file |

### Workers & PWA

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `list_service_workers` | `context_id: str`, `cursor?: str`, `limit?: int` | List registered service workers |
| `unregister_service_worker` | `context_id: str`, `scope: str` | Unregister a service worker |
| `list_web_workers` | `page_id: str`, `cursor?: str`, `limit?: int` | List web workers |
| `evaluate_worker` | `worker_id: str`, `script: str` | Execute JS in a worker |
| `get_cache_storage` | `page_id: str` | Inspect Cache Storage API contents |
| `clear_cache_storage` | `page_id: str`, `cache_name?: str` | Clear cache storage |
| `get_manifest` | `page_id: str` | Get the web app manifest |
| `list_websockets` | `page_id: str`, `cursor?: str`, `limit?: int` | List active WebSocket connections |
| `get_websocket_messages` | `socket_id: str`, `limit?: int`, `cursor?: str` | Get messages from a WebSocket |
| `get_pending_notifications` | `page_id: str`, `cursor?: str`, `limit?: int` | Get captured Notification API calls |

### Performance & Media

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `get_performance_metrics` | `page_id: str` | Performance timing metrics |
| `get_memory_usage` | `page_id: str` | JS heap memory usage |
| `capture_performance_timeline` | `page_id: str`, `duration_ms: int` | Record a performance timeline for a duration |
| `get_media_state` | `element_id: str` | State of a `<video>` or `<audio>` element |
| `control_media` | `element_id: str`, `action: str`, `value?: float` | Play, pause, seek, set volume on media elements |
| `mock_media_devices` | `context_id: str`, `config: dict` | Mock camera/microphone enumeration |
| `get_pdf_content` | `page_id: str` | Extract text content from an embedded PDF |

### Accessibility

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `run_accessibility_audit` | `page_id: str`, `scope?: str = "page"` | Run an accessibility audit |
| `get_issue_digest` | `page_id: str`, `sources: list[str]`, `limit?: int = 10` | Aggregate issues from multiple sources |
| `get_focus_order` | `page_id: str` | Get tab-order focus sequence |
| `get_live_regions` | `page_id: str` | Get ARIA live regions |

### CDP (Chrome DevTools Protocol)

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `send_cdp_command` | `page_id: str`, `method: str`, `params?: dict` | Send a raw CDP command (Chromium-only) |
| `subscribe_cdp_events` | `page_id: str`, `events: list[str]` | Subscribe to CDP events |
| `get_cdp_events` | `subscription_id: str`, `limit?: int`, `cursor?: str` | Retrieve captured CDP events |
| `get_coverage` | `page_id: str` | Get JS/CSS coverage data (Chromium-only, requires `enable_coverage` in profile) |

### Session & Checkpoints

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `configure_session` | `defaults?: dict`, `proactive_events?: bool` | Configure session defaults (observation mode, etc.) |
| `create_checkpoint` | `page_id: str`, `name: str`, `kinds?: list[str]` | Create a named checkpoint for later diffing |
| `release_checkpoint` | `checkpoint_id: str` | Release a checkpoint's resources |
| `diff_since_checkpoint` | `page_id: str`, `checkpoint_name: str`, `kinds?: list[str]`, `mode?: str = "compact"` | Diff current state against a named checkpoint |
| `get_cache_stats` | `context_id: str` | Internal cache statistics |

### Permissions & Geolocation

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `set_permission` | `context_id: str`, `permission: str`, `state: str` | Set a browser permission (e.g., `"geolocation"`, `"granted"`) |
| `update_geolocation` | `context_id: str`, `latitude: float`, `longitude: float`, `accuracy?: float` | Update geolocation override |

### Downloads

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `intercept_download` | `page_id: str`, `trigger?: dict` | Intercept and capture a file download |

---

## Recommended Workflows

### Navigate and Inspect a Page

```
create_context(browser="chromium")
  -> context_id

open_page(context_id=..., url="https://example.com")
  -> page_id, digest

get_page_digest(page_id=...)
  -> title, URL, content summary

get_page_outline(page_id=...)
  -> heading structure, landmarks
```

### Click a Button

```
find_elements(page_id=..., query={"role": "button", "name": "Submit"})
  -> element_id

click(element_id=...)
  -> observation with post-action state
```

Or with inline target:

```
click(page_id=..., target={"text": "Submit"})
```

### Fill and Submit a Login Form

```
create_context(browser="chromium")
store_credential(context_id=..., alias="password", value="s3cret")
open_page(context_id=..., url="https://app.example.com/login")

fill_form(page_id=..., fields=[
  {"target": {"css": "input[name='email']"}, "value": "user@example.com"},
  {"target": {"css": "input[name='password']"}, "value": "{{cred:password}}"}
], submit=true)

wait_for(page_id=..., target={"css": ".dashboard"}, state="visible")
get_page_digest(page_id=...)
```

### Scrape Text and Table Data

```
open_page(context_id=..., url="https://example.com/data")
extract_text(page_id=..., scope="visible")

find_elements(page_id=..., query={"css": "table.results"})
  -> element_id

extract_table_data(element_id=...)
  -> structured rows and columns
```

### Take a Screenshot

```
# Full page
take_screenshot(target="page", page_id=...)

# Viewport only
take_screenshot(target="viewport", page_id=...)

# Specific element
take_screenshot(target="element", element_id=...)

# Get inline base64 instead of disk path
take_screenshot(target="viewport", page_id=..., return_method="inline")
```

### Handle Dynamic Content / SPAs

```
# After clicking something that triggers async rendering:
click(page_id=..., target={"text": "Load More"})

# Wait for the new content to appear
wait_for(page_id=..., target={"css": ".results-loaded"}, state="visible")

# Re-inspect — don't reuse stale element_ids after major rerenders
find_elements(page_id=..., query={"css": ".result-item"})
```

### Multi-Step Flow with Checkpoints

```
create_checkpoint(page_id=..., name="before_action")

click(page_id=..., target={"text": "Delete Account"})
handle_dialog(action="accept")

diff_since_checkpoint(page_id=..., checkpoint_name="before_action")
  -> what changed in DOM, network, console
```

### Handle Popups / Multi-Window Auth

```
click(page_id=..., target={"text": "Sign in with Google"})

list_pages(context_id=...)
  -> find new page by opener_page_id or URL pattern

switch_page(page_id=<popup_page_id>)

# Complete auth flow in popup
fill_form(page_id=..., fields=[...])
click(page_id=..., target={"text": "Allow"})

# Switch back to original page
switch_page(page_id=<original_page_id>)
wait_for(page_id=..., target={"css": ".logged-in"}, state="visible")
```

### Visual Regression Check

```
open_page(context_id=..., url="https://example.com")
take_screenshot(target="viewport", page_id=...)
  -> baseline_path

# ... make a change ...

take_screenshot(target="viewport", page_id=...)
  -> candidate_path

get_visual_diff(context_id=..., baseline_path=..., candidate_path=...)
  -> diff image path, changed pixel ratio
```

### Debug Network Issues

```
open_page(context_id=..., url="https://example.com")
get_network_digest(page_id=...)
  -> summary of all requests, status codes, timings

# Drill into a specific failed request
get_network_traffic(page_id=..., filters={"status_code_min": 400})
  -> request_ids of failed requests

get_request_detail(request_id=...)
get_response_body(request_id=...)
```

### Block Analytics / Mock API Responses

```
block_routes(context_id=..., patterns=["*google-analytics*", "*hotjar*"])

mock_routes(context_id=..., routes=[
  {"pattern": "**/api/user", "response": {"status": 200, "body": "{\"name\": \"Test\"}", "content_type": "application/json"}}
])

open_page(context_id=..., url="https://example.com")
```

### Test with Iframes

```
open_page(context_id=..., url="https://example.com")
list_frames(page_id=...)
  -> frame_ids with URLs

switch_frame(frame_id=...)
find_elements(page_id=..., query={"css": "button.inside-iframe"})
click(element_id=...)
```

### Shadow DOM Interaction

```
find_elements(page_id=..., query={"css": "my-component"})
  -> host_element_id

get_shadow_root(element_id=<host_element_id>)
query_shadow_dom(host_element_id=..., selector="button.inner")
  -> shadow_element_id

click(element_id=<shadow_element_id>)
```

### Test a Local App (from Docker container)

When browser-puppet runs in a Docker container, `localhost` inside the container refers to the container itself, **not** the host machine. To reach an app running on the host:

1. **Use the host's LAN IP** (e.g., `10.0.2.15`) instead of `localhost` or `127.0.0.1`
2. **Use local network targets directly** — local and private addresses are allowed by default
3. **Accept self-signed certs** — Chromium's `--ignore-certificate-errors` flag handles this via the `ignore_https_errors` profile option

```
# Find the host's LAN IP (run on the host, not in the container):
#   ip -4 addr show | grep 'inet 10\.'
#   -> e.g., 10.0.2.15

create_context(
  browser="chromium",
  profile={
    "ignore_https_errors": true
  }
)

# Use the host LAN IP, NOT localhost
open_page(context_id=..., url="https://10.0.2.15:3000")

get_page_digest(page_id=...)
```

**Key points for local testing:**

- `localhost`, loopback addresses, private network ranges such as `10.x.x.x`, `172.16-31.x.x`, and `192.168.x.x`, plus link-local targets, are allowed by default
- Set **`allow_local_network: false`** if you want to re-enable blocking for those local targets in a specific context
- **`ignore_https_errors: true`** tells the browser to accept self-signed, expired, or otherwise invalid TLS certificates. Essential when your local dev server uses a self-signed cert
- If your app runs on HTTP (no TLS), you can skip `ignore_https_errors`
- If using Docker Compose, you can also use the service name as the hostname if browser-puppet and your app share a Docker network
- **`host.docker.internal`** may work on Docker Desktop (macOS/Windows) as an alias for the host, but is not available on native Linux Docker by default — prefer the actual LAN IP for reliability

**Common failure modes:**

| Symptom | Cause | Fix |
|---------|-------|-----|
| Connection refused to `localhost:3000` | Container's localhost is not the host | Use the host LAN IP (e.g., `10.0.2.15:3000`) |
| Request blocked / network error on `10.x.x.x` | Local network access explicitly disabled | Remove `allow_local_network: false` from the profile |
| SSL/TLS certificate error on `https://192.168.x.x` | Self-signed or dev cert | Add `ignore_https_errors: true` to profile |
| Page loads but API calls fail with network error | App makes requests to `localhost` from JS | API base URL in the app also needs to use the LAN IP, or use `set_host_overrides` to remap |

**Using `set_host_overrides` for local DNS-like routing:**

If the app under test expects a real hostname (e.g., `api.myapp.local`), map it to the host LAN IP:

```
create_context(
  browser="chromium",
  profile={
    "ignore_https_errors": true
  }
)

set_host_overrides(context_id=..., mappings={
  "api.myapp.local": "10.0.2.15",
  "myapp.local": "10.0.2.15"
})

open_page(context_id=..., url="https://myapp.local:3000")
```

---

## Handling Timing & Dynamic Pages

1. **Use `wait_for`** after any action that triggers async rendering, navigation, or network activity
2. **Re-discover elements** after major page changes — stale `element_id` handles will fail after structural rerenders
3. **Use `observe: "full"`** on critical mutations to capture before/after state automatically
4. **Check `redirect_chain`** in navigation results for redirect-heavy flows
5. **Use checkpoints** (`create_checkpoint` / `diff_since_checkpoint`) to detect exactly what changed

## Interpreting Results

- All tools return `dict` results. Look for `status`, `error`, or domain-specific fields
- Discovery tools return `element_id` handles — save these for follow-up interaction
- Paginated results include `next_cursor` and `remaining_count` — loop if needed
- Mutation tools with `observe` return pre/post state bundles
- Screenshot tools return `artifact_path` (disk) or base64 data (inline)

## Known Limitations & Gotchas

- **Chromium-only features**: `print_to_pdf`, `set_user_agent` runtime override, `emulate_network`, `pinch_zoom`, CDP tools, `get_coverage` — these will fail or degrade on Firefox/WebKit
- **Coverage is opt-in**: Set `enable_coverage: true` in the `create_context` profile before using `get_coverage`
- **Video must be configured at context creation**: You cannot retroactively enable video recording
- **HAR capture**: Must be enabled via `record_har` in the `create_context` profile
- **Local network access**: `localhost`, private IPs, and link-local targets are allowed by default. Set `allow_local_network: false` in the profile if you need to re-enable blocking. When running in Docker, `localhost` means the container — use the host's LAN IP instead (see "Test a Local App" workflow above)
- **Self-signed / dev TLS certs**: Set `ignore_https_errors: true` in the `create_context` profile. Without this, pages served with self-signed certs will fail to load. The custom CA bundle config is validated and tracked but not fully wired as a Playwright transport trust override, so `ignore_https_errors` is the reliable path for local dev
- **Element identity**: `element_id` handles are best-effort stable. After major DOM changes, rediscover elements
- **Dialogs**: Use `handle_dialog` promptly — browsers block on unhandled dialogs
- **Permission simulation**: Limited by Playwright's API — denied/prompt states may not fully simulate
- **Visual diff**: Requires equal-dimension images
- **Stealth**: `navigator.webdriver` is masked and UA is dynamic, but WebGL renderer strings, canvas fingerprints, and font enumeration may still differ from real browsers

## Anti-Patterns to Avoid

- Clicking without inspecting first (use `get_page_digest` or `find_elements` before acting)
- Embedding raw secrets in tool calls (use `store_credential` + `{{cred:alias}}`)
- Assuming all browsers support all features (check Chromium-only list above)
- Capturing screenshots + HAR + trace + video on every task (only capture what you need)
- Using `mouse_click_at` coordinates when semantic targeting would work
- Reusing stale `element_id` handles after major page changes
