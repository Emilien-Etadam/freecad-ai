"""FreeCAD tool handlers (facade).

Public tool definitions and handlers live in ``handlers/`` and ``tool_common``.
This module re-exports the same surface as the monolithic ``freecad_tools.py``
so existing imports (tests, integrations, registry) keep working.
"""

from ..core.executor import execute_code
from . import tool_common
from .handlers import (
    assembly,
    document,
    enclosure,
    inspection,
    modifiers,
    part_creation,
    sketch,
    skills,
    view,
)

_HANDLER_MODULES = (
    part_creation,
    sketch,
    modifiers,
    inspection,
    document,
    enclosure,
    view,
    skills,
    assembly,
)


def _reexport(module):
    """Copy public names and underscore-prefixed helpers into this module."""
    for key, value in vars(module).items():
        if key.startswith("__"):
            continue
        if key.isupper() or key.startswith("_") or key in (
            "get_reported_skill_params",
            "clear_reported_skill_params",
        ):
            globals()[key] = value


_reexport(tool_common)
for _mod in _HANDLER_MODULES:
    _reexport(_mod)

ALL_TOOLS = [
    CREATE_PRIMITIVE,
    CREATE_BODY,
    CREATE_SKETCH,
    CREATE_DATUM_PLANE,
    EDIT_SKETCH,
    PAD_SKETCH,
    POCKET_SKETCH,
    REVOLVE_SKETCH,
    LOFT_SKETCHES,
    SWEEP_SKETCH,
    BOOLEAN_OPERATION,
    TRANSFORM_OBJECT,
    FILLET_EDGES,
    CHAMFER_EDGES,
    CREATE_INNER_RIDGE,
    CREATE_SNAP_TABS,
    CREATE_ENCLOSURE_LID,
    CREATE_WEDGE,
    SCALE_OBJECT,
    SECTION_OBJECT,
    LINEAR_PATTERN,
    POLAR_PATTERN,
    SHELL_OBJECT,
    MIRROR_FEATURE,
    MULTI_TRANSFORM,
    MEASURE,
    DESCRIBE_MODEL,
    LIST_FACES,
    LIST_EDGES,
    LIST_DOCUMENTS,
    SWITCH_DOCUMENT,
    GET_DOCUMENT_STATE,
    CREATE_VARIABLE_SET,
    CREATE_SPREADSHEET,
    SET_EXPRESSION,
    MODIFY_PROPERTY,
    EXPORT_MODEL,
    EXECUTE_CODE,
    RUN_MACRO,
    UNDO,
    REDO,
    UNDO_HISTORY,
    CAPTURE_VIEWPORT,
    SET_VIEW,
    ZOOM_OBJECT,
    REPORT_SKILL_PARAMS,
    USE_SKILL,
    CREATE_ASSEMBLY,
    ADD_ASSEMBLY_JOINT,
    ADD_PART_TO_ASSEMBLY,
    SELECT_GEOMETRY,
]
