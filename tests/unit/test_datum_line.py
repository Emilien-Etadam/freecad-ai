"""Unit tests for the pure create_datum_line mode resolver (no FreeCAD)."""

from freecad_ai.tools.freecad_tools import _resolve_datum_line_def


def _resolve(**kw):
    base = dict(
        point1=None, point2=None, support="", edge="", axis="",
        body_present=False, support_kind="", edge_exists=False,
        edge_straight=False, in_body=None,
    )
    base.update(kw)
    return _resolve_datum_line_def(**base)


class TestResolveDatumLineDef:
    def test_two_points_mode(self):
        spec = _resolve(point1=[0, 0, 0], point2=[10, 0, 0])
        assert spec == {"mode": "points", "p1": [0, 0, 0], "p2": [10, 0, 0]}

    def test_edge_mode(self):
        spec = _resolve(support="Box", edge="Edge3", support_kind="solid",
                        edge_exists=True, edge_straight=True, in_body="Body")
        assert spec == {"mode": "edge", "support": "Box", "sub": "Edge3",
                        "in_body": "Body"}

    def test_origin_axis_mode(self):
        spec = _resolve(axis="Z", body_present=True)
        assert spec == {"mode": "origin", "axis": "Z"}

    def test_origin_axis_lowercase_normalized(self):
        spec = _resolve(axis="z", body_present=True)
        assert spec == {"mode": "origin", "axis": "Z"}

    def test_coincident_points_error(self):
        spec = _resolve(point1=[1, 2, 3], point2=[1, 2, 3])
        assert spec["mode"] == "error"
        assert "coincident" in spec["message"].lower()

    def test_point1_without_point2_error(self):
        spec = _resolve(point1=[0, 0, 0])
        assert spec["mode"] == "error"
        assert "[x, y, z]" in spec["message"] or "point" in spec["message"].lower()

    def test_point2_without_point1_error(self):
        spec = _resolve(point2=[0, 0, 0])
        assert spec["mode"] == "error"

    def test_edge_without_support_error(self):
        spec = _resolve(edge="Edge1")
        assert spec["mode"] == "error"
        assert "support" in spec["message"].lower()

    def test_support_without_edge_error(self):
        spec = _resolve(support="Box", support_kind="solid")
        assert spec["mode"] == "error"
        assert "edge" in spec["message"].lower()

    def test_missing_support_error(self):
        spec = _resolve(support="Nope", edge="Edge1", support_kind="missing")
        assert spec["mode"] == "error"
        assert "not found" in spec["message"].lower()

    def test_edge_not_found_error(self):
        spec = _resolve(support="Box", edge="Edge99", support_kind="solid",
                        edge_exists=False)
        assert spec["mode"] == "error"
        assert "edge" in spec["message"].lower()

    def test_non_straight_edge_error(self):
        spec = _resolve(support="Cyl", edge="Edge1", support_kind="solid",
                        edge_exists=True, edge_straight=False)
        assert spec["mode"] == "error"
        assert "straight" in spec["message"].lower()

    def test_bad_axis_error(self):
        spec = _resolve(axis="W", body_present=True)
        assert spec["mode"] == "error"
        assert "x, y, or z" in spec["message"].lower()

    def test_axis_without_body_error(self):
        spec = _resolve(axis="Z", body_present=False)
        assert spec["mode"] == "error"
        assert "body_name" in spec["message"].lower()

    def test_two_modes_points_and_axis_error(self):
        spec = _resolve(point1=[0, 0, 0], point2=[1, 0, 0], axis="Z",
                        body_present=True)
        assert spec["mode"] == "error"
        assert "exactly one" in spec["message"].lower()

    def test_two_modes_support_and_points_error(self):
        spec = _resolve(point1=[0, 0, 0], point2=[1, 0, 0], support="Box",
                        edge="Edge1", support_kind="solid")
        assert spec["mode"] == "error"
        assert "exactly one" in spec["message"].lower()

    def test_no_inputs_error(self):
        spec = _resolve()
        assert spec["mode"] == "error"
        assert "exactly one" in spec["message"].lower()
