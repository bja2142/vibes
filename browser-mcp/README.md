# Browser Puppet

Browser Puppet is a Python MCP server that exposes stateful Playwright-driven browser automation for testing, UI validation, and evidence capture.

## Features

- SSE and stdio MCP transports
- Stateful browser contexts and page tracking
- Desktop/mobile emulation support
- Inline target resolution for interaction tools
- Post-action observation bundles and named checkpoints
- DOM, accessibility, console, network, storage, and screenshot tooling
- Batch actions via `fill_form`, `fill_and_click`, `submit_form`, `click_and_wait`, and `run_steps`
- Artifact capture for screenshots, traces, downloads, storage state, and reports
- Compatibility shorthands for legacy `args`/`kwargs` wrappers and loose `target`/`query` payloads

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
playwright install chromium firefox
BROWSER_PUPPET_ARTIFACT_DIR=./artifacts browser-puppet --transport both --host 0.0.0.0 --port 8000
```

Network transport modes:

- `--transport sse` exposes `/sse` and `/messages/`
- `--transport http` exposes `/mcp`
- `--transport both` exposes `/sse`, `/messages/`, and `/mcp`

Logging:

- `--log-level INFO` is the default and logs every MCP tool request
- `--log-level DEBUG` adds tool arguments, summarized results, and failure tracebacks
- `--log-level TRACE` is the deepest troubleshooting mode and is treated as debug-equivalent for the underlying ASGI server
- the same setting can be provided with `BROWSER_PUPPET_LOG_LEVEL`

Local network policy:

- browser contexts allow `localhost`, loopback, private RFC1918 ranges, and link-local targets by default
- set `allow_local_network: false` in the `create_context` profile if you want to re-enable blocking for local targets
- or enable it server-wide with `BROWSER_PUPPET_ALLOW_LOCAL_NETWORK=true`

Browser launch mode:

- browser contexts launch headed by default
- set `headless: true` in the `create_context` profile only when you explicitly want headless mode
- in Docker and Compose, the server starts under Xvfb so headed Playwright has a valid X session

Transient internal tool retry:

- the server retries certain known internal MCP/Playwright bridge failures once before returning an error
- the default retry delay is `100ms`
- configure it with `--transient-retry-delay-ms`
- the same setting can be provided with `BROWSER_PUPPET_TRANSIENT_RETRY_DELAY_MS`

Stale context cleanup:

- the server auto-closes stale browser contexts by default after `3600` seconds of inactivity
- disable server-wide auto-close with `BROWSER_PUPPET_AUTO_CLOSE_STALE_CONTEXTS=false`
- change the stale timeout with `BROWSER_PUPPET_STALE_CONTEXT_TIMEOUT_SECONDS`
- set `persistent_context: true` in the `create_context` profile to exempt a context from stale cleanup
- use `set_context_persistence` later if you need to toggle persistence on an existing context

## Docker

```bash
docker build -t browser-puppet .
docker run --rm -p 8000:8000 -v "$(pwd)/artifacts:/data/artifacts" browser-puppet
```

Container artifact contract:

- inside container: `/data/artifacts`
- configurable via `BROWSER_PUPPET_ARTIFACT_DIR`
- screenshots, downloads, HAR, trace, reports, PDFs, visual diffs, and related artifacts are written under that root
- `upload_text_artifact` and `download_text_artifact` also use that root, but they are limited to lightweight UTF-8 text files

## Docker Compose

```bash
BROWSER_PUPPET_PORT=8001 docker compose up --build
```

If `BROWSER_PUPPET_PORT` is unset, compose defaults to `8000`.
If `BROWSER_PUPPET_TRANSPORT` is unset, compose defaults to `both`.
If `BROWSER_PUPPET_ARTIFACTS_HOST_DIR` is unset, compose bind-mounts `./artifacts` into `/data/artifacts`.
If `BROWSER_PUPPET_LOG_LEVEL` is unset, compose defaults to `INFO`.
