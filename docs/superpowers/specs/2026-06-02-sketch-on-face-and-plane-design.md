# Design: create_sketch on faces and arbitrary planes

- **Date:** 2026-06-02
- **Status:** Approved design, pending implementation plan
- **Scope:** Piece #1 of a small datum-geometry/transform suite (see Roadmap).

## Background

`create_sketch` (`freecad_tools.py:268`, def `_handle_create_sketch`) can only
place a sketch on one of the three **origin planes**. The `plane` parameter is
enum-restricted to `XY`/`XZ`/`YZ` (`freecad_tools.py:522`), and attachment only
happens when a `body_name` is given:

```python
if body and plane.upper() in ("XY", "XZ", "YZ"):
    plane_feat = _get_body_plane(body, plane.upper())
    if plane_feat:
        sketch.AttachmentSupport = [(plane_feat, "")]
        sketch.MapMode = "FlatFace"
```

`_get_body_plane` hard-maps only those three names into `body.Origin.OriginFeatures`;
anything else returns `None`. There is no way to:

- sketch on an arbitrary **planar face** of an existing solid, or
- sketch on a **datum/named plane**.

This gap is what blocked the issue #18 / #17-followup snap-fit workflow:
@0xrushi selected a face on an imported-and-converted (mesh→solid) part and asked
for a sketch on it. With no tool path for "sketch on the selected face," the model
fell out of the structured toolchain into raw `run_macro` Python and ultimately
baked a static, history-less `Part::Feature`.

The codebase already has the right conventions to build on:
- A face is referenced as **object name + sub-element**, e.g. `{object: "Box",
  sub_element: "Face6"}` — returned by `select_geometry` (`freecad_tools.py:4127`)
  and enumerable via `list_faces` (`freecad_tools.py:1844`).
- `_get_object(doc, name_or_label)` (`:4345`) resolves Name then Label.
- `_suggest_similar(doc, name, type_filter)` (`:4392`) produces "did you mean" hints.
- `_with_undo(label, func)` (`:35`) wraps mutations in an undo transaction.

## Goals

Let `create_sketch` attach a sketch to:

1. **A planar face** of an existing object — `support="Box"`, `face="Face6"`.
2. **A datum or named plane** — `support="DatumPlane"` (or an origin-plane object),
   `face=""`.
3. **The current GUI selection** — when both `support` and `face` are omitted,
   fall back to the active viewport selection (the literal "sketch on the selected
   face" flow). This is the only path that needs the GUI; it feeds the same
   resolution logic as the explicit params.

The existing origin-plane (`plane` ∈ {XY,XZ,YZ}) and standalone behavior is
preserved unchanged.

## Non-goals

- No non-planar / curved-face sketching. A non-planar `face` is a hard error,
  not a silent best-effort projection.
- No new datum-plane creation here — that is piece #2 (`create_datum_plane`),
  a separate spec. This change only *consumes* a plane referenced by name.
- No multi-face / averaged-plane attachment. One face or one plane.
- No change to geometry/constraint handling inside the sketch.

## Parameters

Two new optional params on `create_sketch`; all current params keep their
current defaults and meaning.

| Param | Type | Default | Meaning |
|-------|------|---------|---------|
| `support` | string | `""` | Name/label of the object to attach to: a solid (with `face`) or a plane object (datum/origin). |
| `face` | string | `""` | Planar sub-element of `support`, e.g. `"Face6"`. Requires `support`. |

`plane`, `body_name`, `geometries`, `constraints`, `label`, `offset` are
unchanged.

### Attachment resolution order

Resolved by a **pure helper** `_resolve_sketch_attachment(...)` (see Testing),
which decides *what to attach to* without touching FreeCAD:

1. **Explicit face** — `support` given and `face` non-empty → attach
   `[(support_obj, face)]`, `MapMode="FlatFace"`.
2. **Explicit plane** — `support` given, `face` empty, and `support_obj` is a
   plane (`App::Plane` / `PartDesign::Plane` / `Part::DatumPlane`) → attach
   `[(support_obj, "")]`, `MapMode="FlatFace"`.
3. **Selection fallback** — `support` and `face` both empty, and the GUI has a
   selection → use the first selected `(object, sub_element)`; route through
   case 1 (face) or case 2 (plane) accordingly.
4. **Origin plane** — `body` present and `plane` ∈ {XY,XZ,YZ} → current behavior.
5. **Standalone** — none of the above → sketch at default placement (current
   behavior).

## Body handling

The body the sketch lands in depends on the support, because a sketch can only
live in a Body it belongs to:

- If the support object is **inside a `PartDesign::Body`** (walk
  `obj.getParentGeoFeatureGroup()` / check `body.Group`), create the sketch in
  that body via `body.newObject(...)`.
- If the support is a **standalone `Part::Feature`** (an imported mesh→solid, as
  in #18), create a **standalone** sketch (`doc.addObject(...)`) attached to the
  face. This is the path that makes the 0xrushi case work — the sketch attaches
  to the face even though there is no Body.
- An explicit `body_name` that conflicts with the support's owning body is a
  clear error rather than a silent mismatch.

`offset` continues to shift the sketch along the resolved plane's normal via
`AttachmentOffset` (today it is a fixed per-origin-plane vector; for a face/datum
plane the normal comes from the attachment, so the offset is applied as
`AttachmentOffset = Placement(Vector(0,0,offset), Rotation())` in the sketch's
attached frame, whose local Z is the face/plane normal).

## Validation and errors

All failures return `ToolResult(success=False, ...)` with an actionable message;
none may segfault (per the FreeCAD-API gotchas, bad attachment args can crash).

- `face` given without `support` → error: "`face` requires `support`."
- `support` not found → error + `_suggest_similar` hint.
- `support` given, `face` empty, and `support_obj` is **not** a plane (e.g. a
  solid) → error: "`support` 'X' is a solid; specify a `face` (e.g. 'Face6'),
  or pass a datum/origin plane as `support`."
- `face` not found on the support shape (`getElement` raises / missing) → error
  listing available faces (reuse `list_faces` formatting).
- `face` exists but is **non-planar** (`face.Surface` is not a `Part.Plane`) →
  error: "Face 'FaceN' is not planar; sketches need a planar face."
- Selection fallback only activates when `support`/`face` are both empty **and**
  a usable planar face or plane is selected. An empty or non-usable selection
  (edge, vertex, nothing) falls through to the current origin-plane/standalone
  behavior — it is **not** an error, to preserve backward compatibility for
  plain `create_sketch(plane=...)` calls made with an unrelated selection active.
- If `body_name` is passed together with `support`, `body_name` is ignored (the
  sketch's container is the support's owning Body, or standalone) and a warning
  is included in the result. This avoids a fragile name-vs-label comparison.
- After attachment, recompute and verify the sketch's placement resolved (the
  attachment didn't silently fail) before adding geometry.

## Testing

**Pure unit tests (no FreeCAD)** — extract `_resolve_sketch_attachment(...)` that
takes the params plus lightweight descriptors (support type-id, whether `face`
is planar, the selection list) and returns a small spec dict, e.g.
`{"mode": "face", "support": "Box", "sub": "Face6", "in_body": "Body"|None}` or
`{"mode": "error", "message": "..."}`. Cases:
- explicit face → face mode;
- explicit plane (datum/origin object) → plane mode;
- `face` without `support` → error;
- non-planar face → error;
- selection fallback with a planar face → face mode;
- selection fallback with nothing selected → error;
- standalone solid vs in-body solid → correct `in_body`;
- no new args → unchanged origin-plane/standalone behavior (regression guard).

**Integration tests (real FreeCAD, `-m integration`):**
- sketch on a `Part::Box` face attaches and recomputes valid;
- sketch on a standalone (imported-style) solid's face → standalone sketch
  attached (the #18 scenario, minus the invalid geometry);
- sketch on a datum/origin plane object by name;
- non-planar face (cylinder side) → clean error, no crash;
- existing origin-plane call still works (regression).

## Backwards compatibility

- New params default to `""`; existing `create_sketch` calls are byte-for-byte
  unaffected (same resolution falls through to cases 4/5).
- `plane` enum stays `XY`/`XZ`/`YZ`. Datum/named planes are reached through
  `support`, not by overloading `plane`, to avoid ambiguity.
- Tool description updated to document `support`/`face`, the selection fallback,
  and the planar-face requirement. Wiki `Tool-Reference.md` updated in the same
  change.

## Roadmap (context only — not this spec)

This is piece #1. Following pieces get their own specs:

2. `create_datum_plane` — define a `PartDesign::Plane`/`Part::DatumPlane` from an
   origin/existing plane + offset, or a planar face + offset (no 3-point form).
3. `create_datum_line` — datum axis from two points or an origin axis + offset.
4. `transform_object` — add a `copy`/duplicate option and a relative-vs-absolute
   mode (today it overwrites `Placement` and cannot duplicate).
