"""FreeCAD part creation."""

import os

from ..registry import ToolParam, ToolDefinition, ToolResult
from ...core.executor import execute_code
from ..tool_common import *  # noqa: F403

# ── create_primitive ────────────────────────────────────────

def _handle_create_primitive(
    shape_type: str,
    label: str = "",
    body_name: str = "",
    operation: str = "additive",
    length: float = 10.0,
    width: float = 10.0,
    height: float = 10.0,
    radius: float = 5.0,
    radius2: float = 2.0,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
) -> ToolResult:
    """Create a PartDesign primitive (Box, Cylinder, Sphere, Cone, Torus) inside a Body."""
    import FreeCAD as App

    additive_map = {
        "box": "PartDesign::AdditiveBox",
        "cylinder": "PartDesign::AdditiveCylinder",
        "sphere": "PartDesign::AdditiveSphere",
        "cone": "PartDesign::AdditiveCone",
        "torus": "PartDesign::AdditiveTorus",
    }
    subtractive_map = {
        "box": "PartDesign::SubtractiveBox",
        "cylinder": "PartDesign::SubtractiveCylinder",
        "sphere": "PartDesign::SubtractiveSphere",
        "cone": "PartDesign::SubtractiveCone",
        "torus": "PartDesign::SubtractiveTorus",
    }

    def do(doc):
        st = shape_type.lower()
        op = operation.lower()

        if op == "subtractive":
            type_map = subtractive_map
        else:
            type_map = additive_map

        pd_type = type_map.get(st)
        if not pd_type:
            return ToolResult(
                success=False, output="",
                error=f"Unknown shape type: {shape_type}. Use: {list(additive_map.keys())}"
            )

        # Get or create body
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(
                    success=False, output="",
                    error=f"Body '{body_name}' not found.{hint}"
                )
        else:
            body_label = label or st.capitalize()
            body = doc.addObject("PartDesign::Body", body_label)
            body.Label = body_label

        name = label or st.capitalize()
        obj = body.newObject(pd_type, name)
        obj.Label = name

        if st == "box":
            obj.Length = length
            obj.Width = width
            obj.Height = height
        elif st == "cylinder":
            obj.Radius = radius
            obj.Height = height
        elif st == "sphere":
            obj.Radius = radius
        elif st == "cone":
            obj.Radius1 = radius
            obj.Radius2 = radius2
            obj.Height = height
        elif st == "torus":
            obj.Radius1 = radius
            obj.Radius2 = radius2

        if x != 0 or y != 0 or z != 0:
            obj.Placement.Base = App.Vector(x, y, z)

        return ToolResult(
            success=True,
            output=f"Created {op} {st} '{obj.Label}' ({obj.Name}) in body '{body.Label}' ({body.Name})",
            data={"name": obj.Name, "label": obj.Label, "type": pd_type,
                  "body_name": body.Name, "body_label": body.Label},
        )

    return _with_undo(f"Create {shape_type}", do)


CREATE_PRIMITIVE = ToolDefinition(
    name="create_primitive",
    description="Create a PartDesign primitive (box/cube, cylinder, sphere, cone, torus) inside a Body. Auto-creates a Body if body_name is not given. Use operation='subtractive' to cut material from an existing body.",
    category="modeling",
    parameters=[
        ToolParam("shape_type", "string", "Type of primitive to create",
                  enum=["box", "cylinder", "sphere", "cone", "torus"]),
        ToolParam("label", "string", "Display label for the object", required=False, default=""),
        ToolParam("body_name", "string", "Name of existing Body to add primitive to (auto-creates if empty)", required=False, default=""),
        ToolParam("operation", "string", "Additive (add material) or subtractive (cut material)",
                  required=False, default="additive", enum=["additive", "subtractive"]),
        ToolParam("length", "number", "Length (box)", required=False, default=10.0),
        ToolParam("width", "number", "Width (box)", required=False, default=10.0),
        ToolParam("height", "number", "Height (box/cylinder/cone)", required=False, default=10.0),
        ToolParam("radius", "number", "Radius (cylinder/sphere/cone r1/torus major)", required=False, default=5.0),
        ToolParam("radius2", "number", "Second radius (cone r2/torus minor)", required=False, default=2.0),
        ToolParam("x", "number", "X position", required=False, default=0.0),
        ToolParam("y", "number", "Y position", required=False, default=0.0),
        ToolParam("z", "number", "Z position", required=False, default=0.0),
    ],
    handler=_handle_create_primitive,
)


# ── create_body ─────────────────────────────────────────────

def _handle_create_body(
    label: str = "Body",
) -> ToolResult:
    """Create a PartDesign Body for parametric modeling."""
    import FreeCAD as App

    def do(doc):
        body = doc.addObject("PartDesign::Body", label)
        body.Label = label
        return ToolResult(
            success=True,
            output=(f"Created PartDesign body '{body.Name}' (label: '{body.Label}')."
                    f" Use body_name='{body.Name}' in subsequent tool calls."),
            data={"name": body.Name, "label": body.Label},
        )

    return _with_undo("Create Body", do)


CREATE_BODY = ToolDefinition(
    name="create_body",
    description="Create a PartDesign Body. Bodies are containers for parametric features (sketches, pads, pockets, fillets, etc). Create a body first, then add sketches to it using body_name parameter.",
    category="modeling",
    parameters=[
        ToolParam("label", "string", "Display label for the body", required=False, default="Body"),
    ],
    handler=_handle_create_body,
)


