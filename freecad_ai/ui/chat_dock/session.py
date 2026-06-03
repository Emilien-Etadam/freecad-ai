"""Chat sessions: settings, new/load, session logs."""
import json

from ..compat import QtWidgets, QtCore, QtGui
from ...config import LOGS_DIR, get_config, prune_oldest_files, save_current_config
from ...i18n import translate
from ..message_view import render_message
from ..chat_utils import _run_reranker, _extract_latest_user_text, _is_binary_content
from ..chat_workers import _LLMWorker, _CompactionWorker
from ..chat_constants import TEXT_FILE_EXTENSIONS

Qt = QtCore.Qt
Slot = QtCore.Slot

class ChatDockSessionMixin:

    def _open_settings(self):
        """Open the settings dialog."""
        from .settings_dialog import SettingsDialog
        cfg = get_config()
        old_provider = cfg.provider.name
        old_model = cfg.provider.model
        old_mcp = list(cfg.mcp_servers)
        try:
            import FreeCADGui as Gui
            parent = Gui.getMainWindow()
        except ImportError:
            parent = self
        dlg = SettingsDialog(parent)
        dlg.exec()
        # Refresh after settings may have changed
        cfg = get_config()
        if cfg.provider.name != old_provider or cfg.provider.model != old_model:
            self._vision_fallback_tool = None
        if cfg.mcp_servers != old_mcp:
            self._vision_fallback_tool = None
            self._mcp_connected = False
            # Disconnect old MCP servers so stale connections don't linger
            from ...mcp.manager import get_mcp_manager
            get_mcp_manager().disconnect_all()
        self._ensure_vision_fallback()
        self._refresh_image_controls()

    def _new_chat(self):
        """Start a new conversation."""
        # Clean up optimization state
        if self._optimization_active:
            try:
                from ...tools.optimize_tools import stop_optimization
                stop_optimization()
            except ImportError:
                pass
            self._optimization_active = False

        if self.conversation.messages:
            self.conversation.save()

        self.conversation = Conversation()
        self._refresh_input_history()
        self.chat_display.clear()
        self._update_token_count()

    def _load_chat(self):
        """Show a dialog to load a previous chat session."""
        saved = Conversation.list_saved()
        if not saved:
            self._append_html(self._render_message("system", translate("ChatDockWidget", "No saved sessions found.")))
            return

        # Build display items with timestamps and preview
        items = []
        for conv_id in saved[:20]:  # Show last 20
            try:
                conv = Conversation.load(conv_id)
                # Get timestamp from conversation
                import time
                ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(conv.created_at)) if conv.created_at else "?"
                # Get first user message as preview
                preview = ""
                for m in conv.messages:
                    text = Conversation.extract_text(m.get("content", ""))
                    if m["role"] == "user" and not text.startswith("["):
                        preview = text[:60].replace("\n", " ")
                        break
                item_text = f"{ts} | {preview or conv_id}"
                items.append((item_text, conv_id))
            except Exception:
                items.append((conv_id, conv_id))

        # Use QInputDialog to pick a session
        item_labels = [item[0] for item in items]

        try:
            import FreeCADGui as Gui
            parent = Gui.getMainWindow()
        except ImportError:
            parent = self

        from .compat import QtWidgets as _QtWidgets
        selected, ok = _QtWidgets.QInputDialog.getItem(
            parent, translate("ChatDockWidget", "Load Chat Session"),
            translate("ChatDockWidget", "Select a session to resume:"),
            item_labels, 0, False
        )

        if ok and selected:
            idx = item_labels.index(selected)
            conv_id = items[idx][1]

            # Save current conversation first
            if self.conversation.messages:
                self.conversation.save()

            # Load the selected conversation
            try:
                self.conversation = Conversation.load(conv_id)
                self._refresh_input_history()
                self._rerender_chat()
                self._update_token_count()
                self._append_html(self._render_message(
                    "system",
                    translate("ChatDockWidget", "Resumed session from {}").format(
                        items[idx][0].split(' | ')[0])
                ))
            except Exception as e:
                self._append_html(self._render_message(
                    "system",
                    translate("ChatDockWidget", "Failed to load session: {}").format(e)
                ))

    def _save_session_log(self):
        """Save the current session log as JSON for debugging."""
        import os
        from datetime import datetime

        os.makedirs(LOGS_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(LOGS_DIR, f"session_{timestamp}.json")

        # Build the log from conversation messages
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "messages": [],
        }

        for msg in self.conversation.messages:
            entry = {"role": msg["role"]}
            if "content" in msg and msg["content"]:
                entry["content"] = msg["content"]
            if "tool_calls" in msg:
                entry["tool_calls"] = msg["tool_calls"]
            if "tool_call_id" in msg:
                entry["tool_call_id"] = msg["tool_call_id"]
            log_data["messages"].append(entry)

        # Also include the last worker's tool results if available
        if self._worker and hasattr(self._worker, "_tool_results") and self._worker._tool_results:
            log_data["tool_trace"] = self._worker._tool_results

        try:
            with open(filepath, "w") as f:
                json.dump(log_data, f, indent=2, default=str)

            cfg = get_config()
            prune_oldest_files(
                LOGS_DIR,
                lambda n: n.startswith("session_") and n.endswith(".json"),
                cfg.max_session_logs,
                cfg.max_retention_age_days,
            )

            self._append_html(self._render_message(
                "system",
                translate("ChatDockWidget", "Session log saved to: {}").format(filepath)
            ))
        except Exception as e:
            self._append_html(self._render_message(
                "system",
                translate("ChatDockWidget", "Failed to save log: {}").format(e)
            ))

    def _auto_save_log(self):
        """Auto-save tool trace after each tool-using response."""
        import os
        from datetime import datetime

        os.makedirs(LOGS_DIR, exist_ok=True)

        filepath = os.path.join(LOGS_DIR, "latest_session.json")

        log_data = {
            "timestamp": datetime.now().isoformat(),
            "tool_trace": [],
        }

        if self._worker and hasattr(self._worker, "_tool_results"):
            for turn_idx, turn in enumerate(self._worker._tool_results):
                turn_data = {
                    "turn": turn_idx + 1,
                    "assistant_text": turn["assistant_text"],
                    "tool_calls": [],
                }
                for tc, result in zip(turn["tool_calls"], turn["results"]):
                    turn_data["tool_calls"].append({
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                        "result": result["content"],
                    })
                log_data["tool_trace"].append(turn_data)

        try:
            with open(filepath, "w") as f:
                json.dump(log_data, f, indent=2, default=str)
        except Exception:
            pass  # Don't disrupt the UI for auto-save failures
