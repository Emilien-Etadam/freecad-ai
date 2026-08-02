"""Dead solids are forbidden: execute_code refuses and rolls them back.

A `Part::Feature` stores only the final B-Rep — no feature tree, nothing
editable afterwards. Code that leaves one behind is undone and returns an
error telling the model to rebuild with the PartDesign tools. Two
exemptions: a file import (there is no import tool, so execute_code is
the only route) and the `allow_static_solids` opt-in in config.json.
"""

import inspect

from freecad_ai.tools.handlers.document import (
    _code_performs_import,
    _dead_solid_error,
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


class TestImportExemption:
    def test_step_import_exempt(self):
        assert _code_performs_import("import Import\nImport.insert('/tmp/p.step', doc.Name)")

    def test_mesh_import_exempt(self):
        assert _code_performs_import("Mesh.insert('/tmp/part.stl')")

    def test_plain_modelling_not_exempt(self):
        code = ("import Part\nbox = Part.makeBox(20,20,20)\n"
                "doc.addObject('Part::Feature','Die').Shape = box")
        assert not _code_performs_import(code)


class TestRefusalMessage:
    def test_names_the_object_and_states_the_rule(self):
        msg = _dead_solid_error({"Shape001"}, False)
        assert "Shape001" in msg
        assert "NON-PARAMETRIC" in msg
        assert "rolled back" in msg

    def test_points_at_existing_body_when_one_exists(self):
        msg = _dead_solid_error({"Shape"}, True)
        assert "PartDesign Body" in msg
        assert "body_name" in msg

    def test_suggests_rebuild_when_no_body(self):
        msg = _dead_solid_error({"Shape"}, False)
        assert "create_body" in msg

    def test_lists_several_names_sorted(self):
        assert "A, B" in _dead_solid_error({"B", "A"}, False)


class TestHandlerWiring:
    def _src(self):
        from freecad_ai.tools.handlers import document
        return inspect.getsource(document._handle_execute_code)

    def test_refuses_and_rolls_back(self):
        src = self._src()
        assert "ToolResult(success=False" in src
        assert "doc.undo()" in src          # revert the committed transaction
        assert "_dead_solid_error" in src

    def test_only_new_static_solids_count(self):
        src = self._src()
        assert "before_static" in src
        assert "- before_static" in src

    def test_exemptions_wired(self):
        src = self._src()
        assert "_code_performs_import(code)" in src
        assert "allow_static_solids" in src

    def test_rollback_failure_is_reported(self):
        src = self._src()
        assert "Automatic rollback failed" in src


class TestConfigKnob:
    def test_defaults_to_forbidding(self):
        from freecad_ai.config import AppConfig
        assert AppConfig().allow_static_solids is False

    def test_round_trips_through_json(self):
        import dataclasses
        from freecad_ai.config import AppConfig

        d = dataclasses.asdict(AppConfig())
        assert "allow_static_solids" in d


class TestModelIsTold:
    def test_act_prompt_states_the_ban(self):
        from freecad_ai.core import system_prompt
        src = inspect.getsource(system_prompt)
        assert "Non-parametric solids are FORBIDDEN" in src
        assert "Part.makeBox()" in src

    def test_tool_description_states_the_ban(self):
        from freecad_ai.tools.freecad_tools import EXECUTE_CODE
        assert "NON-PARAMETRIC SOLIDS ARE REFUSED" in EXECUTE_CODE.description
        assert "imports" in EXECUTE_CODE.description.lower()
