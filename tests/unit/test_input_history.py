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
