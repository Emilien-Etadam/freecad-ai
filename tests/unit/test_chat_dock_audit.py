"""Audit complet chat_dock : imports, profondeur relative, syntaxe."""
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2] / "freecad_ai" / "ui" / "chat_dock"
_PKG_TOP = {"core", "llm", "mcp", "tools", "hooks", "extensions", "utils", "config", "i18n"}
_BAD_SINGLE_DOT = (
    "from .message_view",
    "from .compat import",
    "from .settings_dialog",
    "from .core.",
    "from .llm.",
    "from .tools.",
    "from .mcp.",
)
_BAD_TWO_DOT = re.compile(r"from \.\.(" + "|".join(_PKG_TOP) + r")")

# Chaque fichier mixin : chaînes obligatoires (sous-chaîne)
_FILE_CHECKS = {
    "session.py": [
        "from ...core.conversation import Conversation",
        "from ..settings_dialog import SettingsDialog",
        "from ..compat import QtWidgets",
    ],
    "display.py": [
        "from ...core.conversation import Conversation",
        "from ...core.executor import extract_code_blocks",
        "QApplication = QtWidgets.QApplication",
    ],
    "streaming.py": [
        "from ...config import get_config",
        "from ...core.executor import extract_code_blocks",
        "CHAT_STREAM_END",
        "render_tool_summary",
        "from ...hooks import fire_hook",
    ],
    "send.py": [
        "render_stream_activity_hint",
        "render_assistant_stream_open",
        "from ...hooks import fire_hook",
        "from ...core.system_prompt import build_system_prompt",
        "def _continue_send_impl",
    ],
    "code.py": [
        "from ...core.executor import extract_code_blocks, execute_code",
        "from ..code_review_dialog import CodeReviewDialog",
    ],
    "files.py": [
        "from ...hooks import fire_hook",
        "from ...utils.viewport import",
    ],
    "ui.py": [
        "from ..chat_attachments import _AttachmentStrip, _ImageAwareTextEdit",
        "from ...core.dangerous_mode import get_dangerous_mode",
    ],
    "layout.py": [
        "from ..chat_dock_state import _area_to_str",
        "from ...config import get_config",
    ],
}


def test_chat_dock_files_compile():
    proc = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", str(_ROOT)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_chat_dock_import_contracts():
    missing = []
    for filename, needles in _FILE_CHECKS.items():
        text = (_ROOT / filename).read_text()
        for needle in needles:
            if needle not in text:
                missing.append(f"{filename}: missing {needle!r}")
    assert not missing, "\n".join(missing)


def test_chat_dock_no_invalid_relative_imports():
    bad = []
    for path in sorted(_ROOT.glob("*.py")):
        if path.name == "__init__.py":
            continue
        for i, line in enumerate(path.read_text().splitlines(), 1):
            for pat in _BAD_SINGLE_DOT:
                if pat in line:
                    bad.append(f"{path.name}:{i}: {line.strip()}")
            if _BAD_TWO_DOT.search(line):
                bad.append(f"depth {path.name}:{i}: {line.strip()}")
    assert not bad, "Invalid imports:\n" + "\n".join(bad)
