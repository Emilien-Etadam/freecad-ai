# MCP URL Client Transport (#41) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the workbench connect to MCP servers by URL — a legacy HTTP+SSE client transport and a Streamable HTTP client transport — with remote `https`, custom auth headers, and optional custom CA / client-cert (mTLS).

**Architecture:** Two new duck-typed transport classes sit beside `StdioClientTransport` in `mcp/transport.py`, sharing an SSE frame parser; the SSE client also uses a small request/response correlator. A `make_client_transport(cfg)` factory in `mcp/client.py` maps a config dict to a transport; `MCPClient` becomes transport-injectable and `manager.connect_all` routes through the factory. The settings dialog gains a transport selector plus URL/headers/TLS fields. `StdioClientTransport` is left untouched.

**Tech Stack:** Python 3.11, stdlib only (`urllib.request`, `urllib.parse`, `ssl`, `http`, `json`, `threading`, `uuid`), PySide6/PySide2 via `ui/compat.py`, pytest.

## Global Constraints

- **Zero external dependencies** — stdlib only. No `requests`/`httpx`/`sse-client`.
- **Backward compatible** — a config with no `transport` key loads as stdio. Existing `MCPClient(name, command, env, …)` callers keep working unchanged.
- **`StdioClientTransport` is NOT modified** — the two new HTTP transports get their own correlation via a shared `_RequestCorrelator`; stdio keeps its inline copy.
- **Never hard-import PySide2** — all GUI code goes through `freecad_ai/ui/compat.py` / the module-level widget aliases already in `settings_dialog.py`.
- **Secrets are never logged** — log method/id/url only, never header values or cert contents.
- **Transport interface (duck-typed, matches `StdioClientTransport`):** `start()`, `send_request(method, params=None, timeout=30) -> dict`, `send_notification(method, params=None)`, `stop()`, and `is_alive` as a **property**.
- **Test run command** (shell `PYTHONPATH` shadows the venv's pluggy — always clear it): `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py`
- **Baseline:** 960 tests green before this work. Every new test is written RED-first.
- **Branch:** all work lands on `feat/41-mcp-url-client` (already checked out; the design spec is already committed there).

---

## File Structure

- `freecad_ai/mcp/transport.py` (modify, additive) — new `_iter_sse_events` helper, `_RequestCorrelator` helper, `SSEClientTransport`, `StreamableHTTPClientTransport`. Add `import urllib.request`, `import urllib.parse` at the top.
- `freecad_ai/mcp/client.py` (modify) — `make_client_transport`, `_validate_url`, `_build_ssl_context`; make `MCPClient.__init__` transport-injectable.
- `freecad_ai/mcp/manager.py` (modify) — `connect_all` routes through the factory.
- `freecad_ai/ui/settings_dialog.py` (modify) — `_AddMCPServerDialog` transport selector + URL/headers/TLS fields + `_apply_transport_visibility`; update `SettingsDialog._mcp_list_label`.
- `freecad_ai/CHANGELOG.md` is at repo root `CHANGELOG.md` (modify) — add an "Added" entry.
- Tests (new): `tests/unit/test_mcp_sse_client.py`, `tests/unit/test_mcp_streamable_client.py`, `tests/unit/test_mcp_client_factory.py`, `tests/unit/test_mcp_add_server_dialog.py`.

---

## Task 1: SSE frame parser (`_iter_sse_events`)

**Files:**
- Modify: `freecad_ai/mcp/transport.py` (add helper + imports near top)
- Test: `tests/unit/test_mcp_sse_client.py` (new — start the file with this test class)

**Interfaces:**
- Consumes: nothing.
- Produces: `_iter_sse_events(fp) -> Iterator[tuple[str, str]]` — reads a streaming file-like object (bytes or str lines) and yields `(event, data)`. Event name defaults to `"message"`. Multiple `data:` lines join with `"\n"`. Comment lines (leading `:`) and unknown fields ignored. A frame is emitted on a blank line only if it had `data`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_mcp_sse_client.py`:

```python
import io
import json
import threading

import pytest

from freecad_ai.mcp.transport import _iter_sse_events


def _stream(text):
    return io.BytesIO(text.encode("utf-8"))


class TestIterSSEEvents:
    def test_endpoint_then_message(self):
        raw = (
            "event: endpoint\ndata: /messages?sessionId=abc\n\n"
            "event: message\ndata: {\"id\":1}\n\n"
        )
        events = list(_iter_sse_events(_stream(raw)))
        assert events == [
            ("endpoint", "/messages?sessionId=abc"),
            ("message", '{"id":1}'),
        ]

    def test_default_event_name_is_message(self):
        events = list(_iter_sse_events(_stream("data: hello\n\n")))
        assert events == [("message", "hello")]

    def test_multiline_data_joined_with_newline(self):
        events = list(_iter_sse_events(_stream("data: a\ndata: b\n\n")))
        assert events == [("message", "a\nb")]

    def test_comment_and_blank_frames_ignored(self):
        raw = ": keepalive\n\nevent: message\ndata: x\n\n"
        events = list(_iter_sse_events(_stream(raw)))
        assert events == [("message", "x")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_sse_client.py::TestIterSSEEvents -v`
Expected: FAIL with `ImportError: cannot import name '_iter_sse_events'`.

- [ ] **Step 3: Implement the helper**

In `freecad_ai/mcp/transport.py`, add to the imports block near the top (after the existing `import uuid`):

```python
import urllib.request
import urllib.parse
```

Then add this module-level function (place it after the `logger = ...` line, before `class StdioClientTransport`):

```python
def _iter_sse_events(fp):
    """Yield (event, data) tuples from a streaming SSE file object.

    Parses the subset of the text/event-stream format MCP uses: ``event:`` and
    ``data:`` fields terminated by a blank line. Multiple ``data:`` lines join
    with a newline. Comment lines (leading ``:``) and other fields (``id:``,
    ``retry:``) are ignored. The event name defaults to ``"message"`` when only
    ``data`` is present.
    """
    event = None
    data_lines = []
    for raw in fp:
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        line = line.rstrip("\n").rstrip("\r")
        if line == "":
            if data_lines:
                yield (event or "message", "\n".join(data_lines))
            event = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value
        elif field == "data":
            data_lines.append(value)
    # A trailing frame with no terminating blank line is dropped (matches the
    # wire convention that events are terminated by a blank line).
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_sse_client.py::TestIterSSEEvents -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/mcp/transport.py tests/unit/test_mcp_sse_client.py
git commit -m "feat(mcp): add SSE frame parser helper (#41)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Request correlator (`_RequestCorrelator`)

**Files:**
- Modify: `freecad_ai/mcp/transport.py` (add helper class)
- Test: `tests/unit/test_mcp_sse_client.py` (add a class)

**Interfaces:**
- Consumes: `threading` (already imported in transport.py).
- Produces: `_RequestCorrelator` with `next_id() -> int`, `register(req_id) -> threading.Event`, `resolve(msg: dict)`, `wait(req_id, event, timeout) -> dict` (raises `TimeoutError` on expiry), `cancel(req_id)`, `fail_all(error: dict)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mcp_sse_client.py`:

```python
from freecad_ai.mcp.transport import _RequestCorrelator


class TestRequestCorrelator:
    def test_next_id_monotonic(self):
        c = _RequestCorrelator()
        assert c.next_id() == 1
        assert c.next_id() == 2

    def test_register_resolve_wait_roundtrip(self):
        c = _RequestCorrelator()
        rid = c.next_id()
        event = c.register(rid)
        c.resolve({"id": rid, "result": {"ok": True}})
        resp = c.wait(rid, event, timeout=1)
        assert resp == {"id": rid, "result": {"ok": True}}

    def test_wait_times_out_when_unresolved(self):
        c = _RequestCorrelator()
        rid = c.next_id()
        event = c.register(rid)
        with pytest.raises(TimeoutError):
            c.wait(rid, event, timeout=0.05)

    def test_resolve_ignores_unknown_and_idless(self):
        c = _RequestCorrelator()
        c.resolve({"result": 1})          # no id — ignored, no crash
        c.resolve({"id": 999, "result": 1})  # unknown id — ignored

    def test_fail_all_unblocks_pending(self):
        c = _RequestCorrelator()
        rid = c.next_id()
        event = c.register(rid)
        c.fail_all({"error": "stopped"})
        assert c.wait(rid, event, timeout=1) == {"error": "stopped"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_sse_client.py::TestRequestCorrelator -v`
Expected: FAIL with `ImportError: cannot import name '_RequestCorrelator'`.

- [ ] **Step 3: Implement the class**

In `freecad_ai/mcp/transport.py`, add after `_iter_sse_events`:

```python
class _RequestCorrelator:
    """Matches asynchronous JSON-RPC responses to blocked callers by id.

    Used by ``SSEClientTransport`` (whose replies arrive on a separate reader
    thread). ``StdioClientTransport`` keeps its own equivalent inline copy.
    """

    def __init__(self):
        self._pending = {}   # id -> {"event": Event, "response": dict|None}
        self._lock = threading.Lock()
        self._next_id = 1

    def next_id(self):
        with self._lock:
            rid = self._next_id
            self._next_id += 1
        return rid

    def register(self, req_id):
        event = threading.Event()
        with self._lock:
            self._pending[req_id] = {"event": event, "response": None}
        return event

    def resolve(self, msg):
        msg_id = msg.get("id")
        if msg_id is None:
            return
        with self._lock:
            entry = self._pending.get(msg_id)
            if entry is not None:
                entry["response"] = msg
                entry["event"].set()

    def wait(self, req_id, event, timeout):
        if not event.wait(timeout):
            with self._lock:
                self._pending.pop(req_id, None)
            raise TimeoutError(f"MCP request id={req_id} timed out after {timeout}s")
        with self._lock:
            entry = self._pending.pop(req_id)
        return entry["response"]

    def cancel(self, req_id):
        with self._lock:
            self._pending.pop(req_id, None)

    def fail_all(self, error):
        with self._lock:
            for entry in self._pending.values():
                entry["response"] = error
                entry["event"].set()
            self._pending.clear()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_sse_client.py::TestRequestCorrelator -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/mcp/transport.py tests/unit/test_mcp_sse_client.py
git commit -m "feat(mcp): add request correlator for async transports (#41)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `SSEClientTransport` (legacy HTTP+SSE client)

**Files:**
- Modify: `freecad_ai/mcp/transport.py` (add class)
- Test: `tests/unit/test_mcp_sse_client.py` (add a class — tested against the workbench's own `SSEServerTransport`)

**Interfaces:**
- Consumes: `_iter_sse_events`, `_RequestCorrelator`, `protocol.*`, `urllib.request`, `urllib.parse`, the existing `SSEServerTransport` (for the test only).
- Produces: `SSEClientTransport(url, headers=None, *, ssl_context=None, connect_timeout=30)` implementing the transport interface. `send_request` returns a JSON-RPC error dict (not raising) on POST failure; `start()` raises on connect failure / missing endpoint event.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mcp_sse_client.py`:

```python
import time

from freecad_ai.mcp import protocol
from freecad_ai.mcp.transport import SSEClientTransport, SSEServerTransport


def _fake_server_handler(msg):
    """Minimal JSON-RPC handler standing in for a real MCP server."""
    method = msg.get("method")
    msg_id = msg.get("id")
    if method == "initialize":
        return protocol.make_response(msg_id, {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "serverInfo": {"name": "fake", "version": "1"},
        })
    if method == "tools/list":
        return protocol.make_response(msg_id, {"tools": [
            {"name": "ping", "description": "d", "inputSchema": {"type": "object"}},
        ]})
    if method == "tools/call":
        return protocol.make_response(msg_id, {
            "content": [{"type": "text", "text": "pong"}], "isError": False})
    # Unknown methods get NO reply — the timeout test relies on this silence.
    return None


class _RunningSSEServer:
    """Start the workbench's own SSE server on an ephemeral port, in a thread."""

    def __enter__(self):
        self.transport = SSEServerTransport(host="127.0.0.1", port=0)
        self.transport._handler = _fake_server_handler
        self.httpd = self.transport._make_server()
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.port}/sse"
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestSSEClientTransport:
    def test_connect_handshake_and_tool_call(self):
        with _RunningSSEServer() as srv:
            t = SSEClientTransport(srv.url, connect_timeout=5)
            t.start()
            try:
                init = t.send_request("initialize", {"protocolVersion": "2025-03-26"},
                                      timeout=5)
                assert "result" in init
                tools = t.send_request("tools/list", timeout=5)
                assert tools["result"]["tools"][0]["name"] == "ping"
                call = t.send_request("tools/call",
                                      {"name": "ping", "arguments": {}}, timeout=5)
                assert call["result"]["content"][0]["text"] == "pong"
            finally:
                t.stop()

    def test_is_alive_transitions(self):
        with _RunningSSEServer() as srv:
            t = SSEClientTransport(srv.url, connect_timeout=5)
            assert t.is_alive is False
            t.start()
            assert t.is_alive is True
            t.stop()
            assert t.is_alive is False

    def test_send_request_timeout(self):
        # A server that never answers a made-up method → wait() times out.
        with _RunningSSEServer() as srv:
            t = SSEClientTransport(srv.url, connect_timeout=5)
            t.start()
            try:
                with pytest.raises(TimeoutError):
                    t.send_request("never/answered", timeout=0.3)
            finally:
                t.stop()

    def test_start_raises_without_endpoint_event(self):
        # A server that opens a stream but never sends an endpoint event.
        import http.server

        class Silent(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                time.sleep(2)  # stream stays open but never sends an endpoint
            def log_message(self, *a):
                pass

        httpd = http.server.HTTPServer(("127.0.0.1", 0), Silent)
        port = httpd.server_address[1]
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        try:
            t = SSEClientTransport(f"http://127.0.0.1:{port}/sse", connect_timeout=0.5)
            with pytest.raises(TimeoutError):
                t.start()
        finally:
            httpd.shutdown()
            httpd.server_close()
```

Note: `never/answered` returns an error response with the request's id via
`_fake_server_handler`, which would resolve the waiter. To make the timeout test
deterministic, change `_fake_server_handler` so an unknown method with an id
returns `None` (no reply) instead of an error. Update the handler's tail to:

```python
    return None
```

i.e. remove the `METHOD_NOT_FOUND` branch. (Real servers reply with an error,
but for this transport-level timeout test we want silence.)

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_sse_client.py::TestSSEClientTransport -v`
Expected: FAIL with `ImportError: cannot import name 'SSEClientTransport'`.

- [ ] **Step 3: Implement the class**

In `freecad_ai/mcp/transport.py`, add after `class StdioClientTransport` (and before `class StdioServerTransport`), so `SSEClientTransport` and the server transports live together:

```python
class SSEClientTransport:
    """Client transport speaking the legacy MCP HTTP+SSE protocol.

    ``start()`` opens ``GET <url>`` as a streaming response on a reader thread,
    reads the advertised ``endpoint`` event, then POSTs JSON-RPC requests to
    that endpoint; responses arrive back over the GET stream and are matched by
    id via ``_RequestCorrelator``.
    """

    def __init__(self, url, headers=None, *, ssl_context=None, connect_timeout=30):
        self._url = url
        self._headers = dict(headers or {})
        self._ssl_context = ssl_context
        self._connect_timeout = connect_timeout
        self._correlator = _RequestCorrelator()
        self._resp = None
        self._reader_thread = None
        self._endpoint_url = None
        self._endpoint_ready = threading.Event()
        self._running = False

    def start(self):
        req = urllib.request.Request(self._url, method="GET")
        for key, value in self._headers.items():
            req.add_header(key, value)
        req.add_header("Accept", "text/event-stream")
        self._resp = urllib.request.urlopen(
            req, timeout=self._connect_timeout, context=self._ssl_context)
        self._running = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        if not self._endpoint_ready.wait(self._connect_timeout):
            self.stop()
            raise TimeoutError(
                f"MCP SSE server '{self._url}' sent no endpoint event "
                f"within {self._connect_timeout}s")
        if self._endpoint_url is None:
            self.stop()
            raise RuntimeError(f"MCP SSE stream '{self._url}' closed before handshake")

    def _read_loop(self):
        try:
            for event, data in _iter_sse_events(self._resp):
                if event == "endpoint":
                    self._endpoint_url = urllib.parse.urljoin(self._url, data)
                    self._endpoint_ready.set()
                elif event == "message":
                    try:
                        msg = protocol.decode(data)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    self._correlator.resolve(msg)
        except Exception:
            pass
        finally:
            self._running = False
            self._endpoint_ready.set()  # unblock start() if the stream died early

    def send_request(self, method, params=None, timeout=30):
        req_id = self._correlator.next_id()
        event = self._correlator.register(req_id)
        try:
            self._post(protocol.make_request(method, params, id=req_id))
        except Exception as exc:  # noqa: BLE001 — surface as JSON-RPC error
            self._correlator.cancel(req_id)
            return protocol.make_error(req_id, protocol.INTERNAL_ERROR, str(exc))
        return self._correlator.wait(req_id, event, timeout)

    def send_notification(self, method, params=None):
        self._post(protocol.make_notification(method, params))

    def _post(self, msg):
        if self._endpoint_url is None:
            raise RuntimeError("MCP SSE transport not connected (no endpoint)")
        req = urllib.request.Request(
            self._endpoint_url, data=protocol.encode(msg), method="POST")
        for key, value in self._headers.items():
            req.add_header(key, value)
        req.add_header("Content-Type", "application/json")
        resp = urllib.request.urlopen(
            req, timeout=self._connect_timeout, context=self._ssl_context)
        resp.read()   # drain the 202 body
        resp.close()

    def stop(self):
        self._running = False
        if self._resp is not None:
            try:
                self._resp.close()
            except Exception:
                pass
            self._resp = None
        self._correlator.fail_all(
            protocol.make_error(None, protocol.INTERNAL_ERROR, "Transport stopped"))

    @property
    def is_alive(self):
        return (self._running and self._reader_thread is not None
                and self._reader_thread.is_alive())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_sse_client.py -v`
Expected: PASS (all classes in the file green).

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/mcp/transport.py tests/unit/test_mcp_sse_client.py
git commit -m "feat(mcp): add legacy HTTP+SSE client transport (#41)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `StreamableHTTPClientTransport` (single-endpoint client)

**Files:**
- Modify: `freecad_ai/mcp/transport.py` (add class)
- Test: `tests/unit/test_mcp_streamable_client.py` (new — driven by a stdlib HTTP stub)

**Interfaces:**
- Consumes: `_iter_sse_events`, `protocol.*`, `urllib.request`.
- Produces: `StreamableHTTPClientTransport(url, headers=None, *, ssl_context=None, connect_timeout=30)` implementing the transport interface. Reads its reply synchronously on the calling thread — inline `application/json` or a `text/event-stream`. Captures/echoes `Mcp-Session-Id`. No `_RequestCorrelator` (single-threaded reply). `is_alive` = started-and-not-stopped.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_mcp_streamable_client.py`:

```python
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from freecad_ai.mcp import protocol
from freecad_ai.mcp.transport import StreamableHTTPClientTransport


class _StubHandler(BaseHTTPRequestHandler):
    """Configurable Streamable-HTTP server stub. Behaviour set on the class."""
    mode = "json"          # "json" | "sse" | "error"
    seen_session = []      # records Mcp-Session-Id sent by the client

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.seen_session.append(self.headers.get("Mcp-Session-Id"))
        req_id = body.get("id")

        if body.get("method") == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
            return

        if type(self).mode == "error":
            self.send_error(500, "boom")
            return

        reply = protocol.make_response(req_id, {"echo": body.get("method")})

        if type(self).mode == "sse":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            if body.get("method") == "initialize":
                self.send_header("Mcp-Session-Id", "sess-123")
            self.end_headers()
            data = json.dumps(reply, separators=(",", ":"))
            self.wfile.write(f"event: message\ndata: {data}\n\n".encode())
            self.wfile.flush()
        else:  # json
            payload = json.dumps(reply, separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            if body.get("method") == "initialize":
                self.send_header("Mcp-Session-Id", "sess-123")
            self.end_headers()
            self.wfile.write(payload)


class _RunningStub:
    def __init__(self, mode):
        self.mode = mode

    def __enter__(self):
        _StubHandler.mode = self.mode
        _StubHandler.seen_session = []
        self.httpd = HTTPServer(("127.0.0.1", 0), _StubHandler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.port}/mcp"
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestStreamableHTTPClient:
    def test_inline_json_response(self):
        with _RunningStub("json") as srv:
            t = StreamableHTTPClientTransport(srv.url, connect_timeout=5)
            t.start()
            resp = t.send_request("tools/list", timeout=5)
            assert resp["result"]["echo"] == "tools/list"
            t.stop()

    def test_event_stream_response(self):
        with _RunningStub("sse") as srv:
            t = StreamableHTTPClientTransport(srv.url, connect_timeout=5)
            t.start()
            resp = t.send_request("tools/list", timeout=5)
            assert resp["result"]["echo"] == "tools/list"
            t.stop()

    def test_session_id_captured_and_echoed(self):
        with _RunningStub("json") as srv:
            t = StreamableHTTPClientTransport(srv.url, connect_timeout=5)
            t.start()
            t.send_request("initialize", {"protocolVersion": "2025-03-26"}, timeout=5)
            t.send_request("tools/list", timeout=5)
            # First POST sent no session id; second echoed the captured one.
            assert _StubHandler.seen_session[0] is None
            assert _StubHandler.seen_session[1] == "sess-123"
            t.stop()

    def test_notification_posts_without_reply(self):
        with _RunningStub("json") as srv:
            t = StreamableHTTPClientTransport(srv.url, connect_timeout=5)
            t.start()
            t.send_notification("notifications/initialized")  # must not raise
            t.stop()

    def test_http_error_surfaces_as_error_dict(self):
        with _RunningStub("error") as srv:
            t = StreamableHTTPClientTransport(srv.url, connect_timeout=5)
            t.start()
            resp = t.send_request("tools/list", timeout=5)
            assert "error" in resp
            t.stop()

    def test_is_alive_transitions(self):
        with _RunningStub("json") as srv:
            t = StreamableHTTPClientTransport(srv.url)
            assert t.is_alive is False
            t.start()
            assert t.is_alive is True
            t.stop()
            assert t.is_alive is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_streamable_client.py -v`
Expected: FAIL with `ImportError: cannot import name 'StreamableHTTPClientTransport'`.

- [ ] **Step 3: Implement the class**

In `freecad_ai/mcp/transport.py`, add after `SSEClientTransport`:

```python
class StreamableHTTPClientTransport:
    """Client transport speaking the MCP Streamable HTTP protocol.

    Each ``send_request`` POSTs JSON-RPC to a single endpoint; the reply is read
    synchronously on the calling thread — either an inline ``application/json``
    body or a ``text/event-stream`` walked until the matching id. The
    ``Mcp-Session-Id`` returned at ``initialize`` is echoed on later requests.
    """

    def __init__(self, url, headers=None, *, ssl_context=None, connect_timeout=30):
        self._url = url
        self._headers = dict(headers or {})
        self._ssl_context = ssl_context
        self._connect_timeout = connect_timeout
        self._session_id = None
        self._next_id = 1
        self._id_lock = threading.Lock()
        self._running = False

    def start(self):
        self._running = True

    def _alloc_id(self):
        with self._id_lock:
            rid = self._next_id
            self._next_id += 1
        return rid

    def send_request(self, method, params=None, timeout=30):
        req_id = self._alloc_id()
        msg = protocol.make_request(method, params, id=req_id)
        try:
            resp = self._post(msg, timeout)
        except Exception as exc:  # noqa: BLE001 — surface as JSON-RPC error
            return protocol.make_error(req_id, protocol.INTERNAL_ERROR, str(exc))

        session = resp.headers.get("Mcp-Session-Id")
        if session:
            self._session_id = session
        content_type = resp.headers.get("Content-Type", "")
        try:
            if "text/event-stream" in content_type:
                for event, data in _iter_sse_events(resp):
                    if event != "message":
                        continue
                    try:
                        candidate = protocol.decode(data)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if candidate.get("id") == req_id:
                        return candidate
                return protocol.make_error(
                    req_id, protocol.INTERNAL_ERROR,
                    "MCP HTTP stream closed before a matching response")
            return protocol.decode(resp.read().decode("utf-8"))
        finally:
            resp.close()

    def send_notification(self, method, params=None):
        resp = self._post(protocol.make_notification(method, params),
                          self._connect_timeout)
        resp.read()
        resp.close()

    def _post(self, msg, timeout):
        req = urllib.request.Request(
            self._url, data=protocol.encode(msg), method="POST")
        for key, value in self._headers.items():
            req.add_header(key, value)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        if self._session_id:
            req.add_header("Mcp-Session-Id", self._session_id)
        return urllib.request.urlopen(
            req, timeout=timeout, context=self._ssl_context)

    def stop(self):
        self._running = False

    @property
    def is_alive(self):
        return self._running
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_streamable_client.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/mcp/transport.py tests/unit/test_mcp_streamable_client.py
git commit -m "feat(mcp): add Streamable HTTP client transport (#41)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Factory, URL/TLS validation, and transport-injectable `MCPClient`

**Files:**
- Modify: `freecad_ai/mcp/client.py`
- Test: `tests/unit/test_mcp_client_factory.py` (new)

**Interfaces:**
- Consumes: `StdioClientTransport`, `SSEClientTransport`, `StreamableHTTPClientTransport`, `_LOOPBACK_HOSTS` (all from `.transport`); `ssl`, `urllib.parse`.
- Produces:
  - `make_client_transport(cfg: dict)` → a transport instance.
  - `_validate_url(url: str)` → raises `ValueError` on a bad scheme / non-loopback `http`.
  - `_build_ssl_context(cfg: dict)` → `ssl.SSLContext | None`.
  - `MCPClient.__init__(name, command=None, env=None, *, transport=None, deferred=True, tool_call_timeout=600)` — uses injected `transport` if given, else builds a stdio transport from `command`/`env`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_mcp_client_factory.py`:

```python
import ssl

import pytest

from freecad_ai.mcp.client import (
    make_client_transport, _validate_url, _build_ssl_context, MCPClient,
)
from freecad_ai.mcp.transport import (
    StdioClientTransport, SSEClientTransport, StreamableHTTPClientTransport,
)


class TestValidateUrl:
    def test_https_ok(self):
        _validate_url("https://example.com/sse")  # no raise

    def test_http_loopback_ok(self):
        _validate_url("http://localhost:3000/sse")
        _validate_url("http://127.0.0.1:3000/sse")

    def test_http_remote_rejected(self):
        with pytest.raises(ValueError):
            _validate_url("http://example.com/sse")

    def test_non_http_scheme_rejected(self):
        with pytest.raises(ValueError):
            _validate_url("ftp://example.com/x")


class TestBuildSSLContext:
    def test_none_when_no_tls_fields(self):
        assert _build_ssl_context({"url": "https://x/"}) is None

    def test_context_when_ca_bundle_set(self, tmp_path):
        # A CA file that doesn't parse still exercises the "build a context"
        # branch via the raised error; here we assert a context is attempted.
        ca = tmp_path / "ca.pem"
        ca.write_text("not a real cert")
        with pytest.raises(ssl.SSLError):
            _build_ssl_context({"ca_bundle": str(ca)})

    def test_client_cert_bad_path_raises(self):
        with pytest.raises(Exception):
            _build_ssl_context({"client_cert": "/nonexistent/client.pem"})


class TestMakeClientTransport:
    def test_absent_transport_is_stdio(self):
        t = make_client_transport({"command": "echo", "args": ["hi"]})
        assert isinstance(t, StdioClientTransport)

    def test_explicit_stdio(self):
        t = make_client_transport({"transport": "stdio", "command": "echo"})
        assert isinstance(t, StdioClientTransport)

    def test_sse_transport_with_headers(self):
        t = make_client_transport({
            "transport": "sse", "url": "https://h/sse",
            "headers": {"Authorization": "Bearer x"}})
        assert isinstance(t, SSEClientTransport)
        assert t._headers["Authorization"] == "Bearer x"

    def test_http_transport(self):
        t = make_client_transport({"transport": "http", "url": "https://h/mcp"})
        assert isinstance(t, StreamableHTTPClientTransport)

    def test_unknown_transport_raises(self):
        with pytest.raises(ValueError):
            make_client_transport({"transport": "carrier-pigeon", "url": "https://h/"})

    def test_remote_http_url_rejected(self):
        with pytest.raises(ValueError):
            make_client_transport({"transport": "sse", "url": "http://example.com/sse"})


class TestMCPClientInjection:
    def test_injected_transport_used(self):
        sentinel = SSEClientTransport("https://h/sse")
        client = MCPClient("srv", transport=sentinel)
        assert client._transport is sentinel

    def test_backward_compatible_stdio_construction(self):
        client = MCPClient("srv", ["echo", "hi"], {"A": "B"})
        assert isinstance(client._transport, StdioClientTransport)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_client_factory.py -v`
Expected: FAIL with `ImportError: cannot import name 'make_client_transport'`.

- [ ] **Step 3: Implement**

In `freecad_ai/mcp/client.py`, update the imports at the top:

```python
import logging
import ssl
import urllib.parse
from dataclasses import dataclass, field

from .transport import (
    StdioClientTransport,
    SSEClientTransport,
    StreamableHTTPClientTransport,
    _LOOPBACK_HOSTS,
)
```

Change `MCPClient.__init__` (currently at lines 47-58) to:

```python
    def __init__(self, name: str, command: list | None = None,
                 env: dict | None = None, *, transport=None,
                 deferred: bool = True, tool_call_timeout: float = 600):
        self.name = name
        if transport is not None:
            self._transport = transport
        else:
            self._transport = StdioClientTransport(command, env)
        self._tools: list[MCPToolInfo] = []
        self._connected = False
        self._deferred = deferred
        self._tool_call_timeout = tool_call_timeout
        self._schema_cache: dict[str, dict] = {}
        self._raw_tools: list[dict] = []
```

Add these module-level functions at the end of `client.py`:

```python
def _validate_url(url: str):
    """Reject non-http(s) schemes and plaintext http to non-loopback hosts."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"MCP URL must be http or https, got '{parsed.scheme}'")
    if parsed.scheme == "http":
        host = (parsed.hostname or "").lower()
        if host not in _LOOPBACK_HOSTS:
            raise ValueError(
                "plaintext http:// is only allowed to localhost; use https://")


def _build_ssl_context(cfg: dict):
    """Return an ssl.SSLContext for custom CA / client cert, or None.

    None => the transport passes context=None to urlopen (urllib's default
    context = system CA store). A context is built only when at least one of
    ca_bundle / client_cert is set.
    """
    ca = cfg.get("ca_bundle") or None
    cert = cfg.get("client_cert") or None
    key = cfg.get("client_key") or None
    if not ca and not cert:
        return None
    context = ssl.create_default_context(cafile=ca)  # cafile=None => system defaults
    if cert:
        context.load_cert_chain(certfile=cert, keyfile=key or None)
    return context


def make_client_transport(cfg: dict):
    """Build the client transport for one MCP server config.

    transport ∈ {"stdio","sse","http"}; absent defaults to "stdio". Raises
    ValueError on a bad URL or unknown transport (caught by connect_all).
    """
    transport = cfg.get("transport", "stdio")
    if transport == "stdio":
        command = [cfg["command"]] + cfg.get("args", [])
        return StdioClientTransport(command, cfg.get("env") or None)
    url = cfg["url"]
    headers = cfg.get("headers") or {}
    _validate_url(url)
    ssl_context = _build_ssl_context(cfg)
    if transport == "sse":
        return SSEClientTransport(url, headers=headers, ssl_context=ssl_context)
    if transport == "http":
        return StreamableHTTPClientTransport(
            url, headers=headers, ssl_context=ssl_context)
    raise ValueError(f"unknown MCP transport '{transport}'")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_client_factory.py -v`
Expected: PASS (all green).

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/mcp/client.py tests/unit/test_mcp_client_factory.py
git commit -m "feat(mcp): transport factory, URL/TLS validation, injectable client (#41)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Route `manager.connect_all` through the factory

**Files:**
- Modify: `freecad_ai/mcp/manager.py:40-64`
- Test: `tests/unit/test_mcp_client_factory.py` (add a class) — reuse `test_mcp_deferred.py` patterns if a manager fixture exists there.

**Interfaces:**
- Consumes: `make_client_transport` from `.client`.
- Produces: `connect_all` builds each client via `make_client_transport(cfg)` and injects it. A URL/transport error is logged and that server skipped; others still connect.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mcp_client_factory.py`:

```python
from unittest.mock import patch

from freecad_ai.mcp.manager import MCPManager


class TestConnectAllRoutesThroughFactory:
    def test_bad_url_server_skipped_others_connect(self):
        mgr = MCPManager()
        good = {"name": "good", "transport": "http", "url": "https://h/mcp"}
        bad = {"name": "bad", "transport": "sse", "url": "http://example.com/sse"}

        connected = {}

        def fake_connect(self):
            connected[self.name] = True

        # Patch MCPClient.connect so we don't hit the network; the bad server
        # fails earlier, inside make_client_transport (_validate_url).
        with patch("freecad_ai.mcp.client.MCPClient.connect", fake_connect):
            mgr.connect_all([good, bad])

        assert "good" in mgr.connected_servers or "good" in connected
        assert "bad" not in mgr._clients

    def test_uses_factory_transport(self):
        mgr = MCPManager()
        cfg = {"name": "s", "transport": "http", "url": "https://h/mcp"}
        with patch("freecad_ai.mcp.client.MCPClient.connect", lambda self: None):
            mgr.connect_all([cfg])
        from freecad_ai.mcp.transport import StreamableHTTPClientTransport
        assert isinstance(mgr._clients["s"]._transport, StreamableHTTPClientTransport)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_client_factory.py::TestConnectAllRoutesThroughFactory -v`
Expected: FAIL — currently `connect_all` calls `[cfg["command"]] + ...` and raises `KeyError: 'command'` on the URL configs (so `test_uses_factory_transport` fails: no client registered).

- [ ] **Step 3: Implement**

In `freecad_ai/mcp/manager.py`, add the import near the top (line 14, alongside the existing `from .client import ...`):

```python
from .client import MCPClient, MCPToolInfo, MCPToolResult, make_client_transport
```

Replace the body of the per-config loop (lines 54-64, from `command = ...` through the `except`) with:

```python
            try:
                transport = make_client_transport(cfg)
                tool_call_timeout = float(cfg.get("timeout", 600))
                client = MCPClient(name, transport=transport, deferred=deferred,
                                   tool_call_timeout=tool_call_timeout)
                client.connect()
                self._clients[name] = client
            except Exception as e:
                logger.error("Failed to connect MCP server '%s': %s", name, e)
```

(The `env = cfg.get("env") or None` line above the try is now unused — the
factory reads `env` itself. Remove that line.)

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_client_factory.py tests/unit/test_mcp_deferred.py -v`
Expected: PASS — new tests green and the existing deferred/manager tests still green (stdio configs still route correctly).

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/mcp/manager.py tests/unit/test_mcp_client_factory.py
git commit -m "feat(mcp): route connect_all through the transport factory (#41)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Settings dialog — transport selector + URL/headers/TLS fields

**Files:**
- Modify: `freecad_ai/ui/settings_dialog.py` — `_AddMCPServerDialog` (lines 1974-2065) and `SettingsDialog._mcp_list_label` (lines 1495-1507).
- Test: `tests/unit/test_mcp_add_server_dialog.py` (new — fake-self / static-method tests, PySide-skipped when absent).

**Interfaces:**
- Consumes: `QComboBox`, `QTableWidget`, `QTableWidgetItem`, `QFileDialog`, `QWidget`, `QLineEdit`, `QFormLayout` (all already aliased in `settings_dialog.py`).
- Produces:
  - `_AddMCPServerDialog._apply_transport_visibility(transport: str)` — toggles `self._stdio_widget` / `self._url_widget` visibility.
  - `_AddMCPServerDialog._collect_headers() -> dict`.
  - `get_config()` emits the stdio or url(+headers+tls) shape (always includes `transport`; omits empty TLS paths).
  - `SettingsDialog._mcp_list_label(entry)` shows `[sse]/[http] <url>` for URL transports.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_mcp_add_server_dialog.py`:

```python
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# The dialog imports through ui/compat.py, which needs PySide6 or PySide2.
try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.ui.settings_dialog import _AddMCPServerDialog, SettingsDialog


class TestApplyTransportVisibility:
    def test_stdio_shows_stdio_hides_url(self):
        d = SimpleNamespace(_stdio_widget=MagicMock(), _url_widget=MagicMock())
        _AddMCPServerDialog._apply_transport_visibility(d, "stdio")
        d._stdio_widget.setVisible.assert_called_with(True)
        d._url_widget.setVisible.assert_called_with(False)

    def test_sse_shows_url_hides_stdio(self):
        d = SimpleNamespace(_stdio_widget=MagicMock(), _url_widget=MagicMock())
        _AddMCPServerDialog._apply_transport_visibility(d, "sse")
        d._stdio_widget.setVisible.assert_called_with(False)
        d._url_widget.setVisible.assert_called_with(True)


class TestCollectHeaders:
    def _table(self, rows):
        table = MagicMock()
        table.rowCount.return_value = len(rows)
        def item(r, c):
            key, val = rows[r]
            text = key if c == 0 else val
            if text is None:
                return None
            cell = MagicMock()
            cell.text.return_value = text
            return cell
        table.item.side_effect = item
        return table

    def test_collects_nonempty_keys(self):
        d = SimpleNamespace(headers_table=self._table(
            [("Authorization", "Bearer x"), ("", "ignored"), ("X-Api-Key", "k")]))
        result = _AddMCPServerDialog._collect_headers(d)
        assert result == {"Authorization": "Bearer x", "X-Api-Key": "k"}


class TestGetConfig:
    def _dialog(self, transport, **fields):
        combo = MagicMock()
        combo.currentData.return_value = transport
        return SimpleNamespace(
            transport_combo=combo,
            name_edit=MagicMock(**{"text.return_value": fields.get("name", "s")}),
            command_edit=MagicMock(**{"text.return_value": fields.get("command", "")}),
            args_edit=MagicMock(**{"text.return_value": fields.get("args", "")}),
            url_edit=MagicMock(**{"text.return_value": fields.get("url", "")}),
            ca_edit=MagicMock(**{"text.return_value": fields.get("ca", "")}),
            cert_edit=MagicMock(**{"text.return_value": fields.get("cert", "")}),
            key_edit=MagicMock(**{"text.return_value": fields.get("key", "")}),
            enabled_check=MagicMock(**{"isChecked.return_value": True}),
            deferred_check=MagicMock(**{"isChecked.return_value": True}),
            timeout_spin=MagicMock(**{"value.return_value": 600}),
            _collect_headers=lambda: fields.get("headers", {}),
        )

    def test_stdio_shape(self):
        d = self._dialog("stdio", command="npx", args="-y srv /tmp")
        cfg = _AddMCPServerDialog.get_config(d)
        assert cfg["transport"] == "stdio"
        assert cfg["command"] == "npx"
        assert cfg["args"] == ["-y", "srv", "/tmp"]
        assert "url" not in cfg

    def test_sse_shape_with_headers_and_tls(self):
        d = self._dialog("sse", url="https://h/sse",
                         headers={"Authorization": "Bearer x"}, ca="/ca.pem")
        cfg = _AddMCPServerDialog.get_config(d)
        assert cfg["transport"] == "sse"
        assert cfg["url"] == "https://h/sse"
        assert cfg["headers"] == {"Authorization": "Bearer x"}
        assert cfg["ca_bundle"] == "/ca.pem"
        assert "client_cert" not in cfg  # empty TLS paths omitted
        assert "command" not in cfg


class TestMcpListLabel:
    def test_url_transport_shows_url(self):
        label = SettingsDialog._mcp_list_label(
            {"name": "remote", "transport": "sse", "url": "https://h/sse"})
        assert "https://h/sse" in label
        assert "[sse]" in label

    def test_stdio_transport_shows_command(self):
        label = SettingsDialog._mcp_list_label(
            {"name": "fs", "command": "npx", "args": ["-y", "srv"]})
        assert "npx" in label
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_add_server_dialog.py -v`
Expected: FAIL — `AttributeError: type object '_AddMCPServerDialog' has no attribute '_apply_transport_visibility'` (and `get_config` still emits the old stdio-only shape).

- [ ] **Step 3: Implement**

In `freecad_ai/ui/settings_dialog.py`, rewrite `_AddMCPServerDialog._build_ui` (lines 1989-2044) to add the transport selector and grouped widgets. Replace the whole method with:

```python
    def _build_ui(self, editing=False):
        layout = QFormLayout(self)

        self.transport_combo = QComboBox()
        self.transport_combo.addItem(
            translate("AddMCPServerDialog", "Command (stdio)"), "stdio")
        self.transport_combo.addItem(
            translate("AddMCPServerDialog", "SSE (URL)"), "sse")
        self.transport_combo.addItem(
            translate("AddMCPServerDialog", "Streamable HTTP (URL)"), "http")
        self.transport_combo.currentIndexChanged.connect(
            lambda _=0: self._apply_transport_visibility(
                self.transport_combo.currentData()))
        layout.addRow(translate("AddMCPServerDialog", "Transport:"),
                      self.transport_combo)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(
            translate("AddMCPServerDialog", "e.g. filesystem"))
        layout.addRow(translate("AddMCPServerDialog", "Name:"), self.name_edit)

        # --- stdio group ---
        self._stdio_widget = QWidget()
        stdio_form = QFormLayout(self._stdio_widget)
        stdio_form.setContentsMargins(0, 0, 0, 0)
        self.command_edit = QLineEdit()
        self.command_edit.setPlaceholderText(
            translate("AddMCPServerDialog", "e.g. npx"))
        stdio_form.addRow(translate("AddMCPServerDialog", "Command:"),
                          self.command_edit)
        self.args_edit = QLineEdit()
        self.args_edit.setPlaceholderText(translate(
            "AddMCPServerDialog", "e.g. -y @modelcontextprotocol/server-filesystem /tmp"))
        self.args_edit.setToolTip(
            translate("AddMCPServerDialog", "Space-separated arguments"))
        stdio_form.addRow(translate("AddMCPServerDialog", "Args:"), self.args_edit)
        layout.addRow(self._stdio_widget)

        # --- url group (sse / http) ---
        self._url_widget = QWidget()
        url_form = QFormLayout(self._url_widget)
        url_form.setContentsMargins(0, 0, 0, 0)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(
            translate("AddMCPServerDialog", "e.g. https://host/sse"))
        url_form.addRow(translate("AddMCPServerDialog", "URL:"), self.url_edit)

        self.headers_table = QTableWidget(0, 2)
        self.headers_table.setHorizontalHeaderLabels([
            translate("AddMCPServerDialog", "Header"),
            translate("AddMCPServerDialog", "Value")])
        self.headers_table.setMaximumHeight(100)
        url_form.addRow(translate("AddMCPServerDialog", "Headers:"),
                        self.headers_table)
        headers_btns = QHBoxLayout()
        add_hdr = QPushButton(translate("AddMCPServerDialog", "Add header"))
        add_hdr.clicked.connect(
            lambda: self.headers_table.insertRow(self.headers_table.rowCount()))
        del_hdr = QPushButton(translate("AddMCPServerDialog", "Remove header"))
        del_hdr.clicked.connect(
            lambda: self.headers_table.removeRow(self.headers_table.currentRow()))
        headers_btns.addWidget(add_hdr)
        headers_btns.addWidget(del_hdr)
        headers_btns.addStretch()
        headers_wrap = QWidget()
        headers_wrap.setLayout(headers_btns)
        url_form.addRow("", headers_wrap)

        self.ca_edit = QLineEdit()
        self.ca_edit.setPlaceholderText(
            translate("AddMCPServerDialog", "optional CA bundle path (.pem)"))
        url_form.addRow(translate("AddMCPServerDialog", "CA bundle:"), self.ca_edit)
        self.cert_edit = QLineEdit()
        self.cert_edit.setPlaceholderText(
            translate("AddMCPServerDialog", "optional client cert path (.pem)"))
        url_form.addRow(translate("AddMCPServerDialog", "Client cert:"), self.cert_edit)
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText(
            translate("AddMCPServerDialog", "optional client key path"))
        url_form.addRow(translate("AddMCPServerDialog", "Client key:"), self.key_edit)
        layout.addRow(self._url_widget)

        # --- shared rows ---
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 3600)
        self.timeout_spin.setValue(600)
        self.timeout_spin.setSuffix(translate("AddMCPServerDialog", " s"))
        self.timeout_spin.setToolTip(translate(
            "AddMCPServerDialog",
            "Maximum time to wait for a tool call to complete.\n"
            "Raise for slow tools (vision models, large builds).\n"
            "Lower for fast tools where you want to fail quickly."))
        layout.addRow(translate("AddMCPServerDialog", "Tool call timeout:"),
                      self.timeout_spin)

        self.deferred_check = QCheckBox(
            translate("AddMCPServerDialog", "Deferred tool loading"))
        self.deferred_check.setChecked(True)
        self.deferred_check.setToolTip(translate(
            "AddMCPServerDialog",
            "Load tool schemas lazily on first use instead of\n"
            "fetching all schemas eagerly on connect.\n"
            "Faster startup when the server exposes many tools."))
        layout.addRow("", self.deferred_check)

        self.enabled_check = QCheckBox(translate("AddMCPServerDialog", "Enabled"))
        self.enabled_check.setChecked(True)
        layout.addRow("", self.enabled_check)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        ok_label = translate("AddMCPServerDialog", "Save") if editing \
            else translate("AddMCPServerDialog", "Add")
        ok_btn = QPushButton(ok_label)
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)
        cancel_btn = QPushButton(translate("AddMCPServerDialog", "Cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        layout.addRow(btn_layout)

        self._apply_transport_visibility(self.transport_combo.currentData())
```

Ensure `QWidget` is aliased near the top of `settings_dialog.py` (it uses
`QtWidgets.QWidget`). If it is not already present alongside the other aliases
(around lines 20-40), add:

```python
QWidget = QtWidgets.QWidget
```

Add these methods to `_AddMCPServerDialog` (after `_build_ui`):

```python
    def _apply_transport_visibility(self, transport):
        is_stdio = (transport == "stdio")
        self._stdio_widget.setVisible(is_stdio)
        self._url_widget.setVisible(not is_stdio)

    def _collect_headers(self):
        headers = {}
        for row in range(self.headers_table.rowCount()):
            key_item = self.headers_table.item(row, 0)
            val_item = self.headers_table.item(row, 1)
            key = key_item.text().strip() if key_item else ""
            val = val_item.text().strip() if val_item else ""
            if key:
                headers[key] = val
        return headers

    def _populate_headers(self, headers):
        self.headers_table.setRowCount(0)
        for key, value in (headers or {}).items():
            row = self.headers_table.rowCount()
            self.headers_table.insertRow(row)
            self.headers_table.setItem(row, 0, QTableWidgetItem(str(key)))
            self.headers_table.setItem(row, 1, QTableWidgetItem(str(value)))
```

Replace `_populate` (lines 2046-2053) with:

```python
    def _populate(self, entry: dict):
        """Pre-populate fields from an existing MCP server config."""
        self.name_edit.setText(entry.get("name", ""))
        transport = entry.get("transport", "stdio")
        idx = self.transport_combo.findData(transport)
        if idx >= 0:
            self.transport_combo.setCurrentIndex(idx)
        self.command_edit.setText(entry.get("command", ""))
        self.args_edit.setText(" ".join(entry.get("args", [])))
        self.url_edit.setText(entry.get("url", ""))
        self._populate_headers(entry.get("headers", {}))
        self.ca_edit.setText(entry.get("ca_bundle", ""))
        self.cert_edit.setText(entry.get("client_cert", ""))
        self.key_edit.setText(entry.get("client_key", ""))
        self.deferred_check.setChecked(entry.get("deferred", True))
        self.enabled_check.setChecked(entry.get("enabled", True))
        self.timeout_spin.setValue(int(entry.get("timeout", 600)))
        self._apply_transport_visibility(transport)
```

Replace `get_config` (lines 2055-2065) with:

```python
    def get_config(self) -> dict:
        transport = self.transport_combo.currentData()
        cfg = {
            "name": self.name_edit.text().strip(),
            "transport": transport,
            "enabled": self.enabled_check.isChecked(),
            "deferred": self.deferred_check.isChecked(),
            "timeout": self.timeout_spin.value(),
        }
        if transport == "stdio":
            args_text = self.args_edit.text().strip()
            cfg["command"] = self.command_edit.text().strip()
            cfg["args"] = args_text.split() if args_text else []
            cfg["env"] = {}
        else:
            cfg["url"] = self.url_edit.text().strip()
            cfg["headers"] = self._collect_headers()
            ca = self.ca_edit.text().strip()
            cert = self.cert_edit.text().strip()
            key = self.key_edit.text().strip()
            if ca:
                cfg["ca_bundle"] = ca
            if cert:
                cfg["client_cert"] = cert
            if key:
                cfg["client_key"] = key
        return cfg
```

Replace `SettingsDialog._mcp_list_label` (lines 1495-1507) with:

```python
    @staticmethod
    def _mcp_list_label(entry: dict) -> str:
        """Build display label for an MCP server entry."""
        tags = []
        if not entry.get("enabled", True):
            tags.append("disabled")
        if entry.get("deferred", True):
            tags.append("deferred")
        timeout = int(entry.get("timeout", 600))
        if timeout != 600:
            tags.append(f"{timeout}s")
        prefix = f"({', '.join(tags)}) " if tags else ""
        transport = entry.get("transport", "stdio")
        if transport in ("sse", "http"):
            target = f"[{transport}] {entry.get('url', '')}"
        else:
            args = " ".join(entry.get("args", []))
            target = f"{entry.get('command', '')} {args}".strip()
        return f"{prefix}{entry.get('name', '?')} — {target}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_add_server_dialog.py -v`
Expected: PASS (all green; module skipped only if PySide is unavailable in the dev venv).

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/ui/settings_dialog.py tests/unit/test_mcp_add_server_dialog.py
git commit -m "feat(ui): transport selector + URL/headers/TLS fields in MCP dialog (#41)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: CHANGELOG entry + full-suite green

**Files:**
- Modify: `CHANGELOG.md` (repo root)
- Test: whole suite.

**Interfaces:** none.

- [ ] **Step 1: Add the CHANGELOG entry**

Open `CHANGELOG.md`. Under a new `## [Unreleased]` section at the top (create it if
absent; the actual version bump happens at release time, not here), add:

```markdown
## [Unreleased]

### Added
- **Connect to MCP servers by URL** (#41) — new HTTP/SSE **client** transports
  alongside STDIO: a legacy HTTP+SSE client and a Streamable HTTP client,
  selectable per server in Add MCP Server. Supports remote `https://`, custom
  auth headers, and optional custom CA bundle / client certificate (mTLS).
  Plain `http://` is allowed only to localhost. Still zero external
  dependencies. This is the client counterpart to the v0.17.0-alpha HTTP/SSE
  server transport.
```

- [ ] **Step 2: Run the full unit suite**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS — baseline 960 plus the new tests from Tasks 1-7, zero failures.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for MCP URL client transport (#41)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Notes for the implementer

- **Do not touch `StdioClientTransport`.** Its inline correlation stays; the new
  `_RequestCorrelator` is only for `SSEClientTransport`.
- **Property vs. method:** `is_alive` is a **property** on every transport — match
  `StdioClientTransport` so `MCPClient.is_connected` (`self._transport.is_alive`)
  works unchanged.
- **Timeouts:** `send_request`'s default `timeout=30` matches stdio and is what
  `connect()` uses for `initialize`/`tools/list`. `call_tool` passes the
  per-server `tool_call_timeout` (default 600s) for `tools/call`.
- **Error surfacing:** transport-level failures return a JSON-RPC error dict from
  `send_request` rather than raising, so `MCPClient.call_tool` turns them into
  `MCPToolResult(is_error=True)`. For `connect()`, an error dict on `initialize`
  makes `connect()` raise `RuntimeError`, which `connect_all` logs and skips.
- **Release follow-ups (out of this plan's code scope):** version bump +
  `package.xml`/`freecad_ai/__init__.py`, wiki `MCP` page, and any forum note are
  handled at release time, not in these tasks.
```

