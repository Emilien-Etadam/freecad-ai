#!/usr/bin/env python3
"""Split freecad_ai/ui/chat_widget.py into focused submodules."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "freecad_ai" / "ui" / "chat_widget.py"
UI = ROOT / "freecad_ai" / "ui"

lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)


def slice_lines(start: int, end: int) -> str:
    """1-based inclusive line range."""
    return "".join(lines[start - 1 : end])


HEADER_COMPAT = '''"""{doc}"""

from .compat import QtWidgets, QtCore, QtGui
from ..i18n import translate

QDockWidget = QtWidgets.QDockWidget
QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QTextBrowser = QtWidgets.QTextBrowser
QTextEdit = QtWidgets.QTextEdit
QPushButton = QtWidgets.QPushButton
QComboBox = QtWidgets.QComboBox
QLabel = QtWidgets.QLabel
QApplication = QtWidgets.QApplication
Qt = QtCore.Qt
Signal = QtCore.Signal
QThread = QtCore.QThread
Slot = QtCore.Slot
QFont = QtGui.QFont
QTextCursor = QtGui.QTextCursor

'''

# ── chat_constants.py
constants_body = slice_lines(53, 81)
text_ext_block = slice_lines(1510, 1517)
# Convert class attr to module-level frozenset
text_ext_lines = [
    "TEXT_FILE_EXTENSIONS = frozenset({\n",
]
for line in text_ext_block.splitlines():
    s = line.strip()
    if not s or s.startswith("#") or "_TEXT_EXTENSIONS" in s:
        continue
    text_ext_lines.append("    " + s + "\n")
text_ext_lines.append("})\n")

(UI / "chat_constants.py").write_text(
    '"""Constants shared by chat UI modules."""\n\n' + constants_body + "\n" + "".join(text_ext_lines),
    encoding="utf-8",
)

# ── chat_utils.py
utils_body = slice_lines(84, 199)
(UI / "chat_utils.py").write_text(
    '"""Helper functions for chat reranking and attachments."""\n\n'
    "from .chat_constants import _BINARY_MAGIC\n\n"
    + utils_body,
    encoding="utf-8",
)

# ── chat_workers.py
workers_body = slice_lines(204, 539)
(UI / "chat_workers.py").write_text(
    HEADER_COMPAT.format(doc="Background workers for LLM streaming and compaction.")
    + "import json\nimport time\n\n"
    "from ..config import get_config\n"
    "from ..core.loop_control import should_continue_loop\n\n"
    + workers_body,
    encoding="utf-8",
)

# ── chat_attachments.py
attachments_body = slice_lines(544, 792)
attachments_body = attachments_body.replace(
    "ChatDockWidget._TEXT_EXTENSIONS",
    "TEXT_FILE_EXTENSIONS",
)
attachments_body = attachments_body.replace(
    "while parent and not isinstance(parent, ChatDockWidget):",
    "while parent is not None:\n"
    "            from .chat_widget import ChatDockWidget\n"
    "            if isinstance(parent, ChatDockWidget):\n"
    "                break\n",
)
# Fix botched replace - need cleaner approach for dropEvent
attachments_body = slice_lines(544, 792)
attachments_body = attachments_body.replace(
    "return ext in ChatDockWidget._TEXT_EXTENSIONS or name in (\"makefile\", \"dockerfile\")",
    'return ext in TEXT_FILE_EXTENSIONS or name in ("makefile", "dockerfile")',
)
old_drop = """            # Not handled (binary file etc.) — forward to ChatDockWidget
            parent = self.parent()
            while parent and not isinstance(parent, ChatDockWidget):
                parent = parent.parent()
            if parent:
                parent.dropEvent(event)"""
new_drop = """            # Not handled (binary file etc.) — forward to ChatDockWidget
            dock = self._find_chat_dock()
            if dock is not None:
                dock.dropEvent(event)"""
attachments_body = attachments_body.replace(old_drop, new_drop)

# Add _find_chat_dock method before _process_image_from_mime - insert after set_images_enabled block
find_dock_method = '''
    def _find_chat_dock(self):
        """Walk parent chain to find the hosting ChatDockWidget."""
        parent = self.parent()
        while parent is not None:
            from .chat_widget import ChatDockWidget
            if isinstance(parent, ChatDockWidget):
                return parent
            parent = parent.parent()
        return None

'''
attachments_body = attachments_body.replace(
    "    def insertFromMimeData(self, source):",
    find_dock_method + "    def insertFromMimeData(self, source):",
)

(UI / "chat_attachments.py").write_text(
    HEADER_COMPAT.format(doc="Image-aware input and attachment preview widgets.")
    + "from .message_view import _get_theme_colors\n"
    "from .chat_constants import TEXT_FILE_EXTENSIONS\n"
    "from .chat_utils import _is_binary_content\n\n"
    + attachments_body,
    encoding="utf-8",
)

# ── chat_dock_state.py
dock_body = slice_lines(2825, 2893)
(UI / "chat_dock_state.py").write_text(
    HEADER_COMPAT.format(doc="Persist and restore chat dock layout.")
    + "from ..config import get_config\n\n"
    + dock_body,
    encoding="utf-8",
)

# ── chat_widget.py (main dock + singleton)
dock_class = slice_lines(797, 2820)
# Remove embedded _TEXT_EXTENSIONS from class
dock_lines = []
skip = False
for line in dock_class.splitlines(keepends=True):
    if "_TEXT_EXTENSIONS = {" in line:
        skip = True
        continue
    if skip:
        if line.strip() == "}":
            skip = False
        continue
    dock_lines.append(line)
dock_class = "".join(dock_lines)
dock_class = dock_class.replace(
    "_STYLESHEET_CONFLICT_THEMES",
    "STYLESHEET_CONFLICT_THEMES",
)
dock_class = dock_class.replace(
    "_CAPTURE_MODE_COLORS",
    "CAPTURE_MODE_COLORS",
)
# Fix constant names - we exported without underscore in constants file
# Actually constants file keeps _STYLESHEET_CONFLICT_THEMES - import as-is

facade = '''"""Main chat dock widget for FreeCAD AI.

Provides the primary user interface: a scrollable chat history,
input field, mode toggle (Plan/Act), and settings access.

LLM calls run in a QThread to keep the UI responsive, with
streaming text pushed via signals. When tools are enabled,
the worker implements an agentic loop: stream response, execute
tool calls on the main thread, feed results back to the LLM.
"""

import json
import time

from .compat import QtWidgets, QtCore, QtGui
from ..i18n import translate

QDockWidget = QtWidgets.QDockWidget
QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QTextBrowser = QtWidgets.QTextBrowser
QTextEdit = QtWidgets.QTextEdit
QPushButton = QtWidgets.QPushButton
QComboBox = QtWidgets.QComboBox
QLabel = QtWidgets.QLabel
QApplication = QtWidgets.QApplication
Qt = QtCore.Qt
Signal = QtCore.Signal
QThread = QtCore.QThread
Slot = QtCore.Slot
QFont = QtGui.QFont
QTextCursor = QtGui.QTextCursor

from ..config import LOGS_DIR, get_config, prune_oldest_files, save_current_config
from ..core.conversation import Conversation
from ..core.executor import extract_code_blocks, execute_code
from ..core.input_history import InputHistory
from .message_view import (
    _get_theme_colors,
    get_chat_display_stylesheet,
    get_freecad_mode_name,
    refresh_theme_cache,
    render_message,
    render_code_block,
    render_execution_result,
    render_tool_call,
)
from .code_review_dialog import CodeReviewDialog

from .chat_constants import (
    STYLESHEET_CONFLICT_THEMES,
    CAPTURE_MODE_COLORS,
    TEXT_FILE_EXTENSIONS,
)
from .chat_utils import (
    _is_binary_content,
    _build_rerank_llm_client,
    _freecad_log,
    _run_reranker,
    _extract_latest_user_text,
)
from .chat_workers import _LLMWorker, _CompactionWorker
from .chat_attachments import _ImageAwareTextEdit, _AttachmentStrip
from .chat_dock_state import _area_to_str, _str_to_area, _apply_saved_dock_state

# Backward-compatible re-exports (tests, docs)
__all__ = [
    "ChatDockWidget",
    "get_chat_dock",
    "_AttachmentStrip",
    "_ImageAwareTextEdit",
    "_is_binary_content",
    "_LLMWorker",
    "_CompactionWorker",
]

'''

singleton = slice_lines(2896, len(lines))

# Fix dock_class constant references - use imported names
dock_class = dock_class.replace("STYLESHEET_CONFLICT_THEMES", "_STYLESHEET_CONFLICT_THEMES")
dock_class = dock_class.replace("CAPTURE_MODE_COLORS", "_CAPTURE_MODE_COLORS")
# Import with aliases in facade instead
facade = facade.replace(
    "from .chat_constants import (\n    STYLESHEET_CONFLICT_THEMES,\n    CAPTURE_MODE_COLORS,\n    TEXT_FILE_EXTENSIONS,\n)",
    "from .chat_constants import (\n    _STYLESHEET_CONFLICT_THEMES,\n    _CAPTURE_MODE_COLORS,\n    TEXT_FILE_EXTENSIONS,\n)",
)

# In dock_class, references to _STYLESHEET_CONFLICT_THEMES already correct

# TEXT_EXTENSIONS usage in dock - grep ext in self._TEXT or TEXT_FILE
dock_class = dock_class.replace("self._TEXT_EXTENSIONS", "TEXT_FILE_EXTENSIONS")
dock_class = dock_class.replace("ChatDockWidget._TEXT_EXTENSIONS", "TEXT_FILE_EXTENSIONS")

SRC.write_text(facade + dock_class + singleton, encoding="utf-8")
print("Split complete:", SRC, "lines:", len((facade + dock_class + singleton).splitlines()))
