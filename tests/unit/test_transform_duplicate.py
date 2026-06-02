"""Unit tests for transform_object relative mode + duplicate_object definitions."""

from freecad_ai.tools.freecad_tools import TRANSFORM_OBJECT


class TestTransformObjectDefinition:
    def test_relative_param_default_true(self):
        params = {p.name: p for p in TRANSFORM_OBJECT.parameters}
        assert "relative" in params
        assert params["relative"].type == "boolean"
        assert params["relative"].default is True

    def test_no_copy_param(self):
        names = {p.name for p in TRANSFORM_OBJECT.parameters}
        assert "copy" not in names
