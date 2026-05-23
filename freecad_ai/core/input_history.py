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
