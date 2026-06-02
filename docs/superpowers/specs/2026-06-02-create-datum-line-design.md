# Design: create_datum_line tool

- **Date:** 2026-06-02
- **Status:** Approved design, pending implementation plan
- **Scope:** Piece #3 of the datum-geometry/transform suite (see Roadmap).
- **Base:** branch `feature/datum-line` off `master` (PR #20 / piece #1 and PR #21 /
  piece #2 both merged — `e76be0a`, `0d439c5`).

## Background

Pieces #1 (`create_sketch` on faces/planes) and #2 (`create_datum_plane`) gave the
workbench parametric *plane* references. `create_datum_line` is the *axis* analog:
a named, reusable line that other features reference as a rotation axis
(`revolve_sketch`), a mirror reference, or a polar-pattern axis.

The codebase already has the conventions to build on:

- `_get_body_axis(body, "X"|"Y"|"Z")` (`freecad_tools.py:87`) resolves an origin
  axis (`App::Line`) from a Body's `Origin.OriginFeatures` — already used by
  `revolve_sketch` and `LinearPattern`.
- `_get_object` (Name then Label), `_suggest_similar` ("did you mean"),
  `_owning_body_name` (walk to the owning `PartDesign::Body`), and
  `_with_undo` wrap the standard mutation/lookup conventions.
- An edge is referenced as **object name + sub-element**, e.g. `(obj, "Edge3")`,
  exactly as `revolve_sketch` already accepts a sketch edge for its axis.

This piece does **not** reuse the plane/face resolution layer
(`_resolve_sketch_attachment`, `_classify_support`, `_inspect_face`,
`_PLANE_TYPE_IDS`) — that layer is plane/face-domain and does not transfer to
lines, edges, and points. It introduces its own small pure resolver instead.

## Goals

A new tool `create_datum_line` that creates a datum line (axis) by one of three
mutually exclusive modes:

1. **Two points** — a line through two 3-D points (`point1`, `point2`). The one
   genuinely new capability: an axis where no geometry exists yet (e.g. a mirror
   or rotation axis through the center of an imported solid). This is a *fixed*
   datum — it has no reference to track.
2. **Edge** — attach parametrically to an existing **straight** edge of an object
   (`support="Obj"`, `edge="Edge3"`). Tracks the edge, preserving model history.
3. **Origin axis** — reference a Body's origin axis (`axis="X"|"Y"|"Z"`, needs
   `body_name`). Parametric.

The created line is referenceable by name as a `revolve_sketch` axis or a mirror
reference, closing the loop the same way `create_datum_plane` feeds
`create_sketch`.

## Non-goals

- **No offset / translate / duplicate.** A datum line's offset direction is
  ambiguous (a line has a 2-D normal space, unlike a plane's 1-D normal), so
  rather than invent a direction parameter, offsetting/copying a line is delegated
  to piece #4 (`transform_object` with `copy` + relative mode). `create_datum_line`
  only *defines* an axis. This is the line analog of datum_plane's deliberate
  "no tilt" decision.
- **No 3-point, line-from-two-planes, or axis-of-inertia definitions.** YAGNI.
- No change to `create_sketch`, `create_datum_plane`, `revolve_sketch`, or any
  existing tool. Purely additive.

## Parameters

All optional; exactly one mode's inputs must be supplied (enforced by the pure
resolver).

| Param | Type | Default | Meaning |
|-------|------|---------|---------|
| `point1` | array(number) | `[]` | First 3-D point `[x, y, z]` (two-points mode). |
| `point2` | array(number) | `[]` | Second 3-D point `[x, y, z]` (two-points mode). |
| `support` | string | `""` | Object whose edge to attach to (edge mode). |
| `edge` | string | `""` | Straight sub-element of `support`, e.g. `"Edge3"`. Requires `support`. |
| `axis` | string | `""` | Origin axis: `X`, `Y`, or `Z` (origin-axis mode). Needs `body_name`. |
| `body_name` | string | `""` | Body to create the line in. Required for origin-axis mode; optional for two-points (places the fixed datum inside the Body); ignored with a warning for edge mode (container is the edge's owning Body). |
| `label` | string | `""` | Display label; defaults to `"DatumLine"`. |

`point1`/`point2` are JSON arrays of three numbers (consistent with how other
tools accept coordinate lists). The schema declares
`type="array"` **with** `items={"type": "number"}` — the array-without-items
omission caused issue #10, and a registry-walking test already guards against it.

## Behavior

### Mode resolution (pure, unit-tested)

A pure function `_resolve_datum_line_def(point1, point2, support, edge, axis,
body_present, support_kind, edge_exists, edge_straight, in_body)` decides the mode
and validates, **without touching FreeCAD**. It returns one of:

```text
{"mode": "points", "p1": [x,y,z], "p2": [x,y,z]}
{"mode": "edge", "support": str, "sub": str, "in_body": str|None}
{"mode": "origin", "axis": "X"|"Y"|"Z"}
{"mode": "error", "message": str}
```

`support_kind` is `""` | `"missing"` | `"solid"` | `"other"` (a *plane* support is
not meaningful for an edge reference, so it is treated like any other object —
the edge lookup is what matters). `edge_exists` / `edge_straight` are meaningful
only when `edge != ""`. `in_body` is the support's owning Body name or `None`.

Mode selection counts which mode's inputs are present and rejects ambiguity:

- More than one mode populated (e.g. `point1` **and** `axis`, or `support` **and**
  `point1`) → error: `"Specify exactly one of: two points (point1+point2), an edge
  (support+edge), or an origin axis (axis)."`
- No mode populated → same error.

Per-mode validation:

- **Two points:** both `point1` and `point2` must be length-3 numeric lists, and
  the two points must not be coincident (distance above a small epsilon) →
  else error.
- **Edge:** `edge` without `support` (or `support` without `edge`) → error
  (`"edge requires support" / "support requires an edge (e.g. 'Edge3')"`);
  `support_kind == "missing"` → error (caller adds `_suggest_similar` hint);
  `edge` not found → error; `edge` not straight → error
  (`"Edge 'EdgeN' on 'X' is not straight; a datum line needs a straight edge."`).
- **Origin axis:** `axis` not in {X, Y, Z} → error; `body_present` false → error
  (`"axis mode needs body_name (origin axes belong to a Body)."`).

### Container and object type

- **edge** mode: edge's owning Body (`in_body`) → that Body via `newObject`;
  edge on a standalone `Part::Feature` → standalone via `doc.addObject`. A
  `body_name` passed alongside `support` is ignored with a warning (same pattern
  as datum_plane).
- **origin** mode: the named `body` (`newObject`).
- **two-points** mode: if `body_name` is given, create inside that Body via
  `newObject` (a fixed datum living in the Body's tree); otherwise standalone via
  `doc.addObject`.

Object created: `PartDesign::Line`. Like `PartDesign::Plane`, it is expected to
work both inside a Body (`newObject`) and standalone (`addObject`). **This, and
the two-point placement mechanism below, are pinned by integration tests before
being trusted** — unit tests and review cannot catch an invalid FreeCAD type
(the `Part::DatumPlane`-is-not-valid surprise in piece #2 is the precedent).

### Attachment / placement per mode

- **edge:** `line.AttachmentSupport = [(support_obj, "Edge3")]`;
  `line.MapMode = "OneEdge"`; `doc.recompute()`.
- **origin:** resolve the origin axis via `_get_body_axis(body, axis)`;
  `line.AttachmentSupport = [(axis_obj, "")]`; `line.MapMode = "OneEdge"`;
  `doc.recompute()`. If the axis can't be resolved, warn and leave the line at the
  document origin (mirrors datum_plane's origin-plane fallback).
- **two-points:** no attachment. Set `line.Placement` so the line passes through
  `p1` directed toward `p2`: position = `p1`, rotation maps the line's local
  direction onto `(p2 - p1)` normalized (`App.Rotation(App.Vector(0,0,1), dir)` or
  the local axis the datum line uses — **confirmed empirically in the integration
  test**, since the local reference direction of `PartDesign::Line` must be
  verified, not assumed). The visible length may be set to `|p2 - p1|` for a sane
  default; `ReferenceAxis` consumers treat the line as infinite regardless.

### Validation and errors

Reuse the datum_plane guarantees; never segfault.

- Resolver errors propagate verbatim (with `_suggest_similar` hint when the
  support name is unknown).
- After recompute (edge/origin modes), verify the attachment resolved via the
  same `State` Invalid/Error check datum_plane uses; on failure return
  `"Failed to attach datum line to '<ref>' — attachment did not resolve."`
  (The executor sandbox remains the higher-level net.)

## Result

`ToolResult(success=True, output=..., data={"name", "label", "type_id"})`. The
output names the created line and notes it can be passed to `revolve_sketch` as
an axis or used as a mirror reference. Warnings (ignored `body_name`, unresolved
origin axis) are surfaced the same way datum_plane surfaces them.

## Code organization

- New pure `_resolve_datum_line_def(...)`, new handler
  `_handle_create_datum_line(...)`, and `CREATE_DATUM_LINE` `ToolDefinition` in
  `freecad_ai/tools/freecad_tools.py`, placed near `create_datum_plane`.
- Register `CREATE_DATUM_LINE` in `ALL_TOOLS` (the list at `freecad_tools.py:5295`,
  after `CREATE_DATUM_PLANE`).
- Reuses `_get_object`, `_suggest_similar`, `_owning_body_name`, `_get_body_axis`,
  `_with_undo`. Inlines the create→attach→verify sequence (the shared-helper
  extraction noted in the datum_plane spec stays deferred; a third consumer now
  exists, so the extraction is worth revisiting as a *separate* cleanup, not part
  of this feature).

## Testing

**Unit (no FreeCAD)** — test `_resolve_datum_line_def`:
- two valid points → `points` mode;
- valid `support` + straight `edge` → `edge` mode with correct `in_body`;
- valid `axis` + `body_present` → `origin` mode;
- coincident points → error;
- `point1` with no `point2` (and vice-versa) → error;
- `edge` without `support`, `support` without `edge` → error;
- missing support (`support_kind == "missing"`) → error;
- non-straight edge → error;
- `axis` not X/Y/Z → error; `axis` without body → error;
- two modes populated at once (points+axis, support+points) → error;
- no inputs at all → error.

**Integration (real FreeCAD, via `run_freecad_script`):**
- two-point line → object is `PartDesign::Line`; its placement/endpoints lie on
  the two points (asserts the placement mechanism);
- two-point line with `body_name` → `PartDesign::Line` created inside the Body;
- edge-attached line on a Body feature → `PartDesign::Line` in the Body, attached
  (`MapMode == "OneEdge"`, State not Invalid/Error);
- edge on a **standalone** `Part::Feature` solid → standalone `PartDesign::Line`
  (created via `doc.addObject`), attached;
- origin-axis line (`axis="Z"`, body) → attached to the Body's Z axis;
- curved edge (cylinder side) → clean error, no crash;
- end-to-end: `create_datum_line` then `revolve_sketch` referencing it as the axis
  succeeds (closes the loop).

## Backwards compatibility

Purely additive: a new tool and one new entry in `ALL_TOOLS`. No existing tool,
schema, or behavior changes.

## Roadmap (context only — not this spec)

Piece #3 of four. Remaining: (4) `transform_object` — add a `copy`/duplicate
option and a relative-vs-absolute mode (it currently overwrites `Placement` and
cannot duplicate). That piece is what provides "offset a datum line to a parallel
position" and "duplicate an object," which is why `create_datum_line` carries no
offset of its own. Wiki `Tool-Reference.md` gets a `create_datum_line` entry as
part of this piece's docs step (committed locally, not pushed — maintainer pushes
the wiki manually).
