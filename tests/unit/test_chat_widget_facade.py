"""Regression tests for split chat UI modules (no Qt required)."""

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
