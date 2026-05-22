# Input History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Up/Down history navigation to the chat dock's input field, scoped to the current conversation's user messages.

**Architecture:** A pure Qt-free / FreeCAD-free `InputHistory` state machine handles the navigation cycle and draft preservation. `ChatDockWidget` rebuilds the entries list from `Conversation.messages` whenever the conversation changes (construction / send / load), extends its existing `eventFilter` with Up/Down handlers gated on caret position, and uses an `_set_input_text` helper bracketed by a `_suppress_history_reset` flag so programmatic text replacement doesn't reset the navigation cycle.

**Tech Stack:** Python 3.11, PySide6 via `freecad_ai/ui/compat.py`, pytest. No new dependencies. Tests use the existing unbound-method-with-fake-self pattern (see `tests/unit/test_settings_dialog_provider_change.py`) — no `QApplication` required.

**Spec:** `docs/superpowers/specs/2026-05-23-input-history-design.md`

**Conventions:**
- Run unit tests with: `env -u PYTHONPATH .venv/bin/python -m pytest <path> -v` (the `-u PYTHONPATH` avoids the dist-packages leak that breaks pluggy).
- Every commit message ends with the trailer:
  `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
- Qt enums use the flat form (e.g. `QtCore.Qt.Key_Up`) for PySide2/6 compat.

---

## File Structure

**Create:**
- `freecad_ai/core/input_history.py` — `InputHistory` state machine. Qt-free, FreeCAD-free.
- `tests/unit/test_input_history.py` — pure helper tests.
- `tests/unit/test_input_history_wiring.py` — wiring tests for `ChatDockWidget`, using mocks (no `QApplication`).

**Modify:**
- `freecad_ai/ui/chat_widget.py` — add `_input_history` member, `_refresh_input_history()`, `_set_input_text()`, Up/Down + reset-on-typing branches in `eventFilter`, refresh calls at construction / after send / on conversation load.

---

## Task 1: `InputHistory` state machine + pure tests

**Files:**
- Create: `freecad_ai/core/input_history.py`
- Create: `tests/unit/test_input_history.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_input_history.py`:

```python
from freecad_ai.core.input_history import InputHistory


def test_empty_history_returns_none():
    h = InputHistory()
    h.set_entries([])
    assert h.up("") is None
    assert h.down() is None


def test_up_navigates_newest_to_oldest():
    h = InputHistory()
    h.set_entries(["a", "b", "c"])
    assert h.up("") == "c"
    assert h.up("") == "b"
    assert h.up("") == "a"
    assert h.up("") is None  # clamp at oldest, no wrap


def test_down_returns_draft_past_newest():
    h = InputHistory()
    h.set_entries(["a", "b", "c"])
    h.up("in progress")        # save draft, return "c"
    h.up("")                   # → "b"
    assert h.down() == "c"
    assert h.down() == "in progress"  # past newest → restored draft
    assert h.down() is None    # exhausted


def test_first_up_saves_draft_subsequent_ups_do_not():
    h = InputHistory()
    h.set_entries(["a", "b"])
    h.up("real draft")         # cycle starts, draft saved as "real draft"
    h.up("ignored")            # mid-cycle text must NOT overwrite the draft
    # Walk all the way back down past newest to recover draft
    h.down()                   # → "b"
    assert h.down() == "real draft"


def test_down_without_navigation_returns_none():
    h = InputHistory()
    h.set_entries(["a", "b"])
    assert h.down() is None    # no Up first → nothing to do


def test_reset_clears_navigation_and_draft():
    h = InputHistory()
    h.set_entries(["a", "b"])
    h.up("draft")
    h.up("")
    h.reset()
    assert h.up("new draft") == "b"        # fresh cycle starts at newest
    h.down()                                # → "new draft" (the new draft survives)


def test_set_entries_during_navigation_resets():
    h = InputHistory()
    h.set_entries(["a", "b", "c"])
    h.up("")
    h.up("")                   # _index now points at "b"
    h.set_entries(["x", "y"])  # entries replaced; old index is meaningless
    # Index must be reset — next up should save a fresh draft and start at newest
    assert h.up("post-set draft") == "y"


def test_set_entries_copies_input_list():
    """set_entries must not retain a reference that the caller can mutate."""
    h = InputHistory()
    src = ["a", "b"]
    h.set_entries(src)
    src.append("c")            # caller mutates after the fact
    assert h.up("") == "b"     # newest should still be "b"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_input_history.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'freecad_ai.core.input_history'`.

- [ ] **Step 3: Write the implementation**

Create `freecad_ai/core/input_history.py`:

```python
"""Shell-style input history walker for the chat dock's text input.

Source-of-truth list is supplied externally via ``set_entries`` so this
module doesn't need to know about Conversation. The owning widget rebuilds
the list whenever the active conversation changes or a message is sent.

Semantics:
  - ``up(current_text)`` first time in a cycle: saves ``current_text`` as the
    draft, returns the newest entry.
  - Subsequent ``up`` calls walk older. Clamps at the oldest (no wrap).
  - ``down`` walks newer. Past the newest entry, returns the saved draft once
    (possibly empty string), then None.
  - Any input-editing keystroke in the owning widget should call ``reset`` to
    end the cycle.
"""


class InputHistory:
    def __init__(self) -> None:
        self._entries: list[str] = []
        self._index: int | None = None   # None = not currently navigating
        self._draft: str = ""

    def set_entries(self, entries: list[str]) -> None:
        """Replace the entry list (oldest first). Ends any in-progress cycle."""
        # Copy so caller-side mutations don't bleed in.
        self._entries = list(entries)
        self._index = None

    def up(self, current_text: str) -> str | None:
        """Walk one step older. Returns the entry to display, or None."""
        if not self._entries:
            return None
        if self._index is None:
            # First Up in a cycle — save the draft and jump to newest.
            self._draft = current_text
            self._index = len(self._entries) - 1
            return self._entries[self._index]
        if self._index > 0:
            self._index -= 1
            return self._entries[self._index]
        return None  # clamp at oldest

    def down(self) -> str | None:
        """Walk one step newer. Returns the entry/draft, or None."""
        if self._index is None:
            return None
        if self._index < len(self._entries) - 1:
            self._index += 1
            return self._entries[self._index]
        # Past the newest — restore the draft and end the cycle.
        self._index = None
        return self._draft

    def reset(self) -> None:
        """End the navigation cycle and forget the draft."""
        self._index = None
        self._draft = ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_input_history.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/core/input_history.py tests/unit/test_input_history.py
git commit -m "feat(core): add InputHistory state machine for chat-input history

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Dock wiring scaffolding (helper, refresh hooks, `_set_input_text`)

**Files:**
- Modify: `freecad_ai/ui/chat_widget.py:799-810` (`ChatDockWidget.__init__`), `:1345` (after `_send_message` appends user message), `:1659` and `:1716` (conversation reset / load), plus a new helper method.

- [ ] **Step 1: Add module-level import + instance member**

Near the existing `..core` imports at the top of `freecad_ai/ui/chat_widget.py` (around lines 36-38, where `..core.loop_control` already lives), add:

```python
from ..core.input_history import InputHistory
```

In `ChatDockWidget.__init__`, immediately after the line `self._worker = None` (line ~805), add two members:

```python
        self._input_history = InputHistory()
        self._suppress_history_reset = False
```

- [ ] **Step 2: Add `_refresh_input_history` and `_set_input_text` helpers**

In `ChatDockWidget`, add these two methods. A good location is right above `eventFilter` (search for `# ── Event filter` / `def eventFilter`):

```python
    # ── Input history ───────────────────────────────────────

    def _refresh_input_history(self) -> None:
        """Rebuild the input-history entries from the current conversation.

        Filters to user messages whose content is a plain string (skips
        multipart messages that carry image attachments alongside text).
        """
        entries = [
            m["content"]
            for m in self.conversation.messages
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ]
        self._input_history.set_entries(entries)

    def _set_input_text(self, text: str) -> None:
        """Replace input contents and place caret at end without tripping the
        history-reset path that user typing goes through."""
        self._suppress_history_reset = True
        try:
            self.input_edit.setPlainText(text)
            cur = self.input_edit.textCursor()
            cur.movePosition(QTextCursor.End)
            self.input_edit.setTextCursor(cur)
        finally:
            self._suppress_history_reset = False
```

(`QTextCursor` is already aliased at the top of `chat_widget.py` — `QTextCursor = QtGui.QTextCursor`. Reuse it.)

- [ ] **Step 3: Hook refresh into the three lifecycle points**

(a) At the **end of `ChatDockWidget.__init__`** — after `_build_ui()` has run and `self.conversation` exists. Add as the last line of `__init__`:

```python
        self._refresh_input_history()
```

(b) In `_send_message`, immediately after the `self.conversation.add_user_message(...)` call (line ~1345), add:

```python
        self._refresh_input_history()
```

(c) At the **two places where `self.conversation` is reassigned** — `self.conversation = Conversation()` (around line 1659) and `self.conversation = Conversation.load(conv_id)` (around line 1716). After each assignment, add:

```python
        self._refresh_input_history()
```

Use `grep -n "self.conversation = " freecad_ai/ui/chat_widget.py` to confirm there are exactly the three sites above (the `__init__` site at line ~804, the new-conversation site at ~1659, and the load site at ~1716). If a fourth turns up, add the refresh after it too.

- [ ] **Step 4: Verify imports compile**

Run: `env -u PYTHONPATH .venv/bin/python -c "import freecad_ai.ui.chat_widget"`
Expected: exit 0, no error.

Then run the full unit suite to confirm nothing was broken:

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/ -q`
Expected: no regressions (still 835 passed).

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/ui/chat_widget.py
git commit -m "feat(ui): wire InputHistory into chat dock with refresh hooks

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Up/Down event-filter dispatch + reset-on-typing

**Files:**
- Modify: `freecad_ai/ui/chat_widget.py:1260-1268` (`ChatDockWidget.eventFilter`).

- [ ] **Step 1: Factor the dispatch into a helper, then thin out `eventFilter`**

The current `eventFilter` (lines ~1260-1268) is:

```python
    def eventFilter(self, obj, event):
        if obj is self.input_edit and event.type() == QtCore.QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & Qt.ShiftModifier:
                    return False  # Shift+Enter: newline
                else:
                    self._send_message()
                    return True
        return super().eventFilter(obj, event)
```

Replace it with a thin dispatcher delegating to a new helper. The helper holds the logic so it is unit-testable without going through Qt's `super().eventFilter` fall-through:

```python
    def eventFilter(self, obj, event):
        if obj is self.input_edit and event.type() == QtCore.QEvent.KeyPress:
            if self._handle_input_keypress(event):
                return True
        return super().eventFilter(obj, event)

    def _handle_input_keypress(self, event) -> bool:
        """Return True if the KeyPress was consumed by dock-level handling.

        Covers (1) Enter/Return send, (2) Up/Down history navigation gated on
        caret position, and (3) cycle reset on input-editing keys. Returning
        False lets the keystroke proceed to Qt's default text-edit handling.
        """
        # 1. Enter / Return — existing send behavior (unchanged).
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                return False  # Shift+Enter: newline
            self._send_message()
            return True

        # 2. History navigation — only when the input is editable.
        if not self.input_edit.isReadOnly():
            cursor = self.input_edit.textCursor()
            if event.key() == Qt.Key_Up and cursor.atStart():
                result = self._input_history.up(self.input_edit.toPlainText())
                if result is not None:
                    self._set_input_text(result)
                return True
            if event.key() == Qt.Key_Down and cursor.atEnd():
                result = self._input_history.down()
                if result is not None:
                    self._set_input_text(result)
                return True

            # 3. Reset the history cycle on any input-editing key.
            if self._is_history_reset_key(event):
                if not self._suppress_history_reset:
                    self._input_history.reset()
                # Don't consume — let Qt handle the keystroke normally.

        return False

    @staticmethod
    def _is_history_reset_key(event) -> bool:
        """Return True if a KeyPress should end the history navigation cycle.

        Triggers on any key that produces a character (event.text() non-empty)
        or any editing key (Backspace/Delete/Home/End). Bare modifier presses
        (Shift/Ctrl/Alt) produce empty text and so do NOT trigger a reset.
        Up/Down are explicitly excluded — they drive the cycle.
        """
        k = event.key()
        if k in (Qt.Key_Up, Qt.Key_Down):
            return False
        if k in (Qt.Key_Backspace, Qt.Key_Delete, Qt.Key_Home, Qt.Key_End):
            return True
        return bool(event.text())
```

- [ ] **Step 2: Verify imports compile**

Run: `env -u PYTHONPATH .venv/bin/python -c "import freecad_ai.ui.chat_widget"`
Expected: exit 0.

- [ ] **Step 3: Full-suite regression**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/ -q`
Expected: no regressions.

- [ ] **Step 4: Commit**

```bash
git add freecad_ai/ui/chat_widget.py
git commit -m "feat(ui): Up/Down navigates input history with caret-position gate

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Wiring tests (mock-based, no QApplication)

**Files:**
- Create: `tests/unit/test_input_history_wiring.py`

The tests use the same unbound-method-with-fake-self pattern as `tests/unit/test_settings_dialog_provider_change.py`: import the method off the class, call it with a `SimpleNamespace` / `MagicMock` standing in for `self`. No widget is ever instantiated, so no `QApplication` is needed.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_input_history_wiring.py`:

```python
"""Wiring tests for the chat dock's input history.

Uses the unbound-method-with-fake-self pattern (see
test_settings_dialog_provider_change.py) so we don't need a QApplication or a
real QTextEdit. Each test calls the relevant method off ChatDockWidget with a
SimpleNamespace/MagicMock standing in for the dock.
"""

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
    _set_input_text, eventFilter, and _is_history_reset_key.
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_input_history_wiring.py -v`
Expected: the tests would FAIL only against the pre-Task-2/3 state of `chat_widget.py`. Since Tasks 2 and 3 are already done by this point, they should PASS.

If you're running this task in isolation against an older `chat_widget.py` (Tasks 2 and 3 not yet applied), the wiring tests will fail with `AttributeError: ... has no attribute '_refresh_input_history'` / `_set_input_text` / `_is_history_reset_key`. That's the expected failure mode — it confirms the tests genuinely exercise the wiring.

- [ ] **Step 3: Confirm they pass against the wired code**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_input_history_wiring.py -v`
Expected: 9 passed.

- [ ] **Step 4: Full-suite regression**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/ -q`
Expected: no regressions; total goes from 835 → 852 (8 pure helper tests + 9 wiring tests).

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_input_history_wiring.py
git commit -m "test(ui): wiring tests for chat-dock input history

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Manual verification

**Files:** none.

- [ ] **Step 1: Walk through the seven manual checks**

Launch FreeCAD with the workbench (the symlinked Mod). Run through:

1. New conversation → press Up in the input → input unchanged (empty history).
2. Send 2 messages. Press Up → newest replaces input; Up again → older; Down → newest; Down → empty.
3. Type `hello` (don't send). Press Up → newest replaces `hello`. Press Down → newest cleared. Press Down again → `hello` restored.
4. Click "Load" and pick a previous conversation. Press Up — that conversation's last user message appears.
5. Send a message and quickly press Up while the Stop button is showing (input is read-only): nothing happens in the input.
6. Compose a 3-line message in the input. Place caret on the middle line. Press Up — caret moves up one line; text unchanged.
7. After a successful navigation, press Up, Up — two distinct prior messages must appear (the subsequent-Up bug regression check).

Note any deviations in the commit body or a follow-up note. If everything works, no further commit needed for this task.

---

## Self-Review

Quick checklist against the spec:

**Spec coverage:**
- §1 Architecture (InputHistory helper + dock state) → Task 1, Task 2 Step 1.
- §1 `_refresh_input_history` semantics (filter user/string content) → Task 2 Step 2 + Task 4 `test_refresh_filters_to_user_string_content`.
- §1 `_set_input_text` with suppress flag → Task 2 Step 2 + Task 4 `test_set_input_text_brackets_suppress_flag_and_replaces`.
- §1 three lifecycle refresh hooks (construction / send / load) → Task 2 Step 3.
- §2 keybinding table (Up at top, Up mid-text, Down at bottom, readonly gate, reset on typing) → Task 3 + Task 4 wiring tests.
- §2 subsequent-Up bug guarded by `_suppress_history_reset` → Task 4 `test_subsequent_up_still_walks_back`.
- §3 pure helper tests (8 listed) → Task 1.
- §3 Qt-aware wiring tests → Task 4.
- §3 manual verification → Task 5.

No gaps.

**Placeholder scan:** no TBD / TODO / vague language.

**Type consistency:** `InputHistory.set_entries/up/down/reset`, `_refresh_input_history`, `_set_input_text`, `_suppress_history_reset`, `_is_history_reset_key` — all consistent across tasks.
