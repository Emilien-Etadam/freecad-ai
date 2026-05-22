# Design: Input history in the chat dock

- **Date:** 2026-05-23
- **Status:** Approved design, pending implementation plan

## Background

The chat dock's input widget (`_ImageAwareTextEdit`, a multi-line `QTextEdit`)
currently has no way to recall a previous prompt. Users who want to re-send a
slightly tweaked version of an earlier message have to retype it or scroll the
chat above and copy-paste.

The dock already has the right hook point: `ChatDockWidget.eventFilter`
(`chat_widget.py:1260`) installs on `input_edit` and intercepts Enter/Return
for send. Adding Up/Down navigation slots in there.

Conversations are already auto-saved per memory; "history" is therefore not a
new persistence problem — it can be a live derivation from the active
conversation.

## Goals

A shell-style Up/Down input history scoped to the **current conversation**:
- Up at the top of the input recalls the previous user message; repeated Ups
  walk back through the conversation's user messages.
- Down walks forward. Past the newest entry, Down restores whatever draft text
  the user had typed before they started navigating.
- Switching conversations (via the "Load" button) automatically gives the new
  conversation's history.

## Non-goals

- No new storage file or schema. Source of truth is `Conversation.messages`.
- No reverse / prefix search (Ctrl-R). Deferred.
- No global / cross-conversation history. Per-conversation by design.
- No deduplication of consecutive duplicate messages. YAGNI.

## Backwards compatibility

Defaults preserve current behavior: Up/Down do exactly what they do today
**unless** the caret is at the very top-edge (Up) or bottom-edge (Down) of the
input. No new config field. No persistent state.

---

## Section 1 — Architecture

Two pieces: a pure helper module and a small bit of wiring on the chat dock.

### `freecad_ai/core/input_history.py` (new, Qt-free / FreeCAD-free)

```python
class InputHistory:
    """Shell-style history walker for a single conversation.

    Source-of-truth list is supplied externally via set_entries() so the
    helper doesn't reach into Conversation; the dock rebuilds the list when
    the conversation changes or a message is sent.
    """
    def __init__(self) -> None:
        self._entries: list[str] = []
        self._index: int | None = None   # None = not navigating
        self._draft: str = ""             # text the user had typed before Up

    def set_entries(self, entries: list[str]) -> None: ...
    def up(self, current_text: str) -> str | None: ...
    def down(self) -> str | None: ...
    def reset(self) -> None:
        """Forget navigation cycle and draft. Called when user types."""
```

Semantics, exhaustively:
- `set_entries([...])` replaces the list (oldest first). Resets `_index` to
  `None` because the old indices are no longer meaningful.
- `up(current_text)`:
  - If `_entries` is empty → return `None`.
  - If `_index is None` (first Up in a cycle): save `current_text` as `_draft`,
    set `_index = len(_entries) - 1`, return `_entries[_index]`.
  - Else if `_index > 0`: decrement `_index`, return `_entries[_index]`.
  - Else (already at oldest): return `None` (clamp; no wrap).
- `down()`:
  - If `_index is None` → return `None`.
  - If `_index < len(_entries) - 1`: increment, return `_entries[_index]`.
  - Else (at newest): clear `_index`, return `_draft`.
- `reset()`: `_index = None`; `_draft = ""`.

Critically: `up()` saves the draft **only on the first call of a navigation
cycle** (when `_index is None`). Subsequent Ups must not overwrite the saved
draft — otherwise the draft becomes whatever entry you're currently sitting
on.

### `ChatDockWidget` wiring

- New member: `self._input_history: InputHistory = InputHistory()`.
- New method `_refresh_input_history()`: rebuilds the entries list from
  `self.conversation.messages`, filtered to `m["role"] == "user"` and
  `isinstance(m["content"], str)` (skip image/multipart content). Called:
  - At dock construction, after `self.conversation` is created.
  - At the end of `_send_message()` (after the input is cleared, the user
    message is appended to `self.conversation`, but before the worker fires).
    Captures the just-sent message into the history.
  - In the "Load" conversation handler, after `self.conversation` is replaced.
- `eventFilter` extended to dispatch Up/Down (see Section 2).
- A small guard `self._suppress_history_reset: bool = False` bracketing the
  programmatic `setPlainText()` call that replaces the input contents on
  navigation — see Section 2 for why.

---

## Section 2 — Keybinding rules

Inside `ChatDockWidget.eventFilter`, gated on `obj is self.input_edit` and
`event.type() == QtCore.QEvent.KeyPress` and `not self.input_edit.isReadOnly()`:

| Key | Cursor position | Behavior |
|---|---|---|
| Up | `cursor.atStart()` is True | `self._input_history.up(self.input_edit.toPlainText())`. If string returned, programmatically replace input via `_set_input_text(result)`. Consume event (`return True`). |
| Up | Anywhere else | Don't consume — Qt moves the caret normally (`return False`). |
| Down | `cursor.atEnd()` is True | `self._input_history.down()`. If string returned (possibly `""` for restored empty draft), `_set_input_text(result)`. Consume. |
| Down | Anywhere else | Don't consume. |
| Return / Enter (no Shift) | — | Existing behavior unchanged. |
| Shift+Return / Shift+Enter | — | Existing behavior unchanged. |
| Any other key with non-empty `event.text()`, OR Backspace/Delete/Home/End | — | Call `self._input_history.reset()` and don't consume — let Qt handle the keystroke. |

`cursor.atStart()` / `cursor.atEnd()` from `QTextCursor` are used — no manual
block/column arithmetic.

### The `_set_input_text` helper

```python
def _set_input_text(self, text: str) -> None:
    self._suppress_history_reset = True
    try:
        self.input_edit.setPlainText(text)
        cur = self.input_edit.textCursor()
        cur.movePosition(QTextCursor.End)
        self.input_edit.setTextCursor(cur)
    finally:
        self._suppress_history_reset = False
```

The `_suppress_history_reset` flag is checked in the eventFilter's "any other
key" branch (or in a `textChanged` handler if we wire it that way). Without it,
`setPlainText` would fire `textChanged`, the reset would clear `_index`, and
the next Up would jump back to the newest entry instead of stepping further
back — a "works on first Up, useless on the second" bug.

Modifier-only key events (Shift alone, Ctrl alone) have empty `event.text()`
and are excluded by the "non-empty text" condition, so they don't reset the
cycle.

---

## Section 3 — Testing

### `tests/unit/test_input_history.py` (pure helper, no Qt)

- `test_empty_history_returns_none` — `set_entries([])`; up/down return `None`.
- `test_up_navigates_oldest_direction` — 3 entries, Ups walk newest→oldest,
  then clamp (no wrap).
- `test_down_navigates_newest_direction` — after Ups, Downs walk back, finally
  return the draft.
- `test_first_up_saves_draft` — draft `"in progress"` returns via Down past
  newest.
- `test_subsequent_ups_do_not_overwrite_draft` — saved draft survives multiple
  Ups; later Down-past-newest still returns the original draft.
- `test_reset_clears_navigation_and_draft` — `reset()` returns helper to a
  fresh state.
- `test_set_entries_during_navigation_resets` — calling `set_entries` while
  mid-navigation clears `_index`.

### `tests/unit/test_input_history_integration.py` (Qt-aware, minimal)

Use the existing `QApplication` fixture (verify in `tests/unit/conftest.py`;
the pattern from `test_settings_dialog_provider_change.py` works). Stub the
worker so no LLM traffic fires.

- `test_eventfilter_up_at_top_navigates` — pre-load `Conversation` with 2 user
  messages; KeyPress Up with cursor at `atStart()`; assert input is the newest
  message.
- `test_eventfilter_up_mid_text_moves_cursor` — multi-line text, cursor in the
  middle; KeyPress Up; assert text unchanged.
- `test_eventfilter_disabled_when_readonly` — `setReadOnly(True)`; KeyPress Up
  at top; assert nothing happens.
- `test_send_refreshes_history` — after `_send_message()` (stubbed worker),
  next `up("")` returns the just-sent message.
- `test_setplaintext_does_not_reset_navigation` — after one Up programmatically
  sets the input via `_set_input_text`, the next Up still steps further back
  (proves the `_suppress_history_reset` guard).

### Manual verification (one walkthrough)

1. New conversation → Up → input unchanged (empty history).
2. Send 2 messages. Up → newest; Up → older; Down → newest; Down → empty.
3. Type "hello"; Up → newest replaces "hello"; Down → newest disappears; Down
   → "hello" restored.
4. Load a previous conversation via "Load"; Up shows that conversation's last
   user message.
5. While a request is running (Stop visible / `isReadOnly()` True), Up at the
   input does nothing.
6. Three-line message, cursor on middle line; Up moves the caret up one line,
   text unchanged.

---

## File structure

**Create:**
- `freecad_ai/core/input_history.py` — `InputHistory` class.
- `tests/unit/test_input_history.py` — pure helper tests.
- `tests/unit/test_input_history_integration.py` — Qt-aware dock-wiring tests.

**Modify:**
- `freecad_ai/ui/chat_widget.py` — add `_input_history`, `_refresh_input_history`,
  `_set_input_text`, Up/Down handling in `eventFilter`, the reset call on
  printable/edit keys (guarded by `_suppress_history_reset`), and the
  `_refresh_input_history()` calls at construction / after send / on load.

## Risk summary

Two failure modes worth naming:

1. **The "subsequent Up" bug** — without `_suppress_history_reset`, programmatic
   `setPlainText` triggers the same key-reset path as user typing. Covered by
   `test_setplaintext_does_not_reset_navigation`.
2. **Cursor-position gate** — getting `atStart()` / `atEnd()` wrong (e.g. using
   block-index comparisons that break in single-line input) would break either
   multi-line cursor movement or history navigation. Standard `QTextCursor`
   API; covered by both navigation and the negative "mid-text" test.
