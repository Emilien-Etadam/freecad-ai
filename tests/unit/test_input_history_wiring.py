"""Wiring tests for the chat dock's input history.

Uses the unbound-method-with-fake-self pattern (see
test_settings_dialog_provider_change.py) so we don't need a QApplication or a
real QTextEdit. Each test calls the relevant method off ChatDockWidget with a
SimpleNamespace/MagicMock standing in for the dock.
"""

import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# chat_widget imports via ui/compat.py which needs PySide6 or PySide2.
try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from PySide6.QtCore import QEvent, Qt  # noqa: E402

from freecad_ai.core.input_history import InputHistory  # noqa: E402
from freecad_ai.ui import chat_widget as cw  # noqa: E402


# ── helpers ─────────────────────────────────────────────────

def _make_self(*, messages=None, readonly=False, at_start=True, at_end=False,
               plain_text=""):
    """Build a fake-self for ChatDockWidget method calls.

    Provides just enough attribute surface to satisfy _refresh_input_history,
    _set_input_text, _handle_input_keypress, and _is_history_reset_key.
    """
    cursor = MagicMock()
    cursor.atStart.return_value = at_start
    cursor.atEnd.return_value = at_end
    cursor.movePosition = MagicMock()

    input_edit = MagicMock()
    input_edit.isReadOnly.return_value = readonly
    input_edit.toPlainText.return_value = plain_text
    input_edit.textCursor.return_value = cursor
    input_edit.setPlainText = MagicMock()
    input_edit.setTextCursor = MagicMock()

    conversation = SimpleNamespace(messages=messages or [])

    fake = SimpleNamespace(
        input_edit=input_edit,
        conversation=conversation,
        _input_history=InputHistory(),
        _suppress_history_reset=False,
    )
    # _handle_input_keypress calls self._set_input_text and
    # self._is_history_reset_key — wire both so the SimpleNamespace fake
    # satisfies those self.method() calls.
    fake._set_input_text = types.MethodType(
        cw.ChatDockWidget._set_input_text, fake
    )
    # _is_history_reset_key is a @staticmethod; assign the raw function.
    fake._is_history_reset_key = cw.ChatDockWidget._is_history_reset_key
    return fake


def _key_event(key, text="", modifiers=Qt.NoModifier):
    """Build a fake KeyPress event with the attributes the filter uses."""
    ev = MagicMock()
    ev.type.return_value = QEvent.KeyPress
    ev.key.return_value = key
    ev.text.return_value = text
    ev.modifiers.return_value = modifiers
    return ev


# ── _refresh_input_history ──────────────────────────────────

def test_refresh_filters_to_user_string_content():
    fake = _make_self(messages=[
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second"},
        # multipart user message (e.g. with image) — content is a list, skip
        {"role": "user", "content": [{"type": "text", "text": "skip me"}]},
        {"role": "user", "content": "third"},
    ])
    cw.ChatDockWidget._refresh_input_history(fake)
    # Cycle through to confirm entries — newest first, oldest clamps.
    assert fake._input_history.up("") == "third"
    assert fake._input_history.up("") == "second"
    assert fake._input_history.up("") == "first"
    assert fake._input_history.up("") is None


# ── _set_input_text ─────────────────────────────────────────

def test_set_input_text_brackets_suppress_flag_and_replaces():
    fake = _make_self()
    cw.ChatDockWidget._set_input_text(fake, "hello")
    fake.input_edit.setPlainText.assert_called_once_with("hello")
    fake.input_edit.setTextCursor.assert_called_once()
    # Flag was reset by the finally block.
    assert fake._suppress_history_reset is False


# ── _handle_input_keypress: Up/Down navigation ──────────────

# Tests call the dispatch helper directly to avoid the super().eventFilter
# fall-through in eventFilter (which would AttributeError against the
# SimpleNamespace fake-self).

def test_up_at_top_navigates_history():
    fake = _make_self(
        messages=[{"role": "user", "content": "earlier"},
                  {"role": "user", "content": "latest"}],
        at_start=True,
    )
    cw.ChatDockWidget._refresh_input_history(fake)
    consumed = cw.ChatDockWidget._handle_input_keypress(fake, _key_event(Qt.Key_Up))
    assert consumed is True
    fake.input_edit.setPlainText.assert_called_once_with("latest")


def test_up_mid_text_does_not_consume():
    fake = _make_self(
        messages=[{"role": "user", "content": "anything"}],
        at_start=False,         # caret is mid-text
    )
    cw.ChatDockWidget._refresh_input_history(fake)
    consumed = cw.ChatDockWidget._handle_input_keypress(fake, _key_event(Qt.Key_Up))
    assert consumed is False    # let Qt move the caret
    fake.input_edit.setPlainText.assert_not_called()


def test_down_at_bottom_after_up_returns_draft():
    fake = _make_self(
        messages=[{"role": "user", "content": "a"},
                  {"role": "user", "content": "b"}],
        at_start=True, at_end=True,
        plain_text="my draft",
    )
    cw.ChatDockWidget._refresh_input_history(fake)
    # Up once → "b"
    cw.ChatDockWidget._handle_input_keypress(fake, _key_event(Qt.Key_Up))
    # Down past newest → "my draft"
    cw.ChatDockWidget._handle_input_keypress(fake, _key_event(Qt.Key_Down))
    calls = [c.args[0] for c in fake.input_edit.setPlainText.call_args_list]
    assert calls == ["b", "my draft"]


def test_up_when_readonly_does_nothing():
    fake = _make_self(
        messages=[{"role": "user", "content": "hi"}],
        readonly=True, at_start=True,
    )
    cw.ChatDockWidget._refresh_input_history(fake)
    consumed = cw.ChatDockWidget._handle_input_keypress(fake, _key_event(Qt.Key_Up))
    assert consumed is False    # not consumed; eventFilter would defer to Qt
    fake.input_edit.setPlainText.assert_not_called()


# ── Subsequent-Up bug regression ────────────────────────────

def test_subsequent_up_still_walks_back():
    """Programmatic setPlainText must not reset the cycle (the _suppress flag
    is the guard). Two Ups in a row must return the second-newest entry."""
    fake = _make_self(
        messages=[{"role": "user", "content": "old"},
                  {"role": "user", "content": "new"}],
        at_start=True,
    )
    cw.ChatDockWidget._refresh_input_history(fake)
    cw.ChatDockWidget._handle_input_keypress(fake, _key_event(Qt.Key_Up))
    cw.ChatDockWidget._handle_input_keypress(fake, _key_event(Qt.Key_Up))
    calls = [c.args[0] for c in fake.input_edit.setPlainText.call_args_list]
    assert calls == ["new", "old"]


# ── _is_history_reset_key ───────────────────────────────────

def test_reset_key_classifier():
    is_reset = cw.ChatDockWidget._is_history_reset_key

    # Printable character → reset
    assert is_reset(_key_event(Qt.Key_A, text="a")) is True
    # Backspace / Delete / Home / End → reset
    assert is_reset(_key_event(Qt.Key_Backspace)) is True
    assert is_reset(_key_event(Qt.Key_Delete)) is True
    assert is_reset(_key_event(Qt.Key_Home)) is True
    assert is_reset(_key_event(Qt.Key_End)) is True
    # Up/Down explicitly do NOT reset (they drive the cycle)
    assert is_reset(_key_event(Qt.Key_Up)) is False
    assert is_reset(_key_event(Qt.Key_Down)) is False
    # Bare modifier press (Shift alone, etc.) — empty text, no reset
    assert is_reset(_key_event(Qt.Key_Shift, text="")) is False
