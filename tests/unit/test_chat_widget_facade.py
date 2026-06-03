"""Regression tests for split chat UI modules (no Qt required)."""
from pathlib import Path

from freecad_ai.ui.chat_constants import TEXT_FILE_EXTENSIONS
from freecad_ai.ui.chat_utils import _is_binary_content


def test_binary_magic_pdf():
    assert _is_binary_content(b"%PDF-1.4 rest")


def test_text_file_extensions_include_common():
    for ext in ("txt", "md", "py", "json"):
        assert ext in TEXT_FILE_EXTENSIONS


def test_text_file_extensions_exclude_images():
    for ext in ("png", "jpg", "pdf"):
        assert ext not in TEXT_FILE_EXTENSIONS


def test_chat_dock_mixin_modules_exist():
    """Mixin modules exist and define key methods (no Qt import)."""
    root = Path(__file__).resolve().parents[2] / "freecad_ai" / "ui" / "chat_dock"
    messaging = (root / "messaging.py").read_text(encoding="utf-8")
    streaming = (root / "streaming.py").read_text(encoding="utf-8")
    assert "def _send_message" in messaging
    assert "def _on_token" in streaming
    assert (root / "layout.py").is_file()
    assert (root / "ui.py").is_file()
