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
