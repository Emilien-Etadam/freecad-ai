"""FreeCAD enclosure."""

import os

from ..registry import ToolParam, ToolDefinition, ToolResult
from ...core.executor import execute_code
from ..tool_common import *  # noqa: F403

# ── create_inner_ridge ─────────────────────────────────────

def _handle_create_inner_ridge(
    body_name: str,
    length: float,
    width: float,
    wall_thickness: float = 2.0,
    ridge_width: float = 0.8,
    ridge_height: float = 0.5,
    z_position: float = 0.0,
    label: str = "Ridge",
) -> ToolResult:
    """Add a thin ridge/ledge around the inside perimeter of a rectangular body."""
    import FreeCAD as App
    import Part
    import Sketcher

    def _add_rect(sketch, x, y, w, h):
        """Add a closed rectangle to the sketch at (x, y) with size (w, h)."""
        g = sketch.GeometryCount
        sketch.addGeometry(Part.LineSegment(App.Vector(x, y, 0), App.Vector(x + w, y, 0)))
        sketch.addGeometry(Part.LineSegment(App.Vector(x + w, y, 0), App.Vector(x + w, y + h, 0)))
        sketch.addGeometry(Part.LineSegment(App.Vector(x + w, y + h, 0), App.Vector(x, y + h, 0)))
        sketch.addGeometry(Part.LineSegment(App.Vector(x, y + h, 0), App.Vector(x, y, 0)))
        sketch.addConstraint(Sketcher.Constraint("Coincident", g, 2, g + 1, 1))
        sketch.addConstraint(Sketcher.Constraint("Coincident", g + 1, 2, g + 2, 1))
        sketch.addConstraint(Sketcher.Constraint("Coincident", g + 2, 2, g + 3, 1))
        sketch.addConstraint(Sketcher.Constraint("Coincident", g + 3, 2, g, 1))

    def do(doc):
        body = _get_object(doc, body_name)
        if not body:
            hint = _suggest_similar(doc, body_name, "Body")
            return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")

        T = wall_thickness
        rw = ridge_width

        sketch = body.newObject("Sketcher::SketchObject", label + "Sketch")
        xy_plane = _get_body_plane(body, "XY")
        if xy_plane:
            sketch.AttachmentSupport = [(xy_plane, "")]
        sketch.MapMode = "FlatFace"
        sketch.AttachmentOffset = App.Placement(
            App.Vector(0, 0, z_position), App.Rotation())

        # Outer rectangle = inner wall of enclosure
        _add_rect(sketch, T, T, length - 2 * T, width - 2 * T)
        # Inner rectangle = inset by ridge_width (creates ring shape)
        _add_rect(sketch, T + rw, T + rw, length - 2 * T - 2 * rw, width - 2 * T - 2 * rw)

        doc.recompute()

        pad = body.newObject("PartDesign::Pad", label)
        pad.Profile = sketch
        pad.Length = ridge_height
        sketch.Visibility = False

        return ToolResult(
            success=True,
            output=f"Created inner ridge '{label}' at z={z_position}mm ({ridge_width}mm wide, {ridge_height}mm tall)",
            data={"name": pad.Name, "label": label},
        )

    return _with_undo("Create Inner Ridge", do)


CREATE_INNER_RIDGE = ToolDefinition(
    name="create_inner_ridge",
    description=(
        "Add a thin ridge/ledge running around the inside perimeter of a rectangular "
        "hollow body. Useful as a catch for snap-fit lids. The defaults (0.8mm wide, "
        "0.5mm tall) are tuned for 3D printing — do NOT override ridge_width/ridge_height "
        "unless the user explicitly requests different dimensions."
    ),
    category="modeling",
    parameters=[
        ToolParam("body_name", "string", "Name of the PartDesign body to add the ridge to"),
        ToolParam("length", "number", "Outer length of the enclosure (L)"),
        ToolParam("width", "number", "Outer width of the enclosure (W)"),
        ToolParam("wall_thickness", "number", "Wall thickness (T) — MUST match the enclosure wall thickness"),
        ToolParam("ridge_width", "number", "Inward protrusion from wall (mm). Default 0.8 — do not increase", required=False, default=0.8),
        ToolParam("ridge_height", "number", "Height along Z (mm). Default 0.5 — do not increase", required=False, default=0.5),
        ToolParam("z_position", "number", "Z height where the ridge starts (typically H-2)"),
        ToolParam("label", "string", "Display label", required=False, default="Ridge"),
    ],
    handler=_handle_create_inner_ridge,
)


# ── create_snap_tabs ──────────────────────────────────────

def _handle_create_snap_tabs(
    body_name: str,
    length: float,
    width: float,
    wall_thickness: float = 2.0,
    clearance: float = 1.0,
    lip_height: float = 3.0,
    tab_width: float = 3.0,
    tab_height: float = 1.0,
    protrusion: float = 0.5,
    label: str = "SnapTab",
) -> ToolResult:
    """Add snap tabs on the outside of a rectangular lip that catch on an inner ridge.

    Creates PartDesign::AdditiveBox features inside the lid body so tabs
    remain editable and compatible with PartDesign tools (fillet, chamfer,
    pattern, etc.).
    """
    import FreeCAD as App

    def do(doc):
        body = _get_object(doc, body_name)
        if not body:
            hint = _suggest_similar(doc, body_name, "Body")
            return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")

        T = wall_thickness
        cl = clearance

        # Clamp protrusion so tabs stay within the clearance gap
        # (otherwise they penetrate the base wall)
        actual_protrusion = min(protrusion, cl - 0.05)
        if actual_protrusion < 0.1:
            return ToolResult(
                success=False, output="",
                error=f"Clearance ({cl}mm) too small for snap tabs. "
                      f"Need at least 0.5mm; got {cl}mm. "
                      f"Use a wider lip clearance for snap-fit lids.")

        # All positions are in body-local coordinates (AdditiveBox
        # placement is relative to the body, not global).
        # Lip outer edges
        lip_x1 = T + cl
        lip_x2 = length - T - cl
        lip_y1 = T + cl
        lip_y2 = width - T - cl
        lip_cx = (lip_x1 + lip_x2) / 2
        lip_cy = (lip_y1 + lip_y2) / 2

        # Tab Z: at the bottom of the lip, with a gap below the ridge.
        # Shorten tab by 0.3mm so it doesn't touch the ridge above.
        snap_gap = 0.3
        th = tab_height - snap_gap  # effective tab height
        p = actual_protrusion

        # Define tab positions: (x, y, z, sx, sy, sz, side_label)
        # x/y/z = corner of the box (not center)
        tabs = []
        third = (lip_x2 - lip_x1) / 3

        # Long sides (front y=lip_y1, back y=lip_y2) — 2 tabs each
        for i, x_center in enumerate([lip_x1 + third, lip_x1 + 2 * third]):
            # Front wall tab: protrudes in -Y direction
            tabs.append((
                x_center - tab_width / 2, lip_y1 - p, 0,
                tab_width, p, th, f"Front{i + 1}"))
            # Back wall tab: protrudes in +Y direction
            tabs.append((
                x_center - tab_width / 2, lip_y2, 0,
                tab_width, p, th, f"Back{i + 1}"))

        # Short sides (left x=lip_x1, right x=lip_x2) — 1 tab each
        tabs.append((
            lip_x1 - p, lip_cy - tab_width / 2, 0,
            p, tab_width, th, "Left"))
        tabs.append((
            lip_x2, lip_cy - tab_width / 2, 0,
            p, tab_width, th, "Right"))

        # Create each tab as an AdditiveBox inside the body
        tab_names = []
        for (bx, by, bz, sx, sy, sz, side) in tabs:
            tab_label = f"{label}_{side}"
            box = body.newObject("PartDesign::AdditiveBox", tab_label)
            box.Length = sx
            box.Width = sy
            box.Height = sz
            box.Placement.Base = App.Vector(bx, by, bz)
            tab_names.append(box.Name)

        return ToolResult(
            success=True,
            output=f"Added {len(tabs)} snap tabs to '{body_name}' (protrusion={actual_protrusion:.1f}mm) as PartDesign features.",
            data={"name": tab_names[-1], "label": label, "tab_count": len(tabs),
                  "tab_names": tab_names},
        )

    return _with_undo("Create Snap Tabs", do)


CREATE_SNAP_TABS = ToolDefinition(
    name="create_snap_tabs",
    description=(
        "Add snap tabs on the outside of a rectangular lip. The tabs catch on an inner "
        "ridge to hold the lid in place. Places 2 tabs on each long side and 1 on each "
        "short side. IMPORTANT: the lid must be built lip-FIRST (lip at body origin, slab "
        "on top) and positioned BEFORE calling this tool. Use defaults for tab dimensions."
    ),
    category="modeling",
    parameters=[
        ToolParam("body_name", "string", "Name of the lid body with the lip"),
        ToolParam("length", "number", "Outer length of the enclosure (L)"),
        ToolParam("width", "number", "Outer width of the enclosure (W)"),
        ToolParam("wall_thickness", "number", "Wall thickness (T) — MUST match the enclosure wall thickness"),
        ToolParam("clearance", "number", "Gap between lip and wall (mm)", required=False, default=1.0),
        ToolParam("lip_height", "number", "Height of the lip (mm)", required=False, default=3.0),
        ToolParam("tab_width", "number", "Width of each tab along the wall (mm)", required=False, default=3.0),
        ToolParam("tab_height", "number", "Height of each tab along Z (mm)", required=False, default=1.0),
        ToolParam("protrusion", "number", "How far each tab protrudes outward (mm)", required=False, default=0.5),
        ToolParam("label", "string", "Display label for the result", required=False, default="SnapTab"),
    ],
    handler=_handle_create_snap_tabs,
)


# ── create_enclosure_lid ─────────────────────────────────

def _handle_create_enclosure_lid(
    length: float,
    width: float,
    wall_thickness: float,
    clearance: float = 1.0,
    lip_height: float = 3.0,
    label: str = "EnclosureLid",
) -> ToolResult:
    """Create a snap-fit enclosure lid with correct lip+slab geometry."""
    import FreeCAD as App
    import Part

    def do(doc):
        T = wall_thickness
        CL = clearance
        LH = lip_height

        body = doc.addObject("PartDesign::Body", label)
        body.Label = label
        doc.recompute()

        # ── Step 1: Lip (built first so it points downward when positioned) ──
        lip_x = T + CL
        lip_y = T + CL
        lip_w = length - 2 * T - 2 * CL
        lip_h = width - 2 * T - 2 * CL

        if lip_w <= 0 or lip_h <= 0:
            return ToolResult(
                success=False, output="",
                error=f"Lip dimensions too small ({lip_w:.1f}x{lip_h:.1f}mm). "
                      f"Reduce wall_thickness or clearance.")

        lip_sketch = body.newObject("Sketcher::SketchObject", "LipSketch")
        xy_plane = _get_body_plane(body, "XY")
        if xy_plane:
            lip_sketch.AttachmentSupport = [(xy_plane, "")]
        lip_sketch.MapMode = "FlatFace"

        # Rectangle for lip
        x1, y1 = lip_x, lip_y
        x2, y2 = lip_x + lip_w, lip_y + lip_h
        lip_sketch.addGeometry(Part.LineSegment(App.Vector(x1, y1, 0), App.Vector(x2, y1, 0)))
        lip_sketch.addGeometry(Part.LineSegment(App.Vector(x2, y1, 0), App.Vector(x2, y2, 0)))
        lip_sketch.addGeometry(Part.LineSegment(App.Vector(x2, y2, 0), App.Vector(x1, y2, 0)))
        lip_sketch.addGeometry(Part.LineSegment(App.Vector(x1, y2, 0), App.Vector(x1, y1, 0)))
        import Sketcher
        lip_sketch.addConstraint(Sketcher.Constraint("Coincident", 0, 2, 1, 1))
        lip_sketch.addConstraint(Sketcher.Constraint("Coincident", 1, 2, 2, 1))
        lip_sketch.addConstraint(Sketcher.Constraint("Coincident", 2, 2, 3, 1))
        lip_sketch.addConstraint(Sketcher.Constraint("Coincident", 3, 2, 0, 1))
        doc.recompute()

        lip_pad = body.newObject("PartDesign::Pad", "LipPad")
        lip_pad.Profile = lip_sketch
        lip_pad.Length = LH
        lip_sketch.Visibility = False

        # ── Step 2: Slab on top of lip (full enclosure size) ──
        slab_sketch = body.newObject("Sketcher::SketchObject", "SlabSketch")
        if xy_plane:
            slab_sketch.AttachmentSupport = [(xy_plane, "")]
        slab_sketch.MapMode = "FlatFace"
        slab_sketch.AttachmentOffset = App.Placement(
            App.Vector(0, 0, LH), App.Rotation())

        slab_sketch.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(length, 0, 0)))
        slab_sketch.addGeometry(Part.LineSegment(App.Vector(length, 0, 0), App.Vector(length, width, 0)))
        slab_sketch.addGeometry(Part.LineSegment(App.Vector(length, width, 0), App.Vector(0, width, 0)))
        slab_sketch.addGeometry(Part.LineSegment(App.Vector(0, width, 0), App.Vector(0, 0, 0)))
        slab_sketch.addConstraint(Sketcher.Constraint("Coincident", 0, 2, 1, 1))
        slab_sketch.addConstraint(Sketcher.Constraint("Coincident", 1, 2, 2, 1))
        slab_sketch.addConstraint(Sketcher.Constraint("Coincident", 2, 2, 3, 1))
        slab_sketch.addConstraint(Sketcher.Constraint("Coincident", 3, 2, 0, 1))
        doc.recompute()

        slab_pad = body.newObject("PartDesign::Pad", "SlabPad")
        slab_pad.Profile = slab_sketch
        slab_pad.Length = T
        slab_sketch.Visibility = False

        return ToolResult(
            success=True,
            output=f"Created enclosure lid '{label}' (lip: {lip_w:.0f}x{lip_h:.0f}x{LH:.0f}mm, slab: {length:.0f}x{width:.0f}x{T:.0f}mm). "
                   f"Use transform_object to position at z=H-{LH:.0f}.",
            data={"name": body.Name, "label": label,
                  "lip_width": lip_w, "lip_height_dim": lip_h, "lip_depth": LH,
                  "slab_thickness": T},
        )

    return _with_undo("Create Enclosure Lid", do)


CREATE_ENCLOSURE_LID = ToolDefinition(
    name="create_enclosure_lid",
    description=(
        "Create a snap-fit enclosure lid body with correct lip+slab geometry. "
        "The lip is automatically inset by wall_thickness+clearance so it fits inside "
        "the base cavity with room for snap tabs. After calling this, position the lid "
        "with transform_object at z=H-lip_height, then add snap tabs."
    ),
    category="modeling",
    parameters=[
        ToolParam("length", "number", "Outer length of the enclosure (L)"),
        ToolParam("width", "number", "Outer width of the enclosure (W)"),
        ToolParam("wall_thickness", "number", "Wall thickness (T) — must match the base"),
        ToolParam("clearance", "number", "Gap between lip and cavity wall (mm). Use 1.0 for snap-fit", required=False, default=1.0),
        ToolParam("lip_height", "number", "How far the lip extends down into the base (mm)", required=False, default=3.0),
        ToolParam("label", "string", "Display label for the lid body", required=False, default="EnclosureLid"),
    ],
    handler=_handle_create_enclosure_lid,
)


# ── create_wedge ───────────────────────────────────────────

def _handle_create_wedge(
    length: float = 10.0,
    width: float = 10.0,
    height: float = 10.0,
    top_length: float | None = None,
    top_width: float | None = None,
    label: str = "",
    body_name: str = "",
    operation: str = "additive",
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
) -> ToolResult:
    """Create a wedge (tapered box) as a PartDesign loft between two rectangular sketches."""
    import FreeCAD as App
    import Part
    import Sketcher

    def _add_rect(sketch, x1, y1, x2, y2):
        """Add a closed rectangle to a sketch with coincident + H/V constraints."""
        sketch.addGeometry(Part.LineSegment(App.Vector(x1, y1, 0), App.Vector(x2, y1, 0)))
        sketch.addGeometry(Part.LineSegment(App.Vector(x2, y1, 0), App.Vector(x2, y2, 0)))
        sketch.addGeometry(Part.LineSegment(App.Vector(x2, y2, 0), App.Vector(x1, y2, 0)))
        sketch.addGeometry(Part.LineSegment(App.Vector(x1, y2, 0), App.Vector(x1, y1, 0)))
        g = sketch.GeometryCount - 4
        sketch.addConstraint(Sketcher.Constraint("Coincident", g, 2, g + 1, 1))
        sketch.addConstraint(Sketcher.Constraint("Coincident", g + 1, 2, g + 2, 1))
        sketch.addConstraint(Sketcher.Constraint("Coincident", g + 2, 2, g + 3, 1))
        sketch.addConstraint(Sketcher.Constraint("Coincident", g + 3, 2, g, 1))
        sketch.addConstraint(Sketcher.Constraint("Horizontal", g))
        sketch.addConstraint(Sketcher.Constraint("Horizontal", g + 2))
        sketch.addConstraint(Sketcher.Constraint("Vertical", g + 1))
        sketch.addConstraint(Sketcher.Constraint("Vertical", g + 3))

    def do(doc):
        tl = top_length if top_length is not None else length
        tw = top_width if top_width is not None else 0.0
        # Clamp degenerate dimensions — lofting a rect to a line/point is unreliable
        tl = max(tl, 0.01)
        tw = max(tw, 0.01)

        op = operation.lower()

        # Get or create body
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                return ToolResult(
                    success=False, output="",
                    error=f"Body '{body_name}' not found",
                )
        else:
            body_label = label or "Wedge"
            body = doc.addObject("PartDesign::Body", body_label)
            body.Label = body_label

        # Attach sketches to XY plane
        xy_plane = _get_body_plane(body, "XY")
        if not xy_plane:
            return ToolResult(
                success=False, output="",
                error="Cannot find XY plane in body's origin",
            )

        # Bottom sketch: rectangle (0,0) to (length, width) on XY plane
        bot = body.newObject("Sketcher::SketchObject", "WedgeBase")
        bot.AttachmentSupport = [(xy_plane, "")]
        bot.MapMode = "FlatFace"
        doc.recompute()
        _add_rect(bot, 0, 0, length, width)

        # Top sketch: centered top rectangle at z=height
        top = body.newObject("Sketcher::SketchObject", "WedgeTop")
        top.AttachmentSupport = [(xy_plane, "")]
        top.MapMode = "FlatFace"
        top.AttachmentOffset = App.Placement(
            App.Vector(0, 0, height), App.Rotation()
        )
        doc.recompute()
        tx1 = (length - tl) / 2
        ty1 = (width - tw) / 2
        _add_rect(top, tx1, ty1, tx1 + tl, ty1 + tw)

        doc.recompute()

        # Loft between the two sketches
        if op == "subtractive":
            type_name = "PartDesign::SubtractiveLoft"
        else:
            type_name = "PartDesign::AdditiveLoft"
        feat_label = label or "Wedge"
        feat = body.newObject(type_name, feat_label)
        feat.Profile = bot
        feat.Sections = [top]
        feat.Ruled = True

        bot.Visibility = False
        top.Visibility = False

        # Position the body if needed
        if x != 0 or y != 0 or z != 0:
            body.Placement.Base = App.Vector(x, y, z)

        doc.recompute()

        return ToolResult(
            success=True,
            output=(
                f"Created {op} wedge '{feat.Label}' ({feat.Name}) in body "
                f"'{body.Label}' ({body.Name}) — "
                f"{length}x{width}x{height}mm, top: {tl}x{tw}mm"
            ),
            data={
                "name": feat.Name,
                "label": feat.Label,
                "body_name": body.Name,
                "body_label": body.Label,
            },
        )

    return _with_undo("Create Wedge", do)


CREATE_WEDGE = ToolDefinition(
    name="create_wedge",
    description="Create a PartDesign wedge (tapered box) inside a Body via loft. Base is length x width, top face is top_length x top_width (centered). Default top_width=0 creates a classic ramp/wedge shape. Compatible with fillet, chamfer, shell, pattern, mirror.",
    category="modeling",
    parameters=[
        ToolParam("length", "number", "Base length (X dimension)", required=False, default=10.0),
        ToolParam("width", "number", "Base width (Y dimension)", required=False, default=10.0),
        ToolParam("height", "number", "Height (Z dimension)", required=False, default=10.0),
        ToolParam("top_length", "number", "Top face length (defaults to base length = no taper in X)", required=False),
        ToolParam("top_width", "number", "Top face width (defaults to 0 = tapers to ridge)", required=False),
        ToolParam("label", "string", "Display label", required=False, default=""),
        ToolParam("body_name", "string", "Name of existing Body to add wedge to (auto-creates if empty)", required=False, default=""),
        ToolParam("operation", "string", "Additive (add material) or subtractive (cut material)",
                  required=False, default="additive", enum=["additive", "subtractive"]),
        ToolParam("x", "number", "X position", required=False, default=0.0),
        ToolParam("y", "number", "Y position", required=False, default=0.0),
        ToolParam("z", "number", "Z position", required=False, default=0.0),
    ],
    handler=_handle_create_wedge,
)


