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
