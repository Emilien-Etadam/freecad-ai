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
