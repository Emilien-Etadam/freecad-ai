"""Regression tests for chat-widget theme detection.

The detector lives in freecad_ai.ui.message_view._is_dark_mode. The
classic bug it guards against: on Linux with a dark-themed Qt host,
QTreeView.palette() reports a dark Base color even when the user has
selected "FreeCAD Light" — palette-first detection misclassified the
session as dark and rendered the chat with a black background. The
fixed logic prefers the FreeCAD `Theme` preference name and only
falls back to palette introspection for unnamed/custom themes.
"""

import pytest


@pytest.fixture
def detector():
    from freecad_ai.ui.message_view import _is_dark_mode
    return _is_dark_mode


@pytest.mark.parametrize(
    "name",
    ["FreeCAD Light", "OpenLight", "Light", "freecad light", "Classic", "Default"],
)
def test_named_light_themes_return_light(detector, name):
    assert detector(name) is False


@pytest.mark.parametrize(
    "name",
    ["FreeCAD Dark", "OpenDark", "Dark", "freecad dark"],
)
def test_named_dark_themes_return_dark(detector, name):
    assert detector(name) is True


def test_dark_in_name_wins_over_light_palette(detector, monkeypatch):
    # Even if a (hypothetical) palette probe says light, an explicit
    # dark theme name must dominate.
    monkeypatch.setattr(
        "freecad_ai.ui.message_view.Gui", None, raising=False
    )
    assert detector("FreeCAD Dark") is True


def test_light_in_name_wins_over_dark_palette(detector):
    # The original bug: Linux Qt palette reports dark Base color but
    # the user picked FreeCAD Light. Name-first logic must win.
    assert detector("FreeCAD Light") is False


def test_unknown_name_with_no_freecad_falls_back_safely(detector):
    # No FreeCAD module available (unit-test environment) — the
    # palette probe raises and we fall through to a light default
    # rather than blowing up.
    assert detector("") is False
    assert detector("Custom/Unknown") is False
    assert detector(None) is False  # type: ignore[arg-type]


def _install_fake_freecad(monkeypatch, params):
    """Inject a fake FreeCAD whose MainWindow ParamGet returns `params`."""
    import sys

    class _FakeGroup:
        def GetString(self, key, default=""):  # noqa: N802 — FreeCAD camelCase
            return params.get(key, default)

    class _FakeFreeCAD:
        @staticmethod
        def ParamGet(path):  # noqa: N802
            return _FakeGroup()

    monkeypatch.setitem(sys.modules, "FreeCAD", _FakeFreeCAD)


class TestStyleSheetFallback:
    """Issue #16: a user can run a dark UI by setting only the StyleSheet
    preference (e.g. "OpenDark.qss") without selecting a PreferencePack
    Theme. With Theme empty the detector previously dropped to the
    unreliable QPalette probe and rendered unreadable light-on-dark text.
    The StyleSheet name is the next-most-reliable signal after Theme.
    """

    def test_dark_stylesheet_used_when_theme_empty(self, monkeypatch):
        from freecad_ai.ui import message_view

        _install_fake_freecad(
            monkeypatch, {"Theme": "", "StyleSheet": "OpenDark.qss"}
        )
        name = message_view._read_freecad_mode_name()
        assert "dark" in name.lower()
        assert message_view._is_dark_mode(name) is True

    def test_light_stylesheet_used_when_theme_empty(self, monkeypatch):
        from freecad_ai.ui import message_view

        _install_fake_freecad(
            monkeypatch, {"Theme": "", "StyleSheet": "OpenLight.qss"}
        )
        name = message_view._read_freecad_mode_name()
        assert message_view._is_dark_mode(name) is False

    def test_theme_takes_precedence_over_stylesheet(self, monkeypatch):
        # An explicit PreferencePack Theme is the user's top-level choice
        # and must win even if a contradictory StyleSheet is set.
        from freecad_ai.ui import message_view

        _install_fake_freecad(
            monkeypatch, {"Theme": "FreeCAD Light", "StyleSheet": "OpenDark.qss"}
        )
        assert message_view._read_freecad_mode_name() == "FreeCAD Light"

    def test_both_empty_falls_back_to_unknown(self, monkeypatch):
        from freecad_ai.ui import message_view

        _install_fake_freecad(monkeypatch, {"Theme": "", "StyleSheet": ""})
        assert message_view._read_freecad_mode_name() == "Custom/Unknown"
