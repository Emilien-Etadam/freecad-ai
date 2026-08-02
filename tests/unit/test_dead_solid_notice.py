"""execute_code flags the dead solids it introduces.

A `Part::Feature` stores only the final B-Rep — no feature tree, nothing
editable afterwards. When a modelling request ends up rebuilt with raw
`Part.makeBox`/`cut()`, the tool used to return a plain "success" and the
user only discovered the dead body by looking at the tree. The result now
carries a warning, without failing the call (imports and mesh→solid
conversion legitimately produce static solids).
"""

from freecad_ai.tools.handlers.document import (
    _dead_solid_notice,
    _static_solid_names,
)


class _FakeShape:
    def __init__(self, solids):
        self.Solids = solids


class _FakeObj:
    def __init__(self, name, type_id, solids=(1,)):
        self.Name = name
        self.TypeId = type_id
        self.Shape = _FakeShape(list(solids))


class _FakeDoc:
    def __init__(self, objects):
        self.Objects = objects


class TestStaticSolidDetection:
    def test_finds_part_feature_with_solid(self):
        doc = _FakeDoc([_FakeObj("Shape", "Part::Feature")])
        assert _static_solid_names(doc) == {"Shape"}

    def test_ignores_parametric_body(self):
        doc = _FakeDoc([_FakeObj("Body", "PartDesign::Body")])
        assert _static_solid_names(doc) == set()

    def test_ignores_part_feature_without_solid(self):
        """A wire or a face is a Part::Feature too — not a dead body."""
        doc = _FakeDoc([_FakeObj("Wire", "Part::Feature", solids=())])
        assert _static_solid_names(doc) == set()

    def test_no_document(self):
        assert _static_solid_names(None) == set()

    def test_shape_access_never_raises(self):
        class Broken:
            Name = "X"
            TypeId = "Part::Feature"

            @property
            def Shape(self):
                raise RuntimeError("no shape")

        doc = _FakeDoc([Broken()])
        assert _static_solid_names(doc) == set()


class TestNotice:
    def test_silent_when_nothing_new(self):
        assert _dead_solid_notice(set(), False) == ""

    def test_names_the_object(self):
        note = _dead_solid_notice({"Shape001"}, False)
        assert "Shape001" in note
        assert "NON-PARAMETRIC" in note

    def test_points_at_existing_body_when_one_exists(self):
        note = _dead_solid_notice({"Shape"}, True)
        assert "PartDesign Body" in note
        assert "body_name" in note

    def test_suggests_rebuild_when_no_body(self):
        note = _dead_solid_notice({"Shape"}, False)
        assert "create_body" in note or "PartDesign tools" in note

    def test_lists_several_names_sorted(self):
        note = _dead_solid_notice({"B", "A"}, False)
        assert "A, B" in note


class TestHandlerWiring:
    def test_notice_is_non_blocking(self):
        """The warning must ride on a successful result, not fail the call —
        imports and mesh→solid conversion legitimately create static solids."""
        import inspect
        from freecad_ai.tools.handlers import document

        src = inspect.getsource(document._handle_execute_code)
        assert "_dead_solid_notice" in src
        assert "before_static" in src  # only NEW static solids are reported
        # the notice is appended to a success result
        assert "output += _dead_solid_notice" in src
        assert "ToolResult(success=True" in src
