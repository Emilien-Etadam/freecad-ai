"""Integration tests for create_datum_plane (runs inside FreeCAD).

Uses the run_freecad_script fixture (see tests/integration/conftest.py).
"""

import pytest

pytestmark = pytest.mark.integration


class TestCreateDatumPlane:
    def test_offset_from_origin_plane_in_body(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_body, _handle_create_datum_plane

_handle_create_body(label="Body")
doc.recompute()
r = _handle_create_datum_plane(plane="XY", body_name="Body", offset=20.0)
doc.recompute()
dp = doc.getObject(r.data["name"]) if r.success else None
results["data"] = {
    "success": r.success,
    "error": r.error,
    "type_id": dp.TypeId if dp else None,
    "z": dp.Placement.Base.z if dp else None,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["type_id"] == "PartDesign::Plane"
        assert abs(d["z"] - 20.0) < 1e-6

    def test_offset_from_body_feature_face(self, run_freecad_script):
        result = run_freecad_script("""
import Part
from freecad_ai.tools.freecad_tools import _handle_create_datum_plane

body = doc.addObject("PartDesign::Body", "Body")
box = body.newObject("PartDesign::AdditiveBox", "Box")
box.Length = 10; box.Width = 10; box.Height = 10
doc.recompute()

planar = None
for i, f in enumerate(box.Shape.Faces, start=1):
    if isinstance(f.Surface, Part.Plane):
        planar = "Face%d" % i
        break

r = _handle_create_datum_plane(support="Box", face=planar)
doc.recompute()
dp = doc.getObject(r.data["name"]) if r.success else None
results["data"] = {
    "success": r.success,
    "error": r.error,
    "type_id": dp.TypeId if dp else None,
    "face": planar,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["type_id"] == "PartDesign::Plane"

    def test_offset_from_standalone_solid_face(self, run_freecad_script):
        result = run_freecad_script("""
import Part
from freecad_ai.tools.freecad_tools import _handle_create_datum_plane

feat = doc.addObject("Part::Feature", "Box")
feat.Shape = Part.makeBox(10, 10, 10)
doc.recompute()

r = _handle_create_datum_plane(support="Box", face="Face6", offset=3.0)
doc.recompute()
dp = doc.getObject(r.data["name"]) if r.success else None
results["data"] = {
    "success": r.success,
    "error": r.error,
    "type_id": dp.TypeId if dp else None,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["type_id"] == "PartDesign::Plane"

    def test_sketch_on_created_datum_plane(self, run_freecad_script):
        result = run_freecad_script("""
import Part
from freecad_ai.tools.freecad_tools import _handle_create_datum_plane, _handle_create_sketch

feat = doc.addObject("Part::Feature", "Box")
feat.Shape = Part.makeBox(10, 10, 10)
doc.recompute()

dp_r = _handle_create_datum_plane(support="Box", face="Face6", offset=5.0)
doc.recompute()

sk_r = _handle_create_sketch(support=dp_r.data["name"])
doc.recompute()
sk = doc.getObject(sk_r.data["name"]) if sk_r.success else None
results["data"] = {
    "datum_ok": dp_r.success,
    "datum_err": dp_r.error,
    "sketch_ok": sk_r.success,
    "sketch_err": sk_r.error,
    "map_mode": sk.MapMode if sk else None,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["datum_ok"], d["datum_err"]
        assert d["sketch_ok"], d["sketch_err"]
        assert d["map_mode"] == "FlatFace"

    def test_no_reference_errors(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_datum_plane

r = _handle_create_datum_plane(offset=10.0)
results["data"] = {"success": r.success, "error": r.error}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert not d["success"]
        assert "reference" in d["error"].lower()

    def test_non_planar_face_errors(self, run_freecad_script):
        result = run_freecad_script("""
import Part
from freecad_ai.tools.freecad_tools import _handle_create_datum_plane

feat = doc.addObject("Part::Feature", "Cyl")
feat.Shape = Part.makeCylinder(5, 10)
doc.recompute()

nonplanar = None
for i, f in enumerate(feat.Shape.Faces, start=1):
    if not isinstance(f.Surface, Part.Plane):
        nonplanar = "Face%d" % i
        break

r = _handle_create_datum_plane(support="Cyl", face=nonplanar)
results["data"] = {"success": r.success, "error": r.error, "face": nonplanar}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["face"] is not None
        assert not d["success"]
        assert "planar" in d["error"].lower()
