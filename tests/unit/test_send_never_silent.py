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

    def test_shutdown_hook_has_no_stray_close_event(self):
        src = (_UI / "chat_dock" / "layout.py").read_text()
        body = src.split("def _mark_shutdown")[1].split("\n    def ")[0]
        assert "super().closeEvent" not in body  # regression: NameError on undefined 'event'
