"""FreeCAD sketch."""

import os

from ..registry import ToolParam, ToolDefinition, ToolResult
from ...core.executor import execute_code
from ..tool_common import *  # noqa: F403

# ── create_sketch ───────────────────────────────────────────

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
    """Create a sketch with geometry and constraints."""
    import FreeCAD as App
    import Part
    import Sketcher

    def do(doc):
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
        # Only consult the viewport selection for a bare call. An explicit
        # plane, body_name, support, or face signals intent and must not be
        # silently overridden by whatever happens to be selected (preserves
        # backward compatibility for create_sketch(plane=..., body_name=...)).
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
            if spec.get("in_body"):
                container = _get_object(doc, spec["in_body"])
            elif sup_obj is not None and getattr(sup_obj, "TypeId", "") == "PartDesign::Body":
                # Sketching on a Body's own face → keep the sketch inside it.
                container = sup_obj
            else:
                container = None
        elif spec["mode"] == "origin":
            container = body
        else:  # standalone
            container = body

        if container is not None:
            sketch = container.newObject("Sketcher::SketchObject", label or "Sketch")
        else:
            sketch = doc.addObject("Sketcher::SketchObject", label or "Sketch")

        # Apply the attachment. sup_obj is the resolved support object (non-None
        # whenever the resolver returned a face/plane mode).
        if spec["mode"] == "face":
            sketch.AttachmentSupport = [(sup_obj, spec["sub"])]
            sketch.MapMode = "FlatFace"
        elif spec["mode"] == "plane":
            sketch.AttachmentSupport = [(sup_obj, "")]
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

        # If a face/plane attachment can't resolve, FreeCAD marks the sketch's
        # State as Invalid/Error after recompute. (The executor sandbox is the
        # higher-level net — it also captures the C++ PositionBySupport error.)
        if spec["mode"] in ("face", "plane"):
            state = list(getattr(sketch, "State", []) or [])
            if any(s in ("Invalid", "Error") for s in state):
                return ToolResult(
                    success=False, output="",
                    error=(f"Failed to attach sketch to '{spec['support']}'"
                           + (f":{spec['sub']}" if spec.get("sub") else "")
                           + " — attachment did not resolve."))

        geo_count = 0
        if geometries:
            for geo in geometries:
                # Some LLMs pass geometry items as JSON strings instead of dicts
                if isinstance(geo, str):
                    try:
                        import json as _json
                        geo = _json.loads(geo)
                    except (ValueError, TypeError):
                        continue
                geo_type = geo.get("type", "")
                if geo_type == "line":
                    p1 = App.Vector(geo.get("x1", 0), geo.get("y1", 0), 0)
                    p2 = App.Vector(geo.get("x2", 0), geo.get("y2", 0), 0)
                    sketch.addGeometry(Part.LineSegment(p1, p2))
                    geo_count += 1
                elif geo_type == "circle":
                    cx = geo.get("cx", geo.get("x", 0))
                    cy = geo.get("cy", geo.get("y", 0))
                    r = geo.get("radius", 10)
                    sketch.addGeometry(Part.Circle(
                        App.Vector(cx, cy, 0), App.Vector(0, 0, 1), r))
                    geo_count += 1
                elif geo_type == "arc":
                    cx = geo.get("cx", geo.get("x", 0))
                    cy = geo.get("cy", geo.get("y", 0))
                    r = geo.get("radius", 10)
                    start_angle = geo.get("start_angle", 0)
                    end_angle = geo.get("end_angle", 3.14159)
                    sketch.addGeometry(Part.ArcOfCircle(
                        Part.Circle(App.Vector(cx, cy, 0), App.Vector(0, 0, 1), r),
                        start_angle, end_angle))
                    geo_count += 1
                elif geo_type == "rectangle":
                    # Accept both (x1,y1,x2,y2) and (x,y,width,height) formats
                    # Also accept "length" as alias for "height" (LLMs often confuse these)
                    # Dimensions can be numbers or expression strings (e.g. "Variables.length")
                    rect_w = geo.get("width", None)
                    rect_h = geo.get("height", None) or geo.get("length", None)
                    w_expr = None
                    h_expr = None
                    if rect_w is not None and rect_h is not None:
                        # If dimensions are expression strings, use a placeholder
                        if isinstance(rect_w, str):
                            w_expr = rect_w
                            rect_w = 10  # placeholder, expression overrides
                        if isinstance(rect_h, str):
                            h_expr = rect_h
                            rect_h = 10
                        x1 = geo.get("x", 0)
                        y1 = geo.get("y", 0)
                        x2 = x1 + rect_w
                        y2 = y1 + rect_h
                    else:
                        x1, y1 = geo.get("x1", 0), geo.get("y1", 0)
                        x2, y2 = geo.get("x2", 10), geo.get("y2", 10)
                    # 4 lines forming a rectangle
                    sketch.addGeometry(Part.LineSegment(App.Vector(x1, y1, 0), App.Vector(x2, y1, 0)))
                    sketch.addGeometry(Part.LineSegment(App.Vector(x2, y1, 0), App.Vector(x2, y2, 0)))
                    sketch.addGeometry(Part.LineSegment(App.Vector(x2, y2, 0), App.Vector(x1, y2, 0)))
                    sketch.addGeometry(Part.LineSegment(App.Vector(x1, y2, 0), App.Vector(x1, y1, 0)))
                    g = sketch.GeometryCount - 4
                    sketch.addConstraint(Sketcher.Constraint("Coincident", g, 2, g+1, 1))
                    sketch.addConstraint(Sketcher.Constraint("Coincident", g+1, 2, g+2, 1))
                    sketch.addConstraint(Sketcher.Constraint("Coincident", g+2, 2, g+3, 1))
                    sketch.addConstraint(Sketcher.Constraint("Coincident", g+3, 2, g, 1))
                    sketch.addConstraint(Sketcher.Constraint("Horizontal", g))
                    sketch.addConstraint(Sketcher.Constraint("Horizontal", g+2))
                    sketch.addConstraint(Sketcher.Constraint("Vertical", g+1))
                    sketch.addConstraint(Sketcher.Constraint("Vertical", g+3))
                    # Add dimension constraints (width = DistanceX, height = DistanceY)
                    # These make the rectangle fully constrained and bindable
                    w_ci = sketch.addConstraint(
                        Sketcher.Constraint("DistanceX", g, 1, g, 2, rect_w))
                    h_ci = sketch.addConstraint(
                        Sketcher.Constraint("DistanceY", g+1, 1, g+1, 2, rect_h))
                    # Bind to expressions if provided
                    if w_expr:
                        sketch.setExpression(f"Constraints[{w_ci}]", w_expr)
                    if h_expr:
                        sketch.setExpression(f"Constraints[{h_ci}]", h_expr)
                    geo_count += 4

        if constraints:
            for con in constraints:
                if isinstance(con, str):
                    try:
                        import json as _json
                        con = _json.loads(con)
                    except (ValueError, TypeError):
                        continue
                con_type = con.get("type", "")
                if not con_type:
                    continue

                # Validate: constraints need at least a geometry index ("first")
                # to be meaningful. Without it, FreeCAD's C++ layer may segfault.
                # Constraints with only type+value and no geometry refs are skipped.
                if "first" not in con and con_type not in ("Block",):
                    continue

                # Remove auto-generated constraint that matches this one
                # so the explicit constraint overwrites it.
                first = con.get("first", -2000)
                first_pos = con.get("first_pos", 0)
                second = con.get("second", -2000)
                second_pos = con.get("second_pos", 0)
                for ci in range(sketch.ConstraintCount - 1, -1, -1):
                    ec = sketch.Constraints[ci]
                    if (ec.Type == con_type
                            and ec.First == first
                            and ec.Second == second):
                        sketch.delConstraint(ci)
                        break

                args = [con_type]
                for key in ("first", "first_pos", "second", "second_pos", "value"):
                    if key in con:
                        v = con[key]
                        # Ensure numeric args are ints (geometry/point indices) or float (value)
                        if key == "value":
                            args.append(float(v))
                        elif isinstance(v, float):
                            args.append(int(v))
                        else:
                            args.append(v)
                try:
                    sketch.addConstraint(Sketcher.Constraint(*args))
                except Exception:
                    pass  # Skip invalid constraints

        # Report all constraints — dimension ones marked as bindable
        constraint_count = sketch.ConstraintCount
        constraint_status = ""
        _DIMENSION_TYPES = {
            "Distance", "DistanceX", "DistanceY", "Radius", "Angle",
        }
        constraint_details = []
        try:
            for ci in range(constraint_count):
                c = sketch.Constraints[ci]
                if c.Type in _DIMENSION_TYPES:
                    constraint_details.append(
                        f"Constraints[{ci}]: {c.Type} = {c.Value}  ← bindable"
                    )
                else:
                    constraint_details.append(
                        f"Constraints[{ci}]: {c.Type}"
                    )
        except Exception:
            pass

        try:
            if sketch.FullyConstrained:
                constraint_status = " Fully constrained."
            else:
                dof = sketch.solve()
                if dof > 0:
                    constraint_status = (
                        f" Under-constrained ({dof} DOF remaining)"
                        " — add constraints to fully define the sketch."
                    )
                elif dof < 0:
                    constraint_status = (
                        " Over-constrained — remove redundant constraints."
                    )
                else:
                    constraint_status = " Fully constrained."
        except Exception:
            pass

        constraint_info = ""
        if constraint_details:
            constraint_info = (
                "\nConstraints:\n"
                + "\n".join(f"  {d}" for d in constraint_details)
            )

        return ToolResult(
            success=True,
            output=(
                ("⚠ " + " ".join(warnings) + "\n" if warnings else "")
                + f"Created sketch '{sketch.Name}' with {geo_count} geometries"
                f" and {constraint_count} constraints.{constraint_status}"
                f"{constraint_info}"
                f"\nUse sketch_name='{sketch.Name}' in pad_sketch/pocket_sketch."),
            data={"name": sketch.Name, "label": sketch.Label,
                  "geometry_count": geo_count, "constraint_count": constraint_count,
                  "constraints": constraint_details,
                  "fully_constrained": getattr(sketch, "FullyConstrained", None)},
        )

    return _with_undo("Create Sketch", do, create_document_if_missing=True)


CREATE_SKETCH = ToolDefinition(
    name="create_sketch",
    description=(
        "Create a 2D sketch with geometry (lines, circles, arcs, rectangles) and constraints. "
        "This is ALSO the correct tool to attach a sketch to a SELECTED or named planar "
        "face of an existing solid (pass support='Obj', face='Face6' — get the name from "
        "list_faces) or to a datum plane (support='PlaneName'); do NOT hand-write an "
        "AttachmentSupport/MapMode macro for this. Without support it attaches to the origin "
        "`plane` (XY/XZ/YZ). "
        "For PartDesign, specify body_name to add the sketch to a body. "
        "Rectangle dimensions can be expression strings for parametric models: "
        "width='Variables.length', height='Variables.width'. "
        "IMPORTANT: rectangles and circles auto-generate all necessary constraints "
        "(Coincident, Horizontal, Vertical, DistanceX, DistanceY, Radius). "
        "Do NOT add DistanceX/DistanceY/Radius in the constraints array for these — "
        "they are already constrained by the coordinates and dimensions you provide. "
        "COORDINATE SYSTEM: FreeCAD sketches use Y-up. When converting from SVG, images, "
        "or screen coordinates (which use Y-down), negate all Y values."
    ),
    category="modeling",
    parameters=[
        ToolParam("plane", "string", "Attachment plane: XY, XZ, or YZ", required=False, default="XY",
                  enum=["XY", "XZ", "YZ"]),
        ToolParam("body_name", "string", "Name of PartDesign body to add sketch to", required=False, default=""),
        ToolParam("geometries", "array",
                  "List of geometry objects. Each has a 'type' key plus type-specific params: "
                  "line: {x1,y1,x2,y2}, "
                  "rectangle: {x,y,width,height}, "
                  "circle: {cx,cy,radius}, "
                  "arc: {cx,cy,radius,start_angle,end_angle}.",
                  required=False, items={"type": "object"}),
        ToolParam("constraints", "array",
                  "List of Sketcher constraints. Each has 'type' plus constraint-specific params "
                  "(e.g. {type:'Distance',object1:'Edge1',value:50}).",
                  required=False, items={"type": "object"}),
        ToolParam("label", "string", "Display label for the sketch", required=False, default=""),
        ToolParam("offset", "number", "Offset the sketch along the plane normal (e.g. offset=40 on XY places sketch at z=40)", required=False, default=0.0),
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
    ],
    handler=_handle_create_sketch,
)


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

        # PartDesign::Plane works both inside a Body (newObject) and standalone
        # (addObject) — FreeCAD 1.1 has no separate standalone datum-plane type.
        if container is not None:
            datum = container.newObject("PartDesign::Plane", label or "DatumPlane")
        else:
            datum = doc.addObject("PartDesign::Plane", label or "DatumPlane")

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
            else:
                warnings.append(
                    f"could not resolve the {spec['plane']} origin plane — "
                    "datum left at the document origin.")

        # AttachmentOffset is in the datum's local frame, whose Z is the
        # reference normal (FlatFace) for every mode — so (0, 0, offset) is
        # always along the normal. (create_sketch maps origin-plane offsets to
        # global axes for legacy reasons; the result is equivalent.)
        if offset != 0:
            datum.AttachmentOffset = App.Placement(
                App.Vector(0, 0, offset), App.Rotation())

        doc.recompute()

        # If the attachment can't resolve, FreeCAD marks the feature's State as
        # Invalid/Error. (The executor sandbox is the higher-level net.)
        state = list(getattr(datum, "State", []) or [])
        if any(s in ("Invalid", "Error") for s in state):
            if spec.get("support"):
                ref = spec["support"]
            elif spec.get("plane"):
                ref = "origin plane " + spec["plane"]
            else:
                ref = "reference"
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
        "is a PartDesign::Plane (inside a Body, or standalone when the reference "
        "is not in a Body), "
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


# ── create_datum_line ───────────────────────────────────────

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

    ``objects`` is an iterable of document objects (e.g. ``doc.Objects``). A Body lists
    its children in ``.Group``.

    See also ``_find_body_for``, which returns the Body object itself.
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

    Never raises — a missing face, unavailable Part module, or non-shape object
    yields (False, False).
    """
    try:
        import Part
    except Exception:
        return (False, False)
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
            line.MapMode = "Tangent"
        elif spec["mode"] == "origin":
            axis_feat = _get_body_axis(container, spec["axis"])
            if axis_feat:
                line.AttachmentSupport = [(axis_feat, "")]
                line.MapMode = "Tangent"
            else:
                warnings.append(
                    f"could not resolve the {spec['axis']} origin axis — datum "
                    "line left at the document origin.")
        else:  # points — place through p1 directed toward p2
            p1 = App.Vector(*spec["p1"])
            p2 = App.Vector(*spec["p2"])
            direction = p2.sub(p1)
            # PartDesign::Line lies along its local Z; orient local Z onto the
            # p1→p2 direction (no attachment in two-points mode).
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

# ── edit_sketch ────────────────────────────────────────────

def _handle_edit_sketch(
    sketch_name: str,
    add_geometries: list | None = None,
    remove_geometries: list | None = None,
    add_constraints: list | None = None,
    remove_constraints: list | None = None,
    clear_all: bool = False,
    label: str = "",
) -> ToolResult:
    """Add/remove geometry and constraints on an existing sketch."""
    import FreeCAD as App
    import Part
    import Sketcher

    def do(doc):
        sketch = _get_object(doc, sketch_name)
        if not sketch:
            hint = _suggest_similar(doc, sketch_name, "Sketcher")
            return ToolResult(success=False, output="",
                              error=f"Sketch '{sketch_name}' not found.{hint}")

        if not hasattr(sketch, "addGeometry"):
            return ToolResult(success=False, output="",
                              error=f"'{sketch_name}' is not a sketch.")

        geo_added = 0
        geo_removed = 0
        con_added = 0
        con_removed = 0
        warnings = []

        # ── Clear all geometry and constraints (for full replacement)
        if clear_all:
            while sketch.ConstraintCount > 0:
                sketch.delConstraint(sketch.ConstraintCount - 1)
                con_removed += 1
            while sketch.GeometryCount > 0:
                sketch.delGeometry(sketch.GeometryCount - 1)
                geo_removed += 1

        # ── Remove constraints first (before geometry removal shifts indices)
        if remove_constraints:
            indices = sorted((int(i) for i in remove_constraints), reverse=True)
            for idx in indices:
                if 0 <= idx < sketch.ConstraintCount:
                    sketch.delConstraint(idx)
                    con_removed += 1

        # ── Remove geometries (highest-first)
        if remove_geometries:
            indices = sorted((int(i) for i in remove_geometries), reverse=True)
            for idx in indices:
                if 0 <= idx < sketch.GeometryCount:
                    sketch.delGeometry(idx)
                    geo_removed += 1

        # ── Add geometries
        if add_geometries:
            for geo in add_geometries:
                if isinstance(geo, str):
                    try:
                        import json as _json
                        geo = _json.loads(geo)
                    except (ValueError, TypeError):
                        continue
                geo_type = geo.get("type", "")
                if geo_type == "line":
                    p1 = App.Vector(geo.get("x1", 0), geo.get("y1", 0), 0)
                    p2 = App.Vector(geo.get("x2", 0), geo.get("y2", 0), 0)
                    sketch.addGeometry(Part.LineSegment(p1, p2))
                    geo_added += 1
                elif geo_type == "circle":
                    cx = geo.get("cx", geo.get("x", 0))
                    cy = geo.get("cy", geo.get("y", 0))
                    r = geo.get("radius", 10)
                    sketch.addGeometry(Part.Circle(
                        App.Vector(cx, cy, 0), App.Vector(0, 0, 1), r))
                    geo_added += 1
                elif geo_type == "arc":
                    cx = geo.get("cx", geo.get("x", 0))
                    cy = geo.get("cy", geo.get("y", 0))
                    r = geo.get("radius", 10)
                    start_angle = geo.get("start_angle", 0)
                    end_angle = geo.get("end_angle", 3.14159)
                    sketch.addGeometry(Part.ArcOfCircle(
                        Part.Circle(App.Vector(cx, cy, 0), App.Vector(0, 0, 1), r),
                        start_angle, end_angle))
                    geo_added += 1
                elif geo_type == "rectangle":
                    rect_w = geo.get("width", None)
                    rect_h = geo.get("height", None) or geo.get("length", None)
                    if rect_w is not None and rect_h is not None:
                        x1 = geo.get("x", 0)
                        y1 = geo.get("y", 0)
                        x2 = x1 + float(rect_w)
                        y2 = y1 + float(rect_h)
                    else:
                        x1, y1 = geo.get("x1", 0), geo.get("y1", 0)
                        x2, y2 = geo.get("x2", 10), geo.get("y2", 10)
                    sketch.addGeometry(Part.LineSegment(App.Vector(x1, y1, 0), App.Vector(x2, y1, 0)))
                    sketch.addGeometry(Part.LineSegment(App.Vector(x2, y1, 0), App.Vector(x2, y2, 0)))
                    sketch.addGeometry(Part.LineSegment(App.Vector(x2, y2, 0), App.Vector(x1, y2, 0)))
                    sketch.addGeometry(Part.LineSegment(App.Vector(x1, y2, 0), App.Vector(x1, y1, 0)))
                    g = sketch.GeometryCount - 4
                    sketch.addConstraint(Sketcher.Constraint("Coincident", g, 2, g+1, 1))
                    sketch.addConstraint(Sketcher.Constraint("Coincident", g+1, 2, g+2, 1))
                    sketch.addConstraint(Sketcher.Constraint("Coincident", g+2, 2, g+3, 1))
                    sketch.addConstraint(Sketcher.Constraint("Coincident", g+3, 2, g, 1))
                    sketch.addConstraint(Sketcher.Constraint("Horizontal", g))
                    sketch.addConstraint(Sketcher.Constraint("Horizontal", g+2))
                    sketch.addConstraint(Sketcher.Constraint("Vertical", g+1))
                    sketch.addConstraint(Sketcher.Constraint("Vertical", g+3))
                    sketch.addConstraint(
                        Sketcher.Constraint("DistanceX", g, 1, g, 2, float(x2 - x1)))
                    sketch.addConstraint(
                        Sketcher.Constraint("DistanceY", g+1, 1, g+1, 2, float(y2 - y1)))
                    geo_added += 4
                elif geo_type == "polygon":
                    points = geo.get("points", [])
                    if len(points) >= 2:
                        for i in range(len(points)):
                            p1 = App.Vector(points[i][0], points[i][1], 0)
                            p2 = App.Vector(points[(i + 1) % len(points)][0],
                                            points[(i + 1) % len(points)][1], 0)
                            sketch.addGeometry(Part.LineSegment(p1, p2))
                            geo_added += 1
                        n = len(points)
                        base = sketch.GeometryCount - n
                        for i in range(n):
                            sketch.addConstraint(Sketcher.Constraint(
                                "Coincident", base + i, 2, base + (i + 1) % n, 1))

        # ── Add constraints
        if add_constraints:
            for con in add_constraints:
                if isinstance(con, str):
                    try:
                        import json as _json
                        con = _json.loads(con)
                    except (ValueError, TypeError):
                        continue
                con_type = con.get("type", "")
                if not con_type:
                    continue

                if "first" not in con and con_type not in ("Block",):
                    warnings.append(f"Skipped {con_type}: missing 'first' index")
                    continue

                # Remove existing constraint that matches this one
                # so the explicit constraint overwrites it.
                first = con.get("first", -2000)
                second = con.get("second", -2000)
                for ci in range(sketch.ConstraintCount - 1, -1, -1):
                    ec = sketch.Constraints[ci]
                    if (ec.Type == con_type
                            and ec.First == first
                            and ec.Second == second):
                        sketch.delConstraint(ci)
                        break

                args = [con_type]
                for key in ("first", "first_pos", "second", "second_pos", "value"):
                    if key in con:
                        v = con[key]
                        if key == "value":
                            args.append(float(v))
                        elif isinstance(v, float):
                            args.append(int(v))
                        else:
                            args.append(v)

                expr = con.get("expression")

                try:
                    ci = sketch.addConstraint(Sketcher.Constraint(*args))
                    con_added += 1
                    if expr:
                        sketch.setExpression(f"Constraints[{ci}]", expr)
                except Exception as e:
                    warnings.append(f"Failed {con_type}: {e}")

        if label:
            sketch.Label = label

        doc.recompute()

        # ── Report constraint status
        _DIMENSION_TYPES = {
            "Distance", "DistanceX", "DistanceY", "Radius", "Angle",
        }
        constraint_details = []
        try:
            for ci in range(sketch.ConstraintCount):
                c = sketch.Constraints[ci]
                if c.Type in _DIMENSION_TYPES:
                    constraint_details.append(
                        f"Constraints[{ci}]: {c.Type} = {c.Value}  <- bindable"
                    )
                else:
                    constraint_details.append(
                        f"Constraints[{ci}]: {c.Type}"
                    )
        except Exception:
            pass

        status = ""
        try:
            if sketch.FullyConstrained:
                status = " Fully constrained."
            else:
                dof = sketch.solve()
                if dof > 0:
                    status = f" Under-constrained ({dof} DOF remaining)."
                elif dof < 0:
                    status = " Over-constrained — remove redundant constraints."
                else:
                    status = " Fully constrained."
        except Exception:
            pass

        warn_info = ""
        if warnings:
            warn_info = "\nWarnings: " + "; ".join(warnings)

        constraint_info = ""
        if constraint_details:
            constraint_info = (
                "\nConstraints:\n"
                + "\n".join(f"  {d}" for d in constraint_details)
            )

        parts = []
        if geo_added or geo_removed:
            parts.append(f"geometry +{geo_added}/-{geo_removed}")
        if con_added or con_removed:
            parts.append(f"constraints +{con_added}/-{con_removed}")
        changes = ", ".join(parts) if parts else "no changes"

        return ToolResult(
            success=True,
            output=(f"Edited sketch '{sketch.Name}': {changes}. "
                    f"Total: {sketch.GeometryCount} geometries, "
                    f"{sketch.ConstraintCount} constraints.{status}"
                    f"{constraint_info}{warn_info}"),
            data={"name": sketch.Name,
                  "geo_added": geo_added, "geo_removed": geo_removed,
                  "con_added": con_added, "con_removed": con_removed,
                  "geometry_count": sketch.GeometryCount,
                  "constraint_count": sketch.ConstraintCount,
                  "constraints": constraint_details,
                  "fully_constrained": getattr(sketch, "FullyConstrained", None)},
        )

    return _with_undo("Edit Sketch", do)


EDIT_SKETCH = ToolDefinition(
    name="edit_sketch",
    description=(
        "Modify an existing sketch: add/remove geometry and constraints in one call. "
        "Geometry types: line, circle, arc, rectangle, polygon (same format as create_sketch). "
        "IMPORTANT: rectangles and circles auto-generate all necessary constraints "
        "(Coincident, Horizontal, Vertical, DistanceX, DistanceY, Radius). "
        "Do NOT add DistanceX/DistanceY/Radius constraints for rectangles or circles — "
        "they are already constrained by the geometry coordinates and dimensions you provide. "
        "Only add constraints for relationships BETWEEN geometries (e.g. Coincident to pin "
        "a circle center to a rectangle edge). "
        "COORDINATE SYSTEM: FreeCAD sketches use Y-up. When converting from SVG, images, "
        "or screen coordinates (which use Y-down), negate all Y values. "
        "BEST PRACTICE: To resize, move, or replace geometry, use clear_all=true and "
        "provide the complete new geometry in add_geometries. This avoids over-constraint "
        "issues. Incremental remove+add is error-prone because a rectangle is 4 lines "
        "(indices 0-3) with 10 constraints, not a single object."
    ),
    category="modeling",
    parameters=[
        ToolParam("sketch_name", "string", "Name of the sketch to edit", required=True),
        ToolParam("clear_all", "boolean",
                  "Clear ALL existing geometry and constraints before adding new ones. "
                  "Use this for resizing, repositioning, or replacing the sketch content. "
                  "The sketch object (and its attachment plane/body) is preserved.",
                  required=False, default=False),
        ToolParam("add_geometries", "array",
                  "Geometries to add. Same format as create_sketch: "
                  "line: {type:'line',x1,y1,x2,y2}, "
                  "rectangle: {type:'rectangle',x,y,width,height}, "
                  "circle: {type:'circle',cx,cy,radius}, "
                  "arc: {type:'arc',cx,cy,radius,start_angle,end_angle}, "
                  "polygon: {type:'polygon',points:[[x,y],...]}.",
                  required=False, items={"type": "object"}),
        ToolParam("remove_geometries", "array",
                  "Geometry indices to remove (0-based, highest-first).",
                  required=False, items={"type": "integer"}),
        ToolParam("add_constraints", "array",
                  "Constraints to add. Each has 'type' plus: "
                  "first (geometry index, required), first_pos (vertex: 1=start 2=end), "
                  "second (2nd geometry index), second_pos, value (for dimensional). "
                  "Optional 'expression' to bind dimensional value. "
                  "Examples: "
                  "{type:'Horizontal', first:0}, "
                  "{type:'Coincident', first:0, first_pos:2, second:1, second_pos:1}, "
                  "{type:'Distance', first:0, value:25.0}, "
                  "{type:'DistanceX', first:0, first_pos:1, second:0, second_pos:2, value:40, expression:'Vars.width'}.",
                  required=False, items={"type": "object"}),
        ToolParam("remove_constraints", "array",
                  "Constraint indices to remove (0-based, highest-first).",
                  required=False, items={"type": "integer"}),
        ToolParam("label", "string", "New label for the sketch", required=False, default=""),
    ],
    handler=_handle_edit_sketch,
)


# ── pad_sketch ──────────────────────────────────────────────

def _handle_pad_sketch(
    sketch_name: str,
    length: float | str = 10.0,
    symmetric: bool = False,
    label: str = "",
    body_name: str = "",
) -> ToolResult:
    """Pad (extrude) a sketch."""

    def do(doc):
        sketch = _get_object(doc, sketch_name)
        if not sketch:
            hint = _suggest_similar(doc, sketch_name, "Sketcher")
            return ToolResult(success=False, output="", error=f"Sketch '{sketch_name}' not found.{hint}")

        # Find the body — prefer explicit body_name, fall back to auto-detect
        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")
        else:
            body = _find_body_for(doc, sketch)
        if not body:
            return ToolResult(success=False, output="", error=f"No PartDesign body found for sketch '{sketch_name}'")

        pad = body.newObject("PartDesign::Pad", label or "Pad")
        pad.Profile = sketch
        # Length can be a number or an expression string (e.g. "Variables.height")
        length_expr = None
        if isinstance(length, str):
            length_expr = length
            pad.Length = 10  # placeholder
        else:
            pad.Length = length
        if length_expr:
            pad.setExpression("Length", length_expr)
        if symmetric:
            pad.Midplane = True
        sketch.Visibility = False

        return ToolResult(
            success=True,
            output=f"Padded sketch '{sketch.Name}' by {length}mm (pad name: '{pad.Name}')",
            data={"name": pad.Name, "label": pad.Label, "length": length},
        )

    return _with_undo("Pad Sketch", do)


PAD_SKETCH = ToolDefinition(
    name="pad_sketch",
    description=(
        "Pad (extrude) a sketch to create a solid. The sketch must be inside a PartDesign Body. "
        "Length can be an expression string for parametric models: length='Variables.height'."
    ),
    category="modeling",
    parameters=[
        ToolParam("sketch_name", "string", "Internal name of the sketch to pad"),
        ToolParam("length", "string", "Extrusion length in mm, or expression string (e.g. 'Variables.height')",
                  required=False, default=10.0),
        ToolParam("symmetric", "boolean", "Pad symmetrically in both directions", required=False, default=False),
        ToolParam("label", "string", "Display label for the pad feature", required=False, default=""),
        ToolParam("body_name", "string", "Explicit body name (use when multiple bodies exist)", required=False, default=""),
    ],
    handler=_handle_pad_sketch,
)


# ── pocket_sketch ───────────────────────────────────────────

def _handle_pocket_sketch(
    sketch_name: str,
    length: float = 10.0,
    through_all: bool = False,
    label: str = "",
    body_name: str = "",
) -> ToolResult:
    """Create a pocket (cut) from a sketch."""

    def do(doc):
        sketch = _get_object(doc, sketch_name)
        if not sketch:
            hint = _suggest_similar(doc, sketch_name, "Sketcher")
            return ToolResult(success=False, output="", error=f"Sketch '{sketch_name}' not found.{hint}")

        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")
        else:
            body = _find_body_for(doc, sketch)
        if not body:
            return ToolResult(success=False, output="", error=f"No PartDesign body found for sketch '{sketch_name}'")

        pocket = body.newObject("PartDesign::Pocket", label or "Pocket")
        pocket.Profile = sketch
        if through_all:
            pocket.Type = 1  # Through All
        else:
            pocket.Length = length

        # Auto-direction: try both directions, keep the one that removes
        # the most material.  This handles sketches at any Z-offset
        # (e.g. offset=3 vs offset=H) and through_all pockets where the
        # default direction may only graze a thin slab.
        vol_before = body.Shape.Volume if body.Shape else 0

        # Try default direction (Reversed=False)
        pocket.Reversed = False
        doc.recompute()
        vol_default = body.Shape.Volume if body.Shape and body.Shape.isValid() else vol_before
        ok_default = pocket.Shape and pocket.Shape.isValid() and vol_default > 0.001

        # Try reversed direction
        pocket.Reversed = True
        doc.recompute()
        vol_reversed = body.Shape.Volume if body.Shape and body.Shape.isValid() else vol_before
        ok_reversed = pocket.Shape and pocket.Shape.isValid() and vol_reversed > 0.001

        # Pick direction that removes the most material
        removed_default = (vol_before - vol_default) if ok_default else 0
        removed_reversed = (vol_before - vol_reversed) if ok_reversed else 0

        if removed_default >= removed_reversed:
            pocket.Reversed = False
            doc.recompute()

        sketch.Visibility = False

        return ToolResult(
            success=True,
            output=f"Created pocket from sketch '{sketch.Name}' (pocket name: '{pocket.Name}')",
            data={"name": pocket.Name, "label": pocket.Label},
        )

    return _with_undo("Pocket Sketch", do)


POCKET_SKETCH = ToolDefinition(
    name="pocket_sketch",
    description=(
        "Create a pocket (cut) from a sketch into the body's solid. "
        "Tip: for hollowing a box (e.g. enclosure), place the sketch at the top face "
        "using offset=H in create_sketch and set length=H-T (height minus wall thickness). "
        "The tool auto-detects the correct cut direction."
    ),
    category="modeling",
    parameters=[
        ToolParam("sketch_name", "string", "Internal name of the sketch to pocket"),
        ToolParam("length", "number", "Pocket depth in mm (prefer explicit depth over through_all)", required=False, default=10.0),
        ToolParam("through_all", "boolean", "Cut through the entire body (use only for holes, prefer explicit length for cavities)", required=False, default=False),
        ToolParam("label", "string", "Display label for the pocket feature", required=False, default=""),
        ToolParam("body_name", "string", "Explicit body name (use when multiple bodies exist)", required=False, default=""),
    ],
    handler=_handle_pocket_sketch,
)


# ── revolve_sketch ──────────────────────────────────────────

def _handle_revolve_sketch(
    sketch_name: str,
    axis: str = "Y",
    angle: float = 360.0,
    subtractive: bool = False,
    body_name: str = "",
    label: str = "",
) -> ToolResult:
    """Revolve a sketch around an axis (Revolution or Groove)."""

    def do(doc):
        sketch = _get_object(doc, sketch_name)
        if not sketch:
            hint = _suggest_similar(doc, sketch_name, "Sketcher")
            return ToolResult(success=False, output="", error=f"Sketch '{sketch_name}' not found.{hint}")

        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")
        else:
            body = _find_body_for(doc, sketch)
        if not body:
            return ToolResult(success=False, output="", error=f"No PartDesign body found for sketch '{sketch_name}'")

        # Resolve axis reference
        axis_upper = axis.upper()
        if axis_upper in ("X", "Y", "Z"):
            ref = (_get_body_axis(body, axis_upper), "")
        else:
            # Edge reference on the sketch: "Edge1", "Edge2", etc.
            ref = (sketch, [axis])

        type_name = "PartDesign::Groove" if subtractive else "PartDesign::Revolution"
        default_label = "Groove" if subtractive else "Revolution"
        feat = body.newObject(type_name, label or default_label)
        feat.Profile = sketch
        feat.ReferenceAxis = ref
        feat.Angle = angle
        sketch.Visibility = False

        return ToolResult(
            success=True,
            output=f"Revolved sketch '{sketch_name}' {angle}° around {axis}",
            data={"name": feat.Name, "label": feat.Label, "angle": angle, "axis": axis},
        )

    return _with_undo("Revolve Sketch", do)


REVOLVE_SKETCH = ToolDefinition(
    name="revolve_sketch",
    description="Revolve a sketch around an axis to create a solid of revolution (vase, bottle, wheel, etc). Uses PartDesign::Revolution (additive) or PartDesign::Groove (subtractive).",
    category="modeling",
    parameters=[
        ToolParam("sketch_name", "string", "Internal name of the sketch to revolve"),
        ToolParam("axis", "string", "Revolution axis: X, Y, Z (origin axes) or Edge1, Edge2... (sketch edge)",
                  required=False, default="Y"),
        ToolParam("angle", "number", "Revolution angle in degrees (360 = full revolution)",
                  required=False, default=360.0),
        ToolParam("subtractive", "boolean", "If true, use Groove (cut) instead of Revolution (add)",
                  required=False, default=False),
        ToolParam("body_name", "string", "Explicit body name (use when multiple bodies exist)",
                  required=False, default=""),
        ToolParam("label", "string", "Display label for the feature", required=False, default=""),
    ],
    handler=_handle_revolve_sketch,
)


# ── loft_sketches ──────────────────────────────────────────

def _handle_loft_sketches(
    section_names: list,
    closed: bool = False,
    ruled: bool = False,
    subtractive: bool = False,
    body_name: str = "",
    label: str = "",
) -> ToolResult:
    """Loft between two or more sketches (AdditiveLoft or SubtractiveLoft)."""

    def do(doc):
        if len(section_names) < 2:
            return ToolResult(
                success=False, output="",
                error=f"Loft requires at least 2 sections, got {len(section_names)}"
            )

        sections = []
        for name in section_names:
            s = _get_object(doc, name)
            if not s:
                hint = _suggest_similar(doc, name, "Sketcher")
                return ToolResult(success=False, output="", error=f"Section '{name}' not found.{hint}")
            sections.append(s)

        # Find the body
        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")
        else:
            body = _find_body_for(doc, sections[0])
        if not body:
            return ToolResult(success=False, output="", error=f"No PartDesign body found for section '{section_names[0]}'")

        # Verify all sections are in the same body
        for s in sections[1:]:
            sb = _find_body_for(doc, s)
            if sb is None or sb.Name != body.Name:
                return ToolResult(
                    success=False, output="",
                    error=f"Section '{s.Label}' is not in body '{body.Label}'. All sections must be in the same body."
                )

        type_name = "PartDesign::SubtractiveLoft" if subtractive else "PartDesign::AdditiveLoft"
        default_label = "SubtractiveLoft" if subtractive else "Loft"
        feat = body.newObject(type_name, label or default_label)
        feat.Profile = sections[0]
        feat.Sections = sections[1:]
        feat.Closed = closed
        feat.Ruled = ruled

        for s in sections:
            s.Visibility = False

        return ToolResult(
            success=True,
            output=f"Lofted {len(sections)} sections: {', '.join(section_names)}",
            data={"name": feat.Name, "label": feat.Label, "section_count": len(sections)},
        )

    return _with_undo("Loft Sketches", do)


LOFT_SKETCHES = ToolDefinition(
    name="loft_sketches",
    description="Loft between two or more sketches to create a smooth transitional solid (tapered shapes, bottles, organic forms). All sketches must be in the same PartDesign Body on different planes/offsets.",
    category="modeling",
    parameters=[
        ToolParam("section_names", "array", "Sketch names to loft between (minimum 2, ordered from start to end)",
                  items={"type": "string"}),
        ToolParam("closed", "boolean", "Close the loft loop (connect last section back to first)",
                  required=False, default=False),
        ToolParam("ruled", "boolean", "Use ruled (flat) surfaces instead of smooth",
                  required=False, default=False),
        ToolParam("subtractive", "boolean", "If true, cut instead of add",
                  required=False, default=False),
        ToolParam("body_name", "string", "Explicit body name (use when multiple bodies exist)",
                  required=False, default=""),
        ToolParam("label", "string", "Display label for the loft feature", required=False, default=""),
    ],
    handler=_handle_loft_sketches,
)


# ── sweep_sketch ───────────────────────────────────────────

def _handle_sweep_sketch(
    profile_name: str,
    spine_name: str,
    subtractive: bool = False,
    body_name: str = "",
    label: str = "",
) -> ToolResult:
    """Sweep a profile sketch along a spine path (AdditivePipe or SubtractivePipe)."""

    def do(doc):
        profile = _get_object(doc, profile_name)
        if not profile:
            hint = _suggest_similar(doc, profile_name, "Sketcher")
            return ToolResult(success=False, output="", error=f"Profile sketch '{profile_name}' not found.{hint}")

        spine = _get_object(doc, spine_name)
        if not spine:
            hint = _suggest_similar(doc, spine_name, "Sketcher")
            return ToolResult(success=False, output="", error=f"Spine sketch '{spine_name}' not found.{hint}")

        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")
        else:
            body = _find_body_for(doc, profile)
        if not body:
            return ToolResult(success=False, output="", error=f"No PartDesign body found for profile '{profile_name}'")

        # Verify spine is in the same body
        spine_body = _find_body_for(doc, spine)
        if spine_body is None or spine_body.Name != body.Name:
            return ToolResult(
                success=False, output="",
                error=f"Spine '{spine_name}' is not in body '{body.Label}'. Profile and spine must be in the same body."
            )

        type_name = "PartDesign::SubtractivePipe" if subtractive else "PartDesign::AdditivePipe"
        default_label = "SubtractiveSweep" if subtractive else "Sweep"
        feat = body.newObject(type_name, label or default_label)
        feat.Profile = profile
        feat.Spine = spine
        profile.Visibility = False
        spine.Visibility = False

        return ToolResult(
            success=True,
            output=f"Swept profile '{profile_name}' along spine '{spine_name}'",
            data={"name": feat.Name, "label": feat.Label},
        )

    return _with_undo("Sweep Sketch", do)


SWEEP_SKETCH = ToolDefinition(
    name="sweep_sketch",
    description="Sweep a profile sketch along a spine path to create a pipe, tube, or complex swept solid. Uses PartDesign::AdditivePipe (additive) or PartDesign::SubtractivePipe (subtractive). Both sketches must be in the same body.",
    category="modeling",
    parameters=[
        ToolParam("profile_name", "string", "Internal name of the cross-section sketch"),
        ToolParam("spine_name", "string", "Internal name of the path sketch (spine)"),
        ToolParam("subtractive", "boolean", "If true, cut instead of add",
                  required=False, default=False),
        ToolParam("body_name", "string", "Explicit body name (use when multiple bodies exist)",
                  required=False, default=""),
        ToolParam("label", "string", "Display label for the sweep feature", required=False, default=""),
    ],
    handler=_handle_sweep_sketch,
)


