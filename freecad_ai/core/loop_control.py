"""Pure decision helper for the agentic tool loop bound."""


def should_continue_loop(max_turns: int, turn: int, interrupted: bool) -> bool:
    """Return whether the agentic loop should run another turn.

    max_turns == 0 means endless. An interruption always stops the loop.
    """
    if interrupted:
        return False
    if max_turns == 0:
        return True
    return turn < max_turns
