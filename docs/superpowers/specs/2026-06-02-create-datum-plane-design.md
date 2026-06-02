# Design: create_datum_plane tool

- **Date:** 2026-06-02
- **Status:** Approved design, pending implementation plan
- **Scope:** Piece #2 of the datum-geometry/transform suite (see Roadmap).
- **Base:** branch `feature/datum-plane` off `master` (PR #20 / piece #1 already merged — `e76be0a` — so the shared resolution helpers are available).

## Background

Piece #1 (merged) gave `create_sketch` the ability to attach to a planar face,
a datum/named plane, or the GUI selection, via a reusable resolution layer in
`freecad_ai/tools/freecad_tools.py`:

- `_resolve_sketch_attachment(support, face, plane, body_present, support_kind,
  face_exists, face_planar, in_body)` — pure decision returning a spec dict with
  `mode` ∈ {`face`, `plane`, `origin`, `standalone`, `error`}.
- helpers `_classify_support`, `_owning_body_name`, `_inspect_face`,
  `_read_planar_selection`, and the constant `_PLANE_TYPE_IDS`.

`create_datum_plane` is structurally `create_sketch` minus the geometry: resolve
a reference, pick the container Body, create a plane object, attach it with an
offset. It therefore **reuses the same resolution layer** rather than
duplicating it.

A datum plane is a first-class need on the original snap-fit workflow that
started this line of work ("create a plane at the center of the object to use as
a mirror reference"). The created plane is referenceable by name as
`create_sketch`'s `support`, closing the loop: *make an offset plane → sketch on
it → pad/pocket*.

## Goals

A new tool `create_datum_plane` that creates a **parametric** datum plane offset
(parallel) from a reference:

1. From an **origin plane** (XY/XZ/YZ) of a Body.
2. From a **planar face** of an existing object (`support` + `face`).
3. From an **existing plane object** by name (`support`).
4. From the **current GUI selection** (bare call) — same fallback as
   `create_sketch`.

The plane attaches parametrically (via `AttachmentSupport` + `MapMode` +
`AttachmentOffset`), so it moves if its reference moves — preserving model
history, which is the through-line of this whole effort (#17/#18).

## Non-goals

- **No tilt/angle.** Offset-along-normal only (parallel planes). Decided with the
  user; an angled-datum variant can come later if needed.
- **No 3-point / line-and-point / two-plane-bisector definitions.** YAGNI.
- No change to `create_sketch` or any existing tool. Purely additive.
- No new datum *line* (that is piece #3).

## Parameters

Mirror `create_sketch` for a consistent vocabulary. All optional; sensible
defaults.

| Param | Type | Default | Meaning |
|-------|------|---------|---------|
| `plane` | string | `"XY"` | Origin-plane reference (XY/XZ/YZ), used when no `support`. Needs `body_name` (origin planes belong to a Body). |
| `support` | string | `""` | Object to offset from: a solid (with `face`) or an existing plane object by name. |
| `face` | string | `""` | Planar sub-element of `support`, e.g. `"Face6"`. Requires `support`. |
| `offset` | number | `0.0` | Parallel distance along the reference normal (mm). May be negative. |
| `body_name` | string | `""` | Body to create the `PartDesign::Plane` in (required for an origin-plane reference). |
| `label` | string | `""` | Display label; defaults to `"DatumPlane"`. |

## Behavior

### Reference resolution (reuses piece #1)

The reference is resolved by a thin **pure** wrapper
`_resolve_datum_plane_attachment(...)` that takes the same arguments as
`_resolve_sketch_attachment`, delegates to it, and then converts the one
datum-specific decision: a `standalone` result becomes an `error` (a datum
plane, unlike a sketch, cannot be free-floating — it needs a reference). All
other modes pass through unchanged. Keeping this remap in a pure function (not
the FreeCAD-importing handler) preserves piece #1's "decision is unit-tested
without FreeCAD" pattern.

The handler still performs the bare-call selection fallback (when `support`,
`face`, `body_name` are empty and `plane == "XY"`, consult
`_read_planar_selection()`) before calling the wrapper. The resulting `mode`
maps as:

- `face` → reference is `(support_obj, "Face6")`.
- `plane` → reference is the existing plane object `(support_obj, "")`.
- `origin` → reference is the Body's origin plane (`_get_body_plane`).
- `error` → return the error verbatim. (Includes the `standalone`→error remap:
  `"create_datum_plane needs a reference: pass a plane (XY/XZ/YZ) with body_name,
  or a support object (optionally with a face)."`)

### Container and object type

Container selection is identical to `create_sketch`:

- reference object lives in a `PartDesign::Body` → that Body;
- reference object **is** a `PartDesign::Body` → that Body itself;
- `origin` mode → the named `body`;
- otherwise → standalone.

Object created:

- container is a Body → `container.newObject("PartDesign::Plane", label or
  "DatumPlane")`.
- standalone (reference is a plain `Part::Feature` face, e.g. an imported
  mesh→solid) → `doc.addObject("Part::DatumPlane", label or "DatumPlane")`.

### Attachment

```text
datum.AttachmentSupport = [(ref_obj, sub)]   # sub is "Face6" or "" for a plane
datum.MapMode = "FlatFace"
datum.AttachmentOffset = App.Placement(App.Vector(0, 0, offset), App.Rotation())
doc.recompute()
```

The sketch's/plane's local Z after `FlatFace` is the reference normal, so the
offset is always along the normal regardless of reference orientation — the same
convention piece #1 uses for face/plane attachment.

## Validation and errors

Reuse piece #1's guarantees; never segfault.

- Resolver errors (missing support, missing/non-planar face, `face` without
  `support`, solid-without-face) propagate verbatim (with `_suggest_similar`
  hint when the support name is unknown).
- `standalone` resolver result → the no-reference error above.
- After recompute, verify the attachment resolved via the same `State`
  Invalid/Error check piece #1 uses; on failure return a clear
  `"Failed to attach datum plane to '<ref>' — attachment did not resolve."`
  (The executor sandbox remains the higher-level net.)

## Result

`ToolResult(success=True, output=..., data={"name", "label", "type_id"})`. The
output names the created plane and notes it can be passed to
`create_sketch(support="<name>")`. A `body_name`-ignored warning is surfaced the
same way as `create_sketch` when `body_name` is passed alongside a `support`
reference.

## Code organization

- New pure `_resolve_datum_plane_attachment(...)` (delegates to
  `_resolve_sketch_attachment`, remaps `standalone`→error), new handler
  `_handle_create_datum_plane(...)`, and `CREATE_DATUM_PLANE` `ToolDefinition` in
  `freecad_ai/tools/freecad_tools.py`, placed near `create_sketch` (the
  resolution layer it reuses).
- Register `CREATE_DATUM_PLANE` in `ALL_TOOLS` (the list at `freecad_tools.py:5103`).
- The reference-resolve → pick-container → attach sequence is ~15 lines shared in
  spirit with `_handle_create_sketch`. To avoid a churny refactor of just-merged
  piece-#1 code, this piece **inlines** those lines rather than extracting a
  shared `_attach_feature(...)` helper now. A future extraction (when piece #3
  `create_datum_line` adds a third consumer) is noted as a refactor opportunity,
  not done here.

## Testing

Reference resolution is already unit-tested in piece #1
(`tests/unit/test_sketch_attachment.py`), so this piece is exercised mainly by
integration tests (real FreeCAD) plus one small pure check.

**Unit (no FreeCAD):** test `_resolve_datum_plane_attachment`:
- `standalone` input (no support, no body) → `error` with the no-reference
  message.
- `face` / `plane` / `origin` / `error` inputs pass through unchanged (delegation
  to `_resolve_sketch_attachment` is intact).

**Integration (real FreeCAD, via `run_freecad_script`):**
- Datum plane offset from XY origin plane in a Body → object is
  `PartDesign::Plane`, lives in the Body, and its global placement is offset by
  `offset` in Z.
- Datum plane offset from a planar face of a Body feature → `PartDesign::Plane`
  attached, offset along the face normal.
- Datum plane from a **standalone** `Part::Feature` solid's face → object is
  `Part::DatumPlane` (standalone), attached to the face.
- End-to-end: `create_datum_plane` then `create_sketch(support="<plane name>")`
  succeeds and the sketch attaches to the datum plane (closes the loop; piece #1
  is on master so this is runnable).
- No-reference call (`create_datum_plane(offset=10)`, no body/support/selection)
  → clean error.
- Non-planar face (`support` = cylinder, curved face) → clean error.

## Backwards compatibility

Purely additive: a new tool and one new entry in `ALL_TOOLS`. No existing tool,
schema, or behavior changes.

## Roadmap (context only — not this spec)

Piece #2 of four. Remaining: (3) `create_datum_line` (two points, or an origin
axis + offset); (4) `transform_object` — add a `copy`/duplicate option and a
relative-vs-absolute mode (it currently overwrites `Placement` and cannot
duplicate). Wiki `Tool-Reference.md` gets a `create_datum_plane` entry as part of
this piece's docs step.
