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


def resolve_turn_outcome(truncated: bool, tool_calls: list, interrupted: bool) -> str:
    """Classify a finished turn: "stopped", "truncated", "done" or "continue".

    Precedence matters. An interruption is the user's explicit stop and outranks
    everything. Truncation halts next: a response cut off at the output limit can
    carry half-formed tool calls, and acting on a partial payload is worse than
    stopping (issue #52). Only an intact turn earns the right to continue.
    """
    if interrupted:
        return "stopped"
    if truncated:
        return "truncated"
    return "continue" if tool_calls else "done"
