# MCP URL Client Transport (#41) — Design

**Issue:** [#41 — Support MCP servers by URL (HTTP/SSE client transport)](https://github.com/ghbalf/freecad-ai/issues/41)
**Date:** 2026-07-22
**Status:** Approved (design)

## Goal

Let the workbench connect to MCP servers **by URL**, not just by spawning a local
`command` over STDIO. Add two new client transports — a **legacy HTTP+SSE**
client and a **Streamable HTTP** client — selected explicitly per server, with
support for remote `https://` endpoints and arbitrary auth headers.

This is the *client* direction. v0.17.0-alpha added an HTTP/SSE **server**
transport (`SSEServerTransport`); this design adds the symmetric client-side
capability that #41 tracks.

## Approved decisions

| Decision | Choice |
|----------|--------|
| Transport generations | **Both** — legacy HTTP+SSE client *and* Streamable HTTP client |
| Selection | **Explicit selector** (dropdown): Command (stdio) / SSE URL / Streamable HTTP URL. No auto-detect. |
| Connection scope | **Remote https + auth** — any `https://` allowed; plain `http://` only to loopback |
| TLS trust | System CA store by default; **optional custom CA bundle and client cert / key** (mutual TLS) per server |
| Credentials | **Custom headers map** (arbitrary key→value), stored in config JSON like the existing LLM API keys |
| Architecture | **Approach A** — peer transport classes + a factory; shared SSE-parse + correlation helpers; **`StdioClientTransport` left untouched/isolated** |

## Architecture

`mcp/` stays **zero-dependency, stdlib-only**. New transports use
`urllib.request` for HTTP(S) and a hand-rolled `text/event-stream` reader.
Each transport implements the same duck-typed interface `StdioClientTransport`
already exposes, so `MCPClient` swaps transparently:

- `start()`
- `send_request(method, params=None, timeout=…) -> dict`
- `send_notification(method, params=None)`
- `stop()`
- `is_alive` (**property**, matching `StdioClientTransport`)

A `make_client_transport(cfg)` factory is the single place that maps a config
dict to a transport instance.

### Global constraints (apply to every task)

- **Zero external dependencies** — stdlib only (`urllib`, `ssl`, `http`,
  `json`, `threading`, `uuid`). No `requests`/`httpx`/`sse-client`.
- **Backward compatible** — a config with no `transport` key loads as stdio,
  unchanged. Existing `MCPClient(name, command, env, …)` callers keep working.
- **Never hard-import PySide2** — all GUI code goes through `ui/compat.py`.
- **`StdioClientTransport` is not modified** — the two new HTTP transports get
  their own copy of correlation logic via a shared helper; stdio's inline copy
  is left alone.
- **Secrets are never logged** — log statements emit method/id/url only, never
  header values.

## Components & file layout

### `freecad_ai/mcp/transport.py` (modify — additive)

Two module-level helpers:

- `_iter_sse_events(fp) -> Iterator[tuple[str, str]]` — **shared by both HTTP
  transports.** Generator that reads a streaming file object line-by-line and
  yields `(event, data)` tuples. Parses `event:` and `data:` fields, dispatches
  an event on a blank line, defaults the event name to `"message"` when only
  `data:` is present. Ignores comment lines (`:` keepalives).
- `_RequestCorrelator` — **used by `SSEClientTransport` only.** Owns
  `_pending: dict[id, {event, response}]`, `_lock`, `_next_id`. Methods:
  `next_id()`, `register(id) -> Event`, `resolve(msg)` (match by `id`, store
  response, set event), `wait(id, event, timeout) -> dict` (pop + return, raise
  `TimeoutError` on expiry), `fail_all(error_dict)` (unblock every pending waiter
  on stop). Needed because SSE replies arrive **asynchronously on a separate
  reader thread** and must be matched back to the blocked caller. This is the
  same correlation logic stdio implements inline; **stdio keeps its own inline
  copy — not refactored.**

`StreamableHTTPClientTransport` does **not** use `_RequestCorrelator`: its reply
is read **synchronously on the calling thread** (the POST response *is* the
answer), so it only needs a private monotonic id counter and a defensive id
check against the decoded reply — no cross-thread `Event` machinery.

`SSEClientTransport(url, headers=None, *, ssl_context=None, connect_timeout=30)`
— legacy HTTP+SSE client:

- `start()`: open `GET <url>` as a **streaming** response (custom headers,
  `context=ssl_context`) on a daemon reader thread. Block (up to
  `connect_timeout`) until the
  reader sees an `endpoint` event; store the **absolute** POST URL it advertises
  (resolve relative paths against the GET URL). If the stream closes or no
  `endpoint` event arrives in time → raise (so `connect()` fails cleanly).
- reader thread: `for event, data in _iter_sse_events(resp):` — on a `message`
  event, `protocol.decode(data)` → `_RequestCorrelator.resolve(msg)`. Non-JSON
  or unmatched ids are dropped (same tolerance as stdio's read loop).
- `send_request(method, params, timeout)`: `next_id()` → `register` → `POST`
  the JSON-RPC message to the stored endpoint URL (headers, JSON body) → `wait`.
- `send_notification(method, params)`: `POST`, no wait.
- `stop()`: set stopped, close the response object, `fail_all(transport-stopped
  error)`.
- `is_alive` (property): reader thread alive **and** not stopped.

`StreamableHTTPClientTransport(url, headers=None, *, ssl_context=None,
connect_timeout=30)` — single-endpoint client:

- `start()`: no persistent stream — records base state only; connection is
  per-request. (The spec's optional server-initiated GET stream is **out of
  scope for v1**.)
- `send_request(method, params, timeout)`: `POST <url>` (`context=ssl_context`)
  with
  `Accept: application/json, text/event-stream`, custom headers, any stored
  `Mcp-Session-Id`, JSON body. Inspect response `Content-Type`:
  - `application/json` → read whole body, `protocol.decode`, return (correlate
    by id defensively; a single POST has a single logical reply).
  - `text/event-stream` → drive `_iter_sse_events(resp)` **on the calling
    thread** until the frame whose decoded `id` matches this request, then return
    and close the stream.
  - Capture `Mcp-Session-Id` from the `initialize` response headers; echo it on
    every subsequent request.
- `send_notification(method, params)`: `POST`, expect `202 Accepted`, no body
  read.
- `stop()`: set stopped; `fail_all` any in-flight waiter. (Stateless between
  calls — nothing persistent to close.)
- `is_alive` (property): started and not stopped.

**v1 exclusions** (documented, not silently dropped): no server-initiated GET
stream for Streamable HTTP; no SSE auto-reconnect / `Last-Event-ID` resume; no
OAuth. A dropped stream surfaces as a normal connection error the user retries.

### `freecad_ai/mcp/client.py` (modify)

Add the factory and make `MCPClient` transport-injectable:

```python
def make_client_transport(cfg: dict):
    transport = cfg.get("transport", "stdio")
    if transport == "stdio":
        command = [cfg["command"]] + cfg.get("args", [])
        return StdioClientTransport(command, cfg.get("env") or None)
    url = cfg["url"]
    headers = cfg.get("headers") or {}
    _validate_url(url)                      # see Error handling & security
    ssl_context = _build_ssl_context(cfg)   # None unless custom CA / client cert set
    if transport == "sse":
        return SSEClientTransport(url, headers=headers, ssl_context=ssl_context)
    if transport == "http":
        return StreamableHTTPClientTransport(url, headers=headers,
                                             ssl_context=ssl_context)
    raise ValueError(f"unknown MCP transport '{transport}'")


def _build_ssl_context(cfg):
    """Return an ssl.SSLContext for custom CA / client cert, or None.

    None => the transport passes context=None to urlopen, i.e. urllib's
    default context (system CA store). A context is built only when at least
    one of ca_bundle / client_cert is set.
    """
    ca = cfg.get("ca_bundle") or None
    cert = cfg.get("client_cert") or None
    key = cfg.get("client_key") or None
    if not ca and not cert:
        return None
    ctx = ssl.create_default_context(cafile=ca)   # cafile=None => system defaults
    if cert:
        ctx.load_cert_chain(certfile=cert, keyfile=key or None)
    return ctx
```

A missing/unreadable cert path makes `load_cert_chain` raise inside
`_build_ssl_context` → caught by `connect_all`'s `try/except` → that one server
is logged and skipped (same failure path as any other bad config).

`MCPClient.__init__(name, command=None, env=None, *, transport=None,
deferred=True, tool_call_timeout=600)`:
- If `transport` is provided, use it directly.
- Else build a stdio transport from `command`/`env` (preserves the exact current
  signature and behaviour for every existing caller).

`connect()`, `_refresh_tools()`, `call_tool()`, deferred-schema logic — all
**unchanged**; they only touch the duck-typed transport interface.

### `freecad_ai/mcp/manager.py` (modify)

`connect_all` stops building `[command] + args` itself. Per enabled config:

```python
transport = make_client_transport(cfg)
client = MCPClient(cfg["name"], transport=transport,
                   deferred=cfg.get("deferred", True),
                   tool_call_timeout=float(cfg.get("timeout", 600)))
client.connect()
```

A bad URL / unknown transport raises inside `make_client_transport`, is caught
by the existing `try/except` around the connect, logged, and that one server is
skipped — the rest still connect. `only_deferred` filtering is unchanged.

### `freecad_ai/ui/settings_dialog.py` (modify `_AddMCPServerDialog`)

- Add a **Transport** dropdown (top row): `Command (stdio)` / `SSE (URL)` /
  `Streamable HTTP (URL)`.
- On change, show/hide two row-groups:
  - stdio → **Command**, **Args** (as today)
  - sse/http → **URL** field + a small **Headers** editor (2-column key/value
    table with add/remove rows, e.g. `Authorization` | `Bearer …`) + three
    optional **TLS** path fields (**CA bundle**, **Client cert**, **Client
    key**), each a `QLineEdit` with an optional Browse button (`QFileDialog`).
- **Name**, **Tool call timeout**, **Deferred**, **Enabled** rows stay shared.
- The URL row shows an inline warning when the user types a non-loopback
  `http://` URL (mirrors `_validate_url`'s rule).
- Extract the show/hide logic into a testable helper
  `_apply_transport_visibility(transport: str)` (fake-self testable, per the
  helper-extraction convention used elsewhere), so it can be exercised without a
  `QApplication`.
- `get_config()` emits the stdio shape or the url+headers(+tls) shape per the
  selected transport, omitting empty TLS paths. `_populate()` restores dropdown +
  fields when editing an entry.

### Config schema (additive, `freecad_ai/config.py` persistence)

```jsonc
// stdio (unchanged — absent transport defaults to "stdio")
{ "name": "fs", "command": "npx", "args": ["-y", "server-fs", "/tmp"],
  "env": {}, "enabled": true, "deferred": true, "timeout": 600 }

// sse / http (new)
{ "name": "remote", "transport": "sse",              // or "http"
  "url": "https://host/sse",
  "headers": { "Authorization": "Bearer …" },
  // all three TLS fields optional (paths); absent => system CA store, no client cert
  "ca_bundle": "/path/to/ca.pem",
  "client_cert": "/path/to/client.pem",
  "client_key": "/path/to/client.key",
  "enabled": true, "deferred": true, "timeout": 600 }
```

`transport` ∈ `{"stdio","sse","http"}`, default `"stdio"`. `url`, `headers`,
`ca_bundle`, `client_cert`, `client_key` all optional. `command`/`args`/`env`
absent (or ignored) for URL transports; the TLS fields absent (or ignored) for
stdio. No migration — every existing config lacks `transport` and reads as
stdio.

## Data flow

**Connect (both HTTP transports).** `MCPClient.connect()` is unchanged:
`transport.start()` → `send_request("initialize", …)` → `send_notification(
"notifications/initialized")` → `_refresh_tools()`. Only `start()`/`send_request`
internals differ per transport.

**Legacy SSE request.** `send_request` allocates an id, POSTs JSON-RPC to the
advertised endpoint URL, and blocks on an `Event`; the reader thread receives the
matching `message` event over the GET stream and wakes the waiter.

**Streamable HTTP request.** `send_request` POSTs to the single URL; the reply is
read on the calling thread — inline JSON returns immediately, an event-stream is
walked until the matching id. `Mcp-Session-Id` is captured at `initialize` and
echoed thereafter.

## Error handling & security

**`_validate_url(url)`** (in `client.py`, enforced by the factory so the config
path and both transports share one gate):
- `urllib.parse.urlparse`; scheme must be `http`/`https` else `ValueError`.
- `https` → always allowed; TLS trust via the default `ssl` context / system CA
  store, **or** an optional per-server custom CA bundle + client cert / key
  (mutual TLS) built by `_build_ssl_context` (see the factory). No cert **content**
  is logged; only the fact that a custom context is in use.
- `http` → allowed **only** when host ∈ `_LOOPBACK_HOSTS`
  (`127.0.0.1`/`localhost`/`::1`, reuse the existing frozenset in
  `transport.py`). Non-loopback `http://` → `ValueError("plaintext http:// is
  only allowed to localhost; use https://")`. The dialog surfaces the same rule
  inline before save.

**Credentials.** Headers attach to every `urllib.request.Request` for that
server. Secrets live in the config JSON exactly like the existing LLM provider
API keys — no new secret store. Never logged.

**Failure mapping** (nothing upstream changes):
- Connect failures (DNS, refused, TLS error, non-2xx initialize, no `endpoint`
  event before init timeout) → `start()`/`connect()` raise → `connect_all`
  logs + skips that one server.
- Per-request failures (HTTP error status, mid-request stream drop, malformed
  JSON) → `send_request` raises or returns a JSON-RPC error dict → `call_tool`
  already turns that into `MCPToolResult(is_error=True)` → a normal tool-error
  string to the model.
- Timeout contract identical to stdio: `tool_call_timeout` (default 600s) for
  tool calls, 30s for init/list. A hung stream cannot block forever.

**Threading / shutdown.** SSE reader thread is a daemon; `stop()` closes the
response and unblocks pending waiters with a transport-stopped error (mirrors
stdio). Each `MCPClient` owns its transport — no shared mutable state between
servers.

## Testing

All unit tests are stdlib-only and headless — no FreeCAD, no real network.
Pattern mirrors `tests/unit/test_mcp_sse_transport.py` (13 tests, server side).
Every new test is written **RED-first** (TDD). Baseline before this work: 960
green.

Run command (known gotcha — shell `PYTHONPATH` shadows the venv's pluggy):
```
env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py
```

**`tests/unit/test_mcp_sse_client.py`** (new) — legacy SSE client tested against
the workbench's **own** `SSEServerTransport` on a real loopback socket (ephemeral
port, background thread):
- connect handshake: `start()` reads the `endpoint` event → `initialize`
  round-trips → `tools/list` populates tools.
- correlation: response for id N wakes the right waiter; interleaved requests
  don't cross.
- `send_request` timeout when no response arrives.
- `stop()` unblocks a pending waiter with a transport-stopped error and closes
  the stream.
- `is_alive` transitions (false before start, true after, false after stop).
- `start()` raises when the GET stream yields no `endpoint` event before the
  init timeout.

**`tests/unit/test_mcp_streamable_client.py`** (new) — Streamable HTTP client
driven by a purpose-built stdlib `BaseHTTPRequestHandler` stub (the workbench's
own server doesn't speak Streamable):
- POST → **inline `application/json`** response path correlates and returns.
- POST → **`text/event-stream`** response path: walk frames until the matching
  id, return, close stream.
- `Mcp-Session-Id` captured from the `initialize` response header and echoed on
  the next request.
- `send_notification` → `202` with no body read.
- HTTP error status → `send_request` surfaces an error (→ `is_error` tool
  result).

**`tests/unit/test_mcp_client_factory.py`** (new) — `make_client_transport` /
`_validate_url` / `_build_ssl_context`:
- `transport` absent → `StdioClientTransport` (backward-compat guard).
- `"sse"`/`"http"` → the right class, headers **and `ssl_context`** passed
  through.
- `https://…` ok; `http://localhost…` ok; `http://example.com…` raises;
  non-http scheme raises; unknown transport raises.
- `_build_ssl_context`: no TLS fields → `None`; a `ca_bundle` path → an
  `ssl.SSLContext`; a `client_cert`/`client_key` pair → `load_cert_chain`
  applied (use a self-signed cert/key fixture generated once and checked into
  `tests/unit/fixtures/`, or skip if generation isn't practical — an unreadable
  path asserting a raised error covers the failure branch without a real cert).

**`tests/unit/test_mcp_add_server_dialog.py`** (new) — `_AddMCPServerDialog` via
the **unbound-method-with-fake-self** pattern (module-level PySide skip when
neither PySide6 nor PySide2 is importable, matching
`test_settings_dialog_provider_change.py`):
- `_apply_transport_visibility("stdio"|"sse"|"http")` toggles the right
  row-groups.
- `get_config()` emits the stdio shape vs. the url+headers shape.
- `_populate()` round-trips an sse/http entry (dropdown + url + headers).

**Regression guard** (in the factory test): the absent-`transport` config path
still builds an unchanged stdio client.

## Out of scope (v1)

OAuth flows; SSE auto-reconnect with `Last-Event-ID`; the Streamable-HTTP
server-initiated GET stream; auto-detection between SSE and Streamable HTTP.

(Custom CA bundles / client certs were folded **into** v1 — see the TLS trust
decision and `_build_ssl_context`.)
