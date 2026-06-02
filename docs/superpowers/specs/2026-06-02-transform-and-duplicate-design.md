# Design: transform_object relative mode + duplicate_object tool

- **Date:** 2026-06-02
- **Status:** Approved design, pending implementation plan
- **Scope:** Piece #4 (final) of the datum-geometry/transform suite (see Roadmap).
- **Base:** branch `feature/transform-object` off `master`. Independent of piece #3
  (PR #22, still open) — `transform_object` is a different code region, so no
  merge-first is required.

## Background

`transform_object` (`freecad_tools.py:1806`, `_handle_transform_object`) currently
does a single thing and does it with a footgun:

```python
placement = App.Placement(
    App.Vector(translate_x, translate_y, translate_z),
    App.Rotation(App.Vector(rotate_axis_x, rotate_axis_y, rotate_axis_z), rotate_angle),
)
obj.Placement = placement          # absolute OVERWRITE
```

Because the placement is overwritten absolutely and the translate params default to
`0`, calling `transform_object(object_name="X", rotate_angle=90)` to spin an object
in place **silently moves it to the world origin** (its translation is reset) — the
result even reports `"placement reset"`. There is no way to nudge-by-delta.

Separately, the suite has a standing need to **duplicate** an object: piece #3
(`create_datum_line`) deliberately has no offset parameter because the plan was for
a transform/duplicate capability to provide parallel/offset placement. A `copy`
flag already exists on `scale_object` (`:3900`), but it bakes a static
`Part::Feature` snapshot of `obj.Shape`, discarding the parametric feature tree —
which contradicts this suite's whole through-line (#17/#18: preserve model history).

This piece therefore splits the concern into two single-responsibility tools rather
than overloading `transform_object` with a `copy` flag (a tool named "transform"
should not secretly create objects — it is both a naming smell and a
tool-selection smell for an LLM caller).

The codebase conventions to build on: `_get_object` (Name then Label),
`_suggest_similar` ("did you mean"), `_with_undo` (undo transaction), and the
`ToolDefinition`/`ToolParam` registry (array params must declare `items` — issue
#10).

## Goals

1. **`transform_object` gains a relative mode** (default on) that composes the
   move/rotate onto the object's existing placement, fixing the reset footgun.
   Absolute placement remains available.
2. **A new `duplicate_object` tool** makes an independent, history-preserving copy
   of an object (the whole feature tree) and optionally offsets the copy in one
   call, leaving the original untouched.

Both ship in one spec / one plan / one PR — they are a cohesive unit and share the
relative-placement composition.

## Non-goals

- **No hand-rolled rotation math.** Placement composition uses FreeCAD's
  `App.Placement` / `App.Rotation`; we do not reimplement axis-angle→quaternion
  composition in a "pure" helper just to manufacture a unit test (that would
  duplicate well-tested FreeCAD math — more bug-prone, not less). Placement
  *semantics* are pinned by integration tests, per the suite's standing principle.
- **No `copy` flag on `transform_object`** — duplication lives in `duplicate_object`.
- **No removal of `scale_object`'s existing `copy` flag** — out of scope; left as-is.
- **No scaling/mirroring** in these tools (separate tools already exist).
- **No absolute mode for `duplicate_object`** — a duplicate is always offset
  *relative* to the original (the only sensible semantic); `0` offset = coincident.

## transform_object — relative mode

### New parameter

| Param | Type | Default | Meaning |
|-------|------|---------|---------|
| `relative` | boolean | `True` | Compose the move/rotate onto the object's current placement (`0` = no change). `False` reproduces today's absolute overwrite. |

All existing params (`object_name`, `translate_*`, `rotate_axis_*`, `rotate_angle`)
are unchanged.

### Semantics

Let `old = obj.Placement` and
`delta_rot = App.Rotation(App.Vector(rotate_axis_x, rotate_axis_y, rotate_axis_z), rotate_angle)`.

- **`relative=True` (default):**
  - **Translate is global**: `new_base = old.Base + App.Vector(tx, ty, tz)`.
  - **Rotate is in place**: `new_rot = delta_rot.multiply(old.Rotation)`, with
    `new_base` (above) kept — so the object pivots about its own placement origin
    using the given global axis direction, rather than flying to the world origin.
  - `new.Placement = App.Placement(new_base, new_rot)`.
  - Translate and rotate are applied **independently** (the translate is not
    rotated by `delta_rot`), which is the predictable, least-surprising behavior.
- **`relative=False`:** unchanged from today —
  `obj.Placement = App.Placement(App.Vector(tx,ty,tz), delta_rot)` (full overwrite).

### Output

The result message states the mode and what changed. The misleading
`"placement reset"` wording is removed; with `relative=True` and no params, the
message is e.g. `"'X' unchanged (relative, no delta given)"`. `data={"name": obj.Name}`.

### Backwards compatibility

The default flips from absolute to relative. The two existing integration tests
(`tests/integration/test_boolean_transform.py::...test_translate` / `test_rotate`)
operate on a **fresh primitive at the origin**, where relative and absolute
coincide — so they stay green. The behavior change is observable only for an
object that already has a non-identity placement; the user has accepted this
(alpha; the new behavior is the intended one).

## duplicate_object — new tool

### Parameters

| Param | Type | Default | Meaning |
|-------|------|---------|---------|
| `object_name` | string | (required) | Object to duplicate (Name or Label). |
| `translate_x` / `_y` / `_z` | number | `0.0` | Relative offset of the copy from the original (mm). |
| `rotate_axis_x` / `_y` / `_z` | number | `0,0,1` | Rotation axis for offsetting the copy. |
| `rotate_angle` | number | `0.0` | Rotation angle for offsetting the copy (deg). |
| `label` | string | `""` | Label for the copy; defaults to `"<original Label>_Copy"`. |

### Semantics

1. Resolve `obj` via `_get_object`; not found → error + `_suggest_similar` hint.
2. `copy = doc.copyObject(obj, with_dependencies=True)` — duplicates the whole
   feature tree (a `PartDesign::Body` and its Sketch/Pad/…; a datum object; or a
   plain `Part::Feature`) into an independent, editable, parametric copy. The
   original is untouched. `copyObject`'s exact return shape is **pinned by
   integration** (it returns the copy of the passed object; guard for the
   possibility it returns a list across FreeCAD versions).
3. Set the copy's label: `copy.Label = label or f"{obj.Label}_Copy"`.
4. Apply the **relative** offset to the copy using the *same* composition as
   `transform_object`'s relative mode (global translate of `copy.Base`,
   rotate-in-place of `copy.Rotation`). `copyObject` preserves the original's
   placement, so a `0` offset leaves the copy coincident with the original and a
   non-zero offset places it parametrically nearby.
5. `doc.recompute()`; verify the copy exists and is not in an Invalid/Error state.

### Result

`ToolResult(success=True, output=..., data={"name", "label"})`. The output names
the copy and notes the original is unchanged.

### Known limitation (documented, not worked around)

If the duplicated object's placement is **driven by an attachment** (e.g. a datum
line/plane attached to an edge/face via `MapMode`), recompute re-derives its
placement from the attachment, so the relative offset will not "stick". This is
expected: to make an offset *parallel* datum line, define it with the two-point
form of `create_datum_line` (a fixed placement) and then `duplicate_object` it —
which is precisely the suite's intended workflow. The tool description notes this.

## Validation and errors

Never segfault. Both tools:
- object not found → `ToolResult(success=False, …)` with `_suggest_similar` hint;
- wrap mutations in `_with_undo`;
- `duplicate_object` verifies the copy was created (handles a list/None return
  from `copyObject`) before transforming it, and checks `State` after recompute.

## Code organization

- Extend `_handle_transform_object` + `TRANSFORM_OBJECT` params in place
  (`freecad_tools.py:1806`/`:1847`); rewrite the description for relative-default
  + the `0`=no-change rule.
- Add `_handle_duplicate_object` + `DUPLICATE_OBJECT` `ToolDefinition` near
  `transform_object`; register `DUPLICATE_OBJECT` in `ALL_TOOLS`.
- The relative-composition is a few lines shared in spirit by both handlers. To
  avoid hand-rolled-quaternion purity (see Non-goals), it is written with `App`
  types. If a clean *FreeCAD-using* helper `_apply_relative(placement, tx,ty,tz,
  ax,ay,az,angle) -> App.Placement` removes duplication without reimplementing
  math, extract it (used by both handlers); it is exercised by integration tests,
  not unit tests.
- The only genuinely-pure, unit-testable bit is the default-label rule
  (`label or f"{base}_Copy"`); a tiny `_duplicate_label(base, requested)` may be
  extracted for a unit test, or covered by the ToolDefinition tests — implementer's
  call, low stakes.

## Testing

**Unit (no FreeCAD):**
- `TRANSFORM_OBJECT` / `DUPLICATE_OBJECT` definition guards: names, `category ==
  "modeling"`, registered in `ALL_TOOLS`, no array param without `items` (issue
  #10 — though these tools use only scalar/string params, keep the guard for
  consistency), `relative` present on transform with default `True`.
- default-label rule: `label` empty → `"<base>_Copy"`; explicit `label` honored.

**Integration (real FreeCAD, via `run_freecad_script`):**
- `transform_object(relative=True)` translate on an object that was first rotated →
  the rotation is **preserved** and the position shifts by the delta (proves
  no-overwrite).
- `transform_object(relative=True, rotate_angle=90)` on an object placed off the
  origin → the **position is preserved** and only the orientation changes (proves
  the footgun is fixed; rotate-in-place, not rotate-to-origin).
- `transform_object(relative=False, …)` → absolute overwrite (matches the two
  existing tests' expectations; regression guard).
- `duplicate_object` of a `PartDesign::Body` → a new independent Body exists with
  its own Sketch/Pad copied (feature tree duplicated, not a baked shape), the
  original is unchanged, and the copy is editable.
- `duplicate_object` with a non-zero `translate_*` → the copy is offset by the
  delta while the original stays put.
- `duplicate_object` of a plain `Part::Feature` → independent copy.
- **End-to-end (closes the suite):** `create_datum_line(point1=…, point2=…)` then
  `duplicate_object(<line>, translate_x=10)` → a second datum line parallel to the
  first, offset by 10 mm. (Requires piece #3; runnable on this branch only after
  #22 merges, OR by inlining an equivalent two-point datum line via run_macro-style
  setup in the test — the plan will use whichever is available; if #22 is unmerged
  at implementation time, build the first line directly in the test script.)

## Backwards compatibility

- `transform_object`: one new param (`relative`, default `True`); existing-tool
  behavior changes only for already-placed objects (accepted). No schema break.
- `duplicate_object`: purely additive (new tool + one `ALL_TOOLS` entry).

## Roadmap

Piece #4 of four — the **final** piece; it completes the suite begun with
`create_sketch` face/plane attachment (#20), `create_datum_plane` (#21), and
`create_datum_line` (#22). Wiki `Tool-Reference.md` gets a new `duplicate_object`
entry and an updated `transform_object` entry (relative mode) as this piece's docs
step — committed locally, not pushed (maintainer pushes the wiki manually).
