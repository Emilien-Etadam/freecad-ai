"""Regression tests for the split freecad_tools facade."""

import freecad_ai.tools.freecad_tools as ft


def test_all_tools_count():
    assert len(ft.ALL_TOOLS) == 51


def test_backward_compat_private_helpers():
    assert callable(ft._resolve_sketch_attachment)
    assert callable(ft._resolve_datum_plane_attachment)
    assert callable(ft._classify_face)
    assert callable(ft._handle_create_primitive)


def test_execute_code_reexported():
    assert callable(ft.execute_code)
