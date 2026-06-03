"""Regression: chat_dock mixins must import symbols moved out of chat_widget."""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2] / "freecad_ai" / "ui" / "chat_dock"

_REQUIRED = {
    "session.py": [
        "from ...core.conversation import Conversation",
        "from ..settings_dialog import SettingsDialog",
    ],
    "display.py": [
        "from ...core.conversation import Conversation",
        "from ...core.executor import extract_code_blocks",
        "QApplication = QtWidgets.QApplication",
    ],
    "streaming.py": [
        "from ...core.executor import extract_code_blocks",
        "CHAT_STREAM_END",
        "render_tool_summary",
    ],
    "ui.py": [
        "from ..chat_attachments import _AttachmentStrip",
        "from ...core.dangerous_mode import get_dangerous_mode",
    ],
}


def test_chat_dock_required_imports_present():
    missing = []
    for filename, needles in _REQUIRED.items():
        text = (_ROOT / filename).read_text()
        for needle in needles:
            if needle not in text:
                missing.append(f"{filename}: {needle}")
    assert not missing, "Missing imports:\n" + "\n".join(missing)


def test_chat_dock_no_single_dot_ui_imports():
    """from .message_view in chat_dock resolves to freecad_ai.ui.chat_dock.message_view (missing)."""
    bad = []
    for path in _ROOT.glob("*.py"):
        if path.name == "__init__.py":
            continue
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if "from .message_view" in line or "from .compat import" in line or "from .settings_dialog" in line:
                bad.append(f"{path.name}:{i}: {line.strip()}")
    assert not bad, "Bad imports:\n" + "\n".join(bad)
