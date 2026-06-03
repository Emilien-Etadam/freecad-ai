"""Chat HTML rendering, anchors, loading state, MCP."""
import base64
import json

from ..compat import QtWidgets, QtCore, QtGui
from ...config import get_config
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
QTextCursor = QtGui.QTextCursor


class ChatDockDisplayMixin:
    """Rerender history, plan buttons, MCP connections."""

    # ── UI helpers ──────────────────────────────────────────

    def _append_html(self, html_str):
        """Append HTML to the chat display and scroll to bottom."""
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(html_str)
        self.chat_display.setTextCursor(cursor)
        self.chat_display.ensureCursorVisible()

    def _rerender_chat(self):
        """Re-render the entire chat history with proper formatting."""
        try:
            html_parts = []
            mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"

            for msg in self.conversation.messages:
                if msg["role"] == "tool_result":
                    # Tool results are rendered inline via tool_call_finished signals
                    continue
                elif msg["role"] == "assistant" and msg.get("tool_calls"):
                    # Render assistant text + tool call indicators
                    if msg.get("content"):
                        html_parts.append(self._render_message("assistant", msg["content"]))
                    for tc in msg["tool_calls"]:
                        html_parts.append(self._render_tool_call(
                            tc["name"], tc["id"], started=False, success=True,
                            output=f"Called with: {json.dumps(tc['arguments'], indent=2)}"
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
                    dlg_layout = QVBoxLayout(dlg)
                    label = QLabel()
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

    def _set_loading(self, loading):
        """Enable/disable input while LLM is processing."""
        from ..theme_palette import pushbutton_loading_stylesheet, pushbutton_accent_stylesheet
        self.send_btn.setEnabled(True)
        self.input_edit.setReadOnly(loading)
        if loading:
            self.send_btn.setText("Stop")
            self.send_btn.setStyleSheet(pushbutton_loading_stylesheet(self.palette()))
        else:
            self.send_btn.setText(translate("ChatDockWidget", "Send"))
            self.send_btn.setStyleSheet(pushbutton_accent_stylesheet(self.palette()))

    def _update_token_count(self):
        """Update the token estimate display."""
        tokens = self.conversation.estimated_tokens()
        if tokens >= 1000:
            self.token_label.setText(
                translate("ChatDockWidget", "tokens: ~{:.1f}k").format(tokens / 1000))
        else:
            self.token_label.setText(
                translate("ChatDockWidget", "tokens: ~{}").format(tokens))

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
