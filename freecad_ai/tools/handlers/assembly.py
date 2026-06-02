"""FreeCAD assembly."""

import os

from ..registry import ToolParam, ToolDefinition, ToolResult
from ...core.executor import execute_code
from ..tool_common import *  # noqa: F403

# ── Assembly tools ──────────────────────────────────────────


def _setup_assembly_imports():
    """Import Assembly workbench modules. Call inside FreeCAD context only."""
    import FreeCAD as App
    import sys
    sys.path.insert(0, App.getHomePath() + "Mod/Assembly")
    import JointObject
    import UtilsAssembly
    return JointObject, UtilsAssembly


def _get_joint_group(asm):
    """Get or create the JointGroup inside an assembly."""
    for child in asm.Group:
        if child.TypeId == "Assembly::JointGroup":
            return child
    return asm.newObject("Assembly::JointGroup", "Joints")


def _handle_create_assembly(
    label: str = "Assembly",
    part_names: list | None = None,
    ground_first: bool = True,
) -> ToolResult:
    """Create an Assembly and optionally add existing bodies/parts to it."""
    import FreeCAD as App

    part_names = _coerce_str_list(part_names) or []

    def do(doc):
        JointObject, _ = _setup_assembly_imports()

        asm = doc.addObject("Assembly::AssemblyObject", label)
        asm.Label = label
        jg = asm.newObject("Assembly::JointGroup", "Joints")

        added = []
        errors = []
        for pname in part_names:
            obj = _get_object(doc, pname)
            if obj:
                asm.addObject(obj)
                added.append(obj)
            else:
                errors.append(f"'{pname}' not found")

        # Ground the first part so the solver has a fixed reference frame
        if ground_first and added:
            ground = jg.newObject("App::FeaturePython", "GroundedJoint")
            JointObject.GroundedJoint(ground, added[0])
            if ground.ViewObject and hasattr(JointObject, "ViewProviderGroundedJoint"):
                JointObject.ViewProviderGroundedJoint(ground.ViewObject)

        labels = [o.Label for o in added]
        parts_str = [f"  - {l}" for l in labels]
        msg = f"Created assembly '{asm.Name}' (label: '{asm.Label}')."
        if parts_str:
            msg += f"\nAdded {len(added)} part(s):\n" + "\n".join(parts_str)
        if ground_first and added:
            msg += f"\nGrounded '{added[0].Label}' (fixed reference frame)."
        if errors:
            msg += f"\nWarnings: {', '.join(errors)}"
        msg += f"\nUse assembly_name='{asm.Name}' for add_assembly_joint."

        return ToolResult(
            success=True, output=msg,
            data={"name": asm.Name, "label": asm.Label, "parts": labels},
        )

    return _with_undo("Create Assembly", do)


CREATE_ASSEMBLY = ToolDefinition(
    name="create_assembly",
    description=(
        "Create an Assembly container and optionally add existing bodies/parts to it. "
        "An assembly groups parts and allows positioning them relative to each other "
        "using joints (via add_assembly_joint). The first part is grounded (fixed in place) "
        "by default. Create the assembly first, then add joints."
    ),
    category="modeling",
    parameters=[
        ToolParam("label", "string", "Display label for the assembly",
                  required=False, default="Assembly"),
        ToolParam("part_names", "array",
                  "List of body/part names to add to the assembly",
                  required=False, items={"type": "string"}),
        ToolParam("ground_first", "boolean",
                  "Ground (fix in place) the first part as reference frame (default: true)",
                  required=False, default=True),
    ],
    handler=_handle_create_assembly,
)


def _find_sub_name(part, face_str):
    """Build the sub-element path for a face on a body (e.g. 'Box1.Face6').

    For PartDesign bodies, the face belongs to the tip feature.
    """
    if hasattr(part, "Tip") and part.Tip:
        return f"{part.Tip.Name}.{face_str}"
    return face_str



def _handle_add_assembly_joint(
    assembly_name: str,
    part1_name: str,
    face1: str,
    part2_name: str,
    face2: str,
    joint_type: str = "Fixed",
    label: str = "",
) -> ToolResult:
    """Add a joint between two faces and use the native solver to position parts."""
    import FreeCAD as App

    def do(doc):
        JointObject, UtilsAssembly = _setup_assembly_imports()

        asm = _get_object(doc, assembly_name)
        if not asm or asm.TypeId != "Assembly::AssemblyObject":
            return ToolResult(
                success=False, output="",
                error=f"Assembly '{assembly_name}' not found. Create one with create_assembly first.",
            )

        part1 = _get_object(doc, part1_name)
        part2 = _get_object(doc, part2_name)
        if not part1:
            return ToolResult(success=False, output="", error=f"Part '{part1_name}' not found.")
        if not part2:
            return ToolResult(success=False, output="", error=f"Part '{part2_name}' not found.")

        # Ensure parts are in the assembly
        asm_children = set(o.Name for o in asm.Group)
        for part in (part1, part2):
            if part.Name not in asm_children:
                asm.addObject(part)

        jg = _get_joint_group(asm)

        # Create the joint
        type_map = {
            "Fixed": 0, "Revolute": 1, "Cylindrical": 2, "Slider": 3,
            "Ball": 4, "Distance": 5, "Parallel": 6, "Perpendicular": 7,
            "Angle": 8,
        }
        type_idx = type_map.get(joint_type, 0)

        joint_label = label or f"{joint_type}_{part1.Label}_{part2.Label}"
        joint = jg.newObject("App::FeaturePython", joint_label)
        JointObject.Joint(joint, type_idx)
        if joint.ViewObject and hasattr(JointObject, "ViewProviderJoint"):
            JointObject.ViewProviderJoint(joint.ViewObject)

        # Set references: (body, ["Tip.FaceN", "Tip.FaceN"])
        # Duplicating the face sub-name means "use face center" (not a specific vertex)
        sub1 = _find_sub_name(part1, face1)
        sub2 = _find_sub_name(part2, face2)
        joint.Reference1 = (part1, [sub1, sub1])
        joint.Reference2 = (part2, [sub2, sub2])

        # Compute placements using the Assembly workbench's own function
        # This handles all geometry types (planar, cylindrical, conical, etc.)
        joint.Placement1 = UtilsAssembly.findPlacement(joint.Reference1)
        joint.Placement2 = UtilsAssembly.findPlacement(joint.Reference2)

        # Pre-position the moving part before solving (replicates GUI's preSolve).
        # preSolve checks JCS orientation and flips if needed for face-to-face contact.
        joint.Proxy.preSolve(joint)

        # Let the native C++ solver position the parts
        doc.recompute()
        solve_result = asm.solve()

        pos = part2.Placement.Base
        msg = (
            f"Created {joint_type} joint '{joint.Label}' between "
            f"'{part1.Label}.{face1}' and '{part2.Label}.{face2}'.\n"
            f"Solver result: {solve_result} (0=OK).\n"
            f"'{part2.Label}' positioned at ({pos.x:.1f}, {pos.y:.1f}, {pos.z:.1f})."
        )

        return ToolResult(
            success=True, output=msg,
            data={"joint_name": joint.Name, "part2_position": [pos.x, pos.y, pos.z]},
        )

    return _with_undo("Add Assembly Joint", do)


ADD_ASSEMBLY_JOINT = ToolDefinition(
    name="add_assembly_joint",
    description=(
        "Add a joint between two parts in an assembly by specifying which faces to mate. "
        "The second part is repositioned so its face meets the first part's face. "
        "Use list_faces first to check face normals and positions.\n"
        "FACE SELECTION GUIDE:\n"
        "- Fixed (stacking): use top face of base + bottom face of part (e.g. Face6+Face5 for boxes)\n"
        "- Fixed (side-by-side): use right face of part1 + left face of part2\n"
        "- Revolute (hinge): use a SIDE face of the mount + an END face of the arm, "
        "so the arm extends outward and rotates around the face normal\n"
        "- Cylindrical: use the curved face (Face1) of a cylinder + a hole face\n"
        "- Ball: REQUIRES spherical geometry — one part needs an additive sphere (ball), "
        "the other a subtractive sphere (socket). Reference the spherical faces.\n"
        "IMPORTANT: The rotation axis of Revolute/Cylindrical joints is the face normal. "
        "For a horizontal hinge, connect vertical side faces (normal along X or Y). "
        "For a vertical turntable, connect horizontal faces (normal along Z)."
    ),
    category="modeling",
    parameters=[
        ToolParam("assembly_name", "string", "Name of the assembly (from create_assembly)"),
        ToolParam("part1_name", "string", "Name of the first (reference) part/body"),
        ToolParam("face1", "string", "Face name on part1 (e.g. 'Face6')"),
        ToolParam("part2_name", "string", "Name of the second part/body to position"),
        ToolParam("face2", "string", "Face name on part2 to mate with face1 (e.g. 'Face1')"),
        ToolParam("joint_type", "string",
                  "Joint type: Fixed (default), Revolute, Cylindrical, Slider, Ball",
                  required=False, default="Fixed"),
        ToolParam("label", "string", "Optional label for the joint",
                  required=False, default=""),
    ],
    handler=_handle_add_assembly_joint,
)


def _handle_add_part_to_assembly(
    assembly_name: str,
    part_name: str,
    position: list | None = None,
) -> ToolResult:
    """Add a part/body to an existing assembly, optionally at a given position."""
    import FreeCAD as App
    from FreeCAD import Placement, Vector, Rotation

    position = _coerce_str_list(position)

    def do(doc):
        asm = _get_object(doc, assembly_name)
        if not asm or asm.TypeId != "Assembly::AssemblyObject":
            return ToolResult(
                success=False, output="",
                error=f"Assembly '{assembly_name}' not found.",
            )

        obj = _get_object(doc, part_name)
        if not obj:
            return ToolResult(success=False, output="", error=f"Part '{part_name}' not found.")

        asm.addObject(obj)

        if position and len(position) >= 3:
            obj.Placement = Placement(
                Vector(float(position[0]), float(position[1]), float(position[2])),
                Rotation(),
            )

        msg = f"Added '{obj.Label}' to assembly '{asm.Label}'."
        if position:
            msg += f" Positioned at ({position[0]}, {position[1]}, {position[2]})."

        return ToolResult(
            success=True, output=msg,
            data={"name": obj.Name, "label": obj.Label},
        )

    return _with_undo("Add Part to Assembly", do)


ADD_PART_TO_ASSEMBLY = ToolDefinition(
    name="add_part_to_assembly",
    description=(
        "Add an existing body/part to an assembly, optionally setting its position. "
        "Use this to add parts that weren't included in create_assembly, "
        "or to reposition parts before adding joints."
    ),
    category="modeling",
    parameters=[
        ToolParam("assembly_name", "string", "Name of the assembly"),
        ToolParam("part_name", "string", "Name of the body/part to add"),
        ToolParam("position", "array",
                  "Optional [x, y, z] position for the part",
                  required=False, items={"type": "number"}),
    ],
    handler=_handle_add_part_to_assembly,
)


