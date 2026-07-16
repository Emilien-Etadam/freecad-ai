"""Provider-status chip and context gauge (dock header, mockup zones 1-2).

The chip shows "● provider · model" with a dot colored by the state of
the last request; the gauge is a thin bar showing conversation tokens
over the configured context window (the compaction threshold).
"""

from pathlib import Path

import pytest

from freecad_ai.ui.chat_constants import _PROVIDER_STATE_COLOR_KEYS
from freecad_ai.ui.message_view import _LIGHT_THEME_COLORS, _DARK_THEME_COLORS

_UI = Path(__file__).resolve().parents[2] / "freecad_ai" / "ui"


def _src(relpath: str) -> str:
    return (_UI / relpath).read_text()


class TestProviderStateColors:
    def test_all_states_mapped(self):
        assert set(_PROVIDER_STATE_COLOR_KEYS) == {"idle", "waiting", "ok", "error"}

    def test_color_keys_exist_in_both_themes(self):
        for key in _PROVIDER_STATE_COLOR_KEYS.values():
            assert key in _LIGHT_THEME_COLORS, key
            assert key in _DARK_THEME_COLORS, key

    def test_semantic_mapping(self):
        assert _PROVIDER_STATE_COLOR_KEYS["ok"] == "tool_success_text"
        assert _PROVIDER_STATE_COLOR_KEYS["error"] == "tool_error_text"


class TestGaugeStylesheet:
    def test_gauge_stylesheet_flat_and_palette_filled(self):
        pytest.importorskip("PySide6", reason="PySide required for theme_palette import")
        from freecad_ai.ui.theme_palette import progressbar_gauge_stylesheet

        class _FakeColor:
            def __init__(self, name):
                self._name = name

            def name(self):
                return self._name

        class _FakePalette:
            def color(self, role):
                return _FakeColor("#336699")

        sheet = progressbar_gauge_stylesheet(_FakePalette())
        assert "QProgressBar::chunk" in sheet
        assert "border: none" in sheet
        assert "#336699" in sheet

        warn = progressbar_gauge_stylesheet(_FakePalette(), chunk_color="#e65100")
        assert "#e65100" in warn


class TestWiringSourceAudit:
    """Source-level checks for the Qt-dependent glue (no QApplication here)."""

    def test_ui_builds_chip_and_gauge(self):
        src = _src("chat_dock/ui.py")
        assert "self.provider_chip = QLabel" in src
        assert "self.context_gauge = QtWidgets.QProgressBar()" in src
        assert 'self._update_provider_chip("idle")' in src
        assert "self._update_context_gauge()" in src

    def test_theme_refresh_updates_chip_and_gauge(self):
        src = _src("chat_dock/ui.py")
        apply_theme = src.split("def _apply_theme")[1].split("\n    def ")[0]
        assert "_update_provider_chip()" in apply_theme
        assert "_update_context_gauge()" in apply_theme

    def test_display_has_update_methods(self):
        src = _src("chat_dock/display.py")
        assert "def _update_provider_chip(self, state=None):" in src
        assert "def _update_context_gauge(self):" in src
        assert "_PROVIDER_STATE_COLOR_KEYS" in src
        assert "progressbar_gauge_stylesheet" in src

    def test_gauge_follows_token_count(self):
        src = _src("chat_dock/display.py")
        body = src.split("def _update_token_count")[1].split("\n    def ")[0]
        assert "_update_context_gauge()" in body

    def test_gauge_warns_near_threshold(self):
        src = _src("chat_dock/display.py")
        body = src.split("def _update_context_gauge")[1].split("\n    def ")[0]
        assert "80" in body and "system_label" in body
        assert "context_window" in body

    def test_chip_reflects_activity_phases(self):
        src = _src("chat_dock/display.py")
        body = src.split("def _set_chat_activity")[1].split("\n    def ")[0]
        assert '_update_provider_chip("waiting")' in body
        assert '_update_provider_chip("ok")' in body

    def test_chip_error_and_success_on_stream_end(self):
        src = _src("chat_dock/streaming.py")
        assert '_update_provider_chip("error")' in src
        assert '_update_provider_chip("ok")' in src

    def test_settings_close_refreshes_chip_and_gauge(self):
        src = _src("chat_dock/session.py")
        body = src.split("def _open_settings")[1].split("\n    def ")[0]
        assert "_update_provider_chip()" in body
        assert "_update_context_gauge()" in body

    def test_chip_escapes_provider_and_model(self):
        src = _src("chat_dock/display.py")
        body = src.split("def _update_provider_chip")[1].split("\n    def ")[0]
        assert "escape" in body
