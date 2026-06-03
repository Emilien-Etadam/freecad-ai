#!/usr/bin/env python3
"""Split chat_dock/messaging.py into send, files, session mixins."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "freecad_ai" / "ui" / "chat_dock" / "messaging.py"
DOCK = ROOT / "freecad_ai" / "ui" / "chat_dock"

lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)


def extract(start: int, end: int) -> str:
    return "".join(lines[start - 1 : end])


COMMON = '''from ..compat import QtWidgets, QtCore, QtGui
from ...config import LOGS_DIR, get_config, prune_oldest_files, save_current_config
from ...i18n import translate
from ..message_view import render_message
from ..chat_utils import _run_reranker, _extract_latest_user_text, _is_binary_content
from ..chat_workers import _LLMWorker, _CompactionWorker
from ..chat_constants import TEXT_FILE_EXTENSIONS

Qt = QtCore.Qt
Slot = QtCore.Slot

'''

SPLITS = {
    "send": (
        21,
        107,
        '''"""LLM send pipeline: message dispatch, compaction, worker wiring."""
import json
import time

''',
    ),
    "send2": (
        475,
        686,
        None,
    ),
    "files": (
        108,
        360,
        '''"""Attachments: drag-drop, file picker, viewport capture helpers."""
''',
    ),
    "session": (
        361,
        474,
        '''"""Chat sessions: settings, new/load, session logs."""
import json

''',
    ),
    "session2": (
        687,
        771,
        None,
    ),
}

send_body = SPLITS["send"][2] + COMMON + "class ChatDockSendMixin:\n\n" + extract(21, 107) + extract(475, 686)
files_body = SPLITS["files"][2] + COMMON + "class ChatDockFilesMixin:\n\n" + extract(108, 360)
session_body = SPLITS["session"][2] + COMMON + "class ChatDockSessionMixin:\n\n" + extract(361, 474) + extract(687, 771)

(DOCK / "send.py").write_text(send_body, encoding="utf-8")
(DOCK / "files.py").write_text(files_body, encoding="utf-8")
(DOCK / "session.py").write_text(session_body, encoding="utf-8")
SRC.unlink()

init = '''"""ChatDockWidget mixins (split from chat_widget)."""
from .layout import ChatDockLayoutMixin
from .ui import ChatDockUIMixin
from .send import ChatDockSendMixin
from .files import ChatDockFilesMixin
from .session import ChatDockSessionMixin
from .streaming import ChatDockStreamingMixin
from .code import ChatDockCodeMixin
from .display import ChatDockDisplayMixin
'''
(DOCK / "__init__.py").write_text(init, encoding="utf-8")

widget = ROOT / "freecad_ai" / "ui" / "chat_widget.py"
wt = widget.read_text(encoding="utf-8")
wt = wt.replace(
    "from .chat_dock import (\n"
    "    ChatDockLayoutMixin,\n"
    "    ChatDockUIMixin,\n"
    "    ChatDockMessagingMixin,\n"
    "    ChatDockStreamingMixin,\n"
    "    ChatDockCodeMixin,\n"
    "    ChatDockDisplayMixin,\n"
    ")",
    "from .chat_dock import (\n"
    "    ChatDockLayoutMixin,\n"
    "    ChatDockUIMixin,\n"
    "    ChatDockSendMixin,\n"
    "    ChatDockFilesMixin,\n"
    "    ChatDockSessionMixin,\n"
    "    ChatDockStreamingMixin,\n"
    "    ChatDockCodeMixin,\n"
    "    ChatDockDisplayMixin,\n"
    ")",
)
wt = wt.replace(
    "class ChatDockWidget(\n"
    "    ChatDockDisplayMixin,\n"
    "    ChatDockCodeMixin,\n"
    "    ChatDockStreamingMixin,\n"
    "    ChatDockMessagingMixin,\n"
    "    ChatDockUIMixin,\n"
    "    ChatDockLayoutMixin,\n"
    "    QDockWidget,\n"
    "):",
    "class ChatDockWidget(\n"
    "    ChatDockDisplayMixin,\n"
    "    ChatDockCodeMixin,\n"
    "    ChatDockStreamingMixin,\n"
    "    ChatDockSendMixin,\n"
    "    ChatDockFilesMixin,\n"
    "    ChatDockSessionMixin,\n"
    "    ChatDockUIMixin,\n"
    "    ChatDockLayoutMixin,\n"
    "    QDockWidget,\n"
    "):",
)
widget.write_text(wt, encoding="utf-8")
print("send", len(send_body.splitlines()), "files", len(files_body.splitlines()), "session", len(session_body.splitlines()))
