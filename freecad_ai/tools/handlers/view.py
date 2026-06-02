"""FreeCAD view."""

import os

from ..registry import ToolParam, ToolDefinition, ToolResult
from ...core.executor import execute_code
from ..tool_common import *  # noqa: F403

# ── Interactive selection ─────────────────────────────────────

def _handle_select_geometry(prompt="Select geometry", select_type="any", max_count=0):
    """Open an interactive selection panel and wait for user picks."""
    from freecad_ai.ui.selection_panel import SelectionPanel

    panel = SelectionPanel(prompt=prompt, select_type=select_type, max_count=max_count)
    selections = panel.exec()

    if not selections:
        return ToolResult(
            success=True,
            output="User cancelled selection or selected nothing.",
            data={"selections": []},
        )

    lines = [
        f"- {s['object']}.{s['sub_element']} at "
        f"({s['point'][0]:.2f}, {s['point'][1]:.2f}, {s['point'][2]:.2f})"
        for s in selections
    ]
    return ToolResult(
        success=True,
        output="Selected:\n" + "\n".join(lines),
        data={"selections": selections},
    )


SELECT_GEOMETRY = ToolDefinition(
    name="select_geometry",
    description=(
        "Ask the user to select geometry (edges, faces, vertices) in the 3D viewport. "
        "Opens an interactive selection panel and waits for the user to click on "
        "geometry and press Done."
    ),
    category="interactive",
    parameters=[
        ToolParam("prompt", "string",
                  "Instruction shown to the user, e.g. 'Select edges to fillet'",
                  required=False, default="Select geometry"),
        ToolParam("select_type", "string",
                  "Type of geometry to accept",
                  required=False, default="any",
                  enum=["any", "edge", "face", "vertex"]),
        ToolParam("max_count", "integer",
                  "Max selections (0=unlimited)",
                  required=False, default=0),
    ],
    handler=_handle_select_geometry,
)


# ── capture_viewport ───────────────────────────────────────

def _handle_capture_viewport(
    filepath: str,
    width: int = 800,
    height: int = 600,
    background: str = "Current",
) -> ToolResult:
    """Save a screenshot of the 3D viewport to a file."""
    from ..utils.viewport import capture_viewport_image

    img_bytes = capture_viewport_image(width, height, background)
    if img_bytes is None:
        return ToolResult(success=False, output="", error="No active document or view")

    try:
        with open(filepath, "wb") as f:
            f.write(img_bytes)
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Failed to write file: {e}")

    return ToolResult(
        success=True,
        output=f"Screenshot saved to {filepath} ({width}x{height}, background={background})",
        data={"filepath": filepath, "width": width, "height": height},
    )


CAPTURE_VIEWPORT = ToolDefinition(
    name="capture_viewport",
    description="Save a screenshot of the 3D viewport to a file.",
    category="view",
    parameters=[
        ToolParam("filepath", "string", "Output file path (e.g. /tmp/screenshot.png)"),
        ToolParam("width", "integer", "Image width in pixels",
                  required=False, default=800),
        ToolParam("height", "integer", "Image height in pixels",
                  required=False, default=600),
        ToolParam("background", "string",
                  "Background color for the screenshot",
                  required=False, default="Current",
                  enum=["Current", "White", "Black", "Transparent"]),
    ],
    handler=_handle_capture_viewport,
)


# ── set_view ───────────────────────────────────────────────

def _handle_set_view(
    orientation: str,
    fit_all: bool = True,
    projection: str = "",
) -> ToolResult:
    """Set the camera to a standard view orientation."""
    import FreeCADGui as Gui

    if not Gui.ActiveDocument:
        return ToolResult(success=False, output="", error="No active document")

    view = Gui.ActiveDocument.ActiveView

    view_methods = {
        "isometric": "viewIsometric",
        "front": "viewFront",
        "back": "viewRear",
        "top": "viewTop",
        "bottom": "viewBottom",
        "left": "viewLeft",
        "right": "viewRight",
    }
    method_name = view_methods.get(orientation.lower())
    if not method_name:
        return ToolResult(
            success=False, output="",
            error=f"Unknown orientation: {orientation}. "
                  f"Use: {', '.join(view_methods.keys())}"
        )

    try:
        getattr(view, method_name)()

        if fit_all:
            Gui.SendMsgToActiveView("ViewFit")

        if projection:
            view.setCameraType(projection)
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Set view failed: {e}")

    parts = [f"Set view to {orientation}"]
    if fit_all:
        parts.append("fit all")
    if projection:
        parts.append(f"projection={projection}")
    return ToolResult(
        success=True,
        output=", ".join(parts),
        data={"orientation": orientation, "fit_all": fit_all, "projection": projection},
    )


SET_VIEW = ToolDefinition(
    name="set_view",
    description=(
        "Set the camera to a standard view orientation (front, top, isometric, etc.) "
        "and optionally adjust zoom and projection mode."
    ),
    category="view",
    parameters=[
        ToolParam("orientation", "string", "Camera orientation",
                  enum=["isometric", "front", "back", "top", "bottom", "left", "right"]),
        ToolParam("fit_all", "boolean", "Zoom to fit all objects in view",
                  required=False, default=True),
        ToolParam("projection", "string", "Projection mode",
                  required=False, default="",
                  enum=["Orthographic", "Perspective"]),
    ],
    handler=_handle_set_view,
)


# ── zoom_object ────────────────────────────────────────────

def _handle_zoom_object(object_name: str) -> ToolResult:
    """Zoom the viewport to focus on a specific object."""
    import FreeCAD as App
    import FreeCADGui as Gui

    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")

    obj = _get_object(doc, object_name)
    if not obj:
        return ToolResult(
            success=False, output="",
            error=f"Object '{object_name}' not found"
        )

    try:
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(obj)
        Gui.SendMsgToActiveView("ViewSelection")
        Gui.Selection.clearSelection()
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Zoom failed: {e}")

    return ToolResult(
        success=True,
        output=f"Zoomed to object '{obj.Label}'",
        data={"object_name": obj.Name, "label": obj.Label},
    )


ZOOM_OBJECT = ToolDefinition(
    name="zoom_object",
    description="Zoom the viewport to focus on a specific object.",
    category="view",
    parameters=[
        ToolParam("object_name", "string", "Name or label of the object to zoom to"),
    ],
    handler=_handle_zoom_object,
)


