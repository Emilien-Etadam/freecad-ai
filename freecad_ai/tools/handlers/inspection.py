"""FreeCAD inspection."""

import os

from ..registry import ToolParam, ToolDefinition, ToolResult
from ...core.executor import execute_code
from ..tool_common import *  # noqa: F403

# ── measure ─────────────────────────────────────────────────

def _handle_measure(
    measure_type: str,
    target: str = "",
    target2: str = "",
) -> ToolResult:
    """Measure properties of objects (volume, area, bounding box, distance)."""
    import FreeCAD as App

    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")

    if measure_type == "distance" and target and target2:
        obj1 = _get_object(doc, target)
        obj2 = _get_object(doc, target2)
        if not obj1 or not obj2:
            return ToolResult(success=False, output="", error="One or both objects not found")
        bb1 = obj1.Shape.BoundBox
        bb2 = obj2.Shape.BoundBox
        c1 = App.Vector(bb1.Center)
        c2 = App.Vector(bb2.Center)
        dist = c1.distanceToPoint(c2)
        return ToolResult(
            success=True,
            output=f"Distance between centers of '{obj1.Label}' and '{obj2.Label}': {dist:.3f}mm",
            data={"distance": dist},
        )

    obj = _get_object(doc, target) if target else None
    if not obj:
        return ToolResult(success=False, output="", error=f"Object '{target}' not found")

    if measure_type == "volume":
        vol = obj.Shape.Volume
        return ToolResult(
            success=True,
            output=f"Volume of '{obj.Label}': {vol:.3f} mm^3",
            data={"volume": vol},
        )
    elif measure_type == "area":
        area = obj.Shape.Area
        return ToolResult(
            success=True,
            output=f"Surface area of '{obj.Label}': {area:.3f} mm^2",
            data={"area": area},
        )
    elif measure_type == "bbox":
        bb = obj.Shape.BoundBox
        return ToolResult(
            success=True,
            output=(f"Bounding box of '{obj.Label}': "
                    f"X[{bb.XMin:.1f}, {bb.XMax:.1f}] "
                    f"Y[{bb.YMin:.1f}, {bb.YMax:.1f}] "
                    f"Z[{bb.ZMin:.1f}, {bb.ZMax:.1f}] "
                    f"Size: {bb.XLength:.1f} x {bb.YLength:.1f} x {bb.ZLength:.1f}mm"),
            data={
                "xmin": bb.XMin, "xmax": bb.XMax,
                "ymin": bb.YMin, "ymax": bb.YMax,
                "zmin": bb.ZMin, "zmax": bb.ZMax,
                "size_x": bb.XLength, "size_y": bb.YLength, "size_z": bb.ZLength,
            },
        )
    elif measure_type == "edges":
        count = len(obj.Shape.Edges)
        edge_info = [f"Edge{i+1}" for i in range(count)]
        return ToolResult(
            success=True,
            output=f"'{obj.Label}' has {count} edges: {', '.join(edge_info)}",
            data={"edge_count": count, "edges": edge_info},
        )
    else:
        return ToolResult(
            success=False, output="",
            error=f"Unknown measure type: {measure_type}. Use: volume, area, bbox, distance, edges"
        )


MEASURE = ToolDefinition(
    name="measure",
    description="Measure properties of objects: volume, surface area, bounding box, distance between objects, or list edges.",
    category="query",
    parameters=[
        ToolParam("measure_type", "string", "What to measure",
                  enum=["volume", "area", "bbox", "distance", "edges"]),
        ToolParam("target", "string", "Internal name of the object to measure"),
        ToolParam("target2", "string", "Second object (for distance measurements)", required=False, default=""),
    ],
    handler=_handle_measure,
)


# ── describe_model ──────────────────────────────────────────

def _handle_describe_model(object_name: str) -> ToolResult:
    """Return a comprehensive geometry summary of an object."""
    import FreeCAD as App

    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")

    obj = _get_object(doc, object_name)
    if not obj:
        hint = _suggest_similar(doc, object_name)
        return ToolResult(success=False, output="", error=f"Object '{object_name}' not found.{hint}")

    shape = getattr(obj, "Shape", None)
    if not shape:
        return ToolResult(success=False, output="",
                          error=f"Object '{object_name}' has no Shape")

    lines = [f"## Geometry of '{obj.Label}' ({obj.TypeId})"]

    # Bounding box
    bb = shape.BoundBox
    lines.append(f"**Bounding box:** {bb.XLength:.2f} x {bb.YLength:.2f} x {bb.ZLength:.2f} mm")
    lines.append(f"  X: [{bb.XMin:.2f}, {bb.XMax:.2f}]  Y: [{bb.YMin:.2f}, {bb.YMax:.2f}]  Z: [{bb.ZMin:.2f}, {bb.ZMax:.2f}]")

    # Volume and area
    if shape.Volume > 0:
        lines.append(f"**Volume:** {shape.Volume:.1f} mm\u00b3")
    if shape.Area > 0:
        lines.append(f"**Surface area:** {shape.Area:.1f} mm\u00b2")

    # Solid check
    lines.append(f"**Valid:** {shape.isValid()}")
    lines.append(f"**Solids:** {len(shape.Solids)}  **Shells:** {len(shape.Shells)}  **Faces:** {len(shape.Faces)}  **Edges:** {len(shape.Edges)}")

    # Hollow detection via comparing volume to bounding box volume
    bb_vol = bb.XLength * bb.YLength * bb.ZLength
    if bb_vol > 0 and shape.Volume > 0:
        fill_ratio = shape.Volume / bb_vol
        if fill_ratio < 0.5:
            lines.append(f"**Likely hollow** (fill ratio: {fill_ratio:.1%})")
        else:
            lines.append(f"**Likely solid** (fill ratio: {fill_ratio:.1%})")

    # Wall thickness estimation via ray casting from center
    try:
        center = App.Vector(bb.Center)
        thicknesses = []
        for direction in [App.Vector(1, 0, 0), App.Vector(0, 1, 0), App.Vector(0, 0, 1),
                          App.Vector(-1, 0, 0), App.Vector(0, -1, 0), App.Vector(0, 0, -1)]:
            # Cast ray from center outward, find first two face intersections
            # to estimate wall thickness
            try:
                hits = shape.distToShape(
                    shape.__class__.makeBox(0.01, 0.01, 0.01,
                                           center + direction * 500)
                )
            except Exception:
                continue
        # Alternative: use section cuts to estimate wall thickness
        for axis, offset in [("XY", bb.ZMax), ("XZ", bb.YMax), ("YZ", bb.XMax)]:
            try:
                if axis == "XY":
                    plane_base = App.Vector(0, 0, offset - 0.1)
                    plane_norm = App.Vector(0, 0, 1)
                elif axis == "XZ":
                    plane_base = App.Vector(0, offset - 0.1, 0)
                    plane_norm = App.Vector(0, 1, 0)
                else:
                    plane_base = App.Vector(offset - 0.1, 0, 0)
                    plane_norm = App.Vector(1, 0, 0)
                wires = shape.slice(plane_norm, offset - 0.1)
                if len(wires) >= 2:
                    # Two concentric wires = hollow with walls
                    bbs = sorted([w.BoundBox for w in wires],
                                 key=lambda b: b.XLength * b.YLength, reverse=True)
                    if len(bbs) >= 2:
                        outer = bbs[0]
                        inner = bbs[1]
                        wall_x = (outer.XLength - inner.XLength) / 2
                        wall_y = (outer.YLength - inner.YLength) / 2
                        if wall_x > 0.1 and wall_y > 0.1:
                            thicknesses.append(min(wall_x, wall_y))
            except Exception:
                continue
        if thicknesses:
            avg_wall = sum(thicknesses) / len(thicknesses)
            lines.append(f"**Estimated wall thickness:** ~{avg_wall:.1f} mm")
    except Exception:
        pass

    # PartDesign body features
    if obj.TypeId == "PartDesign::Body" and hasattr(obj, "Group"):
        features = [m for m in obj.Group if not m.TypeId.startswith("App::")]
        if features:
            lines.append(f"**Features ({len(features)}):**")
            for feat in features:
                feat_info = f"  - {feat.Name} ({feat.TypeId})"
                if hasattr(feat, "Length"):
                    feat_info += f" — Length: {float(feat.Length):.1f} mm"
                if hasattr(feat, "Radius"):
                    feat_info += f" — Radius: {float(feat.Radius):.1f} mm"
                lines.append(feat_info)

    output = "\n".join(lines)
    return ToolResult(success=True, output=output, data={"label": obj.Label})


DESCRIBE_MODEL = ToolDefinition(
    name="describe_model",
    description=(
        "Get a comprehensive geometry summary of an object: dimensions, volume, "
        "face/edge counts, hollow/solid detection, estimated wall thickness, "
        "and PartDesign feature list. Use this to inspect or verify a model."
    ),
    category="query",
    parameters=[
        ToolParam("object_name", "string", "Internal name or label of the object to describe"),
    ],
    handler=_handle_describe_model,
)


# ── list_faces ──────────────────────────────────────────────

def _classify_face(face, bbox) -> str:
    """Classify a face by its geometry and position relative to the object bounding box.

    Returns a human-readable label like 'top', 'bottom', 'front', etc.
    """
    surface = face.Surface
    surface_type = surface.__class__.__name__

    # Planar faces — classify by normal direction and position
    if surface_type == "Plane":
        normal = face.normalAt(0, 0)
        center = face.CenterOfMass
        tol = 0.1  # tolerance for axis-aligned detection

        # Determine which axis the normal is closest to
        abs_x, abs_y, abs_z = abs(normal.x), abs(normal.y), abs(normal.z)

        if abs_z > abs_x and abs_z > abs_y:
            # Z-axis face
            if normal.z > 0:
                return "top" if abs(center.z - bbox.ZMax) < tol else "horizontal"
            else:
                return "bottom" if abs(center.z - bbox.ZMin) < tol else "horizontal"
        elif abs_y > abs_x and abs_y > abs_z:
            # Y-axis face
            if normal.y > 0:
                return "back" if abs(center.y - bbox.YMax) < tol else "side"
            else:
                return "front" if abs(center.y - bbox.YMin) < tol else "side"
        elif abs_x > abs_y and abs_x > abs_z:
            # X-axis face
            if normal.x > 0:
                return "right" if abs(center.x - bbox.XMax) < tol else "side"
            else:
                return "left" if abs(center.x - bbox.XMin) < tol else "side"
        return "angled"

    elif surface_type == "Cylinder":
        radius = surface.Radius
        return f"cylindrical (R={radius:.1f})"
    elif surface_type == "Cone":
        return "conical"
    elif surface_type == "Sphere":
        radius = surface.Radius
        return f"spherical (R={radius:.1f})"
    elif surface_type == "Toroid":
        return "toroidal"
    else:
        return surface_type.lower()


def _handle_list_faces(object_name: str, filter: str = "") -> ToolResult:
    """List faces of an object, optionally filtered by keyword."""
    import FreeCAD as App

    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")

    obj = _get_object(doc, object_name)
    if not obj:
        hint = _suggest_similar(doc, object_name)
        return ToolResult(success=False, output="", error=f"Object '{object_name}' not found.{hint}")

    shape = getattr(obj, "Shape", None)
    if not shape:
        return ToolResult(success=False, output="",
                          error=f"Object '{object_name}' has no Shape")

    if not shape.Faces:
        return ToolResult(success=False, output="",
                          error=f"Object '{object_name}' has no faces")

    bbox = shape.BoundBox
    filter_lower = filter.strip().lower() if filter else ""

    lines = []
    face_data = []

    for i, face in enumerate(shape.Faces):
        name = f"Face{i + 1}"
        center = face.CenterOfMass
        area = face.Area
        label = _classify_face(face, bbox)

        # Apply filter if specified
        if filter_lower and filter_lower not in label.lower():
            continue

        surface_type = face.Surface.__class__.__name__

        # Get normal for planar faces
        normal_str = ""
        if surface_type == "Plane":
            n = face.normalAt(0, 0)
            normal_str = f"  normal=({n.x:.2f}, {n.y:.2f}, {n.z:.2f})"

        lines.append(
            f"- **{name}** \"{label}\" — center=({center.x:.1f}, {center.y:.1f}, {center.z:.1f}), "
            f"area={area:.1f}mm²{normal_str}"
        )

        face_data.append({
            "name": name,
            "label": label,
            "type": surface_type.lower(),
            "center": [round(center.x, 2), round(center.y, 2), round(center.z, 2)],
            "area": round(area, 2),
        })

    total = len(shape.Faces)
    shown = len(face_data)
    if filter_lower:
        header = f"## Faces of '{obj.Label}' matching '{filter}' ({shown}/{total} faces)"
    else:
        header = f"## Faces of '{obj.Label}' ({total} faces)"

    output = "\n".join([header] + lines)
    return ToolResult(success=True, output=output, data={"faces": face_data})


LIST_FACES = ToolDefinition(
    name="list_faces",
    description=(
        "List faces of an object with reference names (Face1, Face2, ...), "
        "human-readable labels (top, bottom, front, back, left, right, cylindrical), "
        "center positions, normals, and areas. Use this to identify which face to "
        "reference in shell_object, assembly constraints, or other face-based operations. "
        "Optional filter to show only matching faces (e.g. 'top', 'cylindrical')."
    ),
    category="query",
    parameters=[
        ToolParam("object_name", "string", "Internal name or label of the object"),
        ToolParam("filter", "string",
                  "Filter keyword to show only matching faces (e.g. 'top', 'cylindrical', 'side')",
                  required=False, default=""),
    ],
    handler=_handle_list_faces,
)


# ── list_edges ──────────────────────────────────────────────

def _classify_edge(edge, bbox) -> str:
    """Classify an edge by its geometry, direction, and position relative to the bounding box.

    Returns a human-readable label like 'top-front horizontal', 'front-left vertical', etc.
    """
    curve = edge.Curve
    curve_type = curve.__class__.__name__

    if curve_type not in ("Line", "LineSegment"):
        # Curved edges — report type and radius if available
        if curve_type in ("Circle", "ArcOfCircle"):
            return f"circular (R={curve.Radius:.1f})"
        elif curve_type == "BSplineCurve":
            return "spline"
        elif curve_type in ("Ellipse", "ArcOfEllipse"):
            return "elliptical"
        return curve_type.lower()

    # Straight edge — classify by direction and position
    mid = edge.CenterOfMass
    tol = 0.1
    length = edge.Length

    # Determine direction from start/end vertices
    p1 = edge.Vertexes[0].Point
    p2 = edge.Vertexes[1].Point
    dx = abs(p2.x - p1.x)
    dy = abs(p2.y - p1.y)
    dz = abs(p2.z - p1.z)
    max_d = max(dx, dy, dz)

    if max_d < tol:
        return "point"

    if dz / max_d > 0.9:
        direction = "vertical"
    elif dx / max_d > 0.9 and dy / max_d < 0.1:
        direction = "horizontal-X"
    elif dy / max_d > 0.9 and dx / max_d < 0.1:
        direction = "horizontal-Y"
    elif dz / max_d < 0.1:
        direction = "horizontal"
    else:
        direction = "diagonal"

    # Determine position labels from midpoint proximity to bounding box faces
    parts = []

    # Z position
    if abs(mid.z - bbox.ZMax) < tol:
        parts.append("top")
    elif abs(mid.z - bbox.ZMin) < tol:
        parts.append("bottom")

    # Y position
    if abs(mid.y - bbox.YMin) < tol:
        parts.append("front")
    elif abs(mid.y - bbox.YMax) < tol:
        parts.append("back")

    # X position
    if abs(mid.x - bbox.XMin) < tol:
        parts.append("left")
    elif abs(mid.x - bbox.XMax) < tol:
        parts.append("right")

    position = "-".join(parts) if parts else "interior"
    return f"{position} {direction}"


# ── Edge / face filter keywords ────────────────────────────
#
# Filter keywords that can be used instead of (or mixed with) explicit
# Edge/Face references in fillet_edges, chamfer_edges, shell_object, etc.
#
# Edge keywords: "all", "vertical", "horizontal", "top", "bottom",
#                "front", "back", "left", "right", "circular"
# Face keywords: "all", "top", "bottom", "front", "back", "left",
#                "right", "cylindrical", "spherical"

_EDGE_FILTER_KEYWORDS = {
    "all", "vertical", "horizontal", "top", "bottom",
    "front", "back", "left", "right", "circular",
}

_FACE_FILTER_KEYWORDS = {
    "all", "top", "bottom", "front", "back", "left", "right",
    "cylindrical", "spherical", "side",
}


def _resolve_edge_refs(shape, edge_input: list[str]) -> list[str]:
    """Resolve a mix of explicit edge names and filter keywords into edge names.

    Args:
        shape: FreeCAD Shape with .Edges and .BoundBox.
        edge_input: List of strings — Edge references ("Edge1") and/or
            filter keywords ("all", "vertical", "top", etc.).

    Returns:
        Sorted, deduplicated list of edge reference strings.
    """
    bbox = shape.BoundBox
    result = set()

    # Check if any element is a filter keyword
    has_filters = any(e.lower() in _EDGE_FILTER_KEYWORDS for e in edge_input)

    if not has_filters:
        # All explicit — return as-is
        return list(edge_input)

    for token in edge_input:
        token_lower = token.lower()
        if token_lower not in _EDGE_FILTER_KEYWORDS:
            # Explicit edge name — keep it
            result.add(token)
            continue

        if token_lower == "all":
            return [f"Edge{i + 1}" for i in range(len(shape.Edges))]

        # Filter by classification label
        for i, edge in enumerate(shape.Edges):
            label = _classify_edge(edge, bbox).lower()
            if token_lower == "circular":
                if "circular" in label:
                    result.add(f"Edge{i + 1}")
            elif token_lower in label:
                result.add(f"Edge{i + 1}")

    # Sort numerically: Edge1, Edge2, ..., Edge12
    return sorted(result, key=lambda e: int(e.replace("Edge", "")))


def _resolve_face_refs(shape, face_input: list[str]) -> list[str]:
    """Resolve a mix of explicit face names and filter keywords into face names.

    Args:
        shape: FreeCAD Shape with .Faces and .BoundBox.
        face_input: List of strings — Face references ("Face1") and/or
            filter keywords ("all", "top", "bottom", etc.).

    Returns:
        Sorted, deduplicated list of face reference strings.
    """
    bbox = shape.BoundBox
    result = set()

    has_filters = any(f.lower() in _FACE_FILTER_KEYWORDS for f in face_input)

    if not has_filters:
        return list(face_input)

    for token in face_input:
        token_lower = token.lower()
        if token_lower not in _FACE_FILTER_KEYWORDS:
            result.add(token)
            continue

        if token_lower == "all":
            return [f"Face{i + 1}" for i in range(len(shape.Faces))]

        for i, face in enumerate(shape.Faces):
            label = _classify_face(face, bbox).lower()
            if token_lower in label:
                result.add(f"Face{i + 1}")

    return sorted(result, key=lambda f: int(f.replace("Face", "")))


def _handle_list_edges(object_name: str, filter: str = "") -> ToolResult:
    """List edges of an object, optionally filtered by keyword."""
    import FreeCAD as App

    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")

    obj = _get_object(doc, object_name)
    if not obj:
        hint = _suggest_similar(doc, object_name)
        return ToolResult(success=False, output="", error=f"Object '{object_name}' not found.{hint}")

    shape = getattr(obj, "Shape", None)
    if not shape:
        return ToolResult(success=False, output="",
                          error=f"Object '{object_name}' has no Shape")

    if not shape.Edges:
        return ToolResult(success=False, output="",
                          error=f"Object '{object_name}' has no edges")

    bbox = shape.BoundBox
    filter_lower = filter.strip().lower() if filter else ""

    lines = []
    edge_data = []

    for i, edge in enumerate(shape.Edges):
        name = f"Edge{i + 1}"
        mid = edge.CenterOfMass
        length = edge.Length
        label = _classify_edge(edge, bbox)

        # Apply filter if specified
        if filter_lower and filter_lower not in label.lower():
            continue

        lines.append(
            f"- **{name}** \"{label}\" — midpoint=({mid.x:.1f}, {mid.y:.1f}, {mid.z:.1f}), "
            f"length={length:.1f}mm"
        )

        edge_data.append({
            "name": name,
            "label": label,
            "midpoint": [round(mid.x, 2), round(mid.y, 2), round(mid.z, 2)],
            "length": round(length, 2),
        })

    total = len(shape.Edges)
    shown = len(edge_data)
    if filter_lower:
        header = f"## Edges of '{obj.Label}' matching '{filter}' ({shown}/{total} edges)"
    else:
        header = f"## Edges of '{obj.Label}' ({total} edges)"

    output = "\n".join([header] + lines)
    return ToolResult(success=True, output=output, data={"edges": edge_data})


LIST_EDGES = ToolDefinition(
    name="list_edges",
    description=(
        "List edges of an object with reference names (Edge1, Edge2, ...), "
        "human-readable labels (top-front horizontal, front-left vertical, circular, etc.), "
        "midpoint positions, and lengths. Use this to identify which edges to "
        "reference in fillet_edges, chamfer_edges, or other edge-based operations. "
        "Optional filter to show only matching edges (e.g. 'vertical', 'top', 'circular')."
    ),
    category="query",
    parameters=[
        ToolParam("object_name", "string", "Internal name or label of the object"),
        ToolParam("filter", "string",
                  "Filter keyword to show only matching edges (e.g. 'vertical', 'top', 'circular')",
                  required=False, default=""),
    ],
    handler=_handle_list_edges,
)


