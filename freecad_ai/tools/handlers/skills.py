"""FreeCAD skills."""

import os

from ..registry import ToolParam, ToolDefinition, ToolResult
from ...core.executor import execute_code
from ..tool_common import *  # noqa: F403

# ── report_skill_params ────────────────────────────────────

_reported_skill_params = None


def get_reported_skill_params():
    """Get the last reported skill params, or None."""
    return _reported_skill_params


def clear_reported_skill_params():
    """Clear stored skill params."""
    global _reported_skill_params
    _reported_skill_params = None


def _handle_report_skill_params(params: dict) -> ToolResult:
    """Store skill parameters for validation."""
    global _reported_skill_params
    _reported_skill_params = dict(params)
    return ToolResult(
        success=True,
        output=f"Skill parameters recorded: {', '.join(f'{k}={v}' for k, v in params.items())}",
    )


REPORT_SKILL_PARAMS = ToolDefinition(
    name="report_skill_params",
    description="Report the parameters used for the current skill execution. Call this after completing a skill so the system can validate the result.",
    parameters=[
        ToolParam("params", "object", "Dict of parameter names and values used (e.g., {\"L\": 100, \"W\": 80})", required=True),
    ],
    handler=_handle_report_skill_params,
    category="query",
)


# ── use_skill ──────────────────────────────────────────────

def _handle_use_skill(name: str, args: str = "") -> ToolResult:
    """Load a skill's instructions and return them for the model to follow.

    The skill content (SKILL.md) is returned as the tool result. The model
    should read these instructions and follow them step by step using its
    available tools.  If the exact name isn't found, a fuzzy search on skill
    names and descriptions is attempted.
    """
    from ..extensions.skills import SkillsRegistry
    registry = SkillsRegistry()
    result = registry.execute_skill(name, args)

    if "error" in result:
        # Fuzzy match: search skill names and descriptions
        query = name.lower()
        matches = []
        for skill in registry.get_available():
            if query in skill.name.lower() or query in skill.description.lower():
                matches.append(skill)
        if len(matches) == 1:
            # Exactly one match — use it
            result = registry.execute_skill(matches[0].name, args)
        elif matches:
            names = [s.name for s in matches]
            return ToolResult(
                success=False, output="",
                error=f"Skill '{name}' not found. Did you mean: {', '.join(names)}?")
        else:
            available = [s.name for s in registry.get_available()]
            return ToolResult(
                success=False, output="",
                error=f"Skill '{name}' not found. Available: {', '.join(available)}")

    if "inject_prompt" in result:
        content = result["inject_prompt"]
        if args:
            content += f"\n\nUser request: {args}"
        return ToolResult(success=True, output=content)

    if "output" in result:
        return ToolResult(success=True, output=result["output"])

    return ToolResult(success=False, output="", error="Skill returned no content")


USE_SKILL = ToolDefinition(
    name="use_skill",
    description=(
        "Load a skill's detailed instructions for a complex task. "
        "Skills provide step-by-step construction guides (e.g. enclosure, gear). "
        "Call this when the user's request matches a skill, then follow the "
        "returned instructions using your tools."
    ),
    parameters=[
        ToolParam("name", "string",
                  "Skill name (e.g. 'enclosure', 'gear', 'fastener-hole')"),
        ToolParam("args", "string",
                  "User's parameters for the skill (e.g. '120x80x60mm, screw lid')",
                  required=False, default=""),
    ],
    handler=_handle_use_skill,
    category="query",
)


