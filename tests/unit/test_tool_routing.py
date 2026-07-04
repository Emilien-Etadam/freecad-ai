"""Regression guards for the #28 tool-routing steering.

These are text-assertion guards, not behavioural tests: they lock in the
prompt/description cues that steer the model toward `create_sketch` for
"sketch on the selected face" instead of hand-rolling a raw
`AttachmentSupport`/`MapMode` macro via `execute_code`/`run_macro`.

They cannot prove the LLM routes correctly (that needs a live eval); they
prevent the steering text from silently regressing.
"""

from freecad_ai.core.system_prompt import build_system_prompt
from freecad_ai.tools.freecad_tools import CREATE_SKETCH, EXECUTE_CODE


def _act_tools_prompt():
    return build_system_prompt(mode="act", tools_enabled=True)


class TestActModeSteering:
    def test_has_sketch_on_face_routing_rule(self):
        """Act-mode prompt explicitly routes face-sketching to create_sketch."""
        prompt = _act_tools_prompt()
        assert "create_sketch" in prompt
        # The selected/named-face case must be called out with support+face.
        lower = prompt.lower()
        assert "selected" in lower and "face" in lower
        assert "support" in prompt and "list_faces" in prompt

    def test_warns_against_handwritten_attachment_macro(self):
        """The prompt tells the model NOT to hand-write AttachmentSupport/MapMode."""
        prompt = _act_tools_prompt()
        assert "AttachmentSupport" in prompt
        assert "MapMode" in prompt

    def test_escape_hatches_marked_last_resort(self):
        """execute_code/run_macro are framed as last resorts, not peers."""
        prompt = _act_tools_prompt().lower()
        assert "last resort" in prompt
        assert "execute_code" in prompt and "run_macro" in prompt


class TestCreateSketchDescription:
    def test_face_capability_is_prominent(self):
        """The face/support capability appears BEFORE the constraint/coordinate
        boilerplate, not buried as the trailing sentence (the #28 root cause)."""
        desc = CREATE_SKETCH.description
        assert "list_faces" in desc and "support" in desc and "face" in desc
        # Prominence guard: face attachment is introduced before COORDINATE SYSTEM.
        assert desc.index("list_faces") < desc.index("COORDINATE SYSTEM")

    def test_discourages_raw_attachment_macro(self):
        desc = CREATE_SKETCH.description
        assert "AttachmentSupport" in desc


class TestExecuteCodeDescription:
    def test_marked_last_resort_and_points_at_create_sketch(self):
        desc = EXECUTE_CODE.description.lower()
        assert "last-resort" in desc or "last resort" in desc
        assert "create_sketch" in desc
