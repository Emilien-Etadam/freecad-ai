# create_datum_plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `create_datum_plane` tool that creates a parametric datum plane offset from an origin plane, a planar face, an existing plane, or the GUI selection — referenceable by `create_sketch`'s `support`.

**Architecture:** Reuse piece #1's resolution layer (now on `master`): a thin pure wrapper `_resolve_datum_plane_attachment` delegates to `_resolve_sketch_attachment` and remaps `standalone`→error. The handler mirrors `_handle_create_sketch` — gather facts (params or selection), resolve, pick the container Body, create a `PartDesign::Plane` (in a Body) or `Part::DatumPlane` (standalone), attach via `FlatFace` + `AttachmentOffset` along the normal, recompute, verify.

**Tech Stack:** Python 3.11, FreeCAD 1.1.1 Python API, pytest. Unit tests run without FreeCAD (`env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/ -q`); integration tests run inside FreeCAD via the `run_freecad_script` fixture (`-m integration`).

**Branch:** `feature/datum-plane` (already created off `master`; spec committed at `1df3616`).

---

## File structure

- **Modify** `freecad_ai/tools/freecad_tools.py`
  - Add pure `_resolve_datum_plane_attachment(...)` immediately after `_resolve_sketch_attachment` (it ends just before the `_PLANE_TYPE_IDS = (...)` line, ~line 332).
  - Add handler `_handle_create_datum_plane(...)` and `CREATE_DATUM_PLANE` `ToolDefinition` right before the `# ── edit_sketch ─` section comment.
  - Register `CREATE_DATUM_PLANE` in `ALL_TOOLS` (after `CREATE_SKETCH,` at ~line 5106).
- **Test (unit)** `tests/unit/test_datum_plane.py` — wrapper resolver tests (no FreeCAD).
- **Test (integration)** `tests/integration/test_datum_plane_integration.py` — real-FreeCAD tests.
- **Docs** wiki `../freecad-ai-wiki/Tool-Reference.md` — add a `create_datum_plane` entry.

Reused as-is (already on master): `_resolve_sketch_attachment`, `_classify_support`, `_owning_body_name`, `_inspect_face`, `_read_planar_selection`, `_get_body_plane`, `_get_object`, `_suggest_similar`, `_with_undo`, `_PLANE_TYPE_IDS`.

---

## Task 1: Pure resolver wrapper

**Files:**
- Modify: `freecad_ai/tools/freecad_tools.py` (add after `_resolve_sketch_attachment`)
- Test: `tests/unit/test_datum_plane.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_datum_plane.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_datum_plane.py -q`
Expected: FAIL — `ImportError: cannot import name '_resolve_datum_plane_attachment'`.

- [ ] **Step 3: Implement the wrapper**

In `freecad_ai/tools/freecad_tools.py`, immediately AFTER the `_resolve_sketch_attachment` function returns (i.e. just before the `_PLANE_TYPE_IDS = (...)` line, ~line 332), add:

```python
def _resolve_datum_plane_attachment(support, face, plane, body_present,
                                    support_kind, face_exists, face_planar, in_body):
    """Reference resolution for create_datum_plane. Pure — no FreeCAD calls.

    Delegates to ``_resolve_sketch_attachment`` and remaps the one datum-specific
    decision: a datum plane cannot be free-floating, so a ``standalone`` result
    (no usable reference) becomes an error. All other modes pass through.
    """
    spec = _resolve_sketch_attachment(
        support, face, plane, body_present,
        support_kind, face_exists, face_planar, in_body)
    if spec["mode"] == "standalone":
        return {"mode": "error",
                "message": ("create_datum_plane needs a reference: pass a plane "
                            "(XY/XZ/YZ) with body_name, or a support object "
                            "(optionally with a face).")}
    return spec
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_datum_plane.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/tools/freecad_tools.py tests/unit/test_datum_plane.py
git commit -m "feat(create_datum_plane): pure reference resolver wrapper"
```

---

## Task 2: Handler + ToolDefinition + registration

**Files:**
- Modify: `freecad_ai/tools/freecad_tools.py` (add handler + ToolDefinition before `# ── edit_sketch ─`; register in `ALL_TOOLS`)

- [ ] **Step 1: Add the handler and ToolDefinition**

Find the `# ── edit_sketch ─` section comment (it follows the `CREATE_SKETCH = ToolDefinition(...)` block). Immediately BEFORE that comment line, insert:

```python
# ── create_datum_plane ──────────────────────────────────────

def _handle_create_datum_plane(
    plane: str = "XY",
    support: str = "",
    face: str = "",
    offset: float = 0.0,
    body_name: str = "",
    label: str = "",
) -> ToolResult:
    """Create a parametric datum plane offset from a reference."""
    import FreeCAD as App

    def do(doc):
        warnings = []

        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")

        # Bare-call selection fallback — same gate as create_sketch: only when
        # no explicit reference and the default plane were given.
        sup, fc = support, face
        if (not support and not face and not body_name
                and plane.upper() == "XY"):
            picked = _read_planar_selection()
            if picked:
                sup, fc = picked

        sup_kind = ""
        face_exists = face_planar = False
        in_body = None
        sup_obj = None
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

        spec = _resolve_datum_plane_attachment(
            sup, fc, plane, body is not None,
            sup_kind, face_exists, face_planar, in_body,
        )
        if spec["mode"] == "error":
            hint = _suggest_similar(doc, support) if sup_kind == "missing" else ""
            return ToolResult(success=False, output="", error=spec["message"] + hint)

        # Choose the container the datum plane is created in (same logic as
        # create_sketch).
        if spec["mode"] in ("face", "plane"):
            if body_name:
                warnings.append("body_name ignored — datum plane placed relative to support.")
            if spec.get("in_body"):
                container = _get_object(doc, spec["in_body"])
            elif sup_obj is not None and getattr(sup_obj, "TypeId", "") == "PartDesign::Body":
                container = sup_obj
            else:
                container = None
        else:  # origin
            container = body

        # PartDesign::Plane inside a Body, else a standalone Part::DatumPlane.
        if container is not None:
            datum = container.newObject("PartDesign::Plane", label or "DatumPlane")
        else:
            datum = doc.addObject("Part::DatumPlane", label or "DatumPlane")

        # Attach to the reference. All modes use FlatFace, so the datum's local
        # Z is the reference normal and the offset is always (0, 0, offset).
        if spec["mode"] == "face":
            datum.AttachmentSupport = [(sup_obj, spec["sub"])]
            datum.MapMode = "FlatFace"
        elif spec["mode"] == "plane":
            datum.AttachmentSupport = [(sup_obj, "")]
            datum.MapMode = "FlatFace"
        elif spec["mode"] == "origin":
            plane_feat = _get_body_plane(container, spec["plane"])
            if plane_feat:
                datum.AttachmentSupport = [(plane_feat, "")]
                datum.MapMode = "FlatFace"

        if offset != 0:
            datum.AttachmentOffset = App.Placement(
                App.Vector(0, 0, offset), App.Rotation())

        doc.recompute()

        # If the attachment can't resolve, FreeCAD marks the feature's State as
        # Invalid/Error. (The executor sandbox is the higher-level net.)
        state = list(getattr(datum, "State", []) or [])
        if any(s in ("Invalid", "Error") for s in state):
            ref = spec.get("support") or spec.get("plane") or "reference"
            return ToolResult(
                success=False, output="",
                error=(f"Failed to attach datum plane to '{ref}'"
                       + (f":{spec['sub']}" if spec.get("sub") else "")
                       + " — attachment did not resolve."))

        return ToolResult(
            success=True,
            output=(("⚠ " + " ".join(warnings) + "\n" if warnings else "")
                    + f"Created datum plane '{datum.Name}' ({datum.TypeId})."
                    + f" Use support='{datum.Name}' in create_sketch."),
            data={"name": datum.Name, "label": datum.Label,
                  "type_id": datum.TypeId},
        )

    return _with_undo("Create Datum Plane", do)


CREATE_DATUM_PLANE = ToolDefinition(
    name="create_datum_plane",
    description=(
        "Create a parametric datum plane offset (parallel) from a reference — "
        "useful as a mid-plane, an offset sketch plane, or a mirror reference. "
        "Reference options: an origin plane (plane=XY/XZ/YZ, needs body_name); a "
        "planar face of an object (support='Obj', face='Face6' — names from "
        "list_faces); or an existing plane by name (support='PlaneName'). With no "
        "reference, the current planar-face/plane selection is used. The result "
        "is a PartDesign::Plane inside a Body, or a standalone Part::DatumPlane, "
        "and can be passed to create_sketch as support='<name>'."
    ),
    category="modeling",
    parameters=[
        ToolParam("plane", "string", "Origin-plane reference: XY, XZ, or YZ "
                  "(used when no support; needs body_name).",
                  required=False, default="XY", enum=["XY", "XZ", "YZ"]),
        ToolParam("support", "string", "Object to offset from: a solid (with "
                  "`face`) or an existing plane object by name.",
                  required=False, default=""),
        ToolParam("face", "string", "Planar sub-element of `support`, e.g. "
                  "'Face6' (from list_faces). Requires `support`.",
                  required=False, default=""),
        ToolParam("offset", "number", "Parallel offset along the reference "
                  "normal in mm (may be negative).",
                  required=False, default=0.0),
        ToolParam("body_name", "string", "Body to create the PartDesign::Plane "
                  "in (required for an origin-plane reference).",
                  required=False, default=""),
        ToolParam("label", "string", "Display label for the datum plane.",
                  required=False, default=""),
    ],
    handler=_handle_create_datum_plane,
)


```

- [ ] **Step 2: Register in ALL_TOOLS**

Find the `ALL_TOOLS = [` list (~line 5103). After the `    CREATE_SKETCH,` line, add:

```python
    CREATE_DATUM_PLANE,
```

- [ ] **Step 3: Verify the module loads and the tool is registered**

Run:
```bash
env -u PYTHONPATH .venv/bin/python -c "from freecad_ai.tools.freecad_tools import CREATE_DATUM_PLANE, ALL_TOOLS; print(CREATE_DATUM_PLANE.name); print('registered:', CREATE_DATUM_PLANE in ALL_TOOLS); print([p.name for p in CREATE_DATUM_PLANE.parameters])"
```
Expected: prints `create_datum_plane`; `registered: True`; param list `['plane', 'support', 'face', 'offset', 'body_name', 'label']`.

- [ ] **Step 4: Run the unit + registry suites to confirm no regression**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_datum_plane.py tests/unit/test_sketch_attachment.py tests/unit/test_new_tools.py tests/unit/test_registry.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/tools/freecad_tools.py
git commit -m "feat(create_datum_plane): handler, tool definition, registration"
```

---

## Task 3: Integration tests (real FreeCAD)

**Files:**
- Create: `tests/integration/test_datum_plane_integration.py`

- [ ] **Step 1: Write the integration tests**

Create `tests/integration/test_datum_plane_integration.py`:

```python
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

# A planar face of the box feature (reference an earlier feature, not the tip).
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
        assert d["type_id"] == "Part::DatumPlane"

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
```

- [ ] **Step 2: Run the integration tests**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/integration/test_datum_plane_integration.py -m integration -v`
Expected: 6 passed (each spawns a FreeCAD subprocess; allow 1-3 min total).

If a test fails, read `result["data"]["error"]`. Do NOT change the production code to make a test pass unless the test itself is demonstrably wrong; if a real production bug surfaces, STOP and report it (DONE_WITH_CONCERNS / BLOCKED) with the exact error.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_datum_plane_integration.py
git commit -m "test(create_datum_plane): integration tests"
```

---

## Task 4: Docs — verify description and update wiki

**Files:**
- Modify: `../freecad-ai-wiki/Tool-Reference.md`

- [ ] **Step 1: Confirm the in-code description renders**

Run: `env -u PYTHONPATH .venv/bin/python -c "from freecad_ai.tools.freecad_tools import CREATE_DATUM_PLANE; print(CREATE_DATUM_PLANE.description)"`
Expected: prints the description mentioning origin plane / face / existing plane references and that the result is usable as `create_sketch` `support`.

- [ ] **Step 2: Add a wiki entry**

Open `../freecad-ai-wiki/Tool-Reference.md`. Find how the `create_sketch` entry is formatted (parameter table + notes). Add a new `create_datum_plane` section in the SAME style, near `create_sketch`, documenting:
- One-line purpose: "Create a parametric datum plane offset (parallel) from an origin plane, a planar face, or an existing plane."
- Parameter table rows for `plane`, `support`, `face`, `offset`, `body_name`, `label` (descriptions matching the ToolParam text from Task 2).
- A note: "Produces a `PartDesign::Plane` inside a Body, or a standalone `Part::DatumPlane`. Pass the result name to `create_sketch(support=...)` to sketch on it. Origin-plane references need `body_name`. Offset is along the reference normal (parallel planes only — no tilt)."

Match the file's existing heading level and table format; do not impose a new style.

- [ ] **Step 3: Commit the wiki (in the wiki repo only; do NOT push)**

```bash
cd ../freecad-ai-wiki
git add Tool-Reference.md
git commit -m "docs(tool-reference): create_datum_plane"
cd -
```

---

## Self-review notes

- **Spec coverage:** reference resolution + standalone→error → Task 1 (`_resolve_datum_plane_attachment`). Params/behavior/container/object-type/attachment/offset/verification → Task 2 handler. Registration → Task 2 Step 2. Origin/face/standalone/end-to-end/no-reference/non-planar cases → Task 3. Docs → Task 4. Selection fallback → Task 2 (same gate as create_sketch). Backward-compat (purely additive) → no existing tool touched; verified by running the existing suites in Task 2 Step 4.
- **Type/name consistency:** `_resolve_datum_plane_attachment` returns the same spec-dict shape as `_resolve_sketch_attachment` (keys `mode`/`support`/`sub`/`in_body`/`plane`/`message`), consumed identically in the handler. Tool name `create_datum_plane`, constant `CREATE_DATUM_PLANE`, handler `_handle_create_datum_plane` used consistently across tasks.
- **No placeholders:** every code step shows complete code.
- **Offset simplification vs create_sketch:** create_sketch keeps a legacy per-origin-plane `offset_map`; this new tool uses a single `(0,0,offset)` along the FlatFace local-Z for all modes (equivalent for FlatFace attachment, simpler). Intentional, noted here.
