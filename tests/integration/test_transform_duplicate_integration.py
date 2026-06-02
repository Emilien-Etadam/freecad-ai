"""Integration tests for transform_object relative mode + duplicate_object.

Uses the run_freecad_script fixture (see tests/integration/conftest.py).
"""

import math

import pytest

pytestmark = pytest.mark.integration


class TestTransformRelative:
    def test_relative_translate_preserves_rotation(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive, _handle_transform_object

_handle_create_primitive(shape_type="box", label="Box")
doc.recompute()
# First set an absolute rotation (no translation).
_handle_transform_object(object_name="Box", relative=False, rotate_axis_z=1.0, rotate_angle=90.0)
doc.recompute()
# Then a RELATIVE translate — rotation must survive, position must shift.
r = _handle_transform_object(object_name="Box", translate_x=10.0)
doc.recompute()
obj = doc.getObject("Box")
results["data"] = {
    "success": r.success,
    "x": obj.Placement.Base.x,
    "angle": obj.Placement.Rotation.Angle,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"]
        assert abs(d["x"] - 10.0) < 1e-6
        assert abs(d["angle"] - math.pi / 2) < 0.01  # rotation preserved

    def test_relative_rotate_in_place_preserves_position(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive, _handle_transform_object

_handle_create_primitive(shape_type="box", label="Box")
doc.recompute()
# Place the box away from the origin (absolute).
_handle_transform_object(object_name="Box", relative=False, translate_x=5.0)
doc.recompute()
# RELATIVE rotate — position must be preserved (the footgun fix).
r = _handle_transform_object(object_name="Box", rotate_axis_z=1.0, rotate_angle=90.0)
doc.recompute()
obj = doc.getObject("Box")
results["data"] = {
    "success": r.success,
    "x": obj.Placement.Base.x,
    "angle": obj.Placement.Rotation.Angle,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"]
        assert abs(d["x"] - 5.0) < 1e-6           # position preserved
        assert abs(d["angle"] - math.pi / 2) < 0.01

    def test_absolute_mode_overwrites(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive, _handle_transform_object

_handle_create_primitive(shape_type="box", label="Box")
doc.recompute()
_handle_transform_object(object_name="Box", relative=False, translate_x=7.0)
doc.recompute()
# Absolute rotate with no translate → position resets to origin.
r = _handle_transform_object(object_name="Box", relative=False, rotate_axis_z=1.0, rotate_angle=45.0)
doc.recompute()
obj = doc.getObject("Box")
results["data"] = {
    "success": r.success,
    "x": obj.Placement.Base.x,
    "angle": obj.Placement.Rotation.Angle,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"]
        assert abs(d["x"]) < 1e-6                  # absolute overwrite reset position
        assert abs(d["angle"] - math.pi / 4) < 0.01


class TestDuplicateObject:
    def test_duplicate_body_copies_feature_tree(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_duplicate_object

body = doc.addObject("PartDesign::Body", "Body")
box = body.newObject("PartDesign::AdditiveBox", "Box")
box.Length = 10; box.Width = 10; box.Height = 10
doc.recompute()

r = _handle_duplicate_object(object_name="Body")
doc.recompute()
copy = doc.getObject(r.data["name"]) if r.success else None
bodies = [o for o in doc.Objects if o.TypeId == "PartDesign::Body"]
copy_children = [o.TypeId for o in (copy.Group if copy else [])]
results["data"] = {
    "success": r.success,
    "error": r.error,
    "copy_type": copy.TypeId if copy else None,
    "n_bodies": len(bodies),
    "copy_has_box": any("AdditiveBox" in t for t in copy_children),
    "original_intact": doc.getObject("Body") is not None,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["copy_type"] == "PartDesign::Body"
        assert d["n_bodies"] == 2                 # original + copy
        assert d["copy_has_box"]                  # feature tree duplicated
        assert d["original_intact"]

    def test_duplicate_with_offset(self, run_freecad_script):
        result = run_freecad_script("""
import Part
from freecad_ai.tools.freecad_tools import _handle_duplicate_object

feat = doc.addObject("Part::Feature", "Box")
feat.Shape = Part.makeBox(10, 10, 10)
doc.recompute()

r = _handle_duplicate_object(object_name="Box", translate_x=20.0)
doc.recompute()
copy = doc.getObject(r.data["name"]) if r.success else None
orig = doc.getObject("Box")
results["data"] = {
    "success": r.success,
    "error": r.error,
    "copy_x": copy.Placement.Base.x if copy else None,
    "orig_x": orig.Placement.Base.x,
    "distinct": (copy is not None and copy.Name != "Box"),
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["distinct"]
        assert abs(d["copy_x"] - 20.0) < 1e-6      # copy offset
        assert abs(d["orig_x"]) < 1e-6             # original untouched

    def test_duplicate_part_feature_is_independent(self, run_freecad_script):
        result = run_freecad_script("""
import Part
from freecad_ai.tools.freecad_tools import _handle_duplicate_object

feat = doc.addObject("Part::Feature", "Box")
feat.Shape = Part.makeBox(10, 10, 10)
doc.recompute()

r = _handle_duplicate_object(object_name="Box", label="BoxClone")
doc.recompute()
copy = doc.getObject(r.data["name"]) if r.success else None
results["data"] = {
    "success": r.success,
    "error": r.error,
    "copy_label": copy.Label if copy else None,
    "copy_type": copy.TypeId if copy else None,
    "copy_valid": copy.Shape.isValid() if copy else False,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["copy_label"] == "BoxClone"
        assert d["copy_type"] == "Part::Feature"
        assert d["copy_valid"]

    def test_duplicate_datum_line_parallel_offset(self, run_freecad_script):
        # Closes the suite: duplicate a fixed-placement datum line, offset it →
        # a parallel line. (create_datum_line lives on PR #22, not this branch, so
        # the first line is built directly here as a two-point datum would be.)
        result = run_freecad_script("""
import FreeCAD as App
from freecad_ai.tools.freecad_tools import _handle_duplicate_object

line = doc.addObject("PartDesign::Line", "DatumLine")
# A fixed Y-directed line through the origin (two-point style placement).
line.Placement = App.Placement(App.Vector(0, 0, 0),
                               App.Rotation(App.Vector(0, 0, 1), App.Vector(0, 1, 0)))
doc.recompute()
zdir0 = line.Placement.Rotation.multVec(App.Vector(0, 0, 1))

r = _handle_duplicate_object(object_name="DatumLine", translate_x=10.0)
doc.recompute()
copy = doc.getObject(r.data["name"]) if r.success else None
zdir1 = copy.Placement.Rotation.multVec(App.Vector(0, 0, 1)) if copy else None
results["data"] = {
    "success": r.success,
    "error": r.error,
    "copy_x": copy.Placement.Base.x if copy else None,
    "parallel": (copy is not None and
                 abs(zdir0.x - zdir1.x) < 1e-6 and
                 abs(zdir0.y - zdir1.y) < 1e-6 and
                 abs(zdir0.z - zdir1.z) < 1e-6),
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert abs(d["copy_x"] - 10.0) < 1e-6      # offset by 10mm
        assert d["parallel"]                       # same direction → parallel
