# create_sketch on faces and arbitrary planes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `create_sketch` attach a sketch to a planar face of an existing object, to a datum/named plane, or to the current GUI selection — in addition to the existing origin planes — with body-aware placement and clean planar-face validation.

**Architecture:** A pure decision function `_resolve_sketch_attachment(...)` (no FreeCAD) chooses the attachment mode from already-inspected facts. Thin FreeCAD helpers gather those facts (classify the support, find its owning Body, inspect the face, read the selection). The `_handle_create_sketch` `do(doc)` body is rewired to: resolve facts → call the pure resolver → create the sketch in the right container → apply the attachment → offset → recompute. Origin-plane and standalone behavior is preserved.

**Tech Stack:** Python 3.11, FreeCAD 1.1.1 Python API, pytest. Unit tests run without FreeCAD (`env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/`); integration tests need the AppImage (`-m integration`).

**Branch:** `feature/sketch-on-face` (already created; spec committed at `f91832c`).

---

## File structure

- **Modify** `freecad_ai/tools/freecad_tools.py`
  - Add pure `_resolve_sketch_attachment(...)` near `_handle_create_sketch` (~line 266).
  - Add FreeCAD helpers `_classify_support`, `_owning_body_name`, `_inspect_face`, `_read_planar_selection`.
  - Rewire `_handle_create_sketch` `do(doc)` attachment section (current lines ~281–311).
  - Add `support` + `face` params to `_handle_create_sketch` signature and to the `CREATE_SKETCH` `ToolDefinition` (current lines ~268, ~521–538); extend the tool description.
- **Create** `tests/unit/test_sketch_attachment.py` — unit tests for the pure resolver + fake-based helper tests.
- **Modify** `tests/integration/` — add `tests/integration/test_sketch_attachment_integration.py` for real-FreeCAD attachment tests.
- **Modify** wiki `../freecad-ai-wiki/Tool-Reference.md` — document `support`/`face` (done after code, separate commit).

---

## Task 1: Pure attachment resolver

**Files:**
- Modify: `freecad_ai/tools/freecad_tools.py` (add function above `_handle_create_sketch`, ~line 266)
- Test: `tests/unit/test_sketch_attachment.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_sketch_attachment.py`:

```python
"""Unit tests for the pure create_sketch attachment resolver (no FreeCAD)."""

from freecad_ai.tools.freecad_tools import _resolve_sketch_attachment


def _resolve(**kw):
    base = dict(
        support="", face="", plane="XY", body_present=False,
        support_kind="", face_exists=False, face_planar=False, in_body=None,
    )
    base.update(kw)
    return _resolve_sketch_attachment(**base)


class TestResolveSketchAttachment:
    # --- explicit face ---
    def test_planar_face_on_solid_resolves_to_face_mode(self):
        spec = _resolve(support="Box", face="Face6", support_kind="solid",
                        face_exists=True, face_planar=True, in_body="Body")
        assert spec == {"mode": "face", "support": "Box", "sub": "Face6",
                        "in_body": "Body"}

    def test_face_on_standalone_solid_has_no_body(self):
        spec = _resolve(support="Imported", face="Face3", support_kind="solid",
                        face_exists=True, face_planar=True, in_body=None)
        assert spec["mode"] == "face"
        assert spec["in_body"] is None

    def test_missing_face_is_error(self):
        spec = _resolve(support="Box", face="Face99", support_kind="solid",
                        face_exists=False)
        assert spec["mode"] == "error"
        assert "Face99" in spec["message"]

    def test_non_planar_face_is_error(self):
        spec = _resolve(support="Cyl", face="Face1", support_kind="solid",
                        face_exists=True, face_planar=False)
        assert spec["mode"] == "error"
        assert "planar" in spec["message"].lower()

    # --- explicit plane ---
    def test_plane_support_without_face_resolves_to_plane_mode(self):
        spec = _resolve(support="DatumPlane", support_kind="plane", in_body="Body")
        assert spec == {"mode": "plane", "support": "DatumPlane", "in_body": "Body"}

    # --- validation ---
    def test_face_without_support_is_error(self):
        spec = _resolve(face="Face6")  # support empty
        assert spec["mode"] == "error"
        assert "support" in spec["message"].lower()

    def test_missing_support_is_error(self):
        spec = _resolve(support="Nope", support_kind="missing")
        assert spec["mode"] == "error"
        assert "not found" in spec["message"].lower()

    def test_solid_support_without_face_is_error(self):
        spec = _resolve(support="Box", support_kind="solid")
        assert spec["mode"] == "error"
        assert "face" in spec["message"].lower()

    # --- fall-through to current behavior ---
    def test_no_support_with_body_and_origin_plane(self):
        spec = _resolve(plane="XZ", body_present=True)
        assert spec == {"mode": "origin", "plane": "XZ"}

    def test_no_support_no_body_is_standalone(self):
        spec = _resolve(plane="XY", body_present=False)
        assert spec == {"mode": "standalone"}

    def test_origin_plane_is_case_insensitive(self):
        spec = _resolve(plane="xy", body_present=True)
        assert spec == {"mode": "origin", "plane": "XY"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_sketch_attachment.py -q`
Expected: FAIL — `ImportError: cannot import name '_resolve_sketch_attachment'`.

- [ ] **Step 3: Implement the resolver**

In `freecad_ai/tools/freecad_tools.py`, immediately above `# ── create_sketch ───` (~line 266), add:

```python
def _resolve_sketch_attachment(support, face, plane, body_present,
                               support_kind, face_exists, face_planar, in_body):
    """Decide where a sketch attaches. Pure — no FreeCAD calls.

    Inputs are already-inspected facts so this is unit-testable. When the
    GUI-selection fallback is used, the caller passes the selected object as
    ``support`` and its planar face as ``face`` — selection collapses into the
    same inputs as the explicit params.

    ``support_kind``: ``""`` (no support given and no usable selection),
    else ``"missing"`` | ``"plane"`` | ``"solid"`` | ``"other"``.
    ``face_exists`` / ``face_planar`` are meaningful only when ``face`` != "".
    ``in_body`` is the Body name owning the support, or ``None``.

    Returns one of:
      {"mode": "face", "support": str, "sub": str, "in_body": str|None}
      {"mode": "plane", "support": str, "in_body": str|None}
      {"mode": "origin", "plane": str}
      {"mode": "standalone"}
      {"mode": "error", "message": str}
    """
    if face and not support:
        return {"mode": "error", "message": "`face` requires `support`."}

    if support_kind == "missing":
        return {"mode": "error", "message": f"Object '{support}' not found."}

    if support_kind in ("plane", "solid", "other"):
        if face:
            if not face_exists:
                return {"mode": "error",
                        "message": f"Face '{face}' not found on '{support}'."}
            if not face_planar:
                return {"mode": "error",
                        "message": (f"Face '{face}' on '{support}' is not planar; "
                                    "sketches need a planar face.")}
            return {"mode": "face", "support": support, "sub": face,
                    "in_body": in_body}
        if support_kind == "plane":
            return {"mode": "plane", "support": support, "in_body": in_body}
        return {"mode": "error",
                "message": (f"`support` '{support}' is a solid; specify a `face` "
                            "(e.g. 'Face6'), or pass a datum/origin plane as "
                            "`support`.")}

    # No support / no usable selection — original behavior.
    if body_present and plane.upper() in ("XY", "XZ", "YZ"):
        return {"mode": "origin", "plane": plane.upper()}
    return {"mode": "standalone"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_sketch_attachment.py -q`
Expected: PASS (12 passed).

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/tools/freecad_tools.py tests/unit/test_sketch_attachment.py
git commit -m "feat(create_sketch): pure attachment resolver"
```

---

## Task 2: FreeCAD fact-gathering helpers

These translate live FreeCAD objects into the plain facts the resolver needs.
`_classify_support` and `_owning_body_name` are testable with lightweight fakes;
`_inspect_face` and `_read_planar_selection` are exercised by the integration
tests in Task 4.

**Files:**
- Modify: `freecad_ai/tools/freecad_tools.py` (add helpers below `_resolve_sketch_attachment`)
- Test: `tests/unit/test_sketch_attachment.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_sketch_attachment.py`:

```python
from freecad_ai.tools.freecad_tools import _classify_support, _owning_body_name


class _FakeObj:
    def __init__(self, type_id, name="Obj", has_solids=False, group_of=None):
        self.TypeId = type_id
        self.Name = name
        self._has_solids = has_solids
        # FreeCAD groups expose .Group; a Body lists its children there.
        if group_of is not None:
            self.Group = group_of

    @property
    def Shape(self):
        class _S:
            Solids = [1] if self._has_solids else []
        return _S()


class TestClassifySupport:
    def test_datum_plane_is_plane(self):
        assert _classify_support(_FakeObj("PartDesign::Plane")) == "plane"

    def test_origin_app_plane_is_plane(self):
        assert _classify_support(_FakeObj("App::Plane")) == "plane"

    def test_part_datum_plane_is_plane(self):
        assert _classify_support(_FakeObj("Part::DatumPlane")) == "plane"

    def test_solid_feature_is_solid(self):
        assert _classify_support(
            _FakeObj("Part::Feature", has_solids=True)) == "solid"

    def test_feature_without_solids_is_other(self):
        assert _classify_support(
            _FakeObj("Part::Feature", has_solids=False)) == "other"

    def test_sketch_is_other(self):
        assert _classify_support(_FakeObj("Sketcher::SketchObject")) == "other"


class TestOwningBodyName:
    def test_object_in_body_returns_body_name(self):
        child = _FakeObj("PartDesign::Pad", name="Pad")
        body = _FakeObj("PartDesign::Body", name="Body", group_of=[child])
        # The fake document is just the list of objects to scan.
        assert _owning_body_name(child, [body, child]) == "Body"

    def test_standalone_object_returns_none(self):
        feat = _FakeObj("Part::Feature", name="Imported")
        assert _owning_body_name(feat, [feat]) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_sketch_attachment.py -q`
Expected: FAIL — `ImportError` for `_classify_support` / `_owning_body_name`.

- [ ] **Step 3: Implement the helpers**

Add below `_resolve_sketch_attachment` in `freecad_ai/tools/freecad_tools.py`:

```python
_PLANE_TYPE_IDS = ("App::Plane", "PartDesign::Plane", "Part::DatumPlane")


def _classify_support(obj):
    """Classify a support object for sketch attachment.

    Returns 'plane' for origin/datum planes, 'solid' for shapes with solids,
    'other' for everything else (sketches, wires, empty features).
    """
    if getattr(obj, "TypeId", "") in _PLANE_TYPE_IDS:
        return "plane"
    shape = getattr(obj, "Shape", None)
    try:
        if shape is not None and shape.Solids:
            return "solid"
    except Exception:
        pass
    return "other"


def _owning_body_name(obj, objects):
    """Return the Name of the PartDesign::Body that contains ``obj``, or None.

    ``objects`` is the document's object list (``doc.Objects``). A Body lists
    its children in ``.Group``.
    """
    for cand in objects:
        if getattr(cand, "TypeId", "") != "PartDesign::Body":
            continue
        try:
            if any(getattr(c, "Name", None) == obj.Name for c in cand.Group):
                return cand.Name
        except Exception:
            pass
    return None


def _inspect_face(obj, face_name):
    """Return (exists, planar) for ``face_name`` on ``obj``'s shape.

    Never raises — a missing face or non-shape object yields (False, False).
    """
    import Part
    try:
        face = obj.Shape.getElement(face_name)
    except Exception:
        return (False, False)
    if face is None or face.ShapeType != "Face":
        return (False, False)
    try:
        return (True, isinstance(face.Surface, Part.Plane))
    except Exception:
        return (True, False)


def _read_planar_selection():
    """Return (object_name, sub_element) for the first usable planar-face or
    plane selection in the GUI, or None.

    Used as a fallback when create_sketch is called with no support/face. Any
    error or non-usable selection (edge, vertex, nothing, non-planar face)
    returns None so the caller falls through to default behavior.
    """
    try:
        import FreeCADGui as Gui
        import Part
    except Exception:
        return None
    try:
        sel = Gui.Selection.getSelectionEx()
    except Exception:
        return None
    for s in sel or []:
        obj = getattr(s, "Object", None)
        if obj is None:
            continue
        subs = getattr(s, "SubElementNames", None) or []
        if subs:
            sub = subs[0]
            try:
                el = obj.Shape.getElement(sub)
                if (el is not None and el.ShapeType == "Face"
                        and isinstance(el.Surface, Part.Plane)):
                    return (obj.Name, sub)
            except Exception:
                continue
        elif getattr(obj, "TypeId", "") in _PLANE_TYPE_IDS:
            return (obj.Name, "")
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_sketch_attachment.py -q`
Expected: PASS (20 passed).

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/tools/freecad_tools.py tests/unit/test_sketch_attachment.py
git commit -m "feat(create_sketch): FreeCAD fact-gathering helpers for attachment"
```

---

## Task 3: Wire params and rewire the handler

**Files:**
- Modify: `freecad_ai/tools/freecad_tools.py` — `_handle_create_sketch` signature (~line 268), attachment section (~lines 281–311), and the `CREATE_SKETCH` `ToolDefinition` (~lines 506–540).

- [ ] **Step 1: Add the two params to the handler signature**

Change (current ~line 268):

```python
def _handle_create_sketch(
    plane: str = "XY",
    body_name: str = "",
    geometries: list | None = None,
    constraints: list | None = None,
    label: str = "",
    offset: float = 0.0,
) -> ToolResult:
```

to:

```python
def _handle_create_sketch(
    plane: str = "XY",
    body_name: str = "",
    geometries: list | None = None,
    constraints: list | None = None,
    label: str = "",
    offset: float = 0.0,
    support: str = "",
    face: str = "",
) -> ToolResult:
```

- [ ] **Step 2: Rewire the attachment section**

Replace the current block (from `body = None` through `doc.recompute()` that
precedes the `geo_count = 0` line — current lines ~282–311):

```python
        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")

        if body:
            sketch = body.newObject("Sketcher::SketchObject", label or "Sketch")
        else:
            sketch = doc.addObject("Sketcher::SketchObject", label or "Sketch")

        # Attach to plane
        if body and plane.upper() in ("XY", "XZ", "YZ"):
            plane_feat = _get_body_plane(body, plane.upper())
            if plane_feat:
                sketch.AttachmentSupport = [(plane_feat, "")]
                sketch.MapMode = "FlatFace"

        # Offset the sketch along the plane normal
        if offset != 0:
            offset_map = {
                "XY": App.Vector(0, 0, offset),
                "XZ": App.Vector(0, offset, 0),
                "YZ": App.Vector(offset, 0, 0),
            }
            ovec = offset_map.get(plane.upper(), App.Vector(0, 0, offset))
            sketch.AttachmentOffset = App.Placement(ovec, App.Rotation())

        doc.recompute()
```

with:

```python
        warnings = []

        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")

        # Gather attachment facts from explicit params, or fall back to the GUI
        # selection when neither support nor face was given.
        sup, fc = support, face
        if not sup and not fc:
            picked = _read_planar_selection()
            if picked:
                sup, fc = picked

        sup_kind = ""
        face_exists = face_planar = False
        in_body = None
        if sup:
            sup_obj = _get_object(doc, sup)
            if sup_obj is None:
                sup_kind = "missing"
            else:
                sup = sup_obj.Name
                sup_kind = _classify_support(sup_obj)
                in_body = _owning_body_name(sup_obj, doc.Objects)
                if fc:
                    face_exists, face_planar = _inspect_face(sup_obj, fc)

        spec = _resolve_sketch_attachment(
            sup, fc, plane, body is not None,
            sup_kind, face_exists, face_planar, in_body,
        )
        if spec["mode"] == "error":
            hint = _suggest_similar(doc, support) if sup_kind == "missing" else ""
            return ToolResult(success=False, output="", error=spec["message"] + hint)

        # Choose the container the sketch is created in.
        if spec["mode"] in ("face", "plane"):
            if body_name:
                warnings.append("body_name ignored — sketch placed relative to support.")
            container = _get_object(doc, spec["in_body"]) if spec.get("in_body") else None
        elif spec["mode"] == "origin":
            container = body
        else:  # standalone
            container = body

        if container is not None:
            sketch = container.newObject("Sketcher::SketchObject", label or "Sketch")
        else:
            sketch = doc.addObject("Sketcher::SketchObject", label or "Sketch")

        # Apply the attachment.
        if spec["mode"] == "face":
            sketch.AttachmentSupport = [(_get_object(doc, spec["support"]), spec["sub"])]
            sketch.MapMode = "FlatFace"
        elif spec["mode"] == "plane":
            sketch.AttachmentSupport = [(_get_object(doc, spec["support"]), "")]
            sketch.MapMode = "FlatFace"
        elif spec["mode"] == "origin":
            plane_feat = _get_body_plane(container, spec["plane"])
            if plane_feat:
                sketch.AttachmentSupport = [(plane_feat, "")]
                sketch.MapMode = "FlatFace"

        # Offset along the attachment normal. For face/plane attachments the
        # sketch's local Z is the face/plane normal, so offset is (0,0,offset).
        # For origin planes keep the explicit per-plane vector.
        if offset != 0:
            if spec["mode"] in ("face", "plane"):
                sketch.AttachmentOffset = App.Placement(
                    App.Vector(0, 0, offset), App.Rotation())
            else:
                offset_map = {
                    "XY": App.Vector(0, 0, offset),
                    "XZ": App.Vector(0, offset, 0),
                    "YZ": App.Vector(offset, 0, 0),
                }
                ovec = offset_map.get(plane.upper(), App.Vector(0, 0, offset))
                sketch.AttachmentOffset = App.Placement(ovec, App.Rotation())

        doc.recompute()

        # Verify a face/plane attachment actually resolved (no silent failure).
        if spec["mode"] in ("face", "plane"):
            try:
                placement_ok = sketch.Placement is not None and (
                    sketch.MapMode == "FlatFace")
            except Exception:
                placement_ok = False
            if not placement_ok:
                return ToolResult(
                    success=False, output="",
                    error=(f"Failed to attach sketch to '{spec['support']}'"
                           + (f":{spec['sub']}" if spec.get('sub') else "")
                           + " — attachment did not resolve."))
```

- [ ] **Step 3: Surface warnings in the success result**

In the final `return ToolResult(success=True, ...)` of `do(doc)` (current ~line 491), prepend any warnings to the output. Change the `output=(...)` argument so it begins with:

```python
            output=(
                ("⚠ " + " ".join(warnings) + "\n" if warnings else "")
                + f"Created sketch '{sketch.Name}' with {geo_count} geometries"
                f" and {constraint_count} constraints.{constraint_status}"
                f"{constraint_info}"
                f"\nUse sketch_name='{sketch.Name}' in pad_sketch/pocket_sketch."),
```

- [ ] **Step 4: Add the params to the ToolDefinition + extend the description**

In `CREATE_SKETCH` (current ~line 506), add to the `parameters=[...]` list (after the `offset` param):

```python
        ToolParam("support", "string",
                  "Object to attach the sketch to: a solid (with `face`) or a "
                  "datum/origin plane object (by name). Overrides `plane`. If "
                  "omitted and a planar face/plane is selected in the viewport, "
                  "that selection is used.",
                  required=False, default=""),
        ToolParam("face", "string",
                  "Planar sub-element of `support` to sketch on, e.g. 'Face6'. "
                  "Get face names from list_faces. Requires `support`. The face "
                  "must be planar.",
                  required=False, default=""),
```

And append to the `description=(...)` string (before the closing `)`):

```python
        " To sketch on an existing solid's planar face, pass support='Obj' and "
        "face='Face6' (use list_faces to find the name); to sketch on a datum "
        "plane pass support='PlaneName'. Without support, attaches to the origin "
        "`plane` (XY/XZ/YZ) as before."
```

- [ ] **Step 5: Run the unit suite to confirm nothing regressed**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_sketch_attachment.py tests/unit/test_new_tools.py tests/unit/test_registry.py -q`
Expected: PASS (no failures; resolver tests still green, tool registry still loads).

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/tools/freecad_tools.py
git commit -m "feat(create_sketch): attach to faces, datum planes, and selection"
```

---

## Task 4: Integration tests (real FreeCAD)

**Files:**
- Create: `tests/integration/test_sketch_attachment_integration.py`

- [ ] **Step 1: Write the integration tests**

```python
"""Integration tests for create_sketch face/plane attachment (needs FreeCAD)."""

import pytest

from freecad_ai.core.executor import _find_freecad_cmd

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def freecad_available():
    if not _find_freecad_cmd():
        pytest.skip("No FreeCAD binary available")


@pytest.fixture
def doc(freecad_available):
    import FreeCAD as App
    d = App.newDocument("SketchAttachTest")
    yield d
    App.closeDocument(d.Name)


def _run(**kwargs):
    from freecad_ai.tools.freecad_tools import _handle_create_sketch
    return _handle_create_sketch(**kwargs)


class TestCreateSketchAttachment:
    def test_sketch_on_box_face_attaches(self, doc):
        import Part
        feat = doc.addObject("Part::Feature", "Box")
        feat.Shape = Part.makeBox(10, 10, 10)
        doc.recompute()
        res = _run(support="Box", face="Face6")
        assert res.success, res.error
        sk = doc.getObject(res.data["name"])
        assert sk.MapMode == "FlatFace"
        assert sk.AttachmentSupport  # non-empty

    def test_sketch_on_standalone_solid_face_is_standalone(self, doc):
        import Part
        feat = doc.addObject("Part::Feature", "Imported")
        feat.Shape = Part.makeBox(8, 8, 8)
        doc.recompute()
        res = _run(support="Imported", face="Face1")
        assert res.success, res.error
        sk = doc.getObject(res.data["name"])
        # Not inside a Body, but attached to the face.
        assert sk.MapMode == "FlatFace"

    def test_sketch_on_datum_plane_by_name(self, doc):
        body = doc.addObject("PartDesign::Body", "Body")
        dp = body.newObject("PartDesign::Plane", "DatumPlane")
        doc.recompute()
        res = _run(support="DatumPlane")
        assert res.success, res.error
        sk = doc.getObject(res.data["name"])
        assert sk.MapMode == "FlatFace"

    def test_non_planar_face_errors_cleanly(self, doc):
        import Part
        feat = doc.addObject("Part::Feature", "Cyl")
        feat.Shape = Part.makeCylinder(5, 10)
        doc.recompute()
        # Face1 of a cylinder is the curved lateral surface.
        res = _run(support="Cyl", face="Face1")
        assert not res.success
        assert "planar" in res.error.lower()

    def test_missing_support_errors(self, doc):
        res = _run(support="DoesNotExist", face="Face1")
        assert not res.success
        assert "not found" in res.error.lower()

    def test_origin_plane_still_works(self, doc):
        body = doc.addObject("PartDesign::Body", "Body")
        doc.recompute()
        res = _run(plane="XZ", body_name="Body")
        assert res.success, res.error
        sk = doc.getObject(res.data["name"])
        assert sk.MapMode == "FlatFace"

    def test_standalone_sketch_still_works(self, doc):
        res = _run(plane="XY")
        assert res.success, res.error
```

- [ ] **Step 2: Run the integration tests**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/integration/test_sketch_attachment_integration.py -m integration -v`
Expected: PASS (7 passed). If `Face6`/`Face1` indexing differs on this FreeCAD build, adjust the face name using a quick `list_faces`-style probe, but a `Part.makeBox` has 6 planar faces so any `FaceN` (N=1..6) is planar.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_sketch_attachment_integration.py
git commit -m "test(create_sketch): integration tests for face/plane attachment"
```

---

## Task 5: Docs — tool description verification and wiki

**Files:**
- Modify: `../freecad-ai-wiki/Tool-Reference.md` (the wiki is a sibling repo per project memory)

- [ ] **Step 1: Confirm the in-code description renders**

Run: `env -u PYTHONPATH .venv/bin/python -c "from freecad_ai.tools.freecad_tools import CREATE_SKETCH; print(CREATE_SKETCH.description); print([p.name for p in CREATE_SKETCH.parameters])"`
Expected: description mentions `support`/`face`; param list includes `support` and `face`.

- [ ] **Step 2: Update the wiki `create_sketch` entry**

In `../freecad-ai-wiki/Tool-Reference.md`, find the `create_sketch` section and add the `support` and `face` parameters to its parameter table, plus a short note:

> `support` + `face` attach the sketch to a planar face of an existing object
> (e.g. `support="Box", face="Face6"` — get the name from `list_faces`).
> `support` alone attaches to a datum/origin plane object by name. With no
> `support`, the sketch uses the origin `plane` (XY/XZ/YZ) as before, or the
> current planar-face selection if one exists. The face must be planar.

- [ ] **Step 3: Commit the wiki (in the wiki repo)**

```bash
cd ../freecad-ai-wiki
git add Tool-Reference.md
git commit -m "docs(tool-reference): create_sketch support/face attachment"
cd -
```

---

## Self-review notes

- **Spec coverage:** Goals 1–3 (face / plane / selection) → Tasks 1–4. Body handling → Task 3 (container selection + `in_body`). Validation/errors → resolver (Task 1) + handler verification (Task 3) + integration (Task 4). Testing section → Tasks 1, 2, 4. Backward compat → resolver fall-through + `test_origin_plane_still_works` / `test_standalone_sketch_still_works`.
- **Deviations from spec (flagged to user, spec updated):** empty/unusable selection falls through to current behavior (not an error); `body_name` + `support` together → warning, not hard error.
- **Type consistency:** the resolver's returned dict keys (`mode`, `support`, `sub`, `in_body`, `plane`, `message`) are used consistently in Task 3's handler branches. Helper names (`_classify_support`, `_owning_body_name`, `_inspect_face`, `_read_planar_selection`) match between definition (Task 2) and use (Task 3).
- **No placeholders:** every code step shows complete code.
