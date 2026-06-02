"""Unit tests for the pure create_datum_plane reference resolver (no FreeCAD)."""

from freecad_ai.tools.freecad_tools import _resolve_datum_plane_attachment


def _resolve(**kw):
    base = dict(
        support="", face="", plane="XY", body_present=False,
        support_kind="", face_exists=False, face_planar=False, in_body=None,
    )
    base.update(kw)
    return _resolve_datum_plane_attachment(**base)


class TestResolveDatumPlaneAttachment:
    def test_standalone_becomes_no_reference_error(self):
        # No support, no body → sketch resolver would say "standalone", but a
        # datum plane needs a reference.
        spec = _resolve(plane="XY", body_present=False)
        assert spec["mode"] == "error"
        assert "reference" in spec["message"].lower()

    def test_face_passes_through(self):
        spec = _resolve(support="Box", face="Face6", support_kind="solid",
                        face_exists=True, face_planar=True, in_body="Body")
        assert spec == {"mode": "face", "support": "Box", "sub": "Face6",
                        "in_body": "Body"}

    def test_plane_passes_through(self):
        spec = _resolve(support="DatumPlane", support_kind="plane", in_body=None)
        assert spec == {"mode": "plane", "support": "DatumPlane", "in_body": None}

    def test_origin_passes_through(self):
        spec = _resolve(plane="XZ", body_present=True)
        assert spec == {"mode": "origin", "plane": "XZ"}

    def test_resolver_error_passes_through(self):
        # face without support is an error in the underlying resolver.
        spec = _resolve(face="Face1")
        assert spec["mode"] == "error"
        assert "support" in spec["message"].lower()

    def test_non_planar_face_error_passes_through(self):
        spec = _resolve(support="Cyl", face="Face1", support_kind="solid",
                        face_exists=True, face_planar=False)
        assert spec["mode"] == "error"
        assert "planar" in spec["message"].lower()


from freecad_ai.tools.freecad_tools import CREATE_DATUM_PLANE, ALL_TOOLS


class TestCreateDatumPlaneDefinition:
    def test_name_and_category(self):
        assert CREATE_DATUM_PLANE.name == "create_datum_plane"
        assert CREATE_DATUM_PLANE.category == "modeling"

    def test_registered_in_all_tools(self):
        assert CREATE_DATUM_PLANE in ALL_TOOLS

    def test_no_array_param_missing_items(self):
        # GitHub Models rejects array params declared without `items` (issue #10).
        for p in CREATE_DATUM_PLANE.parameters:
            if getattr(p, "type", None) == "array":
                assert getattr(p, "items", None) is not None, p.name
