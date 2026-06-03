"""Live activity hints for assistant streaming."""
from freecad_ai.ui.message_view import (
    render_stream_activity_hint,
    render_thinking_stream_open,
)


def test_activity_hint_thinking():
    html = render_stream_activity_hint(kind="thinking")
    assert "Reflecting" in html or "Réflexion" in html
    assert "<span" in html


def test_thinking_stream_open_has_reflection_header():
    html = render_thinking_stream_open()
    assert "Reflection" in html or "Réflexion" in html or "Thinking" in html
    assert "progress" in html or "cours" in html
