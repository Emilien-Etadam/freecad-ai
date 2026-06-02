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


from freecad_ai.tools.freecad_tools import (
    DUPLICATE_OBJECT, ALL_TOOLS, _duplicate_label,
)


class TestDuplicateObjectDefinition:
    def test_name_and_category(self):
        assert DUPLICATE_OBJECT.name == "duplicate_object"
        assert DUPLICATE_OBJECT.category == "modeling"

    def test_registered_in_all_tools(self):
        assert DUPLICATE_OBJECT in ALL_TOOLS

    def test_array_params_declare_items(self):
        # Consistency guard (issue #10) even though these params are scalar/string.
        for p in DUPLICATE_OBJECT.parameters:
            if getattr(p, "type", None) == "array":
                assert getattr(p, "items", None) is not None, p.name


class TestDuplicateLabel:
    def test_default_label(self):
        assert _duplicate_label("Box", "") == "Box_Copy"

    def test_explicit_label_wins(self):
        assert _duplicate_label("Box", "MyCopy") == "MyCopy"
