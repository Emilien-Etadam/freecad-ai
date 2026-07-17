"""Creation tools auto-create a document on an empty FreeCAD session.

"Make me a die" from a fresh FreeCAD (no document open) used to fail on
every tool with "No active document". The pure-creation entry points
(create_primitive, create_body, create_sketch) now create one; tools that
operate on existing geometry keep the explicit error.
"""

from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[2] / "freecad_ai" / "tools"


def _src(relpath: str) -> str:
    return (_TOOLS / relpath).read_text()


class TestAutoDocument:
    def test_with_undo_supports_document_creation(self):
        src = _src("tool_common.py")
        body = src.split("def _with_undo")[1].split("\ndef ")[0]
        assert "create_document_if_missing" in body
        assert "App.newDocument()" in body
        assert "refresh_gui_for_document" in body
        # The explicit error survives for the default path
        assert "No active document" in body

    def test_creation_tools_opt_in(self):
        part = _src("handlers/part_creation.py")
        assert ('_with_undo(f"Create {shape_type}", do, '
                'create_document_if_missing=True)') in part
        assert ('_with_undo("Create Body", do, '
                'create_document_if_missing=True)') in part
        sketch = _src("handlers/sketch.py")
        assert ('_with_undo("Create Sketch", do, '
                'create_document_if_missing=True)') in sketch

    def test_mutating_tools_do_not_opt_in(self):
        # Fillet/pocket/pad & co. operate on existing geometry — creating an
        # empty document for them would only defer the error confusingly.
        for relpath in ("handlers/modifiers.py", "handlers/document.py"):
            assert "create_document_if_missing" not in _src(relpath), relpath
