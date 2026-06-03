"""Regression: chat_dock mixins must import freecad_ai.* with three dots.

From ``freecad_ai.ui.chat_dock.<module>``, ``from ..core`` resolves to the
non-existent ``freecad_ai.ui.core``; use ``from ...core`` instead.
"""
import re
from pathlib import Path

_PKG_ROOT = {
    "core",
    "llm",
    "mcp",
    "tools",
    "hooks",
    "extensions",
    "utils",
    "config",
    "i18n",
}
_BAD = re.compile(r"from \.\.(" + "|".join(_PKG_ROOT) + r")")


def _chat_dock_py_files():
    root = Path(__file__).resolve().parents[2] / "freecad_ai" / "ui" / "chat_dock"
    return sorted(root.glob("*.py"))


def test_chat_dock_no_two_dot_package_imports():
    violations = []
    for path in _chat_dock_py_files():
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if _BAD.search(line):
                violations.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not violations, "Invalid relative imports:\n" + "\n".join(violations)


def test_chat_dock_ui_imports_attachment_strip():
    ui = Path(__file__).resolve().parents[2] / "freecad_ai" / "ui" / "chat_dock" / "ui.py"
    text = ui.read_text()
    assert "from ..chat_attachments import" in text
    assert "_AttachmentStrip" in text
    assert "_ImageAwareTextEdit" in text
