"""Unit tests for palette-based theme helpers."""

import pytest

# Qt QPalette.ColorRole values (stable across PySide2/6)
_ROLE_BASE = 9
_ROLE_TEXT = 10
_ROLE_MID = 11
_ROLE_ALT = 12
_ROLE_HIGHLIGHT = 13
_ROLE_HIGHLIGHTED_TEXT = 14
_ROLE_PLACEHOLDER = 17


class _FakeColor:
    def __init__(self, lightness: int, name: str = "#aabbcc"):
        self._lightness = lightness
        self._name = name

    def lightness(self):
        return self._lightness

    def name(self):
        return self._name


class _FakePalette:
    def __init__(self, *, dark: bool = False):
        self._dark = dark

    def color(self, role):
        if role == _ROLE_BASE:
            return _FakeColor(40 if self._dark else 200, "#141414" if self._dark else "#ffffff")
        if role == _ROLE_TEXT:
            return _FakeColor(180 if self._dark else 30, "#dddddd" if self._dark else "#000000")
        if role == _ROLE_MID:
            return _FakeColor(100, "#555555")
        if role == _ROLE_ALT:
            return _FakeColor(60 if self._dark else 210, "#222222")
        if role == _ROLE_HIGHLIGHT:
            return _FakeColor(120, "#336699")
        if role == _ROLE_HIGHLIGHTED_TEXT:
            return _FakeColor(220, "#ffffff")
        if role == _ROLE_PLACEHOLDER:
            return _FakeColor(120, "#888888")
        return _FakeColor(128, "#888888")


@pytest.fixture
def fake_palette_dark():
    return _FakePalette(dark=True)


@pytest.fixture
def fake_palette_light():
    return _FakePalette(dark=False)


def test_colors_from_palette_uses_base_for_chat_bg(fake_palette_dark):
    from freecad_ai.ui.message_view import colors_from_palette

    colors = colors_from_palette(fake_palette_dark)
    assert colors["chat_bg"] == "#141414"
    assert colors["chat_text"] == "#dddddd"


def test_colors_from_palette_light_semantic(fake_palette_light):
    from freecad_ai.ui.message_view import colors_from_palette, _LIGHT_THEME_COLORS

    colors = colors_from_palette(fake_palette_light)
    assert colors["chat_bg"] == "#ffffff"
    assert colors["tool_error_text"] == _LIGHT_THEME_COLORS["tool_error_text"]


def test_qtextedit_stylesheet_uses_palette_roles(fake_palette_dark):
    pytest.importorskip("PySide6", reason="PySide required for theme_palette import")
    from freecad_ai.ui.theme_palette import qtextedit_palette_stylesheet

    sheet = qtextedit_palette_stylesheet(fake_palette_dark)
    assert "#141414" in sheet
    assert "#dddddd" in sheet


def test_render_hint_uses_thinking_color(fake_palette_dark):
    from freecad_ai.ui.message_view import render_hint, _DARK_THEME_COLORS

    html = render_hint("tip text", palette=fake_palette_dark)
    assert _DARK_THEME_COLORS["thinking_text"] in html
    assert "tip text" in html
