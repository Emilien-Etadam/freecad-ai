"""Regression: chat UI must route HTML rendering through palette-aware helpers."""

import re
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[2] / "freecad_ai" / "ui"
CHAT_WIDGET = UI_ROOT / "chat_widget.py"
CHAT_DOCK = UI_ROOT / "chat_dock"


def _chat_sources() -> str:
    parts = [CHAT_WIDGET.read_text(encoding="utf-8")]
    for py in sorted(CHAT_DOCK.glob("*.py")):
        parts.append(py.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _chat_widget_facade() -> str:
    return CHAT_WIDGET.read_text(encoding="utf-8")


def test_chat_dock_defines_palette_wrappers():
    body = _chat_widget_facade()
    assert "return render_message(role, content, palette=self.palette(), ts=ts)" in body
    assert "palette=self.palette()" in body.split("def _render_tool_call")[1].split("def _render_execution")[0]


def test_forbidden_raw_render_entry_points():
    source = _chat_sources()
    forbidden = [
        "_append_html(render_message(",
        "_append_html(render_tool_call(",
        "_append_html(render_execution_result(",
        "html_parts.append(render_message(",
        "html_parts.append(render_tool_call(",
        "_append_html(render_tool_summary(",
    ]
    for pattern in forbidden:
        assert pattern not in source, f"found forbidden pattern: {pattern}"


def test_required_palette_wrappers_used():
    source = _chat_sources()
    assert source.count("_append_html(self._render_message(") >= 10
    assert "_append_html(self._render_tool_call(" in source
    assert "_append_html(self._render_execution_result(" in source


def test_rerender_chat_uses_palette_wrappers():
    display = (CHAT_DOCK / "display.py").read_text(encoding="utf-8")
    rerender = display.split("def _rerender_chat")[1].split("\n    def ")[0]
    assert "self._render_message(" in rerender
    assert "html_parts.append(render_message(" not in rerender
