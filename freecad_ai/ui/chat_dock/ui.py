"""Build chat UI, theme, dangerous mode, input history."""
from ..compat import QtWidgets, QtCore, QtGui
from ...config import get_config
from ...i18n import translate
from ..message_view import (
    colors_from_palette,
    _get_theme_colors,
    get_chat_display_stylesheet,
    get_freecad_mode_name,
    refresh_theme_cache,
)
from ..chat_constants import _STYLESHEET_CONFLICT_THEMES, _CAPTURE_MODE_COLORS
from ..chat_attachments import _AttachmentStrip, _ImageAwareTextEdit
from ..theme_palette import (
    qtextedit_palette_stylesheet,
    pushbutton_accent_stylesheet,
    pushbutton_loading_stylesheet,
    danger_banner_stylesheet,
)

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

    def _build_ui(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Header bar ──
        header = QHBoxLayout()

        title = QLabel("<b>{}</b>".format(translate("ChatDockWidget", "FreeCAD AI")))
        header.addWidget(title)
        header.addStretch()

        # Mode toggle
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([
            translate("ChatDockWidget", "Plan"),
            translate("ChatDockWidget", "Act"),
        ])
        cfg = get_config()
        self.mode_combo.setCurrentIndex(0 if cfg.mode == "plan" else 1)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        header.addWidget(QLabel(translate("ChatDockWidget", "Mode:")))
        header.addWidget(self.mode_combo)

        # Viewport capture toggle
        self._capture_btn = QPushButton(translate("ChatDockWidget", "Capture"))
        self._capture_btn.setMaximumWidth(70)
        self._capture_btn.setToolTip(translate("ChatDockWidget", "Viewport capture: off"))
        self._capture_btn.clicked.connect(self._cycle_capture_mode)
        header.addWidget(self._capture_btn)

        # Dangerous-mode session toggle
        self.danger_toggle = QtWidgets.QCheckBox(
            translate("ChatDockWidget", "⚠ Dangerous mode"))
        self.danger_toggle.setToolTip(
            translate("ChatDockWidget",
                      "Disable code safety checks and allow running macros from any path. "
                      "Session-only — resets when FreeCAD restarts."))
        self.danger_toggle.toggled.connect(self._on_danger_toggled)
        header.addWidget(self.danger_toggle)

        # Settings button
        settings_btn = QPushButton(translate("ChatDockWidget", "Settings"))
        settings_btn.setMaximumWidth(80)
        settings_btn.clicked.connect(self._open_settings)
        header.addWidget(settings_btn)

        # ── Dangerous-mode banner (inserted before header) ──
        self.danger_banner = QtWidgets.QLabel(
            translate("ChatDockWidget",
                      "⚠ DANGEROUS MODE ACTIVE — safety checks disabled"))
        self.danger_banner.setStyleSheet("")  # themed in _apply_theme
        self.danger_banner.setAlignment(QtCore.Qt.AlignCenter)
        self.danger_banner.setVisible(False)
        layout.addWidget(self.danger_banner)

        layout.addLayout(header)

        # ── Chat display ──
        self.chat_display = QTextBrowser()
        self.chat_display.setAcceptDrops(False)  # Drops handled by ChatDockWidget
        self.chat_display.setOpenExternalLinks(False)
        self.chat_display.setOpenLinks(False)
        self.chat_display.setFont(QFont("Sans", 10))
        self.chat_display.document().setDocumentMargin(12)
        self.chat_display.setStyleSheet(get_chat_display_stylesheet(self.palette()))
        self.chat_display.anchorClicked.connect(self._handle_anchor_click)
        layout.addWidget(self.chat_display, 1)

        # ── Attachment strip ──
        self._attachment_strip = _AttachmentStrip()
        layout.addWidget(self._attachment_strip)

        # ── Input area ──
        input_layout = QHBoxLayout()

        self.input_edit = _ImageAwareTextEdit()
        self.input_edit.setPlaceholderText(translate("ChatDockWidget", "Describe what you want to create..."))
        self.input_edit.setMaximumHeight(80)
        self.input_edit.setFont(QFont("Sans", 10))
        colors = colors_from_palette(self.palette())
        self.input_edit.setStyleSheet(qtextedit_palette_stylesheet(self.palette()))
        self.input_edit.installEventFilter(self)
        self.input_edit.image_added.connect(self._on_image_added)
        self.input_edit.document_added.connect(self._on_document_added)
        input_layout.addWidget(self.input_edit, 1)

        # Button column: attach + send
        btn_layout = QVBoxLayout()
        btn_layout.setSpacing(2)

        self._attach_btn = QPushButton(translate("ChatDockWidget", "Attach"))
        self._attach_btn.setMaximumHeight(20)
        self._attach_btn.setToolTip(translate("ChatDockWidget", "Attach a file (image, text, or document)"))
        self._attach_btn.clicked.connect(self._attach_file)
        btn_layout.addWidget(self._attach_btn)

        self.send_btn = QPushButton(translate("ChatDockWidget", "Send"))
        self.send_btn.setMinimumHeight(30)
        self.send_btn.setStyleSheet(pushbutton_accent_stylesheet(self.palette()))
        self.send_btn.clicked.connect(self._send_message)
        btn_layout.addWidget(self.send_btn)

        input_layout.addLayout(btn_layout)

        layout.addLayout(input_layout)

        # ── Footer ──
        footer = QHBoxLayout()

        new_chat_btn = QPushButton(translate("ChatDockWidget", "+ New Chat"))
        new_chat_btn.setMaximumWidth(100)
        new_chat_btn.clicked.connect(self._new_chat)
        footer.addWidget(new_chat_btn)

        load_chat_btn = QPushButton(translate("ChatDockWidget", "Load"))
        load_chat_btn.setMaximumWidth(60)
        load_chat_btn.setToolTip(translate("ChatDockWidget", "Load a previous chat session"))
        load_chat_btn.clicked.connect(self._load_chat)
        footer.addWidget(load_chat_btn)

        save_log_btn = QPushButton(translate("ChatDockWidget", "Save Log"))
        save_log_btn.setMaximumWidth(80)
        save_log_btn.setToolTip(translate("ChatDockWidget", "Save session log for debugging"))
        save_log_btn.clicked.connect(self._save_session_log)
        footer.addWidget(save_log_btn)

        # _capture_btn is intentionally excluded — its stylesheet is
        # composed in _capture_btn_stylesheet() so that mode color and
        # conflict-busting padding share a single setStyleSheet call.
        self._theme_ui_conflict_buttons = [
            settings_btn,
            new_chat_btn,
            load_chat_btn,
            save_log_btn,
        ]

        footer.addStretch()

        self.token_label = QLabel(translate("ChatDockWidget", "tokens: ~0"))
        self.token_label.setStyleSheet(f"color: {colors['thinking_text']}; font-size: 11px;")
        footer.addWidget(self.token_label)

        layout.addLayout(footer)

        self.setWidget(container)

        # Sync banner/toggle with current dangerous-mode state
        # (shows banner at startup if dangerous_skip_safety was hand-edited in config.json)
        self._update_danger_banner()

    # ── Dangerous-mode toggle ──────────────────────────────

    def _on_danger_toggled(self, checked):
        from ...core.dangerous_mode import get_dangerous_mode
        dm = get_dangerous_mode()
        if checked:
            box = QtWidgets.QMessageBox(self)
            box.setIcon(QtWidgets.QMessageBox.Warning)
            box.setWindowTitle(translate("ChatDockWidget", "Enable Dangerous mode?"))
            box.setText(translate(
                "ChatDockWidget",
                "Dangerous mode disables the safety checks built into FreeCAD AI."))
            box.setInformativeText(translate(
                "ChatDockWidget",
                "While active:\n"
                "• AI-run code may call shell commands, delete files, and touch "
                "anything your user account can.\n"
                "• A macro with an infinite loop will FREEZE FreeCAD with no "
                "recovery — unsaved work will be lost.\n"
                "• Generated code runs against your live document without the "
                "headless sandbox pre-check.\n\n"
                "You are solely responsible for what you run. Continue?"))
            box.setStandardButtons(
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            box.setDefaultButton(QtWidgets.QMessageBox.No)
            if box.exec() != QtWidgets.QMessageBox.Yes:
                self.danger_toggle.blockSignals(True)
                self.danger_toggle.setChecked(False)
                self.danger_toggle.blockSignals(False)
                return
            dm.arm()
        else:
            dm.disarm()
        self._update_danger_banner()

    def _update_danger_banner(self):
        from ...core.dangerous_mode import get_dangerous_mode
        active = get_dangerous_mode().active
        self.danger_banner.setVisible(active)
        if active and not self.danger_toggle.isChecked():
            self.danger_toggle.blockSignals(True)
            self.danger_toggle.setChecked(True)
            self.danger_toggle.blockSignals(False)
        elif not active and self.danger_toggle.isChecked():
            self.danger_toggle.blockSignals(True)
            self.danger_toggle.setChecked(False)
            self.danger_toggle.blockSignals(False)

    # ── Theme refresh on show ──────────────────────────────

    def showEvent(self, event):
        """Refresh theme colors when the widget becomes visible."""
        super().showEvent(event)
        refresh_theme_cache()
        self._apply_theme()

    def _resolve_stylesheet_conflict(self, theme_name: str):
        """OpenDark/OpenLight theme packs inject global QPushButton styles that
        override padding/margins, causing button text to be clipped.
        Re-applying explicit padding via setStyleSheet restores correct sizing.
        Each button keeps its construction-time setMaximumWidth(); only the
        padding stylesheet is reapplied here.
        """
        if theme_name.casefold() in _STYLESHEET_CONFLICT_THEMES:
            for btn in self._theme_ui_conflict_buttons:
                btn.setStyleSheet(
                    "QPushButton { padding: 4px 16px; margin: 1px; }"
                )

    def _capture_btn_stylesheet(self) -> str:
        """Build the _capture_btn stylesheet by composing capture-mode
        color and (under conflicting themes) explicit padding.

        Both rule sets are applied via a single setStyleSheet call so
        that capture-mode cycling and theme refresh can never overwrite
        each other's contribution.
        """
        mode = (
            getattr(self, "_capture_mode_override", None)
            or get_config().viewport_capture
        )
        color = _CAPTURE_MODE_COLORS.get(mode, "")
        needs_padding = (
            get_freecad_mode_name().casefold() in _STYLESHEET_CONFLICT_THEMES
        )
        if not color and not needs_padding:
            return ""
        rules = []
        if needs_padding:
            rules.append("padding: 4px 16px; margin: 1px;")
        if color:
            rules.append(color)
        return "QPushButton { " + " ".join(rules) + " }"

    def _apply_theme(self):
        """Reapply all theme-dependent stylesheets."""
        colors = colors_from_palette(self.palette())
        theme_name = get_freecad_mode_name(force_refresh=True)
        self._resolve_stylesheet_conflict(theme_name)
        self._capture_btn.setStyleSheet(self._capture_btn_stylesheet())
        self.chat_display.setStyleSheet(get_chat_display_stylesheet(self.palette()))
        self.input_edit.setStyleSheet(qtextedit_palette_stylesheet(self.palette()))
        if not self.send_btn.isEnabled():
            self.send_btn.setStyleSheet(pushbutton_loading_stylesheet(self.palette()))
        else:
            self.send_btn.setStyleSheet(pushbutton_accent_stylesheet(self.palette()))
        self.token_label.setStyleSheet(f"color: {colors['thinking_text']}; font-size: 11px;")
        colors_banner = colors_from_palette(self.palette())
        self.danger_banner.setStyleSheet(
            danger_banner_stylesheet(
                colors_banner["tool_error_border"],
                self.palette().color(QtGui.QPalette.HighlightedText).name(),
            ))

    # ── Input history ───────────────────────────────────────

    def _refresh_input_history(self) -> None:
        """Rebuild the input-history entries from the current conversation.

        Filters to user messages whose content is a plain string (skips
        multipart messages that carry image attachments). Also skips system
        messages — Conversation.add_system_message stores them as role=user
        with a "[System] " prefix (see freecad_ai/core/conversation.py), and
        those are not real user prompts that belong in the history.
        """
        entries = [
            m["content"]
            for m in self.conversation.messages
            if m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and not m["content"].startswith("[System] ")
        ]
        self._input_history.set_entries(entries)

    def _set_input_text(self, text: str) -> None:
        """Replace input contents and place caret at end without tripping the
        history-reset path that user typing goes through."""
        self._suppress_history_reset = True
        try:
            self.input_edit.setPlainText(text)
            cur = self.input_edit.textCursor()
            cur.movePosition(QTextCursor.End)
            self.input_edit.setTextCursor(cur)
        finally:
            self._suppress_history_reset = False

    # ── Event filter (Enter to send, Up/Down for history) ───

    def _handle_input_keypress(self, event) -> bool:
        """Return True if the KeyPress was consumed by dock-level handling.

        Covers (1) Enter/Return send, (2) Up/Down history navigation gated on
        caret position, and (3) cycle reset on input-editing keys. Returning
        False lets the keystroke proceed to Qt's default text-edit handling.
        """
        # 1. Enter / Return — existing send behavior (unchanged).
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                return False  # Shift+Enter: newline
            self._send_message()
            return True

        # 2. History navigation — only when the input is editable.
        if not self.input_edit.isReadOnly():
            cursor = self.input_edit.textCursor()
            # Up/Down at the document edge: consume the event even when the
            # helper returns None (empty / clamped history) — at the edge there
            # is nowhere else for the caret to move, so swallowing the key
            # avoids a dead-feeling keystroke. atStart()/atEnd() are
            # document-level, so in a multi-line draft history only triggers
            # when the caret is at the very first/last position; mid-document
            # arrows fall through below to Qt's default caret movement.
            if event.key() == Qt.Key_Up and cursor.atStart():
                result = self._input_history.up(self.input_edit.toPlainText())
                if result is not None:
                    self._set_input_text(result)
                return True
            if event.key() == Qt.Key_Down and cursor.atEnd():
                result = self._input_history.down()
                if result is not None:
                    self._set_input_text(result)
                return True

            # 3. Reset the history cycle on any input-editing key.
            if self._is_history_reset_key(event):
                if not self._suppress_history_reset:
                    self._input_history.reset()

        return False  # Let Qt handle any non-history keystroke.

    @staticmethod
    def _is_history_reset_key(event) -> bool:
        """Return True if a KeyPress should end the history navigation cycle.

        Triggers on any key that produces a character (event.text() non-empty)
        or any editing key (Backspace/Delete/Home/End). Bare modifier presses
        (Shift/Ctrl/Alt) produce empty text and so do NOT trigger a reset.
        Up/Down are explicitly excluded — they drive the cycle.
        """
        k = event.key()
        if k in (Qt.Key_Up, Qt.Key_Down):
            return False
        if k in (Qt.Key_Backspace, Qt.Key_Delete, Qt.Key_Home, Qt.Key_End):
            return True
        return bool(event.text())

    # ── Actions ─────────────────────────────────────────────
