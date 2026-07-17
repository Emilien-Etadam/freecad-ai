"""FreeCAD tool handlers.

Each tool wraps a FreeCAD operation in an undo transaction with error handling.
Tools are designed to be called by the LLM via structured tool calling.
"""

import os

from .registry import ToolParam, ToolDefinition, ToolResult
from ..core.executor import execute_code


def _coerce_str_list(value):
    """Coerce a stringified list into an actual list.

    LLMs sometimes send ``"['Face1', 'Face6']"`` (a string) instead of
    ``["Face1", "Face6"]`` (a JSON array).  This helper detects and parses
    that so tool handlers get a real list.
    """
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        import ast
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, list):
                return parsed
        except (ValueError, SyntaxError):
            pass
    return value


def _with_undo(label: str, func, *, create_document_if_missing: bool = False):
    """Run func inside a FreeCAD undo transaction. Returns ToolResult.

    ``create_document_if_missing`` lets pure-creation tools (first primitive,
    body, sketch) start from an empty FreeCAD by creating a new document —
    "make me a die" on a fresh session should just work. Tools that operate
    on existing geometry keep the explicit error.
    """
    from ..core.active_document import (
        get_synced_active_document, refresh_gui_for_document,
    )
    doc = get_synced_active_document()
    if not doc and create_document_if_missing:
        try:
            import FreeCAD as App
            doc = App.newDocument()
            refresh_gui_for_document(doc)
        except Exception:
            doc = None
    if not doc:
        return ToolResult(
            success=False,
            output="",
            error="No active document — open a document in FreeCAD or select its tab.",
        )
    doc.openTransaction(label)
    try:
        result = func(doc)
        doc.recompute()
        doc.commitTransaction()
        return result
    except Exception as e:
        try:
            doc.abortTransaction()
            doc.recompute()
        except Exception:
            pass
        return ToolResult(success=False, output="", error=str(e))


def _get_body_plane(body, plane_name: str):
    """Get a plane (XY/XZ/YZ) from a body's origin.

    Uses index-based access into OriginFeatures with error handling.
    Falls back to searching by Name if OriginFeatures fails (can happen
    in non-English locales where role-based lookup breaks).
    """
    plane_map = {"XY": 3, "XZ": 4, "YZ": 5}
    idx = plane_map.get(plane_name.upper())
    if idx is None:
        return None
    try:
        return body.Origin.OriginFeatures[idx]
    except Exception:
        pass
    # Fallback: search document objects by Name prefix
    try:
        prefix = plane_name.upper() + "_Plane"
        for obj in body.Document.Objects:
            if (obj.Name == prefix or obj.Name.startswith(prefix)) and \
               obj.TypeId == "App::Plane":
                return obj
    except Exception:
        pass
    return None


def _get_body_axis(body, axis_name: str):
    """Get an axis (X/Y/Z) from a body's origin.

    Same fallback strategy as _get_body_plane.
    """
    axis_map = {"X": 0, "Y": 1, "Z": 2}
    idx = axis_map.get(axis_name.upper())
    if idx is None:
        return None
    try:
        return body.Origin.OriginFeatures[idx]
    except Exception:
        pass
    # Fallback: search document objects by Name prefix
    try:
        prefix = axis_name.upper() + "_Axis"
        for obj in body.Document.Objects:
            if (obj.Name == prefix or obj.Name.startswith(prefix)) and \
               obj.TypeId == "App::Line":
                return obj
    except Exception:
        pass
    return None


def _resolve_sketch_attachment(support, face, plane, body_present,
                               support_kind, face_exists, face_planar, in_body):
    """Decide where a sketch attaches. Pure — no FreeCAD calls.

    Inputs are already-inspected facts so this is unit-testable. When the
    GUI-selection fallback is used, the caller passes the selected object as
    ``support`` and its planar face as ``face`` — selection collapses into the
    same inputs as the explicit params.

    ``support_kind``: ``""`` (no support given and no usable selection),
    else ``"missing"`` | ``"plane"`` | ``"solid"`` | ``"other"``.
    ``face_exists`` / ``face_planar`` are meaningful only when ``face`` != "".
    ``in_body`` is the Body name owning the support, or ``None``.

    Returns one of:
      {"mode": "face", "support": str, "sub": str, "in_body": str|None}
      {"mode": "plane", "support": str, "in_body": str|None}
      {"mode": "origin", "plane": str}
      {"mode": "standalone"}
      {"mode": "error", "message": str}
    """
    support = support or ""
    face = face or ""

    if face and not support:
        return {"mode": "error", "message": "`face` requires `support`."}

    if support_kind == "missing":
        return {"mode": "error", "message": f"Object '{support}' not found."}

    if support_kind in ("plane", "solid", "other"):
        if face:
            if not face_exists:
                return {"mode": "error",
                        "message": f"Face '{face}' not found on '{support}'."}
            if not face_planar:
                return {"mode": "error",
                        "message": (f"Face '{face}' on '{support}' is not planar; "
                                    "sketches need a planar face.")}
            return {"mode": "face", "support": support, "sub": face,
                    "in_body": in_body}
        if support_kind == "plane":
            return {"mode": "plane", "support": support, "in_body": in_body}
        if support_kind == "solid":
            return {"mode": "error",
                    "message": (f"`support` '{support}' is a solid; specify a `face` "
                                "(e.g. 'Face6'), or pass a datum/origin plane as "
                                "`support`.")}
        # support_kind == "other" (e.g. a mesh-derived feature, a wire)
        return {"mode": "error",
                "message": (f"`support` '{support}' can't host a sketch on its own; "
                            "specify a planar `face`, or pass a datum/origin plane "
                            "as `support`.")}

    if support_kind:
        # Unknown classification — fail loudly rather than silently falling
        # through to standalone.
        return {"mode": "error",
                "message": f"Unsupported support kind '{support_kind}' for '{support}'."}

    # No support / no usable selection — original behavior.
    if body_present and plane.upper() in ("XY", "XZ", "YZ"):
        return {"mode": "origin", "plane": plane.upper()}
    return {"mode": "standalone"}


def _resolve_datum_plane_attachment(support, face, plane, body_present,
                                    support_kind, face_exists, face_planar, in_body):
    """Reference resolution for create_datum_plane. Pure — no FreeCAD calls.

    Delegates to ``_resolve_sketch_attachment`` and remaps the one datum-specific
    decision: a datum plane cannot be free-floating, so a ``standalone`` result
    (no usable reference) becomes an error. All other modes pass through.
    """
    spec = _resolve_sketch_attachment(
        support, face, plane, body_present,
        support_kind, face_exists, face_planar, in_body)
    if spec["mode"] == "standalone":
        return {"mode": "error",
                "message": ("create_datum_plane needs a reference: pass a plane "
                            "(XY/XZ/YZ) with body_name, or a support object "
                            "(optionally with a face).")}
    return spec


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


def _inspect_edge(obj, edge_name):
    """Return (exists, straight) for ``edge_name`` on ``obj``'s shape.

    Never raises — a missing edge, unavailable Part module, or non-shape object
    yields (False, False). "straight" means the edge's curve is a ``Part.Line``.
    """
    try:
        import Part
    except Exception:
        return (False, False)
    try:
        edge = obj.Shape.getElement(edge_name)
    except Exception:
        return (False, False)
    if edge is None or edge.ShapeType != "Edge":
        return (False, False)
    try:
        return (True, isinstance(edge.Curve, Part.Line))
    except Exception:
        return (True, False)


def _read_planar_selection():
    """Return (object_name, sub_element) for the first usable planar-face or
    plane selection in the GUI, or None.

    Used as a fallback when create_sketch is called with no support/face. Any
    error or non-usable selection (edge, vertex, nothing, non-planar face)
    returns None so the caller falls through to default behavior.
    """
    try:
        import FreeCADGui as Gui
        import Part
    except Exception:
        return None
    try:
        sel = Gui.Selection.getSelectionEx()
    except Exception:
        return None
    for s in sel or []:
        obj = getattr(s, "Object", None)
        if obj is None:
            continue
        subs = getattr(s, "SubElementNames", None) or []
        if subs:
            for sub in subs:
                try:
                    el = obj.Shape.getElement(sub)
                    if (el is not None and el.ShapeType == "Face"
                            and isinstance(el.Surface, Part.Plane)):
                        return (obj.Name, sub)
                except Exception:
                    continue
        elif getattr(obj, "TypeId", "") in _PLANE_TYPE_IDS:
            return (obj.Name, "")
    return None


# ── Helpers ─────────────────────────────────────────────────

def _get_object(doc, name_or_label):
    """Find a document object by internal Name first, then by Label.

    FreeCAD may assign different internal Names than requested (e.g., "Body"
    instead of "EnclosureBase"), so we fall back to Label matching.

    Also handles common LLM naming mistakes:
      - "Sketch0" → "Sketch" (first object has no numeric suffix)
      - "Sketch1" → "Sketch001" (FreeCAD uses zero-padded 3-digit suffixes)
      - "Body0" → "Body", "Body1" → "Body001", etc.
    """
    obj = doc.getObject(name_or_label)
    if obj:
        return obj
    # Fallback: search by Label
    for o in doc.Objects:
        if o.Label == name_or_label:
            return o

    # Try common LLM naming variants (e.g. "Sketch0" → "Sketch",
    # "Sketch1" → "Sketch001", "Pad2" → "Pad002")
    import re
    m = re.match(r'^(.+?)(\d+)$', name_or_label)
    if m:
        base, num_str = m.group(1), m.group(2)
        num = int(num_str)
        variants = []
        if num == 0:
            # "Sketch0" → try "Sketch" (first object has no suffix)
            variants.append(base)
        else:
            # "Sketch1" → try "Sketch001"; "Sketch12" → try "Sketch012"
            variants.append(f"{base}{num:03d}")
        # Also try without leading zeros: "Sketch001" when given "Sketch1"
        if len(num_str) == 1 and num > 0:
            variants.append(f"{base}0{num_str}")  # e.g. "Sketch01"
        for variant in variants:
            obj = doc.getObject(variant)
            if obj:
                return obj
            for o in doc.Objects:
                if o.Label == variant:
                    return o

    return None


def _suggest_similar(doc, name_or_label, type_filter=None):
    """Return a hint string listing objects with similar names.

    Args:
        doc: FreeCAD document
        name_or_label: The name that was not found
        type_filter: Optional TypeId substring to filter (e.g. "Sketcher" or "Body")
    """
    import re
    # Extract the base name (letters) for matching
    base = re.match(r'^[A-Za-z_]+', name_or_label)
    base_str = base.group(0).lower() if base else ""

    candidates = []
    for o in doc.Objects:
        if type_filter and type_filter not in o.TypeId:
            continue
        # Match by base name similarity
        o_base = re.match(r'^[A-Za-z_]+', o.Name)
        o_base_str = o_base.group(0).lower() if o_base else ""
        if base_str and o_base_str == base_str:
            candidates.append(o.Name)
        elif base_str and base_str in o.Label.lower():
            candidates.append(o.Name)

    if not candidates:
        # No base-name match — list all objects of that type
        for o in doc.Objects:
            if type_filter and type_filter not in o.TypeId:
                continue
            # Skip internal objects like Origin, axes, planes
            if o.TypeId.startswith("App::"):
                continue
            candidates.append(o.Name)

    if candidates:
        return f" Available: {', '.join(candidates[:8])}"
    return ""


def _find_body_for(doc, obj):
    """Find the PartDesign body containing an object, if any."""
    target_name = obj.Name
    for o in doc.Objects:
        if hasattr(o, "TypeId") and o.TypeId == "PartDesign::Body":
            if hasattr(o, "Group"):
                for member in o.Group:
                    if member.Name == target_name:
                        return o
