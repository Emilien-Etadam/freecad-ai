# Streamable HTTP Server Transport (#65) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve MCP over Streamable HTTP at `POST /mcp` on the same listener that already serves HTTP+SSE, so a client connects with whichever of the two transports it speaks.

**Architecture:** `SSEServerTransport` is renamed `HTTPServerTransport` (the old name stays as an alias) and gains a third route inside its existing `RequestHandler`. No new class, no new socket, no new configuration: the bind/serve/stop lifecycle, the `Host`/`Origin` gate and the `MCP_ALLOWED_HOSTS` allowlist all carry over untouched. `POST /mcp` answers a request inline as `application/json`, answers a notification with `202`, and issues no session id.

**Tech Stack:** Python 3.11, standard library only (`http.server`, `socketserver`, `json`, `urllib`), pytest.

**Spec:** `docs/superpowers/specs/2026-08-30-mcp-streamable-http-server-design.md`

## Global Constraints

- **Zero external dependencies.** `freecad_ai/mcp/` is stdlib-only. Do not add a package.
- **Run tests with:** `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
  A shell-exported `PYTHONPATH` shadows the venv's pluggy and crashes pytest; `test_document_attach.py` Qt-segfaults even on clean master.
- **Baseline before starting: 1147 passed.** Every task must end green at or above that count.
- **`transport.py` must never import `server.py`.** `server.py` imports `transport.py`; the reverse is an import cycle. Shared constants go in `protocol.py`.
- **The legacy path is untouched.** `GET /sse` and `POST /messages` keep their exact current behaviour, including the `202`-then-push-over-SSE reply shape. No existing SSE test may be modified except the advertised-URL assertion in Task 7.
- **`initialize` keeps answering `2025-03-26`.** Adopting a newer revision is #64, not this work.
- **Commit trailers** on every commit:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01BRah8RdoyrFLoWWka9Wbgb
  ```

---

### Task 1: Protocol version constants

**Files:**
- Modify: `freecad_ai/mcp/protocol.py:11-15` (add after the error-code block)
- Test: `tests/unit/test_protocol.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `protocol.DEFAULT_PROTOCOL_VERSION: str` and `protocol.SUPPORTED_PROTOCOL_VERSIONS: frozenset[str]`, both used by Task 5.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_protocol.py`:

```python
def test_default_protocol_version_matches_what_the_server_advertises():
    """The header default and the initialize reply must not drift apart."""
    from freecad_ai.mcp import server
    from freecad_ai.mcp import protocol

    assert protocol.DEFAULT_PROTOCOL_VERSION == server.PROTOCOL_VERSION


def test_supported_protocol_versions_contain_the_default():
    from freecad_ai.mcp import protocol

    assert protocol.DEFAULT_PROTOCOL_VERSION in protocol.SUPPORTED_PROTOCOL_VERSIONS


def test_supported_protocol_versions_exclude_the_2026_redesign():
    """2026-07-28 is a redesign we do not serve — claiming it would be a lie."""
    from freecad_ai.mcp import protocol

    assert "2026-07-28" not in protocol.SUPPORTED_PROTOCOL_VERSIONS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_protocol.py -q`
Expected: FAIL — `AttributeError: module 'freecad_ai.mcp.protocol' has no attribute 'DEFAULT_PROTOCOL_VERSION'`

- [ ] **Step 3: Add the constants**

In `freecad_ai/mcp/protocol.py`, immediately after `INTERNAL_ERROR = -32603`:

```python
# The revision we advertise from initialize, and the one a client is told to
# assume when it sends no MCP-Protocol-Version header.
DEFAULT_PROTOCOL_VERSION = "2025-03-26"

# Revisions the Streamable HTTP endpoint accepts in MCP-Protocol-Version.
# Wider than what initialize advertises on purpose: our whole wire surface
# (initialize, tools/list, tools/call, ping) is identical across these three,
# because 2025-06-18 and 2025-11-25 only add optional fields we neither emit
# nor require. Rejecting a client for naming one of them would be a 400 it
# could not act on. 2026-07-28 is excluded deliberately — it is a redesign,
# and accepting it would promise behaviour we do not have (#64).
SUPPORTED_PROTOCOL_VERSIONS = frozenset({
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_protocol.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/mcp/protocol.py tests/unit/test_protocol.py
git commit -m "feat(#65): add the protocol revisions the HTTP endpoint accepts"
```

---

### Task 2: Rename the transport, keep the old name working

**Files:**
- Modify: `freecad_ai/mcp/transport.py:5` (module docstring), `freecad_ai/mcp/transport.py:519` (class statement and docstring), and add the alias after the class body ends at line 801
- Test: `tests/unit/test_mcp_sse_transport.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `HTTPServerTransport` — the class formerly named `SSEServerTransport`, same constructor `(host="127.0.0.1", port=3000, allowed_hosts=None, allowed_origins=())` and same `bind()` / `serve(handler=None)` / `stop()` / `run(handler)` methods. `SSEServerTransport` remains bound to the same object. Tasks 3-7 refer to the class as `HTTPServerTransport`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mcp_sse_transport.py`:

```python
def test_sse_server_transport_is_an_alias_of_http_server_transport():
    """The class serves /mcp too now, but the old name must keep importing.

    mcp_server_entry.py, the wiki and user scripts all name SSEServerTransport.
    """
    from freecad_ai.mcp.transport import HTTPServerTransport, SSEServerTransport

    assert SSEServerTransport is HTTPServerTransport
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_sse_transport.py::test_sse_server_transport_is_an_alias_of_http_server_transport -q`
Expected: FAIL — `ImportError: cannot import name 'HTTPServerTransport'`

- [ ] **Step 3: Rename the class and add the alias**

In `freecad_ai/mcp/transport.py`, change line 519 from `class SSEServerTransport:` to `class HTTPServerTransport:`, and replace the first two lines of its docstring with:

```python
class HTTPServerTransport:
    """Server-side transport: serves MCP over HTTP on one listener.

    Endpoints:
        POST /mcp       — Streamable HTTP; the JSON-RPC reply comes back
                          inline as application/json
        GET  /mcp       — 405; this server offers no server-to-client stream
        GET  /sse       — legacy HTTP+SSE event stream (client subscribes here)
        POST /messages  — legacy HTTP+SSE requests (responses arrive via SSE)

    Both transports run side by side so a client connects with whichever it
    speaks. HTTP+SSE was deprecated in 2026-07-28 with a minimum twelve-month
    removal window (#65); the spec's own guidance for servers is to host both
    during it.
```

Leave the rest of the docstring (the single-client note and the `Host`/`Origin`
paragraph) unchanged.

Update the module docstring at line 5 from
`SSEServerTransport  — serves MCP over HTTP with Server-Sent Events.`
to
`HTTPServerTransport — serves MCP over HTTP: Streamable HTTP and HTTP+SSE.`

Then append at the very end of the file (after `_write_locked`, at module level, unindented):

```python
# The class was SSE-only until #65 added the Streamable HTTP route. The old
# name stays bound to it permanently: mcp_server_entry.py, the wiki and user
# scripts all import it, and nothing is gained by breaking them.
SSEServerTransport = HTTPServerTransport
```

- [ ] **Step 4: Run the full suite to verify nothing broke**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS, 1151 passed (baseline 1147 + Task 1's 3 + this task's 1)

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/mcp/transport.py tests/unit/test_mcp_sse_transport.py
git commit -m "refactor(#65): rename SSEServerTransport to HTTPServerTransport"
```

---

### Task 3: `POST /mcp` — the Streamable HTTP route

**Files:**
- Modify: `freecad_ai/mcp/transport.py:693-699` (`do_POST`) and add `_handle_streamable` after `_handle_messages` (which ends around line 754)
- Create: `tests/unit/test_mcp_streamable_server.py`

**Interfaces:**
- Consumes: `protocol.PARSE_ERROR`, `protocol.INVALID_REQUEST`, `protocol.INTERNAL_ERROR`, `protocol.make_error` (all pre-existing); `HTTPServerTransport` from Task 2.
- Produces: the `_RunningServer` context manager and the `_post` helper in `tests/unit/test_mcp_streamable_server.py`, reused by Tasks 4, 5 and 6.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_mcp_streamable_server.py`:

```python
"""Tests for the Streamable HTTP server route (#65).

The transport serves /mcp alongside the legacy /sse + /messages pair. These
tests drive a really-bound loopback socket rather than a mocked handler,
because the behaviour under test is HTTP status codes and headers.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from freecad_ai.mcp import protocol
from freecad_ai.mcp.transport import HTTPServerTransport


def _echo_handler(msg):
    """Answer any request with a result; stay silent for notifications."""
    if msg.get("id") is None:
        return None
    return protocol.make_response(msg["id"], {"echoed": msg.get("method")})


class _RunningServer:
    """Serve HTTPServerTransport on an ephemeral loopback port, in a thread."""

    def __init__(self, handler=_echo_handler, **kwargs):
        self._handler = handler
        self._kwargs = kwargs

    def __enter__(self):
        self.transport = HTTPServerTransport(host="127.0.0.1", port=0,
                                             **self._kwargs)
        self.transport._handler = self._handler
        self.httpd = self.transport._make_server()
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def _request(port, path="/mcp", method="POST", data=None, headers=None):
    """Return (status, body_bytes, headers) without raising on 4xx."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method=method,
        headers=headers or {})
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


def _post(port, payload, headers=None):
    """POST a JSON-RPC message to /mcp. ``payload`` is encoded verbatim if bytes."""
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    return _request(port, data=body, headers=hdrs)


class TestStreamableRequests:
    def test_a_request_gets_its_response_inline_as_json(self):
        with _RunningServer() as srv:
            status, body, headers = _post(
                srv.port, {"jsonrpc": "2.0", "id": 7, "method": "ping"})

        assert status == 200
        assert headers.get("Content-Type") == "application/json"
        assert json.loads(body) == {
            "jsonrpc": "2.0", "id": 7, "result": {"echoed": "ping"}}

    def test_no_session_id_is_ever_issued(self):
        """We keep no cross-request state, and 2026-07-28 removes sessions."""
        with _RunningServer() as srv:
            _, _, headers = _post(
                srv.port, {"jsonrpc": "2.0", "id": 1, "method": "ping"})

        assert headers.get("Mcp-Session-Id") is None

    def test_a_notification_is_accepted_with_no_body(self):
        with _RunningServer() as srv:
            status, body, _ = _post(
                srv.port,
                {"jsonrpc": "2.0", "method": "notifications/initialized"})

        assert status == 202
        assert body == b""

    def test_unparseable_json_is_a_parse_error(self):
        with _RunningServer() as srv:
            status, body, _ = _post(srv.port, b"{not json")

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.PARSE_ERROR

    def test_a_batch_array_is_an_invalid_request(self):
        """2025-03-26 permits batches; /messages never handled them and
        2025-11-25 removes them again. Refusing beats half-processing."""
        with _RunningServer() as srv:
            status, body, _ = _post(
                srv.port, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.INVALID_REQUEST

    def test_a_bare_scalar_body_is_an_invalid_request(self):
        with _RunningServer() as srv:
            status, body, _ = _post(srv.port, 42)

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.INVALID_REQUEST

    def test_a_request_the_handler_ignores_becomes_an_internal_error(self):
        """A request MUST get a response. A silent handler is a server bug —
        say so, rather than leaving the client to time out on an empty body."""
        with _RunningServer(handler=lambda msg: None) as srv:
            status, body, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 3, "method": "ping"})

        assert status == 200
        decoded = json.loads(body)
        assert decoded["id"] == 3
        assert decoded["error"]["code"] == protocol.INTERNAL_ERROR

    def test_a_raising_handler_becomes_a_jsonrpc_error_not_an_http_error(self):
        def _boom(msg):
            raise RuntimeError("tool exploded")

        with _RunningServer(handler=_boom) as srv:
            status, body, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 9, "method": "tools/call"})

        assert status == 200
        decoded = json.loads(body)
        assert decoded["error"]["code"] == protocol.INTERNAL_ERROR
        assert "tool exploded" in decoded["error"]["message"]


class TestStreamableAuthorization:
    """The Host and Origin guards must cover /mcp, not just /messages."""

    def test_a_cross_origin_post_is_rejected(self):
        with _RunningServer() as srv:
            status, _, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Origin": "https://evil.example"})

        assert status == 403

    def test_a_non_loopback_host_header_is_rejected(self):
        with _RunningServer() as srv:
            status, _, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Host": "attacker.example"})

        assert status == 403

    def test_an_explicit_allowlist_admits_the_host_it_names(self):
        with _RunningServer(allowed_hosts=["fileserver.local"]) as srv:
            status, _, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Host": "fileserver.local"})

        assert status == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_streamable_server.py -q`
Expected: FAIL — every test in `TestStreamableRequests` returns `404`, because `do_POST` only routes `/messages`. (`TestStreamableAuthorization`'s two rejection tests may already pass, since `_authorized()` runs before routing — that is correct and they stay as regression cover.)

- [ ] **Step 3: Add the route and the handler**

In `freecad_ai/mcp/transport.py`, replace `do_POST` (lines 693-699):

```python
            def do_POST(self):
                if not self._authorized():
                    return
                path = self._base_path()
                if path == "/messages":
                    self._handle_messages()
                elif path == "/mcp":
                    self._handle_streamable()
                else:
                    self.send_error(404)
```

Then add `_handle_streamable` immediately after `_handle_messages`:

```python
            def _handle_streamable(self):
                """Serve one Streamable HTTP POST.

                Unlike ``/messages``, the reply travels back in this very
                response — there is no side channel and no client to have
                dropped, so none of the SSE bookkeeping applies.

                The request/notification split is decided by ``id``, not by
                whether the handler produced something: a request that gets no
                response is a server bug, and answering it with a bare 202
                would surface as an unparseable empty body on the client.
                """
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")

                try:
                    msg = json.loads(body)
                except json.JSONDecodeError:
                    self._send_json(400, protocol.make_error(
                        None, protocol.PARSE_ERROR, "Parse error"))
                    return

                if not isinstance(msg, dict):
                    self._send_json(400, protocol.make_error(
                        None, protocol.INVALID_REQUEST,
                        "Expected a single JSON-RPC object. Batched requests "
                        "are not supported."))
                    return

                msg_id = msg.get("id")
                try:
                    response = transport._handler(msg) if transport._handler else None
                except Exception as e:
                    response = protocol.make_error(
                        msg_id, protocol.INTERNAL_ERROR, str(e))

                if msg_id is None:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                if response is None:
                    response = protocol.make_error(
                        msg_id, protocol.INTERNAL_ERROR,
                        "Server produced no response for method %r"
                        % msg.get("method"))

                self._send_json(200, response)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_streamable_server.py -q`
Expected: PASS, 11 passed

- [ ] **Step 5: Run the full suite**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS, count only rises

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/mcp/transport.py tests/unit/test_mcp_streamable_server.py
git commit -m "feat(#65): serve Streamable HTTP requests at POST /mcp"
```

---

### Task 4: `GET` and `DELETE` on `/mcp`

**Files:**
- Modify: `freecad_ai/mcp/transport.py` — `do_GET` (lines 685-691) and a new `do_DELETE` beside it
- Test: `tests/unit/test_mcp_streamable_server.py`

**Interfaces:**
- Consumes: `_RunningServer` and `_request` from Task 3.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mcp_streamable_server.py`:

```python
class TestStreamableMethods:
    def test_get_on_mcp_is_405_with_an_allow_header(self):
        """A server offering no server-to-client stream may refuse GET, and
        the spec says so explicitly. Allow: POST tells the client what to do."""
        with _RunningServer() as srv:
            status, _, headers = _request(srv.port, method="GET")

        assert status == 405
        assert headers.get("Allow") == "POST"

    def test_delete_on_mcp_is_405(self):
        """DELETE terminates a session; we never issue one."""
        with _RunningServer() as srv:
            status, _, headers = _request(srv.port, method="DELETE")

        assert status == 405
        assert headers.get("Allow") == "POST"

    def test_delete_is_authorized_like_every_other_verb(self):
        """_authorized() is called per-verb by hand, not by middleware, so a
        new verb silently skips the guard unless it calls it."""
        with _RunningServer() as srv:
            status, _, _ = _request(
                srv.port, method="DELETE",
                headers={"Host": "attacker.example"})

        assert status == 403

    def test_an_unknown_path_is_still_404(self):
        with _RunningServer() as srv:
            status, _, _ = _request(srv.port, path="/nope", method="GET")

        assert status == 404

    def test_the_legacy_messages_path_still_answers_post(self):
        """/mcp must not have stolen the legacy route on the way in."""
        with _RunningServer() as srv:
            status, _, _ = _request(
                srv.port, path="/messages",
                data=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
                headers={"Content-Type": "application/json"})

        assert status == 202
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_streamable_server.py::TestStreamableMethods -q`
Expected: FAIL — `GET /mcp` returns 404, and `DELETE` returns 501 (`BaseHTTPRequestHandler` has no `do_DELETE`)

- [ ] **Step 3: Add the routes**

In `freecad_ai/mcp/transport.py`, replace `do_GET` (lines 685-691) and add `do_DELETE` and `_send_method_not_allowed` beside it:

```python
            def do_GET(self):
                if not self._authorized():
                    return
                path = self._base_path()
                if path == "/sse":
                    self._handle_sse()
                elif path == "/mcp":
                    # The spec allows a server that offers no server-to-client
                    # stream to refuse GET outright, and we originate no
                    # server-initiated messages.
                    self._send_method_not_allowed()
                else:
                    self.send_error(404)

            def do_DELETE(self):
                # _authorized() is invoked per-verb by hand — BaseHTTPRequest-
                # Handler has no dispatch layer to hook — so a new verb that
                # forgets this call silently bypasses the Host/Origin guard.
                if not self._authorized():
                    return
                if self._base_path() == "/mcp":
                    # DELETE terminates a session. We issue none.
                    self._send_method_not_allowed()
                else:
                    self.send_error(404)

            def _send_method_not_allowed(self):
                self.send_response(405)
                self.send_header("Allow", "POST")
                self.send_header("Content-Length", "0")
                self.end_headers()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_streamable_server.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS — in particular every existing `/sse` and `/messages` test still green

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/mcp/transport.py tests/unit/test_mcp_streamable_server.py
git commit -m "feat(#65): answer GET and DELETE on /mcp with 405"
```

---

### Task 5: `MCP-Protocol-Version` enforcement

**Files:**
- Modify: `freecad_ai/mcp/transport.py` — the top of `_handle_streamable` (added in Task 3)
- Test: `tests/unit/test_mcp_streamable_server.py`

**Interfaces:**
- Consumes: `protocol.SUPPORTED_PROTOCOL_VERSIONS` from Task 1; `_RunningServer` and `_post` from Task 3.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mcp_streamable_server.py`:

```python
class TestProtocolVersionHeader:
    _PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}

    def test_a_missing_header_is_assumed_to_be_the_default_revision(self):
        """Spec SHOULD: absent header means 2025-03-26, which is what we speak."""
        with _RunningServer() as srv:
            status, _, _ = _post(srv.port, self._PING)

        assert status == 200

    @pytest.mark.parametrize("version",
                             ["2025-03-26", "2025-06-18", "2025-11-25"])
    def test_every_revision_we_can_serve_is_accepted(self, version):
        with _RunningServer() as srv:
            status, _, _ = _post(srv.port, self._PING,
                                 headers={"MCP-Protocol-Version": version})

        assert status == 200

    def test_the_2026_redesign_is_rejected(self):
        with _RunningServer() as srv:
            status, body, _ = _post(
                srv.port, self._PING,
                headers={"MCP-Protocol-Version": "2026-07-28"})

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.INVALID_REQUEST

    def test_a_garbage_version_is_rejected(self):
        with _RunningServer() as srv:
            status, _, _ = _post(
                srv.port, self._PING,
                headers={"MCP-Protocol-Version": "banana"})

        assert status == 400

    def test_the_rejection_names_what_we_support(self):
        """A 400 a client cannot act on is the #60 failure mode again."""
        with _RunningServer() as srv:
            _, body, _ = _post(
                srv.port, self._PING,
                headers={"MCP-Protocol-Version": "2026-07-28"})

        message = json.loads(body)["error"]["message"]
        for version in protocol.SUPPORTED_PROTOCOL_VERSIONS:
            assert version in message

    def test_the_legacy_messages_path_ignores_the_header(self):
        """Changing /messages would break clients this work is not about."""
        with _RunningServer() as srv:
            status, _, _ = _request(
                srv.port, path="/messages",
                data=json.dumps(self._PING).encode(),
                headers={"Content-Type": "application/json",
                         "MCP-Protocol-Version": "2026-07-28"})

        assert status == 202
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_streamable_server.py::TestProtocolVersionHeader -q`
Expected: FAIL — `test_the_2026_redesign_is_rejected`, `test_a_garbage_version_is_rejected` and `test_the_rejection_names_what_we_support` all get 200, because the header is not read yet

- [ ] **Step 3: Add the check**

In `freecad_ai/mcp/transport.py`, insert at the very top of `_handle_streamable`'s body, before `length = int(...)`:

```python
                # Absent means "assume 2025-03-26" (spec SHOULD), which is what
                # we speak. A named revision we cannot serve is a 400 (spec
                # MUST) whose body says which ones we can — a rejection the
                # client cannot act on is how #60 read to its users.
                version = self.headers.get("MCP-Protocol-Version")
                if (version is not None
                        and version not in protocol.SUPPORTED_PROTOCOL_VERSIONS):
                    self._send_json(400, protocol.make_error(
                        None, protocol.INVALID_REQUEST,
                        "Unsupported MCP-Protocol-Version %r. This server "
                        "speaks %s." % (
                            version,
                            ", ".join(sorted(
                                protocol.SUPPORTED_PROTOCOL_VERSIONS)))))
                    return
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_streamable_server.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/mcp/transport.py tests/unit/test_mcp_streamable_server.py
git commit -m "feat(#65): validate MCP-Protocol-Version on the /mcp route"
```

---

### Task 6: Round-trip against our own client transport

**Files:**
- Test: `tests/unit/test_mcp_streamable_server.py` (no production code changes)

**Interfaces:**
- Consumes: `StreamableHTTPClientTransport` from `freecad_ai/mcp/transport.py:373`; `_RunningServer` from Task 3.
- Produces: nothing.

This task adds no implementation. If a test here fails, the bug is in Tasks 3-5 — fix it there.

- [ ] **Step 1: Write the round-trip tests**

Append to `tests/unit/test_mcp_streamable_server.py`:

```python
class TestClientServerRoundTrip:
    """Our own Streamable HTTP client against our own Streamable HTTP server.

    The client has spoken this transport since #41 and already sends
    ``Accept: application/json, text/event-stream`` and parses an inline JSON
    body, so the two halves exercise each other with no external MCP client.
    """

    @staticmethod
    def _mcp_handler(msg):
        method = msg.get("method")
        msg_id = msg.get("id")
        if msg_id is None:
            return None
        if method == "initialize":
            return protocol.make_response(msg_id, {
                "protocolVersion": protocol.DEFAULT_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test", "version": "0"},
            })
        if method == "tools/list":
            return protocol.make_response(msg_id, {
                "tools": [{"name": "ping", "description": "",
                           "inputSchema": {"type": "object"}}]})
        if method == "tools/call":
            return protocol.make_response(msg_id, {
                "content": [{"type": "text", "text": "pong"}],
                "isError": False})
        return protocol.make_error(msg_id, protocol.METHOD_NOT_FOUND, method)

    def test_handshake_list_and_call(self):
        from freecad_ai.mcp.transport import StreamableHTTPClientTransport

        with _RunningServer(handler=self._mcp_handler) as srv:
            client = StreamableHTTPClientTransport(
                f"http://127.0.0.1:{srv.port}/mcp", connect_timeout=5)
            client.start()
            try:
                init = client.send_request(
                    "initialize",
                    {"protocolVersion": protocol.DEFAULT_PROTOCOL_VERSION},
                    timeout=5)
                assert init["result"]["protocolVersion"] == \
                    protocol.DEFAULT_PROTOCOL_VERSION

                tools = client.send_request("tools/list", timeout=5)
                assert tools["result"]["tools"][0]["name"] == "ping"

                call = client.send_request(
                    "tools/call", {"name": "ping", "arguments": {}}, timeout=5)
                assert call["result"]["content"][0]["text"] == "pong"
            finally:
                client.stop()

    def test_a_notification_round_trips_without_a_body(self):
        from freecad_ai.mcp.transport import StreamableHTTPClientTransport

        with _RunningServer(handler=self._mcp_handler) as srv:
            client = StreamableHTTPClientTransport(
                f"http://127.0.0.1:{srv.port}/mcp", connect_timeout=5)
            client.start()
            try:
                # Reads and closes the 202; must not raise on the empty body.
                client.send_notification("notifications/initialized")
            finally:
                client.stop()

    def test_the_client_never_picks_up_a_session_id(self):
        from freecad_ai.mcp.transport import StreamableHTTPClientTransport

        with _RunningServer(handler=self._mcp_handler) as srv:
            client = StreamableHTTPClientTransport(
                f"http://127.0.0.1:{srv.port}/mcp", connect_timeout=5)
            client.start()
            try:
                client.send_request("initialize", {}, timeout=5)
                assert client._session_id is None
            finally:
                client.stop()
```

- [ ] **Step 2: Run the tests**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_streamable_server.py::TestClientServerRoundTrip -q`
Expected: PASS — Tasks 3-5 already implement everything these exercise. If any fails, fix the transport, not the test.

- [ ] **Step 3: Run the full suite**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_mcp_streamable_server.py
git commit -m "test(#65): round-trip our Streamable HTTP client against our server"
```

---

### Task 7: Advertise `/mcp` as the server URL

**Files:**
- Modify: `freecad_ai/mcp/gui_server.py:145-168` (the `SSEServerTransport` import, the returned URL and the log line)
- Modify: `tests/unit/test_mcp_gui_server.py:178`
- Test: `tests/unit/test_mcp_gui_server.py`

**Interfaces:**
- Consumes: `HTTPServerTransport` from Task 2.
- Produces: `ServerController.start()` now returns `"http://%s:%d/mcp"`. Task 8 documents that string.

- [ ] **Step 1: Write the failing test**

Change `tests/unit/test_mcp_gui_server.py:178` from

```python
        assert url == "http://127.0.0.1:%d/sse" % port
```

to

```python
        assert url == "http://127.0.0.1:%d/mcp" % port
```

and append to the same file:

```python
def test_the_advertised_url_is_the_streamable_endpoint():
    """The toolbar writes this string to the Report view, so it is the URL
    users copy into a client config. Point it at the transport that is not
    on a removal clock; /sse keeps serving for anyone already on it."""
    controller = _controller()
    port = _free_port()
    try:
        url = controller.start("127.0.0.1", port)
        assert url.endswith("/mcp")
    finally:
        controller.stop()
```

Line 178 is inside `test_start_reports_running_and_returns_the_url`. The
`_controller()` factory (line 58) and `_free_port()` (line 24) already exist in
that file — use them; do not introduce new helpers.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_gui_server.py -q`
Expected: FAIL — `assert 'http://127.0.0.1:NNNN/sse' == 'http://127.0.0.1:NNNN/mcp'`

- [ ] **Step 3: Update the controller**

In `freecad_ai/mcp/gui_server.py`, change the import at line 145 from

```python
        from .transport import SSEServerTransport
```

to

```python
        from .transport import HTTPServerTransport
```

and line 147 from `transport = SSEServerTransport(host=host, port=port,` to
`transport = HTTPServerTransport(host=host, port=port,` (keep the second line's
`allowed_hosts=allowed_hosts)` indentation aligned).

Then replace the URL and log lines (around 165-167):

```python
        # The advertised URL is the Streamable HTTP endpoint: HTTP+SSE is
        # deprecated with a removal window (#65), so new configurations should
        # not be pointed at it. The legacy pair keeps serving regardless.
        self._url = "http://%s:%d/mcp" % (host, port)
        logger.info(
            "MCP server listening on %s (legacy HTTP+SSE also served at "
            "http://%s:%d/sse)", self._url, host, port)
        return self._url
```

Also rename the serve thread from `name="mcp-sse-server"` to
`name="mcp-http-server"` at line 160, and update the `docstring` reference at
line 128 from `see SSEServerTransport` to `see HTTPServerTransport`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_gui_server.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/mcp/gui_server.py tests/unit/test_mcp_gui_server.py
git commit -m "feat(#65): advertise the /mcp endpoint as the server URL"
```

---

### Task 8: Documentation

**Files:**
- Modify: `mcp_server_http.py:2-30` (module docstring) and its final `print` (line ~74)
- Modify: `CHANGELOG.md` — the existing `## [Unreleased]` section
- Test: `tests/unit/test_mcp_sse_transport.py` (the entry-point test that already asserts on the printed line, if one exists — grep first)

**Interfaces:**
- Consumes: the `/mcp` URL from Task 7.
- Produces: nothing.

- [ ] **Step 1: Check whether a test pins the printed banner**

Run: `grep -rn "MCP SSE server running" tests/ freecad_ai/ mcp_server_http.py`
If a test asserts on that string, update it in the same commit; if not, no test change is needed.

- [ ] **Step 2: Update the entry-point docstring and banner**

In `mcp_server_http.py`, replace the `MCP configuration:` block in the docstring:

```
MCP configuration (Streamable HTTP — preferred):
{
    "freecad": {
      "type": "http",
      "url": "http://127.0.0.1:3000/mcp"
    }
}

The legacy HTTP+SSE endpoint stays available at http://127.0.0.1:3000/sse for
clients that only speak it. That transport was deprecated in the 2026-07-28
protocol revision with a removal window, so prefer /mcp for new configurations.
```

and change the summary line at the top from
`Starts FreeCAD with GUI and exposes all built-in tools via the MCP protocol over HTTP + Server-Sent Events, so you can watch FreeCAD update in real-time while an AI client calls tools.`
to
`Starts FreeCAD with GUI and exposes all built-in tools via the MCP protocol over HTTP — Streamable HTTP at /mcp and the legacy HTTP+SSE pair — so you can watch FreeCAD update in real-time while an AI client calls tools.`

Change the final print from

```python
print(f"MCP SSE server running on {url}", flush=True)
```

to

```python
print(f"MCP server running on {url}", flush=True)
```

- [ ] **Step 3: Add the CHANGELOG entry**

In `CHANGELOG.md`, under the existing `## [Unreleased]` → `### Added`, add as the
first bullet of that subsection:

```markdown
- **Streamable HTTP transport for the MCP server** — the server now answers
  `POST /mcp` with the JSON-RPC reply inline, alongside the existing
  `GET /sse` + `POST /messages` pair, on the same address and port. Clients
  connect with whichever transport they speak and nothing needs reconfiguring.
  HTTP+SSE was deprecated in the `2026-07-28` protocol revision with a
  twelve-month removal window, so the URL the toolbar and
  `mcp_server_http.py` report is now `http://host:port/mcp`; existing `/sse`
  configurations keep working. No session ids are issued and `GET /mcp`
  answers `405`, which is what the newer revisions expect anyway.
  ([#65](https://github.com/ghbalf/freecad-ai/issues/65))
```

- [ ] **Step 4: Run the full suite**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server_http.py CHANGELOG.md
git commit -m "docs(#65): document the /mcp endpoint and the deprecated /sse pair"
```

---

## Verification before opening the PR

- [ ] Full suite green: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
- [ ] `grep -rn "SSEServerTransport" freecad_ai/ mcp_server_http.py mcp_server_entry.py` — only the alias line in `transport.py` remains in production code
- [ ] Live probe against a real server, per `reference_verifying_mcp_server_locally`:
  ```bash
  curl -s -X POST http://127.0.0.1:3000/mcp \
       -H 'Content-Type: application/json' \
       -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -c 200
  curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/mcp
  ```
  Expected: a JSON body listing tools, then `405`.
- [ ] The wiki's MCP page still describes `/sse` — note it for a wiki update; the
      wiki lives in a separate repo (`freecad-ai-wiki`) and is not part of this PR.
