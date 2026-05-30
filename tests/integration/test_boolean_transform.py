"""Integration tests for boolean_operation and transform_object tools."""

import pytest

pytestmark = pytest.mark.integration


class TestBooleanOperation:
    def test_fuse_increases_volume(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive, _handle_boolean_operation

_handle_create_primitive(shape_type="box", label="A", length=10, width=10, height=10)
_handle_create_primitive(shape_type="box", label="B", length=10, width=10, height=10, x=5)
doc.recompute()

vol_a = doc.getObject("A").Shape.Volume
vol_b = doc.getObject("B").Shape.Volume

r = _handle_boolean_operation(operation="fuse", object1="A", object2="B")
doc.recompute()

fused = doc.getObject(r.data["name"])
results["data"] = {
    "success": r.success,
    "vol_a": vol_a,
    "vol_b": vol_b,
    "vol_fused": fused.Shape.Volume,
}
""")
        assert result["ok"]
        d = result["data"]
        assert d["success"]
        # Fuse of overlapping boxes: vol < vol_a + vol_b (overlap removed)
        assert d["vol_fused"] < d["vol_a"] + d["vol_b"]
        assert d["vol_fused"] > d["vol_a"]

    def test_cut_reduces_volume(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive, _handle_boolean_operation

_handle_create_primitive(shape_type="box", label="Base", length=20, width=20, height=20)
_handle_create_primitive(shape_type="box", label="Tool", length=10, width=10, height=10, x=5, y=5, z=5)
doc.recompute()

vol_before = doc.getObject("Base").Shape.Volume

r = _handle_boolean_operation(operation="cut", object1="Base", object2="Tool")
doc.recompute()

cut_obj = doc.getObject(r.data["name"])
results["data"] = {
    "success": r.success,
    "vol_before": vol_before,
    "vol_after": cut_obj.Shape.Volume,
}
""")
        assert result["ok"]
        d = result["data"]
        assert d["success"]
        assert d["vol_after"] < d["vol_before"]

    def test_common_intersection(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive, _handle_boolean_operation

_handle_create_primitive(shape_type="box", label="A", length=10, width=10, height=10)
_handle_create_primitive(shape_type="box", label="B", length=10, width=10, height=10, x=5, y=5)
doc.recompute()

r = _handle_boolean_operation(operation="common", object1="A", object2="B")
doc.recompute()

common_obj = doc.getObject(r.data["name"])
results["data"] = {
    "success": r.success,
    "volume": common_obj.Shape.Volume,
}
""")
        assert result["ok"]
        d = result["data"]
        assert d["success"]
        # Common of 5mm overlap: 5*5*10 = 250
        assert abs(d["volume"] - 250.0) < 10.0

    def test_unknown_operation(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive, _handle_boolean_operation

_handle_create_primitive(shape_type="box", label="A")
_handle_create_primitive(shape_type="box", label="B")
doc.recompute()

r = _handle_boolean_operation(operation="xor", object1="A", object2="B")
results["data"] = {
    "success": r.success,
    "error": r.error,
}
""")
        assert result["ok"]
        d = result["data"]
        assert not d["success"]
        assert "Unknown operation" in d["error"]


class TestTransformObject:
    def test_translate(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive, _handle_transform_object

_handle_create_primitive(shape_type="box", label="Box")
doc.recompute()

r = _handle_transform_object(object_name="Box", translate_x=10, translate_y=20, translate_z=30)
doc.recompute()

obj = doc.getObject("Box")
results["data"] = {
    "success": r.success,
    "x": obj.Placement.Base.x,
    "y": obj.Placement.Base.y,
    "z": obj.Placement.Base.z,
}
""")
        assert result["ok"]
        d = result["data"]
        assert d["success"]
        assert d["x"] == 10.0
        assert d["y"] == 20.0
        assert d["z"] == 30.0

    def test_rotate(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive, _handle_transform_object

_handle_create_primitive(shape_type="box", label="Box")
doc.recompute()

r = _handle_transform_object(
    object_name="Box",
    rotate_axis_z=1.0,
    rotate_angle=90.0,
)
doc.recompute()

obj = doc.getObject("Box")
results["data"] = {
    "success": r.success,
    "angle": obj.Placement.Rotation.Angle,
}
""")
        assert result["ok"]
        d = result["data"]
        assert d["success"]
        # Rotation angle should be ~90 degrees (in radians: pi/2 ≈ 1.5708)
        import math
        assert abs(d["angle"] - math.pi / 2) < 0.01


class TestBooleanHistoryPreservation:
    """Regression tests for issue #17: a boolean between two PartDesign Bodies
    must NOT collapse the base Body's parametric history.

    A ``Part::Cut`` (etc.) claims its operands as tree children, reparenting the
    base Body under the new node — it stops being top-level and its Sketch/Pad
    become buried and uneditable. ``boolean_operation`` must instead route
    body-to-body operations through a ``PartDesign::Boolean`` appended inside the
    base Body, so the Body stays top-level with its full feature history intact.
    """

    # Builds two padded Bodies, runs the boolean, then reports whether the base
    # Body survived as a top-level (history-preserving) node and which TypeId was
    # used. OPERATION is substituted per-test.
    _SCRIPT = """
from freecad_ai.tools.freecad_tools import (
    _handle_create_body, _handle_create_sketch, _handle_pad_sketch,
    _handle_boolean_operation,
)


def build_padded_body(label, geo, length):
    bn = _handle_create_body(label).data["name"]
    sn = _handle_create_sketch(
        plane="XY", body_name=bn, geometries=[geo], label=label + "Sketch"
    ).data["name"]
    _handle_pad_sketch(sketch_name=sn, length=length, label=label + "Pad")
    doc.recompute()
    return bn


base = build_padded_body(
    "Base", {"type": "rectangle", "x": 0, "y": 0, "width": 40, "height": 30}, 20.0)
tool = build_padded_body(
    "Tool", {"type": "circle", "cx": 20, "cy": 15, "radius": 5}, 25.0)

r = _handle_boolean_operation(operation="OPERATION", object1=base, object2=tool)
doc.recompute()

# A node is "claimed" (non-top-level) if another object lists it as a child:
# PartDesign::Body.Group, or Part::Cut/Fuse/Common .Base/.Tool.
claimed = set()
for o in doc.Objects:
    if getattr(o, "Group", None):
        claimed.update(c.Name for c in o.Group)
    if o.TypeId in ("Part::Cut", "Part::Fuse", "Part::Common"):
        for attr in ("Base", "Tool"):
            c = getattr(o, attr, None)
            if c is not None:
                claimed.add(c.Name)

base_obj = doc.getObject(base)
results["data"] = {
    "success": r.success,
    "base_top_level": base_obj.Name not in claimed,
    "result_type": doc.getObject(r.data["name"]).TypeId,
    "has_part_boolean": any(
        o.TypeId in ("Part::Cut", "Part::Fuse", "Part::Common") for o in doc.Objects),
    "shape_valid": base_obj.Shape.isValid(),
}
"""

    def test_cut_two_bodies_preserves_base_history(self, run_freecad_script):
        """Cutting two Bodies keeps the base Body top-level and editable."""
        result = run_freecad_script(self._SCRIPT.replace("OPERATION", "cut"))
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"]
        # The crux of issue #17: the base Body must remain a top-level node.
        assert d["base_top_level"], (
            "base Body was reparented (history collapsed) — "
            f"result_type={d['result_type']}")
        # Body-to-body boolean must use the parametric PartDesign feature,
        # never a Part:: boolean that consumes the Body.
        assert d["result_type"] == "PartDesign::Boolean"
        assert not d["has_part_boolean"]
        assert d["shape_valid"]

    def test_fuse_two_bodies_preserves_base_history(self, run_freecad_script):
        """Fusing two Bodies also routes through PartDesign::Boolean."""
        result = run_freecad_script(self._SCRIPT.replace("OPERATION", "fuse"))
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"]
        assert d["base_top_level"]
        assert d["result_type"] == "PartDesign::Boolean"
        assert not d["has_part_boolean"]
        assert d["shape_valid"]
