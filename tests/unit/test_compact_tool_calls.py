"""Compact tool-call lines, code-block action bar, and streamed-token hints.

Covers the chat rendering revamp: completed tool calls render as a single
compact line (status, name, args summary, duration, "details" anchor)
instead of dumping raw output, and code blocks carry Copy / Review & Run
anchor actions in their header.
"""

from pathlib import Path

from freecad_ai.ui.message_view import (
    render_tool_call,
    render_code_block,
    _format_elapsed,
)
from freecad_ai.ui.chat_utils import _summarize_tool_args


class TestFormatElapsed:
    def test_sub_second_uses_milliseconds(self):
        assert _format_elapsed(0.42) == "420ms"

    def test_seconds_one_decimal(self):
        assert _format_elapsed(2.34) == "2.3s"


class TestSummarizeToolArgs:
    def test_dict_key_values(self):
        s = _summarize_tool_args({"length": 20, "name": "Box"})
        assert "length=20" in s and "name=Box" in s

    def test_json_string_input(self):
        s = _summarize_tool_args('{"radius": 5}')
        assert s == "radius=5"

    def test_long_string_value_truncated(self):
        s = _summarize_tool_args({"code": "x" * 100})
        assert "…" in s and len(s) < 100

    def test_list_and_dict_values_collapsed(self):
        s = _summarize_tool_args({"geometries": [1, 2, 3], "opts": {"a": 1}})
        assert "geometries=[3 items]" in s
        assert "opts={…}" in s

    def test_invalid_json_returns_empty(self):
        assert _summarize_tool_args("not json") == ""

    def test_empty_and_non_dict_return_empty(self):
        assert _summarize_tool_args({}) == ""
        assert _summarize_tool_args(None) == ""

    def test_overall_cap(self):
        s = _summarize_tool_args({f"key{i}": "v" * 20 for i in range(10)})
        assert len(s) <= 81  # max_len + ellipsis


class TestCompactToolCallLine:
    def test_success_line_has_name_elapsed_and_summary(self):
        html = render_tool_call(
            "create_primitive", "call_1", started=False, success=True,
            output="Created Box", elapsed=0.4,
            args_summary="shape=box, size=20",
            detail_anchor="tooldetail:call_1",
        )
        assert "<b>create_primitive</b>" in html
        assert "400ms" in html
        assert "shape=box, size=20" in html

    def test_detail_anchor_replaces_raw_output_dump(self):
        html = render_tool_call(
            "list_faces", "call_2", started=False, success=True,
            output="Face1\nFace2\n" * 50, detail_anchor="tooldetail:call_2",
        )
        assert 'href="tooldetail:call_2"' in html
        assert "<pre" not in html  # no raw dump when a details link exists

    def test_legacy_output_dump_without_anchor(self):
        html = render_tool_call(
            "list_faces", "call_3", started=False, success=True,
            output="Face1",
        )
        assert "<pre" in html and "Face1" in html

    def test_error_line_uses_error_icon(self):
        html = render_tool_call(
            "create_sketch", "call_4", started=False, success=False,
            output="Error: Face7 is not planar",
            detail_anchor="tooldetail:call_4",
        )
        assert "&#10007;" in html  # ✕

    def test_args_summary_is_escaped(self):
        html = render_tool_call(
            "t", "c", started=False, success=True,
            args_summary='label=<b>"x"</b>',
        )
        assert "<b>\"x\"</b>" not in html
        assert "&lt;b&gt;" in html

    def test_started_state_unchanged(self):
        html = render_tool_call("pad_sketch", "call_5", started=True)
        assert "pad_sketch" in html and "&#9881;" in html


class TestCodeBlockActions:
    def test_header_has_copy_and_execute_anchors(self):
        html = render_code_block("box = 1", "python")
        assert 'href="copy:' in html
        assert 'href="execute:' in html
        assert "python" in html

    def test_anchors_carry_base64_code(self):
        import base64
        code = "doc.recompute()"
        html = render_code_block(code, "python")
        encoded = base64.b64encode(code.encode()).decode()
        assert f'copy:{encoded}' in html
        assert f'execute:{encoded}' in html

    def test_actions_disabled(self):
        html = render_code_block("x = 1", "python", actions=False)
        assert "copy:" not in html and "execute:" not in html

    def test_code_still_escaped(self):
        html = render_code_block("a < b", "python")
        assert "a &lt; b" in html


_UI = Path(__file__).resolve().parents[2] / "freecad_ai" / "ui"


def _src(relpath: str) -> str:
    return (_UI / relpath).read_text()


class TestWiringSourceAudit:
    """Source-level checks for the Qt-dependent glue (no QApplication here)."""

    def test_worker_signal_carries_elapsed_and_args(self):
        src = _src("chat_workers.py")
        assert "tool_call_finished = Signal(str, str, bool, str, float, str)" in src
        assert "self.tool_call_finished.emit(" in src
        assert "args_json" in src

    def test_streaming_handler_stores_details_and_anchor(self):
        src = _src("chat_dock/streaming.py")
        assert "_tool_call_details" in src
        assert "tooldetail:" in src
        assert "_summarize_tool_args" in src

    def test_streaming_counts_streamed_chars(self):
        src = _src("chat_dock/streaming.py")
        assert src.count("_stream_chars") >= 2  # _on_token and _on_thinking

    def test_display_handles_tooldetail_anchor(self):
        src = _src("chat_dock/display.py")
        assert 'startswith("tooldetail:")' in src
        assert "_show_tool_detail_dialog" in src

    def test_activity_tick_appends_token_estimate(self):
        src = _src("chat_dock/display.py")
        assert "_stream_chars" in src and "tok" in src

    def test_rerender_uses_compact_lines_not_json_dump(self):
        src = _src("chat_dock/display.py")
        assert "Called with:" not in src
        assert "_summarize_tool_args" in src
        assert "detail_anchor" in src

    def test_loading_resets_stream_counter(self):
        src = _src("chat_dock/display.py")
        assert "self._stream_chars = 0" in src
