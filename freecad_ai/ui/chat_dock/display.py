"""Chat HTML rendering, anchors, loading state, MCP."""
import base64
import json

from ..compat import QtWidgets, QtCore, QtGui
from ...config import get_config
from ...core.conversation import Conversation
from ...core.executor import extract_code_blocks
from ...i18n import translate
from ..message_view import (
    render_plan_buttons,
    render_message,
    render_code_block,
    render_execution_result,
    render_tool_call,
    _get_theme_colors,
)

Qt = QtCore.Qt
QApplication = QtWidgets.QApplication
QTextCursor = QtGui.QTextCursor


class ChatDockDisplayMixin:
    """Rerender history, plan buttons, MCP connections."""

    # ── UI helpers ──────────────────────────────────────────

    def _append_html(self, html_str):
        """Append HTML to the chat display and scroll to bottom."""
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        # QTextBrowser merges consecutive insertHtml into one block; start a new
        # paragraph so the next bubble (e.g. AI after You) is not inline.
        if not self.chat_display.document().isEmpty():
            cursor.insertBlock()
        cursor.insertHtml(html_str)
        self.chat_display.setTextCursor(cursor)
        self.chat_display.ensureCursorVisible()

    def _rerender_chat(self):
        """Re-render the entire chat history with proper formatting."""
        try:
            from ..chat_utils import _summarize_tool_args

            html_parts = []
            mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"

            # Map tool_call_id → result content so compact lines can infer
            # success and offer a "details" anchor after a re-render.
            results_by_id = {
                m["tool_call_id"]: m.get("content", "")
                for m in self.conversation.messages
                if m.get("role") == "tool_result" and m.get("tool_call_id")
            }
            if not hasattr(self, "_tool_call_details"):
                self._tool_call_details = {}

            for msg in self.conversation.messages:
                if msg["role"] == "tool_result":
                    # Tool results are rendered inline via tool_call_finished signals
                    continue
                elif msg["role"] == "assistant" and msg.get("tool_calls"):
                    # Render assistant text + tool call indicators
                    if msg.get("content"):
                        html_parts.append(self._render_message("assistant", msg["content"]))
                    for tc in msg["tool_calls"]:
                        result = results_by_id.get(tc["id"], "")
                        detail_anchor = ""
                        if isinstance(result, str) and result.strip():
                            self._tool_call_details[tc["id"]] = result
                            detail_anchor = f"tooldetail:{tc['id']}"
                        success = not (isinstance(result, str)
                                       and result.startswith("Error:"))
                        html_parts.append(self._render_tool_call(
                            tc["name"], tc["id"], started=False, success=success,
                            args_summary=_summarize_tool_args(tc.get("arguments")),
                            detail_anchor=detail_anchor,
                        ))
                else:
                    html_parts.append(self._render_message(msg["role"], msg.get("content", "")))

                if mode == "plan" and msg["role"] == "assistant":
                    content = Conversation.extract_text(msg.get("content", ""))
                    code_blocks = extract_code_blocks(content)
                    for code in code_blocks:
                        html_parts.append(self._make_plan_buttons_html(code))

            full_html = "".join(html_parts)
            self.chat_display.setHtml(full_html)

            scrollbar = self.chat_display.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        except Exception:
            pass  # Keep existing display content on error

    def _make_plan_buttons_html(self, code):
        """Create HTML for Plan mode Execute/Copy buttons."""
        return render_plan_buttons(code, palette=self.palette())

    def _handle_anchor_click(self, url):
        """Handle clicks on anchor links in the chat (Execute/Copy/Image buttons)."""
        import base64
        url_str = url.toString() if hasattr(url, "toString") else str(url)

        if url_str.startswith("image:"):
            self._show_image_dialog(url_str)
            return
        elif url_str.startswith("tooldetail:"):
            self._show_tool_detail_dialog(url_str[len("tooldetail:"):])
            return
        elif url_str.startswith("execute:"):
            encoded = url_str[8:]
            try:
                code = base64.b64decode(encoded).decode()
                self.execute_code_from_plan(code)
            except Exception:
                pass
        elif url_str.startswith("copy:"):
            encoded = url_str[5:]
            try:
                code = base64.b64decode(encoded).decode()
                clipboard = QApplication.clipboard()
                clipboard.setText(code)
            except Exception:
                pass

    def _show_tool_detail_dialog(self, call_id: str):
        """Show the full output of a tool call in a read-only dialog."""
        detail = getattr(self, "_tool_call_details", {}).get(call_id)
        if not detail:
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(translate("ChatDockWidget", "Tool output"))
        layout = QtWidgets.QVBoxLayout(dlg)
        view = QtWidgets.QPlainTextEdit(detail)
        view.setReadOnly(True)
        from ..theme_palette import qtextedit_palette_stylesheet
        view.setStyleSheet(qtextedit_palette_stylesheet(self.palette()).replace(
            "QTextEdit", "QPlainTextEdit"))
        layout.addWidget(view)
        close_btn = QtWidgets.QPushButton(translate("ChatDockWidget", "Close"))
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)
        dlg.resize(560, 400)
        dlg.show()

    def _show_image_dialog(self, url_str: str):
        """Show a full-size image in a dialog when a thumbnail is clicked."""
        import base64 as b64
        try:
            block_idx = int(url_str.split(":", 1)[1])
        except (ValueError, IndexError):
            return

        # Find the most recent message with content blocks containing this index
        for msg in reversed(self.conversation.messages):
            content = msg.get("content")
            if isinstance(content, list) and block_idx < len(content):
                block = content[block_idx]
                if block.get("type") == "image":
                    img_data = b64.b64decode(block["data"])
                    pixmap = QtGui.QPixmap()
                    pixmap.loadFromData(img_data)
                    if pixmap.isNull():
                        return

                    dlg = QtWidgets.QDialog(self)
                    dlg.setWindowTitle("Image")
                    dlg_layout = QtWidgets.QVBoxLayout(dlg)
                    label = QtWidgets.QLabel()
                    # Scale down if very large
                    try:
                        screen_size = QtWidgets.QApplication.primaryScreen().availableGeometry()
                        max_w = int(screen_size.width() * 0.8)
                        max_h = int(screen_size.height() * 0.8)
                    except Exception:
                        max_w, max_h = 1024, 768
                    if pixmap.width() > max_w or pixmap.height() > max_h:
                        pixmap = pixmap.scaled(max_w, max_h, Qt.KeepAspectRatio,
                                               Qt.SmoothTransformation)
                    label.setPixmap(pixmap)
                    dlg_layout.addWidget(label)
                    dlg.show()
                    return


    def _ensure_activity_timer(self):
        if getattr(self, "_activity_timer", None) is not None:
            return
        self._activity_timer = QtCore.QTimer(self)
        self._activity_timer.setInterval(450)
        self._activity_timer.timeout.connect(self._tick_activity_label)

    def _set_chat_activity(self, phase: str, detail: str = ""):
        """Show live status in the footer (reflection, response, tool, etc.)."""
        if not hasattr(self, "activity_label"):
            return
        self._activity_phase = phase or ""
        self._activity_detail = detail or ""
        # Mirror the connection state onto the provider chip: waiting while
        # connecting, ok as soon as the model streams anything back.
        if phase == "connect":
            self._update_provider_chip("waiting")
        elif phase in ("think", "respond", "tool"):
            if getattr(self, "_provider_state", "") != "ok":
                self._update_provider_chip("ok")
        if phase in ("", "idle"):
            self._ensure_activity_timer()
            self._activity_timer.stop()
            self.activity_label.setText("")
            self.activity_label.setToolTip("")
            return
        templates = {
            "connect": translate("ChatDockWidget", "Waiting for the model"),
            "think": translate("ChatDockWidget", "Reflecting"),
            "respond": translate("ChatDockWidget", "Writing response"),
            "tool": translate("ChatDockWidget", "Running tool: {}"),
            "compact": translate("ChatDockWidget", "Compacting context"),
        }
        base = templates.get(phase, "")
        if phase == "tool" and detail:
            base = base.format(detail)
        elif phase == "tool":
            base = translate("ChatDockWidget", "Running tool…")
        self._activity_base_text = base
        self._activity_tick = 0
        self._tick_activity_label()
        self._ensure_activity_timer()
        self._activity_timer.start()
        tips = {
            "connect": translate("ChatDockWidget", "The model is starting. If this lasts, check Settings."),
            "think": translate("ChatDockWidget", "Reasoning tokens stream here when the model supports thinking."),
            "respond": translate("ChatDockWidget", "Answer text is streaming."),
            "tool": translate("ChatDockWidget", "FreeCAD is executing a tool on the document."),
            "compact": translate("ChatDockWidget", "Summarizing older messages to free context."),
        }
        self.activity_label.setToolTip(tips.get(phase, ""))

    def _tick_activity_label(self):
        if not getattr(self, "_activity_phase", "") or not hasattr(self, "activity_label"):
            return
        dots = "." * ((getattr(self, "_activity_tick", 0) % 3) + 1)
        self._activity_tick = getattr(self, "_activity_tick", 0) + 1
        text = getattr(self, "_activity_base_text", "") + dots
        # Streamed-token estimate (chars/4) — handy for gauging a local
        # model's generation speed at a glance.
        tokens = getattr(self, "_stream_chars", 0) // 4
        if tokens > 0:
            if tokens >= 1000:
                text += f" · ~{tokens / 1000:.1f}k tok"
            else:
                text += f" · ~{tokens} tok"
        self.activity_label.setText(text)


    def _set_loading(self, loading):
        """Enable/disable input while LLM is processing."""
        from ..theme_palette import pushbutton_loading_stylesheet, pushbutton_accent_stylesheet
        self.send_btn.setEnabled(True)
        self.input_edit.setReadOnly(loading)
        if loading:
            self._stream_chars = 0
            self.send_btn.setText("Stop")
            self.send_btn.setStyleSheet(pushbutton_loading_stylesheet(self.palette()))
        else:
            self.send_btn.setText(translate("ChatDockWidget", "Send"))
            self.send_btn.setStyleSheet(pushbutton_accent_stylesheet(self.palette()))
            self._set_chat_activity("idle")

    def _update_token_count(self):
        """Update the token estimate display."""
        tokens = self.conversation.estimated_tokens()
        if tokens >= 1000:
            self.token_label.setText(
                translate("ChatDockWidget", "tokens: ~{:.1f}k").format(tokens / 1000))
        else:
            self.token_label.setText(
                translate("ChatDockWidget", "tokens: ~{}").format(tokens))
        self._update_context_gauge()

    # ── Provider chip & context gauge ───────────────────────

    def _update_provider_chip(self, state=None):
        """Refresh the header chip: '● provider · model'.

        The dot color reflects the state of the last request (idle /
        waiting / ok / error, see _PROVIDER_STATE_COLOR_KEYS). Passing a
        state stores it; passing None re-renders with the stored one
        (theme refresh, settings change).
        """
        if not hasattr(self, "provider_chip"):
            return
        import html as html_mod
        from ..chat_constants import _PROVIDER_STATE_COLOR_KEYS
        from ..message_view import colors_from_palette

        if state is not None:
            self._provider_state = state
        state = getattr(self, "_provider_state", "idle")
        colors = colors_from_palette(self.palette())
        dot_color = colors[_PROVIDER_STATE_COLOR_KEYS.get(
            state, _PROVIDER_STATE_COLOR_KEYS["idle"])]

        cfg = get_config()
        provider = html_mod.escape(cfg.provider.name or "?")
        model = cfg.provider.model or ""
        if len(model) > 28:
            model = model[:28] + "…"
        model = html_mod.escape(model)
        self.provider_chip.setText(
            f'<span style="color: {dot_color};">&#9679;</span> '
            f'<span style="color: {colors["thinking_text"]}; font-size: 11px;">'
            f'{provider} &middot; {model}</span>')
        state_tips = {
            "idle": translate("ChatDockWidget", "No request sent yet"),
            "waiting": translate("ChatDockWidget", "Waiting for the model"),
            "ok": translate("ChatDockWidget", "Last request succeeded"),
            "error": translate("ChatDockWidget", "Last request failed"),
        }
        self.provider_chip.setToolTip("{}\n{}".format(
            cfg.provider.base_url or "", state_tips.get(state, "")))

    def _update_context_gauge(self):
        """Refresh the thin context-usage bar under the header.

        Fill = estimated conversation tokens over the configured context
        window (the compaction threshold). The fill switches to the
        warning color from 80% so the user sees compaction coming.
        """
        if not hasattr(self, "context_gauge"):
            return
        from ..message_view import colors_from_palette
        from ..theme_palette import progressbar_gauge_stylesheet

        cfg = get_config()
        window = max(1, int(cfg.context_window))
        tokens = (self.conversation.estimated_tokens()
                  if getattr(self, "conversation", None) else 0)
        pct = min(100, int(tokens * 100 / window))
        self.context_gauge.setValue(pct)

        chunk_color = None
        if pct >= 80:
            chunk_color = colors_from_palette(self.palette())["system_label"]
        self.context_gauge.setStyleSheet(
            progressbar_gauge_stylesheet(self.palette(), chunk_color=chunk_color))
        self.context_gauge.setToolTip(translate(
            "ChatDockWidget",
            "Context: ~{tokens} / {window} tokens ({pct}%) — "
            "older messages are summarized beyond 100%").format(
                tokens=tokens, window=window, pct=pct))

    def _connect_mcp_servers(self, cfg, *, only_deferred=None):
        """Connect to configured MCP servers.

        Args:
            only_deferred: If True, connect only deferred servers.
                If False, connect only non-deferred servers.
                If None, connect all servers.
        """
        if not cfg.mcp_servers:
            self._mcp_connected = True
            return
        try:
            from ...mcp.manager import get_mcp_manager
            manager = get_mcp_manager()
            prev_servers = set(manager.connected_servers)
            manager.connect_all(cfg.mcp_servers, only_deferred=only_deferred)
            if only_deferred is None or only_deferred is True:
                self._mcp_connected = True
            new_servers = set(manager.connected_servers) - prev_servers
            if new_servers:
                self._append_html(
                    '<div style="margin: 4px 0; padding: 4px 8px; '
                    'background-color: #e8f5e9; border-left: 3px solid #4caf50; '
                    'border-radius: 0 4px 4px 0; font-size: 11px; color: #2e7d32;">'
                    '{}</div>'.format(
                        translate("ChatDockWidget", "MCP: connected to {}").format(
                            ", ".join(sorted(new_servers))))
                )
        except Exception as e:
            if only_deferred is None or only_deferred is True:
                self._mcp_connected = True  # Don't retry on failure
            self._append_html(
                '<div style="margin: 4px 0; padding: 4px 8px; '
                'background-color: #fff3e0; border-left: 3px solid #ff9800; '
                'border-radius: 0 4px 4px 0; font-size: 11px; color: #e65100;">'
                '{}</div>'.format(
                    translate("ChatDockWidget", "MCP connection error: {}").format(str(e)))
            )
