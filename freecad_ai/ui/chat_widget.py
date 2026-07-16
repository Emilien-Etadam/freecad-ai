"""Main chat dock widget for FreeCAD AI.

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
    _STYLESHEET_CONFLICT_THEMES,
    _CAPTURE_MODE_COLORS,
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

from .chat_dock import (
    ChatDockLayoutMixin,
    ChatDockUIMixin,
    ChatDockSendMixin,
    ChatDockFilesMixin,
    ChatDockSessionMixin,
    ChatDockStreamingMixin,
    ChatDockCodeMixin,
    ChatDockDisplayMixin,
)

class ChatDockWidget(
    ChatDockDisplayMixin,
    ChatDockCodeMixin,
    ChatDockStreamingMixin,
    ChatDockSendMixin,
    ChatDockFilesMixin,
    ChatDockSessionMixin,
    ChatDockUIMixin,
    ChatDockLayoutMixin,
    QDockWidget,
):
    """Main chat dock widget for FreeCAD AI."""

    _TEXT_EXTENSIONS = TEXT_FILE_EXTENSIONS  # backward compat for tests

    def __init__(self, parent=None):
        super().__init__(translate("ChatDockWidget", "FreeCAD AI"), parent)
        self.setObjectName("FreeCADAIChatDock")
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)

        self.conversation = Conversation()
        self._worker = None
        self._input_history = InputHistory()
        self._suppress_history_reset = False  # set True around programmatic
                                              # _set_input_text() to guard a
                                              # future textChanged-based reset
        self._streaming_html = ""
        self._retry_count = 0
        self._anchor_connected = False
        self._tool_registry = None
        self._in_thinking = False  # Whether currently rendering thinking content
        self._activity_phase = ""
        self._activity_timer = None
        self._capture_mode_override = None  # Session-only viewport capture override
        self._pending_viewport_image = None  # Viewport image queued by after_changes mode
        self._mcp_connected = False
        self._vision_fallback_tool = None   # runtime-only, found after MCP connect
        self._vision_hint_shown = False      # one-time hint for untested state
        self._optimization_active = False
        self._validate_pending = False
        self._active_skill_name = ""

        # Initialize hook registry on main thread (before any worker threads)
        from ..hooks import get_hook_registry
        get_hook_registry()

        self._build_ui()
        self._ensure_vision_fallback()
        self._refresh_image_controls()
        self.setAcceptDrops(True)

        self._shutting_down = False
        # Starts disabled: get_chat_dock flips this on after the restore runs.
        # Otherwise addDockWidget emits dockLocationChanged BEFORE restore can
        # read the saved state from disk, and our first save overwrites the
        # previous session's good state with the current (default) state.
        self._saves_enabled = False

        self.dockLocationChanged.connect(self._save_dock_state)
        self.topLevelChanged.connect(self._save_dock_state)
        # visibilityChanged catches tabify when our dock becomes a background tab
        self.visibilityChanged.connect(self._save_dock_state)

        # Debounced save for tabify-by-drag. Tabification emits no dedicated
        # Qt signal, but the dock's geometry changes when it joins a tab group,
        # which triggers resizeEvent. Debounce to avoid thrashing on active drag.
        self._dock_save_timer = QtCore.QTimer(self)
        self._dock_save_timer.setSingleShot(True)
        self._dock_save_timer.setInterval(500)
        self._dock_save_timer.timeout.connect(self._save_dock_state)

        # Periodic poll — tabify-by-drag may not fire any signal we can hook,
        # so snapshot layout every 3s as a safety net. Cheap: only writes to
        # disk when state actually changes.
        self._dock_poll_timer = QtCore.QTimer(self)
        self._dock_poll_timer.setInterval(3000)
        self._dock_poll_timer.timeout.connect(self._save_dock_state)
        self._dock_poll_timer.start()

        # Shutdown detection. During FreeCAD close the layout can transiently
        # un-tabify docks before teardown completes; if we save during that
        # window we overwrite the last good state. Install an event filter on
        # the main window to catch its Close event and freeze saves from then
        # on. aboutToQuit is a belt-and-suspenders backstop for the same flag.
        try:
            mw_local = self._get_main_window()
            if mw_local is not None:
                mw_local.installEventFilter(self)
        except Exception:
            pass
        try:
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self._mark_shutdown)
        except Exception:
            pass
        self._refresh_input_history()



    # ── Palette-aware HTML wrappers (used by chat_dock mixins) ──

    def _render_message(self, role, content, ts=None):
        from .message_view import render_message
        return render_message(role, content, palette=self.palette(), ts=ts)

    def _render_thinking_block(self, thinking_text):
        from .message_view import render_thinking_block
        return render_thinking_block(thinking_text, palette=self.palette())

    def _render_tool_call(
        self, tool_name, call_id, started=True, success=True, output="",
        elapsed=None, args_summary="", detail_anchor="",
    ):
        from .message_view import render_tool_call
        return render_tool_call(
            tool_name, call_id,
            started=started, success=success, output=output,
            elapsed=elapsed, args_summary=args_summary,
            detail_anchor=detail_anchor,
            palette=self.palette(),
        )

    def _render_execution_result(self, success, stdout, stderr):
        from .message_view import render_execution_result
        return render_execution_result(
            success, stdout, stderr, palette=self.palette(),
        )

    def _render_tool_summary(self, timeline):
        from .message_view import render_tool_summary
        return render_tool_summary(timeline, palette=self.palette())


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

# ── Singleton access ────────────────────────────────────────

_dock_widget = None


def get_chat_dock(create=True):
    """Get or create the singleton chat dock widget."""
    global _dock_widget

    if _dock_widget is not None:
        return _dock_widget

    if not create:
        return None

    try:
        import FreeCADGui as Gui
        mw = Gui.getMainWindow()
    except ImportError:
        mw = None

    _dock_widget = ChatDockWidget(mw)

    if mw:
        cfg = get_config()
        area = _str_to_area(cfg.chat_dock_area)
        mw.addDockWidget(area, _dock_widget)
        _apply_saved_dock_state(mw, _dock_widget)
        # Enable state persistence now that restore has finished. Prevents
        # the addDockWidget-triggered signal avalanche from overwriting the
        # previous session's saved state before we've had a chance to read it.
        _dock_widget._saves_enabled = True

    return _dock_widget
