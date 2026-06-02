# create_datum_line Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `create_datum_line` tool that creates a datum line (axis) by two points, a straight edge reference, or an origin axis — referenceable as a `revolve_sketch` axis or mirror reference.

**Architecture:** A pure `_resolve_datum_line_def(...)` selects the mode and validates without touching FreeCAD (unit-tested). A FreeCAD helper `_inspect_edge(...)` reports edge existence/straightness. The handler `_handle_create_datum_line(...)` resolves, picks the container Body (or standalone), creates a `PartDesign::Line`, attaches it (`OneEdge`) or places it (two points), recomputes, and verifies. Mirrors the just-merged `create_datum_plane` (piece #2).

**Tech Stack:** Python 3.11, FreeCAD 1.1.1 (PySide6), pytest. Unit tests run without FreeCAD (`env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/`); integration tests run inside the FreeCAD AppImage via the `run_freecad_script` fixture (`-m integration`).

**Spec:** `docs/superpowers/specs/2026-06-02-create-datum-line-design.md`

**Branch:** `feature/datum-line` (already created off `master`; the spec is already committed on it).

---

## Conventions to follow (read before starting)

- `ToolParam(name, type, description, required=..., default=..., enum=..., items=...)` — array params **must** pass `items` (issue #10).
- `_get_object(doc, name)` resolves Name then Label; `_suggest_similar(doc, name, type_filter="")` produces a "did you mean" hint string; `_owning_body_name(obj, doc.Objects)` returns the owning `PartDesign::Body` name or `None`; `_get_body_axis(body, "X"|"Y"|"Z")` returns the origin axis feature; `_classify_support(obj)` returns `"plane"|"solid"|"other"`; `_with_undo(label, do)` wraps the mutation. All already exist in `freecad_ai/tools/freecad_tools.py`.
- Handlers import FreeCAD **inside** the function (`import FreeCAD as App`), never at module level — the unit suite imports the module without FreeCAD.
- `ToolResult(success=..., output=..., error=..., data=...)`.

---

## Task 1: Pure mode resolver `_resolve_datum_line_def`

**Files:**
- Modify: `freecad_ai/tools/freecad_tools.py` (add the resolver + a module constant near `_resolve_datum_plane_attachment`, ~line 348)
- Test: `tests/unit/test_datum_line.py` (create)

- [ ] **Step 1: Write the failing unit tests**

Create `tests/unit/test_datum_line.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_datum_line.py -q`
Expected: FAIL — `ImportError: cannot import name '_resolve_datum_line_def'`.

- [ ] **Step 3: Implement the resolver**

In `freecad_ai/tools/freecad_tools.py`, immediately after `_resolve_datum_plane_attachment` (ends ~line 348), add:

```python
_DATUM_LINE_MODE_ERR = (
    "Specify exactly one of: two points (point1+point2), an edge "
    "(support+edge), or an origin axis (axis).")


def _resolve_datum_line_def(point1, point2, support, edge, axis,
                            body_present, support_kind, edge_exists,
                            edge_straight, in_body):
    """Decide how a datum line is defined. Pure — no FreeCAD calls.

    Exactly one mode's inputs must be supplied. ``support_kind`` is ``""`` |
    ``"missing"`` | ``"plane"`` | ``"solid"`` | ``"other"`` (only ``"missing"``
    changes the outcome — any found object can host an edge). ``edge_exists`` /
    ``edge_straight`` matter only when ``edge`` != "". ``in_body`` is the
    support's owning Body name or ``None``.

    Returns one of:
      {"mode": "points", "p1": [x,y,z], "p2": [x,y,z]}
      {"mode": "edge", "support": str, "sub": str, "in_body": str|None}
      {"mode": "origin", "axis": "X"|"Y"|"Z"}
      {"mode": "error", "message": str}
    """
    point1 = list(point1) if point1 else []
    point2 = list(point2) if point2 else []
    support = support or ""
    edge = edge or ""
    axis = (axis or "").upper()

    has_points = bool(point1) or bool(point2)
    has_edge = bool(support) or bool(edge)
    has_axis = bool(axis)

    if sum([has_points, has_edge, has_axis]) != 1:
        return {"mode": "error", "message": _DATUM_LINE_MODE_ERR}

    if has_points:
        if len(point1) != 3 or len(point2) != 3:
            return {"mode": "error",
                    "message": "Two-points mode needs point1 and point2 as "
                               "[x, y, z]."}
        d2 = sum((a - b) ** 2 for a, b in zip(point1, point2))
        if d2 < 1e-14:
            return {"mode": "error",
                    "message": "point1 and point2 are coincident; a line needs "
                               "two distinct points."}
        return {"mode": "points", "p1": point1, "p2": point2}

    if has_edge:
        if not support:
            return {"mode": "error", "message": "`edge` requires `support`."}
        if not edge:
            return {"mode": "error",
                    "message": "`support` requires an `edge` (e.g. 'Edge3')."}
        if support_kind == "missing":
            return {"mode": "error", "message": f"Object '{support}' not found."}
        if not edge_exists:
            return {"mode": "error",
                    "message": f"Edge '{edge}' not found on '{support}'."}
        if not edge_straight:
            return {"mode": "error",
                    "message": (f"Edge '{edge}' on '{support}' is not straight; "
                                "a datum line needs a straight edge.")}
        return {"mode": "edge", "support": support, "sub": edge,
                "in_body": in_body}

    # has_axis
    if axis not in ("X", "Y", "Z"):
        return {"mode": "error", "message": "axis must be X, Y, or Z."}
    if not body_present:
        return {"mode": "error",
                "message": "axis mode needs body_name (origin axes belong to a "
                           "Body)."}
    return {"mode": "origin", "axis": axis}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_datum_line.py -q`
Expected: PASS (17 passed).

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/tools/freecad_tools.py tests/unit/test_datum_line.py
git commit -m "feat(create_datum_line): pure mode resolver _resolve_datum_line_def"
```

---

## Task 2: Edge inspector, handler, ToolDefinition, registration

**Files:**
- Modify: `freecad_ai/tools/freecad_tools.py` (add `_inspect_edge` near `_inspect_face` ~line 409; add `_handle_create_datum_line` + `CREATE_DATUM_LINE` after `CREATE_DATUM_PLANE` ~line 989; register in `ALL_TOOLS` after `CREATE_DATUM_PLANE,` ~line 5295)
- Test: `tests/unit/test_datum_line.py` (append the definition tests)

- [ ] **Step 1: Write the failing definition unit tests**

Append to `tests/unit/test_datum_line.py`:

```python
from freecad_ai.tools.freecad_tools import CREATE_DATUM_LINE, ALL_TOOLS


class TestCreateDatumLineDefinition:
    def test_name_and_category(self):
        assert CREATE_DATUM_LINE.name == "create_datum_line"
        assert CREATE_DATUM_LINE.category == "modeling"

    def test_registered_in_all_tools(self):
        assert CREATE_DATUM_LINE in ALL_TOOLS

    def test_array_params_declare_items(self):
        # GitHub Models rejects array params declared without `items` (issue #10).
        for p in CREATE_DATUM_LINE.parameters:
            if getattr(p, "type", None) == "array":
                assert getattr(p, "items", None) is not None, p.name

    def test_point_params_are_number_arrays(self):
        params = {p.name: p for p in CREATE_DATUM_LINE.parameters}
        for name in ("point1", "point2"):
            assert params[name].type == "array"
            assert params[name].items == {"type": "number"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_datum_line.py::TestCreateDatumLineDefinition -q`
Expected: FAIL — `ImportError: cannot import name 'CREATE_DATUM_LINE'`.

- [ ] **Step 3: Add the edge inspector**

In `freecad_ai/tools/freecad_tools.py`, immediately after `_inspect_face` (ends ~line 409), add:

```python
def _inspect_edge(obj, edge_name):
    """Return (exists, straight) for ``edge_name`` on ``obj``'s shape.

    Never raises — a missing edge, unavailable Part module, or non-shape object
    yields (False, False). "straight" means the edge's curve is a ``Part.Line``.
    """
    try:
        import Part
    except Exception:
        return (False, False)
    try:
        edge = obj.Shape.getElement(edge_name)
    except Exception:
        return (False, False)
    if edge is None or edge.ShapeType != "Edge":
        return (False, False)
    try:
        return (True, isinstance(edge.Curve, Part.Line))
    except Exception:
        return (True, False)
```

- [ ] **Step 4: Add the handler and ToolDefinition**

In `freecad_ai/tools/freecad_tools.py`, immediately after the `CREATE_DATUM_PLANE = ToolDefinition(...)` block (ends ~line 989, before `# ── edit_sketch ─`), add:

```python
def _handle_create_datum_line(
    point1: list | None = None,
    point2: list | None = None,
    support: str = "",
    edge: str = "",
    axis: str = "",
    body_name: str = "",
    label: str = "",
) -> ToolResult:
    """Create a datum line (axis) by two points, a straight edge, or an origin
    axis. No offset — use transform_object to place a parallel/duplicate line."""
    import FreeCAD as App

    def do(doc):
        warnings = []

        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(success=False, output="",
                                  error=f"Body '{body_name}' not found.{hint}")

        # Inspect the support/edge so the pure resolver gets plain facts.
        sup = support
        sup_kind = ""
        edge_exists = edge_straight = False
        in_body = None
        sup_obj = None
        if support:
            sup_obj = _get_object(doc, support)
            if sup_obj is None:
                sup_kind = "missing"
            else:
                sup = sup_obj.Name
                sup_kind = _classify_support(sup_obj)
                in_body = _owning_body_name(sup_obj, doc.Objects)
                if edge:
                    edge_exists, edge_straight = _inspect_edge(sup_obj, edge)

        spec = _resolve_datum_line_def(
            point1, point2, sup, edge, axis, body is not None,
            sup_kind, edge_exists, edge_straight, in_body)
        if spec["mode"] == "error":
            hint = _suggest_similar(doc, support) if sup_kind == "missing" else ""
            return ToolResult(success=False, output="", error=spec["message"] + hint)

        # Choose the container the datum line is created in.
        if spec["mode"] == "edge":
            if body_name:
                warnings.append("body_name ignored — datum line placed relative "
                                "to support.")
            if spec.get("in_body"):
                container = _get_object(doc, spec["in_body"])
            elif sup_obj is not None and getattr(sup_obj, "TypeId", "") == "PartDesign::Body":
                container = sup_obj
            else:
                container = None
        else:  # origin or points — honor body_name (origin requires it)
            container = body

        # PartDesign::Line works in-Body (newObject) and standalone (addObject).
        if container is not None:
            line = container.newObject("PartDesign::Line", label or "DatumLine")
        else:
            line = doc.addObject("PartDesign::Line", label or "DatumLine")

        if spec["mode"] == "edge":
            line.AttachmentSupport = [(sup_obj, spec["sub"])]
            line.MapMode = "OneEdge"
        elif spec["mode"] == "origin":
            axis_feat = _get_body_axis(container, spec["axis"])
            if axis_feat:
                line.AttachmentSupport = [(axis_feat, "")]
                line.MapMode = "OneEdge"
            else:
                warnings.append(
                    f"could not resolve the {spec['axis']} origin axis — datum "
                    "line left at the document origin.")
        else:  # points — place through p1 directed toward p2
            p1 = App.Vector(*spec["p1"])
            p2 = App.Vector(*spec["p2"])
            direction = p2.sub(p1)
            # PartDesign::Line lies along its local Z; FlatFace/OneEdge use the
            # placement's Z. Orient local Z onto the p1→p2 direction.
            line.Placement = App.Placement(
                p1, App.Rotation(App.Vector(0, 0, 1), direction))
            if hasattr(line, "Length"):
                line.Length = direction.Length

        doc.recompute()

        # Attachment modes: FreeCAD marks State Invalid/Error if it can't resolve.
        if spec["mode"] in ("edge", "origin"):
            state = list(getattr(line, "State", []) or [])
            if any(s in ("Invalid", "Error") for s in state):
                ref = spec.get("support") or ("origin axis " + spec.get("axis", ""))
                return ToolResult(
                    success=False, output="",
                    error=(f"Failed to attach datum line to '{ref}'"
                           + (f":{spec['sub']}" if spec.get("sub") else "")
                           + " — attachment did not resolve."))

        return ToolResult(
            success=True,
            output=(("⚠ " + " ".join(warnings) + "\n" if warnings else "")
                    + f"Created datum line '{line.Name}' ({line.TypeId})."
                    + " Use it as a revolve_sketch axis or a mirror reference."),
            data={"name": line.Name, "label": line.Label, "type_id": line.TypeId},
        )

    return _with_undo("Create Datum Line", do)


CREATE_DATUM_LINE = ToolDefinition(
    name="create_datum_line",
    description=(
        "Create a datum line (axis) — useful as a rotation/revolve axis or a "
        "mirror reference. Three mutually exclusive modes: two points "
        "(point1=[x,y,z], point2=[x,y,z]) for an axis where no geometry exists; "
        "a straight edge of an object (support='Obj', edge='Edge3' — names from "
        "list_edges); or an origin axis (axis=X/Y/Z, needs body_name). The result "
        "is a PartDesign::Line (inside a Body, or standalone when the edge is not "
        "in a Body). No offset — use transform_object to place a parallel or "
        "duplicate line."
    ),
    category="modeling",
    parameters=[
        ToolParam("point1", "array", "First 3-D point [x, y, z] (two-points "
                  "mode).", required=False, default=None,
                  items={"type": "number"}),
        ToolParam("point2", "array", "Second 3-D point [x, y, z] (two-points "
                  "mode).", required=False, default=None,
                  items={"type": "number"}),
        ToolParam("support", "string", "Object whose edge to attach to (edge "
                  "mode).", required=False, default=""),
        ToolParam("edge", "string", "Straight sub-element of `support`, e.g. "
                  "'Edge3' (from list_edges). Requires `support`.",
                  required=False, default=""),
        ToolParam("axis", "string", "Origin axis to reference: X, Y, or Z "
                  "(needs body_name).", required=False, default="",
                  enum=["X", "Y", "Z"]),
        ToolParam("body_name", "string", "Body to create the line in (required "
                  "for axis mode; optional for two-points; ignored for edge "
                  "mode).", required=False, default=""),
        ToolParam("label", "string", "Display label for the datum line.",
                  required=False, default=""),
    ],
    handler=_handle_create_datum_line,
)
```

- [ ] **Step 5: Register in ALL_TOOLS**

In the `ALL_TOOLS` list, find the line `    CREATE_DATUM_PLANE,` (~line 5295) and add directly below it:

```python
    CREATE_DATUM_LINE,
```

- [ ] **Step 6: Run the definition tests to verify they pass**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_datum_line.py -q`
Expected: PASS (21 passed — 17 resolver + 4 definition).

- [ ] **Step 7: Run the full unit suite (no regressions)**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/ -q --deselect tests/unit/test_document_attach.py`
Expected: all pass (`test_document_attach.py` is deselected — it segfaults on a Qt issue unrelated to this change, on clean master too).

- [ ] **Step 8: Commit**

```bash
git add freecad_ai/tools/freecad_tools.py tests/unit/test_datum_line.py
git commit -m "feat(create_datum_line): handler, ToolDefinition, edge inspector, registration"
```

---

## Task 3: Integration tests (real FreeCAD — pins the type and placement)

This task pins the two empirically-uncertain assumptions: that `PartDesign::Line` is a valid type both in-Body and standalone, and that the two-point placement orients correctly. **If an assumption is wrong, fix the handler here** (the `Part::DatumPlane`-was-invalid surprise from piece #2 is why this task exists).

**Files:**
- Test: `tests/integration/test_datum_line_integration.py` (create)
- Possibly modify: `freecad_ai/tools/freecad_tools.py` (only if integration reveals a wrong type/placement assumption)

- [ ] **Step 1: Write the integration tests**

Create `tests/integration/test_datum_line_integration.py`:

```python
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

r = _handle_create_datum_line(point1=[1, 2, 3], point2=[1, 2, 13])
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
        # p1→p2 is +Z; the placed line's local Z must align with it.
        assert abs(d["zdir"][0]) < 1e-6
        assert abs(d["zdir"][1]) < 1e-6
        assert abs(d["zdir"][2] - 1.0) < 1e-6

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
        assert d["map_mode"] == "OneEdge"
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
results["data"] = {
    "success": r.success,
    "error": r.error,
    "type_id": ln.TypeId if ln else None,
    "edge": straight,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["edge"] is not None
        assert d["success"], d["error"]
        assert d["type_id"] == "PartDesign::Line"

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
        assert d["map_mode"] == "OneEdge"
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
sk.addGeometry(Part.LineSegment(App.Vector(5, 0, 0), App.Vector(10, 0, 0)), False)
sk.addGeometry(Part.LineSegment(App.Vector(10, 0, 0), App.Vector(10, 5, 0)), False)
sk.addGeometry(Part.LineSegment(App.Vector(10, 5, 0), App.Vector(5, 5, 0)), False)
sk.addGeometry(Part.LineSegment(App.Vector(5, 5, 0), App.Vector(5, 0, 0)), False)
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
        assert d["rev_solids"] >= 1
```

- [ ] **Step 2: Run the integration tests**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/integration/test_datum_line_integration.py -m integration -q`
Expected: PASS.

**If `PartDesign::Line` is not a valid type** (analogous to the `Part::DatumPlane` surprise): determine the correct datum-line type empirically — write a throwaway probe script run via `run_freecad_script` that tries `doc.addObject("<candidate>", "L")` and `body.newObject("<candidate>", "L")` for candidates (`PartDesign::Line`, `Part::DatumLine`, `Part::Line`), recomputes, and reports which attach + recompute cleanly. Update the handler's two `..."PartDesign::Line"...` calls and the test assertions (`type_id == ...`) to the verified type, then re-run.

**If the two-point direction assertion fails** (`test_two_points_direction`): the datum line's local reference direction is not local Z. Probe which local axis the placed line follows (try aligning local X via `App.Rotation(App.Vector(1,0,0), direction)`), update the `points` branch in the handler, and re-run.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_datum_line_integration.py freecad_ai/tools/freecad_tools.py
git commit -m "test(create_datum_line): integration tests pin PartDesign::Line type + two-point placement"
```

(If the handler needed no fix, only the test file is staged — that is fine.)

---

## Task 4: Docs — wiki Tool-Reference entry

The wiki is a **separate repo** at `../freecad-ai-wiki`. The maintainer pushes it manually — **commit locally, do NOT push.**

**Files:**
- Modify: `../freecad-ai-wiki/Tool-Reference.md` (add a `create_datum_line` entry next to `create_datum_plane`)

- [ ] **Step 1: Locate the create_datum_plane entry**

Run: `grep -n "create_datum_plane" ../freecad-ai-wiki/Tool-Reference.md`
Read the surrounding entry to match the exact heading level, table/param format, and Notes style used for `create_datum_plane`.

- [ ] **Step 2: Add the create_datum_line entry**

Directly after the `create_datum_plane` section, add a `create_datum_line` entry in the **same format** as its neighbor. It must cover:
- One-line purpose: a datum line (axis) for revolve/rotation or mirror reference.
- The three modes: two points (`point1`/`point2`); straight edge (`support`+`edge`); origin axis (`axis` X/Y/Z + `body_name`).
- A parameter table with the seven params (`point1`, `point2`, `support`, `edge`, `axis`, `body_name`, `label`) — match the column layout of the `create_datum_plane` table.
- A Notes line: no offset (use `transform_object` for a parallel/duplicate line); the edge must be straight; the result is a `PartDesign::Line` usable as `revolve_sketch`'s axis.

- [ ] **Step 3: Commit locally (do NOT push)**

```bash
cd ../freecad-ai-wiki
git add Tool-Reference.md
git commit -m "docs(tool-reference): add create_datum_line entry"
cd -
```

Report the wiki commit SHA and that it is **not pushed**.

---

## Self-Review (completed during planning)

- **Spec coverage:** two-points / edge / origin-axis modes (Tasks 1–3); container rules incl. two-points+body_name (Task 2 handler); `PartDesign::Line` + placement pinned (Task 3); no-offset documented (Tasks 2, 4); validation incl. curved-edge, coincident points, multi-mode (Tasks 1, 3); revolve loop (Task 3); wiki entry (Task 4). All covered.
- **Placeholder scan:** no TBD/TODO; all code shown in full; the one conditional ("if type assumption wrong") gives an explicit empirical procedure, not a vague instruction.
- **Type consistency:** resolver signature `_resolve_datum_line_def(point1, point2, support, edge, axis, body_present, support_kind, edge_exists, edge_straight, in_body)` is identical in Task 1 implementation, the unit-test `_resolve` wrapper, and the Task 2 handler call. `_inspect_edge(obj, edge_name) -> (exists, straight)` consistent between definition and call site. ToolParam keyword order matches `registry.py`. `data` keys (`name`/`label`/`type_id`) match the integration assertions.
