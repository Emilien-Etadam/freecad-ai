#!/usr/bin/env python3
"""Split ChatDockWidget methods into chat_dock mixins."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "freecad_ai" / "ui" / "chat_widget.py"
DOCK = ROOT / "freecad_ai" / "ui" / "chat_dock"

lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)


def extract(start: int, end: int) -> str:
    return "".join(lines[start - 1 : end])


# 1-based line ranges (methods only, inside ChatDockWidget)
RANGES = {
    "layout": (164, 268),
    "ui": (270, 645),
    "messaging": (647, 1397),
    "streaming": (1399, 1708),
    "code": (1710, 1884),
    "display": (1886, 2080),
}

HEADERS = {
    "layout": '''"""Dock layout persistence and shutdown hooks."""
from ..compat import QtWidgets, QtCore, QtGui
from ...config import get_config, save_current_config
from ..chat_dock_state import _area_to_str

Qt = QtCore.Qt
QDockWidget = QtWidgets.QDockWidget


class ChatDockLayoutMixin:
    """Save/restore dock geometry; detect main-window close."""

''',
    "ui": '''"""Build chat UI, theme, dangerous mode, input history."""
from ..compat import QtWidgets, QtCore, QtGui
from ...config import get_config
from ...i18n import translate
from ..message_view import (
    _get_theme_colors,
    get_chat_display_stylesheet,
    get_freecad_mode_name,
    refresh_theme_cache,
)
from ..chat_constants import _STYLESHEET_CONFLICT_THEMES, _CAPTURE_MODE_COLORS
from ..theme_palette import button_stylesheet, label_stylesheet

Qt = QtCore.Qt
QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QTextBrowser = QtWidgets.QTextBrowser
QTextEdit = QtWidgets.QTextEdit
QPushButton = QtWidgets.QPushButton
QComboBox = QtWidgets.QComboBox
QLabel = QtWidgets.QLabel
QFont = QtGui.QFont


class ChatDockUIMixin:
    """Construct widgets and apply theme / input affordances."""

''',
    "messaging": '''"""Send messages, attachments, sessions, compaction."""
import json
import time

from ..compat import QtWidgets, QtCore, QtGui
from ...config import LOGS_DIR, get_config, prune_oldest_files, save_current_config
from ...i18n import translate
from ..message_view import render_message
from ..chat_utils import _run_reranker, _extract_latest_user_text
from ..chat_workers import _LLMWorker, _CompactionWorker
from ..chat_constants import TEXT_FILE_EXTENSIONS
from ..chat_utils import _is_binary_content

Qt = QtCore.Qt
Slot = QtCore.Slot


class ChatDockMessagingMixin:
    """User send flow, file attach, chat load/save, LLM worker wiring."""

''',
    "streaming": '''"""Stream tokens, tool calls, and post-response validation."""
import html as html_mod

from ..compat import QtWidgets, QtCore, QtGui
from ...i18n import translate
from ..message_view import (
    render_message,
    render_tool_call,
    render_execution_result,
)
from ..chat_workers import _LLMWorker

Qt = QtCore.Qt
Slot = QtCore.Slot
QTextCursor = QtGui.QTextCursor


class ChatDockStreamingMixin:
    """Handle LLM streaming signals and tool-call UI updates."""

''',
    "code": '''"""Plan/Act code execution and skill command shortcuts."""
from ..compat import QtWidgets, QtCore, QtGui
from ...core.executor import extract_code_blocks, execute_code
from ...i18n import translate
from ..message_view import render_code_block, render_execution_result
from ..code_review_dialog import CodeReviewDialog
from ..chat_workers import _LLMWorker

Qt = QtCore.Qt


class ChatDockCodeMixin:
    """Execute code blocks and run skill-injected prompts."""

''',
    "display": '''"""Chat HTML rendering, anchors, loading state, MCP."""
import base64
import json

from ..compat import QtWidgets, QtCore, QtGui
from ...config import get_config
from ...i18n import translate
from ..message_view import (
    render_message,
    render_code_block,
    render_execution_result,
    render_tool_call,
    _get_theme_colors,
)

Qt = QtCore.Qt
QTextCursor = QtGui.QTextCursor


class ChatDockDisplayMixin:
    """Rerender history, plan buttons, MCP connections."""

''',
}

DOCK.mkdir(parents=True, exist_ok=True)

for name, (start, end) in RANGES.items():
    body = extract(start, end)
    (DOCK / f"{name}.py").write_text(HEADERS[name] + body, encoding="utf-8")

(DOCK / "__init__.py").write_text(
    '"""ChatDockWidget mixins (split from chat_widget)."""\n'
    "from .layout import ChatDockLayoutMixin\n"
    "from .ui import ChatDockUIMixin\n"
    "from .messaging import ChatDockMessagingMixin\n"
    "from .streaming import ChatDockStreamingMixin\n"
    "from .code import ChatDockCodeMixin\n"
    "from .display import ChatDockDisplayMixin\n",
    encoding="utf-8",
)

# Rebuild chat_widget.py: header + class with __init__ + merged eventFilter + closeEvent + singleton
init_and_class_start = extract(78, 163)  # class line through end of __init__ block before _mark_shutdown
close_event = extract(2082, 2095)
singleton = extract(2097, len(lines))

facade_header = extract(1, 77)  # docstring through __all__

# Remove duplicate first eventFilter from layout mixin - we'll use merged in main class
layout_path = DOCK / "layout.py"
layout_text = layout_path.read_text()
layout_text = layout_text.replace(
    "    def eventFilter(self, obj, event):\n"
    "        try:\n"
    "            if event.type() == QtCore.QEvent.Close:\n"
    "                mw = self._get_main_window()\n"
    "                if obj is mw:\n"
    "                    self._mark_shutdown()\n"
    "        except Exception:\n"
    "            pass\n"
    "        return False\n\n",
    "",
)
layout_path.write_text(layout_text, encoding="utf-8")

# Remove second eventFilter from ui mixin - merged in main
ui_path = DOCK / "ui.py"
ui_text = ui_path.read_text()
ui_text = ui_text.replace(
    "    def eventFilter(self, obj, event):\n"
    "        if obj is self.input_edit and event.type() == QtCore.QEvent.KeyPress:\n"
    "            if self._handle_input_keypress(event):\n"
    "                return True\n"
    "        return super().eventFilter(obj, event)\n\n",
    "",
)
ui_path.write_text(ui_text, encoding="utf-8")

# Remove closeEvent from display - keep in layout
display_path = DOCK / "display.py"
display_text = display_path.read_text()
# closeEvent is in layout range? 2082 is after display range 1886-2080. closeEvent at 2082 - add to layout file
layout_path.write_text(layout_path.read_text().rstrip() + "\n\n" + close_event, encoding="utf-8")

merged_event_filter = '''
    def eventFilter(self, obj, event):
        """Main-window shutdown + input history key handling."""
        try:
            if event.type() == QtCore.QEvent.Close:
                mw = self._get_main_window()
                if obj is mw:
                    self._mark_shutdown()
        except Exception:
            pass
        if obj is self.input_edit and event.type() == QtCore.QEvent.KeyPress:
            if self._handle_input_keypress(event):
                return True
        return super().eventFilter(obj, event)

'''

new_class = '''class ChatDockWidget(
    ChatDockDisplayMixin,
    ChatDockCodeMixin,
    ChatDockStreamingMixin,
    ChatDockMessagingMixin,
    ChatDockUIMixin,
    ChatDockLayoutMixin,
    QDockWidget,
):
    """Main chat dock widget for FreeCAD AI."""

    _TEXT_EXTENSIONS = TEXT_FILE_EXTENSIONS  # backward compat for tests

'''
# init_and_class_start has old class definition - replace first lines
init_lines = init_and_class_start.splitlines(keepends=True)
# skip until __init__
init_body = []
started = False
for line in init_lines:
    if line.strip().startswith("def __init__"):
        started = True
    if started:
        init_body.append(line)

from_imports = (
    "from .chat_dock import (\n"
    "    ChatDockLayoutMixin,\n"
    "    ChatDockUIMixin,\n"
    "    ChatDockMessagingMixin,\n"
    "    ChatDockStreamingMixin,\n"
    "    ChatDockCodeMixin,\n"
    "    ChatDockDisplayMixin,\n"
    ")\n\n"
)

SRC.write_text(
    facade_header
    + from_imports
    + new_class
    + "".join(init_body)
    + merged_event_filter
    + singleton,
    encoding="utf-8",
)

print("Done. chat_widget.py lines:", len(SRC.read_text().splitlines()))
