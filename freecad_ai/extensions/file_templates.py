"""Starter templates for new hooks and user tools.

Pulls event names from `freecad_ai.hooks.registry.VALID_EVENTS` so the hook
template never drifts from the actual event list.
"""

from ..hooks.registry import VALID_EVENTS


def render_hook_template(name: str) -> str:
    """Return a hook.py starter with one stub per VALID_EVENTS entry.

    The user is expected to keep the handlers they need and delete the rest.
    """
    lines = [
        f'"""Hook: {name}.',
        "",
        "Each on_<event> handler receives a `context` dict and may return",
        "{'block': True, 'reason': '...'} to short-circuit, or {'modify': ...}",
        "to mutate the in-flight value (e.g. user_prompt_submit -> text).",
        '"""',
        "",
    ]
    for event in VALID_EVENTS:
        lines += [
            f"def on_{event}(context):",
            f'    """Fires for the {event} event."""',
            "    pass",
            "",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def render_user_tool_template(name: str) -> str:
    """Return a user-tool .py starter with one valid example function.

    Validation requires: public function, at least one typed parameter,
    and a docstring (for the tool description).
    """
    return (
        f'"""User tool: {name}."""\n'
        "\n"
        f"def {name}(value: float) -> dict:\n"
        f'    """One-line description of what {name} does (shown to the LLM)."""\n'
        '    return {"success": True, "output": str(value)}\n'
    )
