"""Integration tests for create_datum_line (runs inside FreeCAD).

Uses the run_freecad_script fixture (see tests/integration/conftest.py).
"""

import pytest

pytestmark = pytest.mark.integration


class TestCreateDatumLine:
    def test_two_points_standalone(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_datum_line

r = _handle_create_datum_line(point1=[0, 0, 0], point2=[10, 0, 0])
doc.recompute()
ln = doc.getObject(r.data["name"]) if r.success else None
base = ln.Placement.Base if ln else None
results["data"] = {
    "success": r.success,
    "error": r.error,
    "type_id": ln.TypeId if ln else None,
    "base": [base.x, base.y, base.z] if base else None,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["type_id"] == "PartDesign::Line"
        # Line starts at point1.
        assert abs(d["base"][0]) < 1e-6
        assert abs(d["base"][1]) < 1e-6
        assert abs(d["base"][2]) < 1e-6

    def test_two_points_direction(self, run_freecad_script):
        # The line's local Z axis (its direction) should point from p1 to p2.
        result = run_freecad_script("""
import FreeCAD as App
from freecad_ai.tools.freecad_tools import _handle_create_datum_line

r = _handle_create_datum_line(point1=[0, 0, 0], point2=[3, 4, 0])
doc.recompute()
ln = doc.getObject(r.data["name"]) if r.success else None
zdir = ln.Placement.Rotation.multVec(App.Vector(0, 0, 1)) if ln else None
results["data"] = {
    "success": r.success,
    "error": r.error,
    "zdir": [zdir.x, zdir.y, zdir.z] if zdir else None,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        # p1→p2 is (3,4,0); the placed line's local Z must align with it.
        assert abs(d["zdir"][0] - 0.6) < 1e-6
        assert abs(d["zdir"][1] - 0.8) < 1e-6
        assert abs(d["zdir"][2]) < 1e-6

    def test_two_points_in_body(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_body, _handle_create_datum_line

_handle_create_body(label="Body")
doc.recompute()
r = _handle_create_datum_line(point1=[0, 0, 0], point2=[0, 10, 0], body_name="Body")
doc.recompute()
ln = doc.getObject(r.data["name"]) if r.success else None
body = doc.getObject("Body")
in_body = ln in body.Group if (ln and body) else False
results["data"] = {
    "success": r.success,
    "error": r.error,
    "type_id": ln.TypeId if ln else None,
    "in_body": in_body,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["type_id"] == "PartDesign::Line"
        assert d["in_body"]

    def test_edge_on_body_feature(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_datum_line

body = doc.addObject("PartDesign::Body", "Body")
box = body.newObject("PartDesign::AdditiveBox", "Box")
box.Length = 10; box.Width = 10; box.Height = 10
doc.recompute()

# Find a straight edge.
straight = None
for i, e in enumerate(box.Shape.Edges, start=1):
    try:
        import Part
        if isinstance(e.Curve, Part.Line):
            straight = "Edge%d" % i
            break
    except Exception:
        pass

r = _handle_create_datum_line(support="Box", edge=straight)
doc.recompute()
ln = doc.getObject(r.data["name"]) if r.success else None
state = list(getattr(ln, "State", []) or []) if ln else None
results["data"] = {
    "success": r.success,
    "error": r.error,
    "type_id": ln.TypeId if ln else None,
    "map_mode": ln.MapMode if ln else None,
    "edge": straight,
    "state": state,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["edge"] is not None
        assert d["success"], d["error"]
        assert d["type_id"] == "PartDesign::Line"
        assert d["map_mode"] == "Tangent"
        assert not any(s in ("Invalid", "Error") for s in d["state"])

    def test_edge_on_standalone_solid(self, run_freecad_script):
        result = run_freecad_script("""
import Part
from freecad_ai.tools.freecad_tools import _handle_create_datum_line

feat = doc.addObject("Part::Feature", "Box")
feat.Shape = Part.makeBox(10, 10, 10)
doc.recompute()

straight = None
for i, e in enumerate(feat.Shape.Edges, start=1):
    if isinstance(e.Curve, Part.Line):
        straight = "Edge%d" % i
        break

r = _handle_create_datum_line(support="Box", edge=straight)
doc.recompute()
ln = doc.getObject(r.data["name"]) if r.success else None
state = list(getattr(ln, "State", []) or []) if ln else None
results["data"] = {
    "success": r.success,
    "error": r.error,
    "type_id": ln.TypeId if ln else None,
    "map_mode": ln.MapMode if ln else None,
    "state": state,
    "edge": straight,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["edge"] is not None
        assert d["success"], d["error"]
        assert d["type_id"] == "PartDesign::Line"
        assert d["map_mode"] == "Tangent"
        assert not any(s in ("Invalid", "Error") for s in d["state"])

    def test_origin_axis(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_body, _handle_create_datum_line

_handle_create_body(label="Body")
doc.recompute()
r = _handle_create_datum_line(axis="Z", body_name="Body")
doc.recompute()
ln = doc.getObject(r.data["name"]) if r.success else None
state = list(getattr(ln, "State", []) or []) if ln else None
results["data"] = {
    "success": r.success,
    "error": r.error,
    "type_id": ln.TypeId if ln else None,
    "map_mode": ln.MapMode if ln else None,
    "state": state,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["type_id"] == "PartDesign::Line"
        assert d["map_mode"] == "Tangent"
        assert not any(s in ("Invalid", "Error") for s in d["state"])

    def test_curved_edge_errors(self, run_freecad_script):
        result = run_freecad_script("""
import Part
from freecad_ai.tools.freecad_tools import _handle_create_datum_line

feat = doc.addObject("Part::Feature", "Cyl")
feat.Shape = Part.makeCylinder(5, 10)
doc.recompute()

curved = None
for i, e in enumerate(feat.Shape.Edges, start=1):
    if not isinstance(e.Curve, Part.Line):
        curved = "Edge%d" % i
        break

r = _handle_create_datum_line(support="Cyl", edge=curved)
results["data"] = {"success": r.success, "error": r.error, "edge": curved}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["edge"] is not None
        assert not d["success"]
        assert "straight" in d["error"].lower()

    def test_no_inputs_errors(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_datum_line

r = _handle_create_datum_line()
results["data"] = {"success": r.success, "error": r.error}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert not d["success"]
        assert "exactly one" in d["error"].lower()

    def test_revolve_around_created_datum_line(self, run_freecad_script):
        # Closes the loop: a datum line is usable as a revolve axis.
        result = run_freecad_script("""
import FreeCAD as App
from freecad_ai.tools.freecad_tools import _handle_create_datum_line

body = doc.addObject("PartDesign::Body", "Body")
sk = body.newObject("Sketcher::SketchObject", "Sketch")
import Part
sk.addGeometry(Part.Circle(App.Vector(5, 0, 0), App.Vector(0, 0, 1), 1.0), False)
doc.recompute()

# A datum line along global Y, offset in X, as a revolve axis.
ln_r = _handle_create_datum_line(point1=[0, 0, 0], point2=[0, 10, 0], body_name="Body")
doc.recompute()
ln = doc.getObject(ln_r.data["name"]) if ln_r.success else None

rev = body.newObject("PartDesign::Revolution", "Revolution")
rev.Profile = sk
rev.ReferenceAxis = (ln, "")
rev.Angle = 360
doc.recompute()
results["data"] = {
    "datum_ok": ln_r.success,
    "datum_err": ln_r.error,
    "rev_valid": rev.Shape.isValid() if rev.Shape else False,
    "rev_solids": len(rev.Shape.Solids) if rev.Shape else 0,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["datum_ok"], d["datum_err"]
        assert d["rev_valid"]
        assert d["rev_solids"] == 1
