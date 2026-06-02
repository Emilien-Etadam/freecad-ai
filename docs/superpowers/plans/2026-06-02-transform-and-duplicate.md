# transform_object relative mode + duplicate_object Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `transform_object` a relative-by-default move/rotate mode (fixing the placement-reset footgun) and add a `duplicate_object` tool that makes an independent, history-preserving parametric copy and optionally offsets it.

**Architecture:** Both handlers share one FreeCAD-using helper `_apply_relative_placement(...)` (global translate + rotate-in-place). `transform_object` gains a `relative` bool (default True); `relative=False` keeps today's absolute overwrite. `duplicate_object` uses `doc.copyObject(obj, with_dependencies=True)` then applies the same relative offset to the copy. A tiny pure `_duplicate_label(...)` is the only unit-testable logic bit; placement semantics and `copyObject`'s return shape are pinned by integration tests.

**Tech Stack:** Python 3.11, FreeCAD 1.1.1 (PySide6), pytest. Unit: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/ -q`. Integration (FreeCAD AppImage, `run_freecad_script` fixture): add `-m integration`.

**Spec:** `docs/superpowers/specs/2026-06-02-transform-and-duplicate-design.md`

**Branch:** `feature/transform-object` (already created off `master`; spec already committed on it). Note: `create_datum_line` (PR #22) is NOT on this branch, so the end-to-end test builds its datum line directly in the test script.

---

## Conventions to follow (read before starting)

- `ToolParam(name, type, description, required=..., default=..., enum=..., items=...)`.
- `_get_object(doc, name)` resolves Name then Label; `_suggest_similar(doc, name)` → "did you mean" hint string; `_with_undo(label, do)` wraps the mutation. All exist in `freecad_ai/tools/freecad_tools.py`.
- Handlers `import FreeCAD as App` INSIDE the function (the unit suite imports the module without FreeCAD).
- Integration tests: assert `result["ok"]` first, then read `result["data"]`; each test's script sets `results["data"] = {...}`; a fresh `doc` per test.
- Pyright "Import FreeCAD/Part could not be resolved" warnings are EXPECTED (runtime-only imports), not defects.

---

## Task 1: transform_object relative mode + shared placement helper

**Files:**
- Modify: `freecad_ai/tools/freecad_tools.py` — add `_apply_relative_placement` (near `_handle_transform_object`, ~line 1805); rewrite `_handle_transform_object` (`:1806`); add the `relative` param + new description to `TRANSFORM_OBJECT` (`:1847`).
- Test: `tests/unit/test_transform_duplicate.py` (create)

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/test_transform_duplicate.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_transform_duplicate.py -q`
Expected: FAIL — `assert "relative" in params` fails (param not yet added).

- [ ] **Step 3: Add the shared placement helper**

In `freecad_ai/tools/freecad_tools.py`, immediately BEFORE `def _handle_transform_object` (~line 1805), add:

```python
def _apply_relative_placement(old, tx, ty, tz, ax, ay, az, angle):
    """Compose a relative move/rotate onto an existing placement.

    Translate is global (added to the base); rotation is applied in place about
    the object's own origin (pre-multiplied onto the current rotation, base kept).
    Uses FreeCAD's placement math — intentionally NOT a pure/standalone helper
    (reimplementing quaternion composition would be more error-prone). Pinned by
    integration tests.
    """
    import FreeCAD as App
    new_base = old.Base + App.Vector(tx, ty, tz)
    delta_rot = App.Rotation(App.Vector(ax, ay, az), angle)
    new_rot = delta_rot.multiply(old.Rotation)
    return App.Placement(new_base, new_rot)
```

- [ ] **Step 4: Rewrite the handler**

Replace the body of `_handle_transform_object` (`:1806`–`:1844`) with:

```python
def _handle_transform_object(
    object_name: str,
    translate_x: float = 0.0,
    translate_y: float = 0.0,
    translate_z: float = 0.0,
    rotate_axis_x: float = 0.0,
    rotate_axis_y: float = 0.0,
    rotate_axis_z: float = 1.0,
    rotate_angle: float = 0.0,
    relative: bool = True,
) -> ToolResult:
    """Move and/or rotate an object, relative to its current placement by default."""
    import FreeCAD as App

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            hint = _suggest_similar(doc, object_name)
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found.{hint}")

        if relative:
            obj.Placement = _apply_relative_placement(
                obj.Placement, translate_x, translate_y, translate_z,
                rotate_axis_x, rotate_axis_y, rotate_axis_z, rotate_angle)
        else:
            obj.Placement = App.Placement(
                App.Vector(translate_x, translate_y, translate_z),
                App.Rotation(App.Vector(rotate_axis_x, rotate_axis_y, rotate_axis_z), rotate_angle))

        parts = []
        if translate_x or translate_y or translate_z:
            parts.append(f"translated ({translate_x}, {translate_y}, {translate_z})")
        if rotate_angle:
            parts.append(f"rotated {rotate_angle}°")
        mode = "relative" if relative else "absolute"
        if parts:
            desc = ", ".join(parts) + f" ({mode})"
        elif relative:
            desc = "unchanged (relative, no delta given)"
        else:
            desc = "placement reset to origin (absolute)"

        return ToolResult(
            success=True,
            output=f"Transformed '{obj.Label}': {desc}",
            data={"name": obj.Name},
        )

    return _with_undo("Transform Object", do)
```

- [ ] **Step 5: Update the ToolDefinition**

Replace the `TRANSFORM_OBJECT = ToolDefinition(...)` block (`:1847`–`:1862`) with:

```python
TRANSFORM_OBJECT = ToolDefinition(
    name="transform_object",
    description=(
        "Move and/or rotate an object. By default (relative=True) the change is "
        "applied RELATIVE to the object's current placement — translation adds to "
        "its position, rotation spins it in place, and 0 means no change. Set "
        "relative=False for an ABSOLUTE placement (overwrites position and "
        "orientation; omitted values reset to 0). Does not copy — use "
        "duplicate_object to make a copy."
    ),
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object to transform"),
        ToolParam("translate_x", "number", "X translation in mm", required=False, default=0.0),
        ToolParam("translate_y", "number", "Y translation in mm", required=False, default=0.0),
        ToolParam("translate_z", "number", "Z translation in mm", required=False, default=0.0),
        ToolParam("rotate_axis_x", "number", "Rotation axis X component", required=False, default=0.0),
        ToolParam("rotate_axis_y", "number", "Rotation axis Y component", required=False, default=0.0),
        ToolParam("rotate_axis_z", "number", "Rotation axis Z component", required=False, default=1.0),
        ToolParam("rotate_angle", "number", "Rotation angle in degrees", required=False, default=0.0),
        ToolParam("relative", "boolean", "Apply relative to the current placement "
                  "(default). False = absolute overwrite.", required=False, default=True),
    ],
    handler=_handle_transform_object,
)
```

- [ ] **Step 6: Run to verify it passes**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_transform_duplicate.py -q`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add freecad_ai/tools/freecad_tools.py tests/unit/test_transform_duplicate.py
git commit -m "feat(transform_object): relative-by-default move/rotate + shared placement helper"
```

---

## Task 2: duplicate_object tool

**Files:**
- Modify: `freecad_ai/tools/freecad_tools.py` — add `_duplicate_label`, `_handle_duplicate_object`, `DUPLICATE_OBJECT` (right after the `TRANSFORM_OBJECT` block); register `DUPLICATE_OBJECT` in `ALL_TOOLS` (after `    TRANSFORM_OBJECT,` ~line 5303).
- Test: `tests/unit/test_transform_duplicate.py` (append)

- [ ] **Step 1: Write the failing unit tests**

Append to `tests/unit/test_transform_duplicate.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_transform_duplicate.py -q`
Expected: FAIL — `ImportError: cannot import name 'DUPLICATE_OBJECT'`.

- [ ] **Step 3: Add the label helper, handler, and ToolDefinition**

In `freecad_ai/tools/freecad_tools.py`, immediately AFTER the `TRANSFORM_OBJECT = ToolDefinition(...)` block (ends ~line 1862, before `# ── fillet_edges ─`), add:

```python
def _duplicate_label(base_label, requested):
    """Label for a duplicated object: the requested label, or '<base>_Copy'."""
    return requested or f"{base_label}_Copy"


def _handle_duplicate_object(
    object_name: str,
    translate_x: float = 0.0,
    translate_y: float = 0.0,
    translate_z: float = 0.0,
    rotate_axis_x: float = 0.0,
    rotate_axis_y: float = 0.0,
    rotate_axis_z: float = 1.0,
    rotate_angle: float = 0.0,
    label: str = "",
) -> ToolResult:
    """Duplicate an object (independent parametric copy), optionally offsetting it."""

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            hint = _suggest_similar(doc, object_name)
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found.{hint}")

        result = doc.copyObject(obj, True)
        # copyObject returns the copy of the passed object; guard for a list/None
        # return across FreeCAD versions (the exact shape is pinned in integration).
        copy = result[-1] if isinstance(result, (list, tuple)) else result
        if copy is None:
            return ToolResult(success=False, output="", error=f"Failed to duplicate '{obj.Label}'.")

        copy.Label = _duplicate_label(obj.Label, label)

        if translate_x or translate_y or translate_z or rotate_angle:
            copy.Placement = _apply_relative_placement(
                copy.Placement, translate_x, translate_y, translate_z,
                rotate_axis_x, rotate_axis_y, rotate_axis_z, rotate_angle)

        doc.recompute()

        state = list(getattr(copy, "State", []) or [])
        if any(s in ("Invalid", "Error") for s in state):
            return ToolResult(success=False, output="",
                              error=f"Duplicate '{copy.Label}' did not recompute cleanly.")

        return ToolResult(
            success=True,
            output=(f"Duplicated '{obj.Label}' → '{copy.Label}' ({copy.TypeId}); "
                    "original unchanged."),
            data={"name": copy.Name, "label": copy.Label},
        )

    return _with_undo("Duplicate Object", do)


DUPLICATE_OBJECT = ToolDefinition(
    name="duplicate_object",
    description=(
        "Duplicate an object as an independent, editable copy that preserves its "
        "parametric history (the whole feature tree — e.g. a Body with its sketches "
        "and pads). The original is left unchanged. Optional translate/rotate offset "
        "the copy relative to the original (0 = on top of it). To duplicate a solid, "
        "pass its Body. Note: if the object's placement is driven by an attachment "
        "(e.g. a datum attached to an edge), the offset won't stick — duplicate a "
        "fixed-placement object (such as a two-point datum line) for a parallel copy."
    ),
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object to duplicate"),
        ToolParam("translate_x", "number", "X offset of the copy in mm", required=False, default=0.0),
        ToolParam("translate_y", "number", "Y offset of the copy in mm", required=False, default=0.0),
        ToolParam("translate_z", "number", "Z offset of the copy in mm", required=False, default=0.0),
        ToolParam("rotate_axis_x", "number", "Rotation axis X component", required=False, default=0.0),
        ToolParam("rotate_axis_y", "number", "Rotation axis Y component", required=False, default=0.0),
        ToolParam("rotate_axis_z", "number", "Rotation axis Z component", required=False, default=1.0),
        ToolParam("rotate_angle", "number", "Rotation angle for the copy in degrees", required=False, default=0.0),
        ToolParam("label", "string", "Label for the copy (default '<original>_Copy')", required=False, default=""),
    ],
    handler=_handle_duplicate_object,
)
```

- [ ] **Step 4: Register in ALL_TOOLS**

In the `ALL_TOOLS` list, find `    TRANSFORM_OBJECT,` (~line 5303) and add directly below it:

```python
    DUPLICATE_OBJECT,
```

- [ ] **Step 5: Run to verify it passes**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_transform_duplicate.py -q`
Expected: PASS (7 passed — 2 transform + 5 duplicate).

- [ ] **Step 6: Run the full unit suite (no regressions)**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/ -q --deselect tests/unit/test_document_attach.py`
Expected: all pass (`test_document_attach.py` deselected — it segfaults on an unrelated Qt issue, on clean master too).

- [ ] **Step 7: Commit**

```bash
git add freecad_ai/tools/freecad_tools.py tests/unit/test_transform_duplicate.py
git commit -m "feat(duplicate_object): independent parametric copy tool with optional offset"
```

---

## Task 3: Integration tests (real FreeCAD — pins placement semantics + copyObject)

This task pins the empirically-uncertain behavior: the relative composition (rotate-in-place / translate-preserves-rotation), the absolute-mode regression, and **`doc.copyObject`'s return shape** (single object vs list — the guard in Task 2 is provisional). **If `copyObject` returns a list and `result[-1]` is not the copy of `obj`, fix the handler here** (probe what it returns, identify the obj copy, update the guard) — the same discipline that caught the type/MapMode surprises in pieces #2/#3.

**Files:**
- Test: `tests/integration/test_transform_duplicate_integration.py` (create)
- Possibly modify: `freecad_ai/tools/freecad_tools.py` (only if integration reveals a wrong `copyObject` return assumption)

- [ ] **Step 1: Write the integration tests**

Create `tests/integration/test_transform_duplicate_integration.py`:

```python
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
```

- [ ] **Step 2: Run the integration tests**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/integration/test_transform_duplicate_integration.py -m integration -q`
Expected: PASS (7 passed).

**If a duplicate test fails because `doc.copyObject` returns a list (or the copy of `obj` is not the element the guard picks):** write a throwaway probe via `run_freecad_script` that does `r = doc.copyObject(body, True); results["data"] = {"type": type(r).__name__, "is_list": isinstance(r, (list, tuple)), "len": len(r) if isinstance(r,(list,tuple)) else 1}` and inspect which element is the copy of the passed object (compare `.Name`/`.TypeId`). Update the `copy = ...` guard in `_handle_duplicate_object` accordingly (e.g. match by the copied top object rather than `[-1]`), then re-run. Do NOT weaken assertions to dodge a real return-shape bug.

**If `PartDesign::Line` (used only as a test fixture in `test_duplicate_datum_line_parallel_offset`) is unavailable on this branch's FreeCAD:** it was verified valid in piece #3, so this should work; if not, substitute the fixture with any fixed-placement object (e.g. a `Part::Feature` line/edge) — the test's point is the parallel offset of a fixed-placement duplicate, not the specific type.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_transform_duplicate_integration.py freecad_ai/tools/freecad_tools.py
git commit -m "test(transform/duplicate): integration tests pin relative semantics + copyObject"
```

(If the handler needed no fix, only the test file is staged — fine.)

---

## Task 4: Docs — wiki Tool-Reference entries

The wiki is a SEPARATE repo at `../freecad-ai-wiki`. Maintainer pushes it manually — **commit locally, do NOT push.**

**Files:**
- Modify: `../freecad-ai-wiki/Tool-Reference.md`

- [ ] **Step 1: Locate the existing transform_object entry**

Run: `grep -n "transform_object\|scale_object" ../freecad-ai-wiki/Tool-Reference.md`
Read the `transform_object` entry and a neighbor (e.g. `scale_object`) to match the exact heading level, parameter-table format, and Notes style.

- [ ] **Step 2: Update transform_object and add duplicate_object**

1. Update the `transform_object` entry: document the new `relative` param (default True = compose onto current placement, 0 = no change; False = absolute overwrite) and that it no longer resets position when only rotating; note it does not copy (point to `duplicate_object`).
2. Add a `duplicate_object` entry directly after it, in the SAME format, covering: independent parametric copy (whole feature tree, original unchanged); the optional relative translate/rotate offset; `label` default `<original>_Copy`; pass a Body to duplicate a solid; the attachment-driven-placement caveat. Include the seven params (`object_name`, `translate_x/y/z`, `rotate_axis_x/y/z`, `rotate_angle`, `label`) in a table matching the neighbor's layout. If neighbors show example invocations, add an analogous example (e.g. `create_datum_line` → `duplicate_object(translate_x=10)` for a parallel line).

- [ ] **Step 3: Commit locally (do NOT push)**

```bash
cd ../freecad-ai-wiki
git add Tool-Reference.md
git commit -m "docs(tool-reference): transform_object relative mode + duplicate_object entry"
cd -
```

Report the wiki commit SHA, run `git -C ../freecad-ai-wiki status -sb`, and confirm it is **not pushed**.

---

## Self-Review (completed during planning)

- **Spec coverage:** `transform_object` relative mode + footgun fix (Task 1); `duplicate_object` with `copyObject(with_dependencies=True)` + relative offset + default label (Task 2); placement semantics, absolute-mode regression, copyObject return, full-tree copy, offset, datum-line parallel close-the-loop (Task 3); wiki for both (Task 4). No `copy` flag added to transform; no scale_object change. All covered.
- **Placeholder scan:** no TBD/TODO; all code shown in full; the two conditionals (copyObject-returns-a-list, PartDesign::Line-fixture) give explicit empirical procedures, not vague instructions.
- **Type consistency:** `_apply_relative_placement(old, tx, ty, tz, ax, ay, az, angle) -> App.Placement` defined in Task 1, called identically in Task 1's handler and Task 2's `_handle_duplicate_object`. `_duplicate_label(base, requested)` consistent between definition, call site, and unit test. `data` keys (`name`/`label`) match the integration assertions. ToolParam keyword order matches `registry.py`.
