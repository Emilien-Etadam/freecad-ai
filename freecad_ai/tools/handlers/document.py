"""FreeCAD document."""

import os

from ..registry import ToolParam, ToolDefinition, ToolResult
from ...core.executor import execute_code
from ..tool_common import *  # noqa: F403

# ── list_documents ─────────────────────────────────────────

def _handle_list_documents() -> ToolResult:
    """List all open FreeCAD documents."""
    import FreeCAD as App
    from ...core.active_document import resolve_active_document

    docs = list(App.listDocuments().values())
    if not docs:
        return ToolResult(success=True, output="No documents open.",
                          data={"documents": []})

    active = resolve_active_document()
    active_name = active.Name if active else ""

    lines = [f"## Open Documents ({len(docs)})"]
    doc_data = []
    for doc in docs:
        obj_count = len(doc.Objects)
        marker = " (active)" if doc.Name == active_name else ""
        modified = " *" if doc.Modified else ""
        path = doc.FileName or "(unsaved)"
        lines.append(
            f"- **{doc.Name}**{marker}{modified} — "
            f"{obj_count} objects — {path}"
        )
        doc_data.append({
            "name": doc.Name,
            "label": doc.Label,
            "active": doc.Name == active_name,
            "object_count": obj_count,
            "modified": doc.Modified,
            "path": doc.FileName,
        })

    return ToolResult(success=True, output="\n".join(lines),
                      data={"documents": doc_data})


LIST_DOCUMENTS = ToolDefinition(
    name="list_documents",
    description="List all open FreeCAD documents with object counts and active indicator.",
    category="query",
    parameters=[],
    handler=_handle_list_documents,
)


# ── switch_document ───────────────────────────────────────

def _handle_switch_document(document_name: str) -> ToolResult:
    """Switch the active document."""
    import FreeCAD as App
    from ...core.active_document import sync_app_active_document, refresh_gui_for_document

    docs = App.listDocuments()
    doc = docs.get(document_name)
    if not doc:
        # Try matching by label
        for d in docs.values():
            if d.Label == document_name:
                doc = d
                break
    if not doc:
        available = ", ".join(docs.keys())
        return ToolResult(success=False, output="",
                          error=f"Document '{document_name}' not found. Available: {available}")

    sync_app_active_document(doc)
    refresh_gui_for_document(doc)

    return ToolResult(
        success=True,
        output=f"Switched to document '{doc.Name}' ({len(doc.Objects)} objects).",
        data={"name": doc.Name, "label": doc.Label},
    )


SWITCH_DOCUMENT = ToolDefinition(
    name="switch_document",
    description="Switch the active FreeCAD document by name or label.",
    category="query",
    parameters=[
        ToolParam("document_name", "string", "Name or label of the document to activate"),
    ],
    handler=_handle_switch_document,
)


# ── get_document_state ──────────────────────────────────────

def _handle_get_document_state() -> ToolResult:
    """Get the current document state — all objects and their properties."""
    import FreeCAD as App
    from ...core.context import get_document_context

    if not App.ActiveDocument:
        return ToolResult(
            success=False,
            output="",
            error="No active document",
            data={},
        )
    ctx = get_document_context()
    return ToolResult(
        success=True,
        output=ctx,
        data={"context": ctx},
    )


GET_DOCUMENT_STATE = ToolDefinition(
    name="get_document_state",
    description="Get the current document state including all objects, their types, labels, and key properties.",
    category="query",
    parameters=[],
    handler=_handle_get_document_state,
)


# ── create_variable_set ────────────────────────────────────

def _handle_create_variable_set(
    variables: dict | None = None,
    label: str = "Variables",
) -> ToolResult:
    """Create an App::VarSet with typed properties for parametric modeling."""

    def do(doc):
        if not variables:
            return ToolResult(success=False, output="",
                              error="No variables provided. Pass a dict like "
                                    "{\"length\": 50, \"width\": 30}.")

        _PROP_TYPES = {
            int: "App::PropertyInteger",
            float: "App::PropertyFloat",
            str: "App::PropertyString",
            bool: "App::PropertyBool",
        }

        vs = doc.addObject("App::VarSet", label)
        var_names = []
        for name, value in variables.items():
            prop_type = _PROP_TYPES.get(type(value), "App::PropertyFloat")
            try:
                vs.addProperty(prop_type, name, "Parameters", "")
                setattr(vs, name, value)
            except Exception as e:
                return ToolResult(
                    success=False, output="",
                    error=f"Invalid variable name '{name}': {e}")
            var_names.append(name)

        doc.recompute()

        names_str = ", ".join(f"{n}={variables[n]}" for n in var_names)
        usage = ", ".join(f'"{vs.Label}.{n}"' for n in var_names[:3])
        if len(var_names) > 3:
            usage += ", ..."

        return ToolResult(
            success=True,
            output=(f"Created VarSet '{vs.Name}' with {len(var_names)} variables: "
                    f"{names_str}. "
                    f"Reference in expressions as {usage}. "
                    f"Pass these as width/height/length values in create_sketch and pad_sketch."),
            data={"name": vs.Name, "label": vs.Label,
                  "variables": dict(variables)},
        )

    return _with_undo("Create Variable Set", do)


CREATE_VARIABLE_SET = ToolDefinition(
    name="create_variable_set",
    description=(
        "Create a VarSet (App::VarSet) with named, typed variables for parametric modeling. "
        "Variables appear as editable properties in the Data panel. "
        "After creation, pass variable references as dimension values in create_sketch "
        "(e.g. width='Variables.length') and pad_sketch (e.g. length='Variables.height'). "
        "Example: create_variable_set(variables={\"length\": 50, \"width\": 30, \"height\": 20}). "
        "Alternative: use create_spreadsheet for a spreadsheet-based approach."
    ),
    category="modeling",
    parameters=[
        ToolParam("variables", "object",
                  "Dict of variable names and values, e.g. {\"length\": 50, \"wall\": 2}"),
        ToolParam("label", "string", "Display label for the variable set",
                  required=False, default="Variables"),
    ],
    handler=_handle_create_variable_set,
)


# ── create_spreadsheet ────────────────────────────────────

def _handle_create_spreadsheet(
    variables: dict | None = None,
    label: str = "Variables",
) -> ToolResult:
    """Create a Spreadsheet with named cells for parametric modeling."""

    def do(doc):
        if not variables:
            return ToolResult(success=False, output="",
                              error="No variables provided. Pass a dict like "
                                    "{\"length\": 50, \"width\": 30}.")

        sheet = doc.addObject("Spreadsheet::Sheet", label)
        var_names = []
        for i, (name, value) in enumerate(variables.items()):
            row = i + 1
            cell = f"A{row}"
            sheet.set(f"B{row}", str(name))
            sheet.set(cell, str(value))
            try:
                sheet.setAlias(cell, name)
            except Exception as e:
                return ToolResult(
                    success=False, output="",
                    error=f"Invalid variable name '{name}': {e}. "
                          "Avoid names that look like cell addresses (e.g. A1, B2).")
            var_names.append(name)

        doc.recompute()

        names_str = ", ".join(f"{n}={variables[n]}" for n in var_names)
        usage = ", ".join(f'"{sheet.Label}.{n}"' for n in var_names[:3])
        if len(var_names) > 3:
            usage += ", ..."

        return ToolResult(
            success=True,
            output=(f"Created Spreadsheet '{sheet.Name}' with {len(var_names)} variables: "
                    f"{names_str}. "
                    f"Reference in expressions as {usage}. "
                    f"Pass these as width/height/length values in create_sketch and pad_sketch."),
            data={"name": sheet.Name, "label": sheet.Label,
                  "variables": dict(variables)},
        )

    return _with_undo("Create Spreadsheet", do)


CREATE_SPREADSHEET = ToolDefinition(
    name="create_spreadsheet",
    description=(
        "Create a Spreadsheet with named variables (cell aliases) for parametric modeling. "
        "Variables are stored as named cells that can be edited in the Spreadsheet workbench. "
        "After creation, pass variable references as dimension values in create_sketch "
        "(e.g. width='Variables.length') and pad_sketch (e.g. length='Variables.height'). "
        "Supports formulas in cells (e.g. '=A1*2'). "
        "Alternative: use create_variable_set for a cleaner property-based approach."
    ),
    category="modeling",
    parameters=[
        ToolParam("variables", "object",
                  "Dict of variable names and values, e.g. {\"length\": 50, \"wall\": 2}"),
        ToolParam("label", "string", "Display label for the spreadsheet",
                  required=False, default="Variables"),
    ],
    handler=_handle_create_spreadsheet,
)


# ── set_expression ─────────────────────────────────────────

def _handle_set_expression(
    object_name: str,
    property_name: str,
    expression: str,
) -> ToolResult:
    """Bind an object property to an expression."""

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            hint = _suggest_similar(doc, object_name)
            return ToolResult(success=False, output="",
                              error=f"Object '{object_name}' not found.{hint}")

        # Validate property exists — skip check for indexed/nested properties
        # like "Constraints[8]" or "Placement.Base.x" since hasattr doesn't
        # handle those.
        base_prop = property_name.split("[")[0].split(".")[0]
        if not hasattr(obj, base_prop):
            return ToolResult(
                success=False, output="",
                error=f"Object '{object_name}' has no property '{base_prop}'")

        # Clearing an expression
        if not expression or expression.strip() == "":
            obj.setExpression(property_name, None)
            doc.recompute()
            return ToolResult(
                success=True,
                output=f"Cleared expression on {object_name}.{property_name}",
                data={"name": object_name, "property": property_name},
            )

        # Validate expression before applying
        try:
            err = obj.setExpression(property_name, expression)
        except Exception as e:
            return ToolResult(
                success=False, output="",
                error=f"Invalid expression '{expression}' for "
                      f"{object_name}.{property_name}: {e}")

        doc.recompute()

        # Read back the computed value
        try:
            computed = getattr(obj, property_name)
            value_str = f" = {computed}"
        except Exception:
            value_str = ""

        return ToolResult(
            success=True,
            output=(f"Bound {object_name}.{property_name} to expression "
                    f"'{expression}'{value_str}"),
            data={"name": object_name, "property": property_name,
                  "expression": expression},
        )

    return _with_undo("Set Expression", do)


SET_EXPRESSION = ToolDefinition(
    name="set_expression",
    description=(
        "Bind an object property to an expression for parametric relationships. "
        "Use with create_variable_set to make models parametric. "
        "Examples: set_expression('Pad', 'Length', 'Variables.height') for pad length, "
        "set_expression('Sketch', 'Constraints[0]', 'Variables.width') for sketch constraints. "
        "Also supports formulas: 'Variables.length * 2'. "
        "Pass empty expression to clear the binding."
    ),
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object"),
        ToolParam("property_name", "string",
                  "Property to bind (e.g. Length, Width, Height, Radius)"),
        ToolParam("expression", "string",
                  "Expression string (e.g. 'Variables.length', 'Variables.wall * 2'). "
                  "Empty string clears the expression."),
    ],
    handler=_handle_set_expression,
)


# ── modify_property ─────────────────────────────────────────

def _resolve_relative_value(current, expr: str):
    """Resolve a relative expression against a current numeric value.

    Supports: "+10%", "-20%", "*1.5", "+5", "-3", or absolute values.
    Returns the resolved value, or the expression unchanged if not numeric.
    """
    if not isinstance(expr, str):
        return expr
    expr = expr.strip()
    if not expr:
        return expr

    try:
        current_float = float(current)
    except (TypeError, ValueError):
        return expr  # Current value isn't numeric — can't do relative

    # Percentage: "+10%", "-20%"
    if expr.endswith("%"):
        try:
            pct = float(expr[:-1])
            return current_float * (1 + pct / 100)
        except ValueError:
            pass

    # Multiply: "*1.5", "*2"
    if expr.startswith("*"):
        try:
            factor = float(expr[1:])
            return current_float * factor
        except ValueError:
            pass

    # Add/subtract: "+5", "-3"
    if expr.startswith("+") or (expr.startswith("-") and len(expr) > 1):
        try:
            delta = float(expr)
            return current_float + delta
        except ValueError:
            pass

    return expr


def _handle_modify_property(
    object_name: str,
    property_name: str,
    value: str | int | float | bool | list = "",
) -> ToolResult:
    """Modify a property on an object."""

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found")

        if not hasattr(obj, property_name):
            return ToolResult(
                success=False, output="",
                error=f"Object '{object_name}' has no property '{property_name}'"
            )

        current = getattr(obj, property_name)
        resolved = _resolve_relative_value(current, value)

        # Report old→new for relative changes
        if resolved != value:
            msg = f"Set {object_name}.{property_name} = {resolved} (was {current}, applied {value})"
        else:
            msg = f"Set {object_name}.{property_name} = {resolved}"

        setattr(obj, property_name, resolved)
        return ToolResult(
            success=True,
            output=msg,
            data={"name": object_name, "property": property_name,
                  "value": resolved, "previous": current},
        )

    return _with_undo("Modify Property", do)


MODIFY_PROPERTY = ToolDefinition(
    name="modify_property",
    description=(
        "Modify a property on a document object (e.g. Length, Width, Height, Radius). "
        "Values can be absolute (50) or relative expressions: "
        "'+10%' (increase by 10%), '-20%' (decrease by 20%), '*1.5' (multiply by 1.5), "
        "'+5' (add 5mm), '-3' (subtract 3mm)."
    ),
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object"),
        ToolParam("property_name", "string", "Name of the property to modify"),
        ToolParam("value", "string",
                  "New value or relative expression (e.g. 50, '+10%', '*1.5', '+5')"),
    ],
    handler=_handle_modify_property,
)


# ── export_model ────────────────────────────────────────────

def _handle_export_model(
    format: str,
    filename: str,
    objects: list | None = None,
) -> ToolResult:
    """Export the model to a file."""
    import FreeCAD as App
    import Part
    import Mesh

    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")

    if objects:
        objs = [_get_object(doc, n) for n in objects if _get_object(doc, n)]
    else:
        objs = [o for o in doc.Objects if hasattr(o, "Shape")]

    if not objs:
        return ToolResult(success=False, output="", error="No objects to export")

    fmt = format.lower()
    try:
        if fmt == "stl":
            Mesh.export(objs, filename)
        elif fmt in ("step", "stp"):
            Part.export(objs, filename)
        elif fmt in ("iges", "igs"):
            Part.export(objs, filename)
        else:
            return ToolResult(
                success=False, output="",
                error=f"Unknown format: {format}. Use: stl, step, iges"
            )
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Export failed: {e}")

    return ToolResult(
        success=True,
        output=f"Exported {len(objs)} object(s) to {filename} ({fmt.upper()})",
        data={"filename": filename, "format": fmt, "object_count": len(objs)},
    )


EXPORT_MODEL = ToolDefinition(
    name="export_model",
    description="Export objects to a file (STL, STEP, or IGES format).",
    category="file",
    parameters=[
        ToolParam("format", "string", "Export format", enum=["stl", "step", "iges"]),
        ToolParam("filename", "string", "Output file path"),
        ToolParam("objects", "array", "Object names to export (all if omitted)", required=False,
                  items={"type": "string"}),
    ],
    handler=_handle_export_model,
)


# ── execute_code ────────────────────────────────────────────

def _handle_execute_code(code: str) -> ToolResult:
    """Execute arbitrary Python code (fallback tool)."""
    from ...core.active_document import resolve_active_document
    from ...core.dangerous_mode import get_dangerous_mode
    from .. import freecad_tools

    result = freecad_tools.execute_code(code, skip_safety=get_dangerous_mode().active)
    if result.success:
        output = result.stdout.strip() if result.stdout.strip() else "Code executed successfully"
        doc = resolve_active_document()
        data = {"stdout": result.stdout}
        if doc:
            data["document"] = doc.Name
        return ToolResult(success=True, output=output, data=data)
    else:
        return ToolResult(success=False, output=result.stdout, error=result.stderr)


EXECUTE_CODE = ToolDefinition(
    name="execute_code",
    description="Execute arbitrary Python code in FreeCAD's interpreter. Use this as a fallback when structured tools don't cover the needed operation. The code has access to FreeCAD, Part, PartDesign, Sketcher, Draft modules.",
    category="general",
    parameters=[
        ToolParam("code", "string", "Python code to execute"),
    ],
    handler=_handle_execute_code,
)


# ── run_macro ────────────────────────────────────────────────

def _macro_allowed_dirs() -> list:
    """Enumerable macro dirs for safe-mode resolution."""
    dirs = []
    try:
        import FreeCAD as App
        d = App.getUserMacroDir(True)  # True = create if missing
        if d:
            dirs.append(d)
    except Exception:
        pass
    try:
        from ..config import USER_TOOLS_DIR
        dirs.append(USER_TOOLS_DIR)
    except Exception:
        pass
    return dirs


def _active_doc_dir() -> "str | None":
    try:
        import FreeCAD as App
        doc = App.ActiveDocument
        fn = getattr(doc, "FileName", "") if doc else ""
        return os.path.dirname(fn) if fn else None
    except Exception:
        return None


def _handle_run_macro(macro: str) -> ToolResult:
    """Run an existing FreeCAD macro file and return its console output."""
    from ...core.dangerous_mode import get_dangerous_mode
    from .. import freecad_tools
    from ..macro_runner import resolve_macro_path

    dangerous = get_dangerous_mode().active
    path, err = resolve_macro_path(
        macro, freecad_tools._macro_allowed_dirs(), dangerous=dangerous,
        active_doc_dir=freecad_tools._active_doc_dir(),
    )
    if err:
        return ToolResult(success=False, output="", error=err)
    try:
        with open(path, "r", encoding="utf-8") as f:
            code = f.read()
    except (OSError, UnicodeDecodeError) as e:
        return ToolResult(success=False, output="", error=f"Could not read macro: {e}")

    result = freecad_tools.execute_code(code, skip_safety=dangerous)
    if result.success:
        out = result.stdout.strip() or f"Macro '{macro}' ran successfully (no output)."
        return ToolResult(success=True, output=out,
                          data={"macro": path, "stdout": result.stdout})
    return ToolResult(success=False, output=result.stdout, error=result.stderr)


RUN_MACRO = ToolDefinition(
    name="run_macro",
    description=(
        "Run an EXISTING FreeCAD macro file and return its console output "
        "(stdout/stderr). Use this to execute a macro the user already has on "
        "disk, e.g. a test harness. In normal mode, pass a bare macro NAME "
        "(without extension) that lives in FreeCAD's macro directory; file "
        "paths are refused unless the user has enabled Dangerous mode. Use "
        "execute_code instead when you want to run code you are writing inline."
    ),
    category="general",
    parameters=[
        ToolParam("macro", "string",
                  "Macro name (normal mode) or file path (Dangerous mode only)."),
    ],
    handler=_handle_run_macro,
)


# ── undo ────────────────────────────────────────────────────

def _handle_undo(steps: int = 1, until: str = "") -> ToolResult:
    """Undo operations. Either N steps or until a named transaction is reached."""
    import FreeCAD as App

    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")

    available = doc.UndoCount
    if available == 0:
        return ToolResult(
            success=False, output="",
            error="Nothing to undo (undo stack is empty)"
        )

    # Get undo stack names for context
    undo_names = doc.UndoNames if hasattr(doc, "UndoNames") else []

    if until:
        # Undo until we find the named transaction
        query = until.lower()
        found = False
        for i, name in enumerate(undo_names):
            if query in name.lower():
                steps = i + 1
                found = True
                break
        if not found:
            stack_str = ", ".join(undo_names[:10])
            return ToolResult(
                success=False, output="",
                error=f"Transaction '{until}' not found in undo stack. "
                      f"Recent: {stack_str}")

    actual = min(steps, available)
    undone_names = list(undo_names[:actual])
    for i in range(actual):
        doc.undo()
    doc.recompute()

    # Show what was undone and what's left
    remaining = doc.UndoCount
    redo_count = doc.RedoCount if hasattr(doc, "RedoCount") else 0
    output = f"Undid {actual} operation(s): {', '.join(undone_names)}"
    if remaining > 0:
        output += f"\n{remaining} more undo(s) available"
    if redo_count > 0:
        output += f" | {redo_count} redo(s) available"

    return ToolResult(
        success=True,
        output=output,
        data={"steps": actual, "undone": undone_names},
    )


UNDO = ToolDefinition(
    name="undo",
    description=(
        "Undo operations. Use steps=N to undo N operations, or "
        "until='name' to undo back to a named transaction (e.g. 'Pad Sketch'). "
        "Returns what was undone and how many undo/redo steps remain."
    ),
    category="general",
    parameters=[
        ToolParam("steps", "integer", "Number of operations to undo", required=False, default=1),
        ToolParam("until", "string",
                  "Undo until this transaction name is reached (substring match). "
                  "Overrides steps.", required=False, default=""),
    ],
    handler=_handle_undo,
)


def _handle_redo(steps: int = 1) -> ToolResult:
    """Redo previously undone operations."""
    import FreeCAD as App

    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")

    available = doc.RedoCount if hasattr(doc, "RedoCount") else 0
    if available == 0:
        return ToolResult(
            success=False, output="",
            error="Nothing to redo (redo stack is empty)"
        )

    redo_names = doc.RedoNames if hasattr(doc, "RedoNames") else []
    actual = min(steps, available)
    redone_names = list(redo_names[:actual])
    for i in range(actual):
        doc.redo()
    doc.recompute()

    return ToolResult(
        success=True,
        output=f"Redid {actual} operation(s): {', '.join(redone_names)}",
        data={"steps": actual, "redone": redone_names},
    )


REDO = ToolDefinition(
    name="redo",
    description="Redo previously undone operations.",
    category="general",
    parameters=[
        ToolParam("steps", "integer", "Number of operations to redo", required=False, default=1),
    ],
    handler=_handle_redo,
)


def _handle_undo_history() -> ToolResult:
    """Show the undo/redo stack."""
    import FreeCAD as App

    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")

    undo_names = list(doc.UndoNames) if hasattr(doc, "UndoNames") else []
    redo_names = list(doc.RedoNames) if hasattr(doc, "RedoNames") else []

    lines = []
    if undo_names:
        lines.append(f"**Undo stack ({len(undo_names)}):** (most recent first)")
        for i, name in enumerate(undo_names):
            lines.append(f"  {i + 1}. {name}")
    else:
        lines.append("Undo stack is empty.")

    if redo_names:
        lines.append(f"**Redo stack ({len(redo_names)}):**")
        for i, name in enumerate(redo_names):
            lines.append(f"  {i + 1}. {name}")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        data={"undo": undo_names, "redo": redo_names},
    )


UNDO_HISTORY = ToolDefinition(
    name="undo_history",
    description=(
        "Show the undo/redo stack with named transactions. Use this to "
        "see what can be undone or redone before calling undo/redo."
    ),
    category="query",
    parameters=[],
    handler=_handle_undo_history,
)


