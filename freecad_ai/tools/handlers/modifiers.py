"""FreeCAD modifiers."""

import os

from ..registry import ToolParam, ToolDefinition, ToolResult
from ...core.executor import execute_code
from ..tool_common import *  # noqa: F403

# ── boolean_operation ───────────────────────────────────────

def _handle_boolean_operation(
    operation: str,
    object1: str,
    object2: str,
    label: str = "",
) -> ToolResult:
    """Perform a boolean operation (fuse/cut/common) between two objects."""

    def do(doc):
        obj1 = _get_object(doc, object1)
        obj2 = _get_object(doc, object2)
        if not obj1:
            hint = _suggest_similar(doc, object1)
            return ToolResult(success=False, output="", error=f"Object '{object1}' not found.{hint}")
        if not obj2:
            hint = _suggest_similar(doc, object2)
            return ToolResult(success=False, output="", error=f"Object '{object2}' not found.{hint}")

        op = operation.lower()
        if op not in ("fuse", "cut", "common"):
            return ToolResult(
                success=False, output="",
                error=f"Unknown operation: {operation}. Use: fuse, cut, common",
            )

        # When BOTH operands are PartDesign Bodies, use a parametric
        # PartDesign::Boolean inside the base Body instead of a Part:: boolean.
        # A Part::Cut/Fuse/Common claims its operands as tree children,
        # reparenting the base Body under the new node — its sketches and
        # features become buried and uneditable (issue #17). PartDesign::Boolean
        # appends the operation to the base Body's feature history, so the Body
        # stays top-level and fully editable.
        if obj1.TypeId == "PartDesign::Body" and obj2.TypeId == "PartDesign::Body":
            pd_type_map = {"fuse": "Fuse", "cut": "Cut", "common": "Common"}
            boolean = obj1.newObject("PartDesign::Boolean", label or operation.capitalize())
            boolean.Type = pd_type_map[op]
            boolean.addObjects([obj2])
            return ToolResult(
                success=True,
                output=(f"Boolean {operation} of '{obj1.Label}' and '{obj2.Label}' "
                        f"(parametric PartDesign feature inside '{obj1.Label}' — "
                        f"history preserved)"),
                data={"name": boolean.Name, "label": boolean.Label,
                      "base_body": obj1.Name},
            )

        # Otherwise (one or both operands are plain Part shapes) fall back to a
        # generic Part:: boolean, which works on any shape.
        op_map = {
            "fuse": "Part::Fuse",
            "cut": "Part::Cut",
            "common": "Part::Common",
        }
        part_type = op_map[op]
        name = label or operation.capitalize()
        result_obj = doc.addObject(part_type, name)
        result_obj.Base = obj1
        result_obj.Tool = obj2

        return ToolResult(
            success=True,
            output=f"Boolean {operation} of '{obj1.Label}' and '{obj2.Label}'",
            data={"name": result_obj.Name, "label": result_obj.Label},
        )

    return _with_undo(f"Boolean {operation}", do)


BOOLEAN_OPERATION = ToolDefinition(
    name="boolean_operation",
    description=(
        "Boolean operation (fuse/cut/common) between two SEPARATE objects. "
        "If both operands are PartDesign Bodies, this automatically uses a "
        "parametric PartDesign::Boolean inside the first Body, preserving its "
        "editable feature history. For plain Part shapes it uses a Part:: "
        "boolean, which consumes both operands into the result. "
        "Do NOT use this to drill a hole / cut a feature into a SINGLE existing "
        "solid — for that, add a feature inside that Body instead: "
        "create_primitive(operation='subtractive', body_name=...) or "
        "pocket_sketch."
    ),
    category="modeling",
    parameters=[
        ToolParam("operation", "string", "Boolean operation type", enum=["fuse", "cut", "common"]),
        ToolParam("object1", "string", "Internal name of the first object (base for cut)"),
        ToolParam("object2", "string", "Internal name of the second object (tool for cut)"),
        ToolParam("label", "string", "Display label for the result", required=False, default=""),
    ],
    handler=_handle_boolean_operation,
)


# ── transform_object ────────────────────────────────────────

def _handle_transform_object(
    object_name: str,
    translate_x: float = 0.0,
    translate_y: float = 0.0,
    translate_z: float = 0.0,
    rotate_axis_x: float = 0.0,
    rotate_axis_y: float = 0.0,
    rotate_axis_z: float = 1.0,
    rotate_angle: float = 0.0,
) -> ToolResult:
    """Move and/or rotate an object."""
    import FreeCAD as App

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            hint = _suggest_similar(doc, object_name)
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found.{hint}")

        placement = App.Placement(
            App.Vector(translate_x, translate_y, translate_z),
            App.Rotation(App.Vector(rotate_axis_x, rotate_axis_y, rotate_axis_z), rotate_angle),
        )
        obj.Placement = placement

        parts = []
        if translate_x or translate_y or translate_z:
            parts.append(f"moved to ({translate_x}, {translate_y}, {translate_z})")
        if rotate_angle:
            parts.append(f"rotated {rotate_angle} degrees")
        desc = ", ".join(parts) if parts else "placement reset"

        return ToolResult(
            success=True,
            output=f"Transformed '{obj.Label}': {desc}",
            data={"name": obj.Name},
        )

    return _with_undo("Transform Object", do)


TRANSFORM_OBJECT = ToolDefinition(
    name="transform_object",
    description="Move and/or rotate an object by setting its Placement.",
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
    ],
    handler=_handle_transform_object,
)


# ── fillet_edges ────────────────────────────────────────────

def _handle_fillet_edges(
    object_name: str,
    edges: list | None = None,
    radius: float = 1.0,
    label: str = "",
) -> ToolResult:
    """Apply fillet to edges of an object."""

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            hint = _suggest_similar(doc, object_name)
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found.{hint}")

        raw_refs = _coerce_str_list(edges) or ["Edge1"]

        # Check if this is a PartDesign body/feature
        # If obj IS a Body, use its Tip (last feature) as the fillet base
        body = None
        base_feature = obj
        if hasattr(obj, "TypeId") and obj.TypeId == "PartDesign::Body":
            body = obj
            base_feature = obj.Tip
            if not base_feature:
                return ToolResult(success=False, output="",
                                  error=f"Body '{obj.Label}' has no features to fillet.")
        else:
            body = _find_body_for(doc, obj)

        # Resolve filter keywords (all, vertical, top, etc.) into edge names
        edge_refs = _resolve_edge_refs(obj.Shape, raw_refs)
        if not edge_refs:
            return ToolResult(success=False, output="",
                              error=f"No edges match filter {raw_refs} on '{obj.Label}'.")

        if body:
            fillet = body.newObject("PartDesign::Fillet", label or "Fillet")
            fillet.Base = (base_feature, edge_refs)
            fillet.Radius = radius
        else:
            fillet = doc.addObject("Part::Fillet", label or "Fillet")
            fillet.Base = obj
            fillet.Shape = obj.Shape.makeFillet(radius, [
                obj.Shape.Edges[int(e.replace("Edge", "")) - 1] for e in edge_refs
            ])

        return ToolResult(
            success=True,
            output=f"Applied fillet (r={radius}mm) to {len(edge_refs)} edge(s) of '{obj.Label}'",
            data={"name": fillet.Name, "label": fillet.Label, "radius": radius,
                  "edges": edge_refs},
        )

    return _with_undo("Fillet Edges", do)


FILLET_EDGES = ToolDefinition(
    name="fillet_edges",
    description=(
        "Apply a fillet (rounded edge) to one or more edges of an object. "
        "Edges can be explicit names (Edge1, Edge4) or filter keywords: "
        "'all', 'vertical', 'horizontal', 'top', 'bottom', 'front', 'back', "
        "'left', 'right', 'circular'. Filters can be combined: ['top', 'vertical']."
    ),
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object"),
        ToolParam("edges", "array",
                  "Edge references or filter keywords, e.g. ['all'], ['vertical'], ['Edge1', 'Edge4']",
                  required=False, items={"type": "string"}),
        ToolParam("radius", "number", "Fillet radius in mm", required=False, default=1.0),
        ToolParam("label", "string", "Display label for the fillet", required=False, default=""),
    ],
    handler=_handle_fillet_edges,
)


# ── chamfer_edges ───────────────────────────────────────────

def _handle_chamfer_edges(
    object_name: str,
    edges: list | None = None,
    size: float = 1.0,
    label: str = "",
) -> ToolResult:
    """Apply chamfer to edges of an object."""

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            hint = _suggest_similar(doc, object_name)
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found.{hint}")

        raw_refs = _coerce_str_list(edges) or ["Edge1"]

        # If obj IS a Body, use its Tip (last feature) as the chamfer base
        body = None
        base_feature = obj
        if hasattr(obj, "TypeId") and obj.TypeId == "PartDesign::Body":
            body = obj
            base_feature = obj.Tip
            if not base_feature:
                return ToolResult(success=False, output="",
                                  error=f"Body '{obj.Label}' has no features to chamfer.")
        else:
            body = _find_body_for(doc, obj)

        # Resolve filter keywords
        edge_refs = _resolve_edge_refs(obj.Shape, raw_refs)
        if not edge_refs:
            return ToolResult(success=False, output="",
                              error=f"No edges match filter {raw_refs} on '{obj.Label}'.")

        if body:
            chamfer = body.newObject("PartDesign::Chamfer", label or "Chamfer")
            chamfer.Base = (base_feature, edge_refs)
            chamfer.Size = size
        else:
            chamfer = doc.addObject("Part::Chamfer", label or "Chamfer")
            chamfer.Base = obj
            chamfer.Shape = obj.Shape.makeChamfer(size, [
                obj.Shape.Edges[int(e.replace("Edge", "")) - 1] for e in edge_refs
            ])

        return ToolResult(
            success=True,
            output=f"Applied chamfer (size={size}mm) to {len(edge_refs)} edge(s) of '{obj.Label}'",
            data={"name": chamfer.Name, "label": chamfer.Label, "size": size,
                  "edges": edge_refs},
        )

    return _with_undo("Chamfer Edges", do)


CHAMFER_EDGES = ToolDefinition(
    name="chamfer_edges",
    description=(
        "Apply a chamfer (angled edge cut) to one or more edges of an object. "
        "Edges can be explicit names (Edge1, Edge4) or filter keywords: "
        "'all', 'vertical', 'horizontal', 'top', 'bottom', 'front', 'back', "
        "'left', 'right', 'circular'. Filters can be combined: ['top', 'vertical']."
    ),
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object"),
        ToolParam("edges", "array",
                  "Edge references or filter keywords, e.g. ['all'], ['vertical'], ['Edge1', 'Edge4']",
                  required=False, items={"type": "string"}),
        ToolParam("size", "number", "Chamfer size in mm", required=False, default=1.0),
        ToolParam("label", "string", "Display label for the chamfer", required=False, default=""),
    ],
    handler=_handle_chamfer_edges,
)


# ── scale_object ──────────────────────────────────────────

def _handle_scale_object(
    object_name: str,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    scale_z: float = 1.0,
    uniform: float = 0.0,
    copy: bool = False,
    label: str = "",
) -> ToolResult:
    """Scale an object non-uniformly via shape.transformGeometry()."""
    import FreeCAD as App

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found")

        if hasattr(obj, "TypeId") and obj.TypeId == "PartDesign::Body":
            return ToolResult(
                success=False, output="",
                error="Cannot scale a PartDesign::Body directly. Scale individual Part objects instead."
            )

        if not hasattr(obj, "Shape"):
            return ToolResult(success=False, output="", error=f"Object '{object_name}' has no Shape")

        sx = uniform if uniform != 0 else scale_x
        sy = uniform if uniform != 0 else scale_y
        sz = uniform if uniform != 0 else scale_z

        mat = App.Matrix()
        mat.scale(sx, sy, sz)
        new_shape = obj.Shape.transformGeometry(mat)

        if copy:
            new_name = label or f"{obj.Label}_Scaled"
            new_obj = doc.addObject("Part::Feature", new_name)
            new_obj.Label = new_name
            new_obj.Shape = new_shape
            return ToolResult(
                success=True,
                output=f"Created scaled copy '{new_obj.Label}' (scale: {sx}, {sy}, {sz})",
                data={"name": new_obj.Name, "label": new_obj.Label},
            )
        else:
            obj.Shape = new_shape
            return ToolResult(
                success=True,
                output=f"Scaled '{obj.Label}' by ({sx}, {sy}, {sz})",
                data={"name": obj.Name, "label": obj.Label},
            )

    return _with_undo("Scale Object", do)


SCALE_OBJECT = ToolDefinition(
    name="scale_object",
    description="Scale an object uniformly or non-uniformly. Works on Part objects (not PartDesign bodies). Set uniform>0 to scale all axes equally.",
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object to scale"),
        ToolParam("scale_x", "number", "X scale factor", required=False, default=1.0),
        ToolParam("scale_y", "number", "Y scale factor", required=False, default=1.0),
        ToolParam("scale_z", "number", "Z scale factor", required=False, default=1.0),
        ToolParam("uniform", "number", "Uniform scale (overrides x/y/z if non-zero)", required=False, default=0.0),
        ToolParam("copy", "boolean", "Create a scaled copy instead of modifying in-place", required=False, default=False),
        ToolParam("label", "string", "Label for the copy (only used with copy=True)", required=False, default=""),
    ],
    handler=_handle_scale_object,
)


# ── section_object ────────────────────────────────────────

def _handle_section_object(
    object_name: str,
    tool_object: str = "",
    plane: str = "XY",
    offset: float = 0.0,
    label: str = "",
) -> ToolResult:
    """Create a cross-section of an object."""
    import FreeCAD as App
    import Part

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found")

        if tool_object:
            # Shape-vs-shape section
            tool = _get_object(doc, tool_object)
            if not tool:
                return ToolResult(success=False, output="", error=f"Tool object '{tool_object}' not found")
            name = label or "Section"
            sec = doc.addObject("Part::Section", name)
            sec.Base = obj
            sec.Tool = tool
            return ToolResult(
                success=True,
                output=f"Created section of '{obj.Label}' with '{tool.Label}'",
                data={"name": sec.Name, "label": sec.Label},
            )

        # Shape-vs-plane section
        bb = obj.Shape.BoundBox
        size = max(bb.XLength, bb.YLength, bb.ZLength) * 2 + 10

        plane_upper = plane.upper()
        if plane_upper == "XY":
            origin = App.Vector(bb.XMin - 5, bb.YMin - 5, offset)
            normal = App.Vector(0, 0, 1)
        elif plane_upper == "XZ":
            origin = App.Vector(bb.XMin - 5, offset, bb.ZMin - 5)
            normal = App.Vector(0, 1, 0)
        elif plane_upper == "YZ":
            origin = App.Vector(offset, bb.YMin - 5, bb.ZMin - 5)
            normal = App.Vector(1, 0, 0)
        else:
            return ToolResult(success=False, output="", error=f"Unknown plane: {plane}. Use XY, XZ, or YZ")

        cut_plane = Part.makePlane(size, size, origin, normal)
        section_shape = obj.Shape.section(cut_plane)

        name = label or "Section"
        sec_obj = doc.addObject("Part::Feature", name)
        sec_obj.Label = name
        sec_obj.Shape = section_shape

        edge_count = len(section_shape.Edges)
        return ToolResult(
            success=True,
            output=f"Created {plane_upper} section of '{obj.Label}' at offset={offset}mm ({edge_count} edges)",
            data={"name": sec_obj.Name, "label": sec_obj.Label, "edge_count": edge_count,
                  "bbox": {"xmin": section_shape.BoundBox.XMin, "xmax": section_shape.BoundBox.XMax,
                           "ymin": section_shape.BoundBox.YMin, "ymax": section_shape.BoundBox.YMax,
                           "zmin": section_shape.BoundBox.ZMin, "zmax": section_shape.BoundBox.ZMax}},
        )

    return _with_undo("Section Object", do)


SECTION_OBJECT = ToolDefinition(
    name="section_object",
    description="Create a cross-section: either cut an object with a plane (XY/XZ/YZ at a given offset) or intersect two shapes.",
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object to section"),
        ToolParam("tool_object", "string", "Second object for shape-vs-shape section (omit for plane section)", required=False, default=""),
        ToolParam("plane", "string", "Section plane (used when tool_object is omitted)", required=False, default="XY",
                  enum=["XY", "XZ", "YZ"]),
        ToolParam("offset", "number", "Offset along the plane normal (e.g. z-height for XY plane)", required=False, default=0.0),
        ToolParam("label", "string", "Display label for the section", required=False, default=""),
    ],
    handler=_handle_section_object,
)


# ── linear_pattern ────────────────────────────────────────

def _handle_linear_pattern(
    feature_name: str,
    direction: str = "X",
    length: float = 10.0,
    occurrences: int = 2,
    label: str = "",
) -> ToolResult:
    """Create a PartDesign::LinearPattern repeating a feature along an axis."""

    def do(doc):
        feature = _get_object(doc, feature_name)
        if not feature:
            return ToolResult(success=False, output="", error=f"Feature '{feature_name}' not found")

        body = _find_body_for(doc, feature)
        if not body:
            return ToolResult(
                success=False, output="",
                error=f"Feature '{feature_name}' is not inside a PartDesign body. Linear pattern requires a body."
            )

        name = label or "LinearPattern"
        pattern = body.newObject("PartDesign::LinearPattern", name)
        pattern.Originals = [feature]

        # Resolve direction
        dir_upper = direction.upper()
        if dir_upper in ("X", "Y", "Z"):
            axis = _get_body_axis(body, dir_upper)
            if not axis:
                return ToolResult(success=False, output="", error=f"Could not find {dir_upper} axis on body")
            pattern.Direction = (axis, [""])
        else:
            # Sketch edge reference like "Sketch.Edge1"
            parts = direction.split(".")
            if len(parts) == 2:
                ref_obj = _get_object(doc, parts[0])
                if ref_obj:
                    pattern.Direction = (ref_obj, [parts[1]])
                else:
                    return ToolResult(success=False, output="", error=f"Reference object '{parts[0]}' not found")
            else:
                return ToolResult(success=False, output="", error=f"Invalid direction: {direction}. Use X/Y/Z or Sketch.Edge1")

        pattern.Length = length
        pattern.Occurrences = occurrences

        return ToolResult(
            success=True,
            output=f"Created linear pattern of '{feature_name}' ({occurrences} occurrences, {length}mm span, direction={direction})",
            data={"name": pattern.Name, "label": pattern.Label, "occurrences": occurrences},
        )

    return _with_undo("Linear Pattern", do)


LINEAR_PATTERN = ToolDefinition(
    name="linear_pattern",
    description="Repeat a PartDesign feature in a linear pattern along an axis. The feature must be inside a PartDesign Body.",
    category="modeling",
    parameters=[
        ToolParam("feature_name", "string", "Internal name of the feature to repeat"),
        ToolParam("direction", "string", "Pattern direction: X, Y, Z (origin axes) or Sketch.Edge1 (sketch edge)", required=False, default="X"),
        ToolParam("length", "number", "Total span of the pattern in mm"),
        ToolParam("occurrences", "integer", "Number of occurrences (including the original)"),
        ToolParam("label", "string", "Display label for the pattern", required=False, default=""),
    ],
    handler=_handle_linear_pattern,
)


# ── polar_pattern ─────────────────────────────────────────

def _handle_polar_pattern(
    feature_name: str,
    axis: str = "Z",
    angle: float = 360.0,
    occurrences: int = 2,
    label: str = "",
) -> ToolResult:
    """Create a PartDesign::PolarPattern repeating a feature around an axis."""

    def do(doc):
        feature = _get_object(doc, feature_name)
        if not feature:
            return ToolResult(success=False, output="", error=f"Feature '{feature_name}' not found")

        body = _find_body_for(doc, feature)
        if not body:
            return ToolResult(
                success=False, output="",
                error=f"Feature '{feature_name}' is not inside a PartDesign body. Polar pattern requires a body."
            )

        name = label or "PolarPattern"
        pattern = body.newObject("PartDesign::PolarPattern", name)
        pattern.Originals = [feature]

        # Resolve axis
        axis_upper = axis.upper()
        if axis_upper in ("X", "Y", "Z"):
            axis_obj = _get_body_axis(body, axis_upper)
            if not axis_obj:
                return ToolResult(success=False, output="", error=f"Could not find {axis_upper} axis on body")
            pattern.Axis = (axis_obj, [""])
        else:
            parts = axis.split(".")
            if len(parts) == 2:
                ref_obj = _get_object(doc, parts[0])
                if ref_obj:
                    pattern.Axis = (ref_obj, [parts[1]])
                else:
                    return ToolResult(success=False, output="", error=f"Reference object '{parts[0]}' not found")
            else:
                return ToolResult(success=False, output="", error=f"Invalid axis: {axis}. Use X/Y/Z or Sketch.Edge1")

        pattern.Angle = angle
        pattern.Occurrences = occurrences

        return ToolResult(
            success=True,
            output=f"Created polar pattern of '{feature_name}' ({occurrences} occurrences, {angle}° span, axis={axis})",
            data={"name": pattern.Name, "label": pattern.Label, "occurrences": occurrences},
        )

    return _with_undo("Polar Pattern", do)


POLAR_PATTERN = ToolDefinition(
    name="polar_pattern",
    description="Repeat a PartDesign feature in a circular pattern around an axis. The feature must be inside a PartDesign Body.",
    category="modeling",
    parameters=[
        ToolParam("feature_name", "string", "Internal name of the feature to repeat"),
        ToolParam("axis", "string", "Rotation axis: X, Y, Z (origin axes) or Sketch.Edge1 (sketch edge)", required=False, default="Z"),
        ToolParam("angle", "number", "Total angular span in degrees (360 = full circle)", required=False, default=360.0),
        ToolParam("occurrences", "integer", "Number of occurrences (including the original)"),
        ToolParam("label", "string", "Display label for the pattern", required=False, default=""),
    ],
    handler=_handle_polar_pattern,
)


# ── shell_object ───────────────────────────────────────────

def _handle_shell_object(
    object_name: str,
    faces: list | None = None,
    thickness: float = 1.0,
    join: str = "Arc",
    reversed: bool = True,
    label: str = "",
) -> ToolResult:
    """Hollow out a solid by removing faces and applying wall thickness."""

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found")

        raw_refs = _coerce_str_list(faces) or ["Face1"]
        join_map = {"Arc": 0, "Intersection": 1}

        # If obj IS a Body, use its Tip (last feature) as the shell base
        body = None
        base_feature = obj
        if hasattr(obj, "TypeId") and obj.TypeId == "PartDesign::Body":
            body = obj
            base_feature = obj.Tip
            if not base_feature:
                return ToolResult(success=False, output="",
                                  error=f"Body '{obj.Label}' has no features to shell.")
        else:
            body = _find_body_for(doc, obj)

        # Resolve filter keywords
        face_refs = _resolve_face_refs(obj.Shape, raw_refs)
        if not face_refs:
            return ToolResult(success=False, output="",
                              error=f"No faces match filter {raw_refs} on '{obj.Label}'.")

        if body:
            shell = body.newObject("PartDesign::Thickness", label or "Shell")
            shell.Base = (base_feature, face_refs)
            shell.Value = thickness
            shell.Join = join_map.get(join, 0)
            shell.Reversed = reversed
        else:
            return ToolResult(
                success=False, output="",
                error=f"Object '{object_name}' is not inside a PartDesign Body. "
                      "shell_object requires a PartDesign Body. Use create_body + create_sketch + pad_sketch "
                      "to create the solid, then apply shell_object.",
            )

        return ToolResult(
            success=True,
            output=f"Applied shell (thickness={thickness}mm) to '{obj.Label}' removing {len(face_refs)} face(s)",
            data={"name": shell.Name, "label": shell.Label, "thickness": thickness},
        )

    return _with_undo("Shell Object", do)


SHELL_OBJECT = ToolDefinition(
    name="shell_object",
    description=(
        "Hollow out a solid by removing selected faces and applying a wall thickness "
        "(PartDesign::Thickness). Faces can be explicit names (Face1, Face6) or filter "
        "keywords: 'top', 'bottom', 'front', 'back', 'left', 'right', 'cylindrical'."
    ),
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the solid object to shell"),
        ToolParam("faces", "array",
                  "Face references or filter keywords, e.g. ['top'], ['Face1', 'Face6']",
                  required=False, items={"type": "string"}),
        ToolParam("thickness", "number", "Wall thickness in mm", required=False, default=1.0),
        ToolParam("join", "string", "Join type for corners", required=False, default="Arc",
                  enum=["Arc", "Intersection"]),
        ToolParam("reversed", "boolean", "Shell direction: True (default) = inward (preserves outer dimensions), False = outward", required=False, default=True),
        ToolParam("label", "string", "Display label for the shell feature", required=False, default=""),
    ],
    handler=_handle_shell_object,
)


# ── mirror_feature ─────────────────────────────────────────

def _handle_mirror_feature(
    feature_name: str,
    plane: str = "YZ",
    label: str = "",
) -> ToolResult:
    """Mirror a PartDesign feature across a plane."""

    def do(doc):
        feature = _get_object(doc, feature_name)
        if not feature:
            return ToolResult(success=False, output="", error=f"Feature '{feature_name}' not found")

        body = _find_body_for(doc, feature)
        if not body:
            return ToolResult(
                success=False, output="",
                error=f"Feature '{feature_name}' is not inside a PartDesign Body",
            )

        # Only additive/subtractive features can be transformed
        if hasattr(feature, "isDerivedFrom") and feature.isDerivedFrom("PartDesign::Transformed"):
            originals = getattr(feature, "Originals", [])
            hint = ""
            if originals:
                hint = f" Try mirroring '{originals[0].Name}' instead."
            return ToolResult(
                success=False, output="",
                error=f"Feature '{feature_name}' is a transformation (Mirrored/Pattern) and cannot be mirrored. "
                      f"Only additive features (Pad, Loft, etc.) and subtractive features (Pocket, Groove, etc.) "
                      f"can be mirrored.{hint}",
            )

        name = label or "Mirrored"
        mirror = body.newObject("PartDesign::Mirrored", name)
        mirror.Originals = [feature]

        # Resolve mirror plane
        plane_upper = plane.upper()
        if plane_upper in ("XY", "XZ", "YZ"):
            plane_obj = _get_body_plane(body, plane_upper)
            if not plane_obj:
                return ToolResult(success=False, output="", error=f"Could not find {plane_upper} plane on body")
            mirror.MirrorPlane = (plane_obj, [""])
        else:
            # "Sketch.N_Axis" or "Sketch.V_Axis" format
            parts = plane.split(".")
            if len(parts) == 2:
                ref_obj = _get_object(doc, parts[0])
                if ref_obj:
                    mirror.MirrorPlane = (ref_obj, [parts[1]])
                else:
                    return ToolResult(success=False, output="", error=f"Reference object '{parts[0]}' not found")
            else:
                return ToolResult(success=False, output="", error=f"Invalid plane: {plane}. Use XY/XZ/YZ or Sketch.N_Axis")

        return ToolResult(
            success=True,
            output=f"Mirrored '{feature_name}' across {plane}",
            data={"name": mirror.Name, "label": mirror.Label, "plane": plane},
        )

    return _with_undo("Mirror Feature", do)


MIRROR_FEATURE = ToolDefinition(
    name="mirror_feature",
    description="Mirror a PartDesign feature across a plane. The feature must be an additive (Pad, Loft, etc.) or subtractive (Pocket, Groove, etc.) feature inside a Body. Cannot mirror other transformations (Mirrored, LinearPattern, PolarPattern) — mirror the original feature instead.",
    category="modeling",
    parameters=[
        ToolParam("feature_name", "string", "Internal name of the feature to mirror"),
        ToolParam("plane", "string", "Mirror plane: XY, XZ, YZ (origin planes) or Sketch.N_Axis (sketch axis)",
                  required=False, default="YZ"),
        ToolParam("label", "string", "Display label for the mirror", required=False, default=""),
    ],
    handler=_handle_mirror_feature,
)


# ── multi_transform ────────────────────────────────────────

def _handle_multi_transform(
    feature_names: list = None,
    transformations: list = None,
    label: str = "",
    # Backward compat: old callers may pass feature_name as str
    feature_name: str = None,
) -> ToolResult:
    """Chain multiple transformation steps (linear, polar, mirror) into one MultiTransform feature."""

    if not transformations:
        return ToolResult(success=False, output="", error="transformations list must not be empty")

    # Normalize: accept old feature_name kwarg for backward compat
    raw = feature_names if feature_names is not None else feature_name
    if raw is None:
        return ToolResult(success=False, output="", error="feature_names is required")
    names = _coerce_str_list(raw)
    # Bare string → wrap in list
    if isinstance(names, str):
        names = [names]
    if not names:
        return ToolResult(success=False, output="", error="feature_names must not be empty")

    def do(doc):
        features = []
        body = None
        for fname in names:
            feat = _get_object(doc, fname)
            if not feat:
                return ToolResult(success=False, output="", error=f"Feature '{fname}' not found")

            feat_body = _find_body_for(doc, feat)
            if not feat_body:
                return ToolResult(
                    success=False, output="",
                    error=f"Feature '{fname}' is not inside a PartDesign Body",
                )

            if body is None:
                body = feat_body
            elif feat_body.Name != body.Name:
                return ToolResult(
                    success=False, output="",
                    error=f"All features must be in the same Body. "
                          f"'{fname}' is in '{feat_body.Label}', expected '{body.Label}'.",
                )

            # Reject transformation features (same guard as mirror_feature)
            if hasattr(feat, "isDerivedFrom") and feat.isDerivedFrom("PartDesign::Transformed"):
                originals = getattr(feat, "Originals", [])
                hint = ""
                if originals:
                    hint = f" Try transforming '{originals[0].Name}' instead."
                return ToolResult(
                    success=False, output="",
                    error=f"Feature '{fname}' is a transformation and cannot be multi-transformed. "
                          f"Only additive/subtractive features can be used.{hint}",
                )
            features.append(feat)

        name = label or "MultiTransform"
        multi = body.newObject("PartDesign::MultiTransform", name)
        multi.Originals = features

        sub_features = []
        descriptions = []

        for i, step in enumerate(transformations):
            step_type = step.get("type", "")

            if step_type == "linear_pattern":
                sub = body.newObject("PartDesign::LinearPattern", f"LP{i}")
                direction = step.get("direction", "X")
                dir_upper = direction.upper()
                if dir_upper in ("X", "Y", "Z"):
                    axis = _get_body_axis(body, dir_upper)
                    if not axis:
                        return ToolResult(success=False, output="", error=f"Step {i}: could not find {dir_upper} axis")
                    sub.Direction = (axis, [""])
                else:
                    parts = direction.split(".")
                    if len(parts) == 2:
                        ref_obj = _get_object(doc, parts[0])
                        if ref_obj:
                            sub.Direction = (ref_obj, [parts[1]])
                        else:
                            return ToolResult(success=False, output="", error=f"Step {i}: reference '{parts[0]}' not found")
                    else:
                        return ToolResult(success=False, output="", error=f"Step {i}: invalid direction '{direction}'")
                sub.Length = step.get("length", 10.0)
                sub.Occurrences = step.get("occurrences", 2)
                sub_features.append(sub)
                descriptions.append(f"linear({dir_upper}, {sub.Length}mm, {sub.Occurrences}x)")

            elif step_type == "polar_pattern":
                sub = body.newObject("PartDesign::PolarPattern", f"PP{i}")
                axis = step.get("axis", "Z")
                axis_upper = axis.upper()
                if axis_upper in ("X", "Y", "Z"):
                    axis_obj = _get_body_axis(body, axis_upper)
                    if not axis_obj:
                        return ToolResult(success=False, output="", error=f"Step {i}: could not find {axis_upper} axis")
                    sub.Axis = (axis_obj, [""])
                else:
                    parts = axis.split(".")
                    if len(parts) == 2:
                        ref_obj = _get_object(doc, parts[0])
                        if ref_obj:
                            sub.Axis = (ref_obj, [parts[1]])
                        else:
                            return ToolResult(success=False, output="", error=f"Step {i}: reference '{parts[0]}' not found")
                    else:
                        return ToolResult(success=False, output="", error=f"Step {i}: invalid axis '{axis}'")
                sub.Angle = step.get("angle", 360.0)
                sub.Occurrences = step.get("occurrences", 2)
                sub_features.append(sub)
                descriptions.append(f"polar({axis_upper}, {sub.Angle}°, {sub.Occurrences}x)")

            elif step_type == "mirror":
                sub = body.newObject("PartDesign::Mirrored", f"MR{i}")
                plane = step.get("plane", "YZ")
                plane_upper = plane.upper()
                if plane_upper in ("XY", "XZ", "YZ"):
                    plane_obj = _get_body_plane(body, plane_upper)
                    if not plane_obj:
                        return ToolResult(success=False, output="", error=f"Step {i}: could not find {plane_upper} plane")
                    sub.MirrorPlane = (plane_obj, [""])
                else:
                    parts = plane.split(".")
                    if len(parts) == 2:
                        ref_obj = _get_object(doc, parts[0])
                        if ref_obj:
                            sub.MirrorPlane = (ref_obj, [parts[1]])
                        else:
                            return ToolResult(success=False, output="", error=f"Step {i}: reference '{parts[0]}' not found")
                    else:
                        return ToolResult(success=False, output="", error=f"Step {i}: invalid plane '{plane}'")
                sub_features.append(sub)
                descriptions.append(f"mirror({plane_upper})")

            else:
                return ToolResult(
                    success=False, output="",
                    error=f"Step {i}: unknown type '{step_type}'. Use linear_pattern, polar_pattern, or mirror",
                )

        multi.Transformations = sub_features
        body.Tip = multi

        # Ensure visibility: sub-features should be hidden, multi should be visible
        for sub in sub_features:
            sub.Visibility = False
        multi.Visibility = True

        feat_list = ", ".join(f"'{n}'" for n in names)
        return ToolResult(
            success=True,
            output=f"Created MultiTransform on {feat_list} with {len(sub_features)} step(s): {', '.join(descriptions)}",
            data={"name": multi.Name, "label": multi.Label, "steps": len(sub_features)},
        )

    return _with_undo("Multi Transform", do)


MULTI_TRANSFORM = ToolDefinition(
    name="multi_transform",
    description=(
        "Chain multiple transformation steps (linear pattern, polar pattern, mirror) into a single "
        "PartDesign::MultiTransform feature. Accepts one or more features — pass related features "
        "(e.g. a post and its screw hole) together so they are transformed as a group. "
        "Cleaner than stacking separate pattern/mirror features "
        "and avoids 'transformation of a transformation' errors. All features must be additive or "
        "subtractive features inside the same Body."
    ),
    category="modeling",
    parameters=[
        ToolParam("feature_names", "array",
                  "Feature(s) to transform. Order matters: the last feature should be the most "
                  "recent in the model tree (tip). Pass multiple related features to transform them "
                  "as a group (e.g. a boss and its pocket).",
                  items={"type": "string"}),
        ToolParam("transformations", "array",
                  "List of transformation steps. Each is an object with 'type' (linear_pattern, polar_pattern, mirror) "
                  "plus type-specific params. linear_pattern: direction (X/Y/Z), length, occurrences. "
                  "polar_pattern: axis (X/Y/Z), angle, occurrences. mirror: plane (XY/XZ/YZ).",
                  items={
                      "type": "object",
                      "properties": {
                          "type": {"type": "string", "enum": ["linear_pattern", "polar_pattern", "mirror"]},
                          "direction": {"type": "string"},
                          "length": {"type": "number"},
                          "occurrences": {"type": "integer"},
                          "axis": {"type": "string"},
                          "angle": {"type": "number"},
                          "plane": {"type": "string"},
                      },
                      "required": ["type"],
                  }),
        ToolParam("label", "string", "Display label for the MultiTransform", required=False, default=""),
    ],
    handler=_handle_multi_transform,
)


