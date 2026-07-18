"""Every global name loaded by tools/ code must resolve at import time.

Two real bugs of this class shipped after the mixin/handlers refactors:
``json.loads`` in a module that never imported json (hung every tool call),
and ``_with_undo`` referenced by handlers whose ``from ..tool_common import *``
imported nothing (no ``__all__``, underscore-prefixed names) — every
structured tool raised NameError. ``compileall`` cannot catch either.

This walks each function's bytecode (LOAD_GLOBAL, nested code objects
included) and asserts the names exist in module globals or builtins.
Runtime-only names (FreeCAD, Qt aliases bound at runtime, …) are excluded.
"""

import builtins
import dis
import importlib
import types

import pytest

# Importable headless (no Qt, no FreeCAD at module import).
MODULES = [
    "freecad_ai.tools.tool_common",
    "freecad_ai.tools.handlers.assembly",
    "freecad_ai.tools.handlers.document",
    "freecad_ai.tools.handlers.enclosure",
    "freecad_ai.tools.handlers.inspection",
    "freecad_ai.tools.handlers.modifiers",
    "freecad_ai.tools.handlers.part_creation",
    "freecad_ai.tools.handlers.sketch",
    "freecad_ai.tools.handlers.skills",
    "freecad_ai.tools.handlers.view",
    "freecad_ai.tools.registry",
    "freecad_ai.tools.reranker",
    "freecad_ai.tools.setup",
    "freecad_ai.core.active_document",
    "freecad_ai.core.conversation",
    "freecad_ai.core.executor",
    "freecad_ai.core.system_prompt",
    "freecad_ai.llm.client",
    "freecad_ai.llm.providers",
]

# Names only bound at runtime inside FreeCAD / by conditional imports.
RUNTIME_ONLY = {
    "FreeCAD", "FreeCADGui", "App", "Gui", "Part", "PartDesign",
    "Sketcher", "Draft", "Mesh", "MeshPart", "Import", "ImportGui",
    "QtWidgets", "QtCore", "QtGui",
    "__file__",  # absent under some exec() launch modes; guarded in code
}


def _iter_code_objects(code):
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from _iter_code_objects(const)


def _loaded_globals(code):
    for instr in dis.get_instructions(code):
        if instr.opname in ("LOAD_GLOBAL", "LOAD_NAME"):
            yield instr.argval


@pytest.mark.parametrize("module_name", MODULES)
def test_all_global_loads_resolve(module_name):
    module = importlib.import_module(module_name)
    missing = set()
    for obj in vars(module).values():
        if isinstance(obj, types.FunctionType) and obj.__module__ == module_name:
            for code in _iter_code_objects(obj.__code__):
                for name in _loaded_globals(code):
                    if name in RUNTIME_ONLY:
                        continue
                    if hasattr(module, name) or hasattr(builtins, name):
                        continue
                    missing.add(f"{obj.__qualname__}: {name}")
    assert not missing, (
        f"{module_name} loads undefined globals (NameError at runtime): "
        f"{sorted(missing)}")


def test_star_import_actually_exports_the_helpers():
    """tool_common must export its underscore helpers via __all__ —
    without it, `from ..tool_common import *` in every handler imports
    nothing and each tool call raises NameError."""
    from freecad_ai.tools import tool_common
    from freecad_ai.tools.handlers import part_creation, sketch, modifiers

    assert "_with_undo" in tool_common.__all__
    for mod in (part_creation, sketch, modifiers):
        assert hasattr(mod, "_with_undo"), mod.__name__
        assert hasattr(mod, "_get_object"), mod.__name__
