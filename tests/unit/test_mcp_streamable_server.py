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
