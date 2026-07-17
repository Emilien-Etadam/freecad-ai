"""The Send path must never fail silently.

Any exception in _send_message surfaces as a system bubble + Report View
entry, clicking Stop gives visible feedback, and a second Stop click
force-detaches a worker stuck in a blocked network read (e.g. a vLLM/Ollama
server that accepts the connection but never answers while loading a model).
"""

from pathlib import Path

_UI = Path(__file__).resolve().parents[2] / "freecad_ai" / "ui"


def _send_src() -> str:
    return (_UI / "chat_dock" / "send.py").read_text()


class TestSendNeverSilent:
    def test_send_message_wraps_impl_in_try_except(self):
        src = _send_src()
        body = src.split("def _send_message(self):")[1].split("\n    def ")[0]
        assert "self._send_message_impl()" in body
        assert "except Exception" in body
        assert '_render_message(' in body  # error becomes a visible bubble
        assert "PrintError" in body        # and a Report View entry

    def test_stop_click_gives_feedback(self):
        src = _send_src()
        body = src.split("def _send_message_impl(self):")[1].split("\n    def ")[0]
        assert "requestInterruption()" in body
        assert "Stopping the current request" in body
        assert "_stop_requested" in body

    def test_second_stop_click_detaches(self):
        src = _send_src()
        body = src.split("def _send_message_impl(self):")[1].split("\n    def ")[0]
        assert "_detach_stuck_worker()" in body

    def test_detach_disconnects_all_worker_signals(self):
        src = _send_src()
        body = src.split("def _detach_stuck_worker(self):")[1].split("\n    def ")[0]
        for signal in ("token_received", "thinking_received", "response_finished",
                       "error_occurred", "tool_call_started", "tool_call_finished",
                       "tool_exec_requested", "vision_note"):
            assert signal in body, signal
        assert "self._worker = None" in body
        assert "_set_loading(False)" in body
        assert "Request abandoned" in body

    def test_new_request_resets_stop_flag(self):
        src = (_UI / "chat_dock" / "display.py").read_text()
        loading_body = src.split("def _set_loading")[1].split("\n    def ")[0]
        assert "self._stop_requested = False" in loading_body

    def test_empty_completion_is_never_silent(self):
        src = (_UI / "chat_dock" / "streaming.py").read_text()
        body = src.split("def _on_response_finished")[1].split("\n    def ")[0]
        assert "not full_response.strip()" in body
        assert "_render_thinking_block" in body  # orphaned reasoning is shown
        assert "Increase Max Tokens" in body     # actionable hint
        assert "empty response" in body          # no-reasoning variant

    def test_streamed_chunks_keep_edge_spaces(self):
        from freecad_ai.ui.message_view import (
            preserve_edge_spaces, render_thinking_stream_chunk)
        assert preserve_edge_spaces(" wants") == "&nbsp;wants"
        assert preserve_edge_spaces("user ") == "user&nbsp;"
        assert preserve_edge_spaces(" both ") == "&nbsp;both&nbsp;"
        assert preserve_edge_spaces("mid dle") == "mid dle"  # inner spaces untouched
        assert "&nbsp;wants" in render_thinking_stream_chunk(" wants")
        # The plain-token path goes through the same helper
        src = (_UI / "chat_dock" / "streaming.py").read_text()
        token_body = src.split("def _on_token")[1].split("\n    def ")[0]
        assert "preserve_edge_spaces" in token_body

    def test_tool_dispatch_always_delivers_a_result(self):
        src = (_UI / "chat_dock" / "streaming.py").read_text()
        assert "\nimport json\n" in src  # NameError here hung every tool call
        body = src.split("def _execute_tool_call")[1].split("\n    def ")[0]
        assert "except Exception" in body
        assert "Tool dispatch failed" in body
        assert "set_tool_result(result)" in body

    def test_no_module_uses_stdlib_without_importing_it(self):
        """A moved method whose import stayed behind hangs or crashes at
        runtime only (compileall can't catch NameError). Guard the whole
        ui/ tree for the common stdlib modules."""
        import re
        for path in sorted(_UI.rglob("*.py")):
            src = path.read_text()
            for mod in ("json", "time", "base64", "hashlib"):
                if re.search(rf"^(?!.*import).*\b{mod}\.\w", src, re.M):
                    has_import = re.search(
                        rf"^\s*(import {mod}\b|from {mod} import"
                        rf"|import {mod} as)", src, re.M)
                    assert has_import, f"{path.name} uses {mod}. without importing it"

    def test_shutdown_hook_has_no_stray_close_event(self):
        src = (_UI / "chat_dock" / "layout.py").read_text()
        body = src.split("def _mark_shutdown")[1].split("\n    def ")[0]
        assert "super().closeEvent" not in body  # regression: NameError on undefined 'event'
