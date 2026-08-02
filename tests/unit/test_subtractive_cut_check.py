"""A subtractive primitive that cuts nothing (or buries a void) says so.

Observed on a real die: 21 cylindrical pips were created, all valid, and
the tool reported success for every one — but only one actually dimpled a
face. Eleven sat entirely outside the solid (removing nothing) and nine
sat entirely inside it (hollowing invisible cavities, visible only as
extra shells in the shape). Nothing in the result hinted at either.
"""

from freecad_ai.tools.tool_common import (
    _body_shape_stats,
    _subtractive_cut_note,
)


class _FakeShape:
    def __init__(self, volume, shells):
        self.Volume = volume
        self.Shells = list(range(shells))


class _FakeBody:
    def __init__(self, volume=None, shells=1):
        if volume is not None:
            self.Shape = _FakeShape(volume, shells)


class TestBodyStats:
    def test_reads_volume_and_shells(self):
        assert _body_shape_stats(_FakeBody(100.0, 3)) == (100.0, 3)

    def test_unmeasurable_body_is_none(self):
        """An empty Body (no Tip yet) has no usable shape — skip the check
        rather than guess."""
        assert _body_shape_stats(_FakeBody()) == (None, None)


class TestCutNote:
    def test_additive_is_silent(self):
        assert _subtractive_cut_note("additive", 100.0, 90.0, 1, 1) == ""

    def test_unmeasurable_is_silent(self):
        assert _subtractive_cut_note("subtractive", None, 90.0, 1, 1) == ""
        assert _subtractive_cut_note("subtractive", 100.0, None, 1, 1) == ""

    def test_no_material_removed_is_flagged(self):
        note = _subtractive_cut_note("subtractive", 100.0, 100.0, 1, 1)
        assert "NO material" in note
        assert "never reaches" in note
        # the note must explain the placement convention that caused it
        assert "+Z" in note

    def test_internal_void_is_flagged(self):
        note = _subtractive_cut_note("subtractive", 100.0, 93.0, 1, 2)
        assert "INTERNAL VOID" in note
        assert "7.0" in note  # the hollowed volume
        assert "not visible from outside" in note

    def test_good_cut_reports_volume(self):
        note = _subtractive_cut_note("subtractive", 100.0, 92.5, 1, 1)
        assert "removed 7.5" in note
        assert "[!]" not in note

    def test_shells_unknown_falls_back_to_plain_report(self):
        note = _subtractive_cut_note("subtractive", 100.0, 92.5, None, None)
        assert "removed 7.5" in note

    def test_float_noise_counts_as_no_cut(self):
        note = _subtractive_cut_note("subtractive", 100.0, 100.0 - 1e-9, 1, 1)
        assert "NO material" in note


class TestWiring:
    def test_handler_measures_before_and_after(self):
        import inspect
        from freecad_ai.tools.handlers import part_creation

        src = inspect.getsource(part_creation._handle_create_primitive)
        assert "before_volume, before_shells = _body_shape_stats(body)" in src
        assert "after_volume, after_shells = _body_shape_stats(body)" in src
        assert "_subtractive_cut_note(" in src
        # the body must be recomputed before measuring, or the shape is stale
        assert "doc.recompute()" in src

    def test_placement_convention_documented(self):
        from freecad_ai.tools.freecad_tools import CREATE_PRIMITIVE

        x = next(p for p in CREATE_PRIMITIVE.parameters if p.name == "x")
        assert "not a centre" in x.description
        assert "CROSS" in x.description

    def test_die_pattern_gives_crossing_coordinates(self):
        import inspect
        from freecad_ai.core import system_prompt

        src = inspect.getsource(system_prompt)
        pattern = src.split("**Playing die")[1].split('"""')[0]
        # every face gets an explicit start + height that crosses it
        for face in ("top", "bottom", "front", "back", "left", "right"):
            assert face in pattern
        assert "height=2" in pattern
        assert "INTERNAL VOID" in pattern  # tells the model to react to the note

    def test_pocket_sketch_reports_its_cut_too(self):
        """The same silent failure exists for sketch pockets."""
        import inspect
        from freecad_ai.tools.handlers import sketch

        src = inspect.getsource(sketch._handle_pocket_sketch)
        assert "_subtractive_cut_note(" in src
        assert "shells_before" in src

    def test_helpers_shared_via_tool_common(self):
        """Both handlers must see the helpers through the star import."""
        from freecad_ai.tools import tool_common
        from freecad_ai.tools.handlers import part_creation, sketch

        assert "_subtractive_cut_note" in tool_common.__all__
        assert "_body_shape_stats" in tool_common.__all__
        for mod in (part_creation, sketch):
            assert hasattr(mod, "_subtractive_cut_note"), mod.__name__
