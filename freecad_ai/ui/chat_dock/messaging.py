"""Send messages, attachments, sessions, compaction."""
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

    def _send_message(self):
        """Send the current input to the LLM."""
        if self._worker and self._worker.isRunning():
            # Button is in "Stop" state — interrupt the in-flight run instead
            # of sending. Input is usually empty here, so this must run before
            # the empty-text guard below.
            self._worker.requestInterruption()
            return

        text = self.input_edit.toPlainText().strip()
        if not text:
            return

        self.input_edit.clear()
        self._retry_count = 0  # Reset retries for new user message
        self._active_skill_name = ""

        # Check for --validate flag
        self._validate_pending = False
        if "--validate" in text:
            text = text.replace("--validate", "").strip()
            self._validate_pending = True

        # Check for skill commands
        if text.startswith("/"):
            handled = self._handle_skill_command(text)
            if handled:
                return

        # Fire user_prompt_submit hook
        from ..hooks import fire_hook
        mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"
        hook_result = fire_hook("user_prompt_submit", {
            "text": text, "images": [], "mode": mode,
        })
        if hook_result.get("block"):
            self._append_html(render_message("system",
                f"Blocked by hook: {hook_result.get('reason', 'no reason given')}"))
            return
        if hook_result.get("modify"):
            text = hook_result["modify"]

        # Show one-time hint if vision not tested and user is sending images
        pending_images = self._attachment_strip.get_images()
        cfg = get_config()
        if pending_images and cfg.vision_detected is None and not self._vision_hint_shown:
            self._vision_hint_shown = True
            self._append_html(
                '<div style="color: #888; font-size: 9pt; margin: 4px 12px;">'
                'Tip: click Test Connection in Settings to enable vision auto-detection.'
                '</div>'
            )

        # Collect attached images
        images = pending_images or None

        # Collect attached documents
        pending_docs = self._attachment_strip.get_documents()
        documents = pending_docs or None

        # Auto-capture viewport if configured
        capture_mode = getattr(self, "_capture_mode_override", None) or get_config().viewport_capture
        if capture_mode == "every_message":
            vp_img = self._capture_viewport_for_chat()
            if vp_img:
                images = (images or []) + [vp_img]

        # Prepend pending viewport image (from after_changes mode)
        if getattr(self, "_pending_viewport_image", None):
            images = (images or []) + [self._pending_viewport_image]
            self._pending_viewport_image = None

        # Add to conversation and display
        self.conversation.add_user_message(text, images=images, documents=documents)
        self._refresh_input_history()
        display_content = self.conversation.messages[-1]["content"]
        self._append_html(render_message("user", display_content))
        self._attachment_strip.clear()

        # Check if conversation needs compaction
        cfg = get_config()
        if self.conversation.needs_compaction(cfg.context_window):
            self._compact_and_send()
            return

        self._continue_send()

    def _on_image_added(self, media_type: str, base64_data: str):
        """Handle image added via paste or drop."""
        self._attachment_strip.add_image(media_type, base64_data)

    def _on_document_added(self, filename: str, text: str):
        """Handle text file added via paste or drop."""
        self._attachment_strip.add_document(filename, text)

    # ── Dock-level drag-and-drop (accepts drops anywhere on the panel) ──

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasImage():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        """Accept drag move so the drop cursor stays valid."""
        if event.mimeData().hasUrls() or event.mimeData().hasImage():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        """Handle files dropped anywhere on the chat panel."""
        import os
        mime = event.mimeData()
        if mime.hasImage() and self.input_edit._images_enabled:
            self.input_edit._process_image_from_mime(mime)
            event.acceptProposedAction()
            return
        if mime.hasUrls():
            for url in mime.urls():
                path = url.toLocalFile()
                if not path:
                    continue
                filename = os.path.basename(path)
                ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
                # Image files
                if ext in ("png", "jpg", "jpeg", "bmp", "gif", "webp"):
                    if self.input_edit._images_enabled:
                        self.input_edit._process_image_file(path)
                    else:
                        self._append_html(render_message("system",
                            "Cannot attach images — no vision support detected. Check Settings or use a vision-capable model."))
                    event.acceptProposedAction()
                    return
                # Try reading as text
                text = self._read_text_file(path)
                if text is not None:
                    self._attachment_strip.add_document(filename, text)
                    event.acceptProposedAction()
                    return
                # Binary file — try hook
                self._process_file_with_hook(path, filename, ext)
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    # File extensions that can be read as text without external tools.

    def _attach_file(self):
        """Open file picker to attach an image or document.

        Routing logic:
        - Image files → sent as base64 vision blocks (handled by LLM vision)
        - Text files → read content, included as text in the message
        - Other files → fire 'file_attach' hook for user-defined conversion;
          if no hook handles the file, show a helpful message
        """
        try:
            import FreeCADGui as Gui
            parent = Gui.getMainWindow()
        except ImportError:
            parent = self
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            parent,
            translate("ChatDockWidget", "Attach File"),
            "",
            translate("ChatDockWidget",
                      "All supported files (*.png *.jpg *.jpeg *.bmp *.gif *.webp "
                      "*.txt *.md *.csv *.tsv *.json *.xml *.yaml *.yml "
                      "*.ini *.cfg *.conf *.toml *.log *.py *.js *.ts "
                      "*.html *.htm *.css *.sql *.sh *.bash *.svg "
                      "*.c *.cpp *.h *.hpp *.java *.rs *.go *.rb *.lua "
                      "*.pdf *.docx *.xlsx *.odt *.rtf);;"
                      "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;"
                      "Text files (*.txt *.md *.csv *.json *.xml *.yaml *.py *.js *.ts);;"
                      "All files (*)"),
        )
        if not path:
            return
        import os
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        filename = os.path.basename(path)

        # Route 1: Image files → vision block (requires vision support)
        if ext in ("png", "jpg", "jpeg", "bmp", "gif", "webp"):
            if not self.input_edit._images_enabled:
                self._append_html(render_message("system",
                    "Cannot attach images — no vision support detected. "
                    "Check Settings or use a vision-capable model."))
                return
            self.input_edit._process_image_file(path)
            return

        # Route 2: Try to read as text — known extensions first, then probe
        text = self._read_text_file(path)
        if text is not None:
            self._attachment_strip.add_document(filename, text)
            return

        # Route 3: Binary/unknown files → fire file_attach hook
        self._process_file_with_hook(path, filename, ext)

    def _read_text_file(self, path: str, max_size: int = 512_000) -> str | None:
        """Read a file as text, return content or None if binary/error.

        Rejects known binary formats (by magic bytes) and files
        containing null bytes.
        """
        import os
        try:
            size = os.path.getsize(path)
            if size > max_size:
                self._append_html(render_message("system",
                    f"File too large ({size // 1024} KB). Maximum is {max_size // 1024} KB."))
                return None
            with open(path, "rb") as f:
                raw = f.read()
            if _is_binary_content(raw):
                return None  # Binary file — let the hook handle it
            return raw.decode("utf-8", errors="replace")
        except OSError as e:
            self._append_html(render_message("system", f"Cannot read file: {e}"))
            return None

    def _process_file_with_hook(self, path: str, filename: str, ext: str):
        """Try to convert a file via the file_attach hook."""
        from ..hooks import fire_hook
        import mimetypes
        mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        result = fire_hook("file_attach", {
            "path": path,
            "filename": filename,
            "extension": ext,
            "mime_type": mime_type,
        })
        if result.get("block"):
            self._append_html(render_message("system",
                f"Attachment blocked: {result.get('reason', 'no reason given')}"))
            return
        if result.get("text"):
            self._attachment_strip.add_document(filename, result["text"])
            return
        # No hook handled it
        self._append_html(render_message("system",
            f"No converter for .{ext} files. To handle this format, either:\n"
            f"- Add a file_attach hook (see docs/hooks/file-attach-example/)\n"
            f"- Install an MCP server like markdownify-mcp for rich conversion"))

    def _capture_viewport_for_chat(self) -> dict | None:
        """Capture the viewport and return an image content block dict."""
        from ..utils.viewport import capture_viewport_image, make_image_content_block, RESOLUTION_PRESETS
        cfg = get_config()
        w, h = RESOLUTION_PRESETS.get(cfg.viewport_resolution, (800, 600))
        img_bytes = capture_viewport_image(w, h)
        if img_bytes:
            return make_image_content_block(img_bytes)
        return None

    def _cycle_capture_mode(self):
        """Cycle viewport capture mode: off -> every_message -> after_changes -> off."""
        modes = ["off", "every_message", "after_changes"]
        labels = {
            "off": translate("ChatDockWidget", "Viewport capture: off"),
            "every_message": translate("ChatDockWidget", "Viewport capture: every message"),
            "after_changes": translate("ChatDockWidget", "Viewport capture: after changes"),
        }
        current = getattr(self, "_capture_mode_override", None) or get_config().viewport_capture
        try:
            idx = modes.index(current)
        except ValueError:
            idx = 0
        next_mode = modes[(idx + 1) % len(modes)]
        self._capture_mode_override = next_mode
        self._capture_btn.setToolTip(labels.get(next_mode, next_mode))
        # Visual feedback: distinct colors per active mode (composed with
        # conflict-theme padding via _capture_btn_stylesheet()).
        self._capture_btn.setStyleSheet(self._capture_btn_stylesheet())

    def _on_mode_changed(self, index):
        """Update config when mode is toggled."""
        cfg = get_config()
        cfg.mode = "plan" if index == 0 else "act"
        save_current_config()

    def _ensure_vision_fallback(self):
        """Connect non-deferred MCP servers and search for a vision fallback.

        Called on startup and after settings changes so that image controls
        can be enabled/disabled correctly without waiting for the first message.
        Non-deferred servers are connected eagerly; deferred servers wait for
        the first Act-mode message.
        """
        cfg = get_config()
        if cfg.supports_vision or not cfg.mcp_servers:
            return
        if self._vision_fallback_tool is not None:
            return
        # Only connect non-deferred servers at this point
        has_non_deferred = any(
            not s.get("deferred", True) and s.get("enabled", True)
            for s in cfg.mcp_servers
        )
        if has_non_deferred:
            self._connect_mcp_servers(cfg, only_deferred=False)
        # Build registry (with whatever is connected so far) and search
        from ..mcp.manager import get_mcp_manager
        manager = get_mcp_manager()
        if manager.connected_servers:
            from ..tools.setup import create_default_registry
            from ..mcp.manager import find_vision_fallback
            self._tool_registry = create_default_registry()
            self._vision_fallback_tool = find_vision_fallback(self._tool_registry)

    def _refresh_image_controls(self):
        """Enable/disable image controls based on vision capability."""
        cfg = get_config()
        # Disable only when we know there's no vision AND no fallback
        disable = (cfg.vision_detected is not None
                   and not cfg.supports_vision
                   and self._vision_fallback_tool is None)

        no_vision_tip = translate(
            "ChatDockWidget",
            "No vision support \u2014 configure a vision MCP server or enable in Settings"
        )

        self._capture_btn.setEnabled(not disable)
        self.input_edit.set_images_enabled(not disable)
        # Attach button always enabled — supports text/document files regardless of vision
        self._attach_btn.setEnabled(True)

        if disable:
            self._capture_btn.setToolTip(no_vision_tip)
            self._attach_btn.setToolTip(translate("ChatDockWidget",
                "Attach a file (text/document — image attach requires vision)"))
        else:
            self._capture_btn.setToolTip(translate("ChatDockWidget", "Viewport capture: off"))
            self._attach_btn.setToolTip(translate("ChatDockWidget",
                "Attach a file (image, text, or document)"))

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
            from ..mcp.manager import get_mcp_manager
            get_mcp_manager().disconnect_all()
        self._ensure_vision_fallback()
        self._refresh_image_controls()

    def _new_chat(self):
        """Start a new conversation."""
        # Clean up optimization state
        if self._optimization_active:
            try:
                from ..tools.optimize_tools import stop_optimization
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
            self._append_html(render_message("system", translate("ChatDockWidget", "No saved sessions found.")))
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
                self._append_html(render_message(
                    "system",
                    translate("ChatDockWidget", "Resumed session from {}").format(
                        items[idx][0].split(' | ')[0])
                ))
            except Exception as e:
                self._append_html(render_message(
                    "system",
                    translate("ChatDockWidget", "Failed to load session: {}").format(e)
                ))

    def _compact_and_send(self):
        """Compact conversation by summarizing older messages, then continue sending."""
        self._append_html(
            '<div style="margin: 4px 0; padding: 6px 10px; '
            'background-color: #fff3e0; border-left: 3px solid #ff9800; '
            'border-radius: 0 4px 4px 0; font-size: 12px; color: #e65100;">'
            '{}</div>'.format(
                translate("ChatDockWidget", "Compacting context (~{}k tokens)...").format(
                    self.conversation.estimated_tokens() // 1000))
        )

        # Build summary of older messages (all except last 4)
        keep_recent = 4
        older = self.conversation.messages[:-keep_recent] if len(self.conversation.messages) > keep_recent else []
        if not older:
            # Nothing to compact, just send normally
            self._continue_send()
            return

        # Build a text summary of older messages for the LLM to compress
        summary_parts = []
        for msg in older:
            role = msg["role"]
            content = msg.get("content", "")
            if role == "tool_result":
                # Truncate long tool results for the summary request
                if len(content) > 500:
                    content = content[:500] + "..."
                summary_parts.append(f"[Tool Result] {content}")
            elif role == "assistant" and msg.get("tool_calls"):
                tc_names = [tc["name"] for tc in msg["tool_calls"]]
                summary_parts.append(f"[Assistant] Called tools: {', '.join(tc_names)}")
                if content:
                    summary_parts.append(f"  Text: {content[:300]}")
            else:
                label = "User" if role == "user" else "Assistant" if role == "assistant" else "System"
                if len(content) > 500:
                    content = content[:500] + "..."
                summary_parts.append(f"[{label}] {content}")

        summary_text = "\n".join(summary_parts)

        # Use a background thread to generate the summary
        self._set_loading(True)
        self._compaction_worker = _CompactionWorker(summary_text, parent=self)
        self._compaction_worker.finished.connect(self._on_compaction_finished)
        self._compaction_worker.start()

    def _on_compaction_finished(self, summary):
        """Handle compaction result and continue sending."""
        if summary:
            self.conversation.compact(summary, keep_recent=4)
            self._append_html(
                '<div style="margin: 4px 0; padding: 6px 10px; '
                'background-color: #e8f5e9; border-left: 3px solid #4caf50; '
                'border-radius: 0 4px 4px 0; font-size: 12px; color: #2e7d32;">'
                '{}</div>'.format(
                    translate("ChatDockWidget", "Context compacted to ~{}k tokens").format(
                        self.conversation.estimated_tokens() // 1000))
            )
        self._set_loading(False)
        self._update_token_count()
        # Continue with the normal send flow
        self._continue_send()

    def _continue_send(self):
        """Continue the send flow after optional compaction."""
        from ..core.system_prompt import build_system_prompt
        mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"
        cfg = get_config()

        # Determine if we should use tools. cfg.supports_tools combines the
        # provider-wide flag with per-model detection from /api/show — so an
        # Ollama embedding/reranker accidentally selected as the main model
        # won't get tools sent to it.
        use_tools = cfg.enable_tools and mode == "act" and cfg.supports_tools
        tools_schema = None
        api_style = "openai"

        if use_tools:
            # Connect MCP servers on first tool-enabled send
            if not self._mcp_connected:
                self._connect_mcp_servers(cfg)

            from ..tools.setup import create_default_registry
            from ..llm.providers import get_api_style

            # Build extra tools for active optimization
            extra_tools = []
            if self._optimization_active:
                try:
                    from ..tools.optimize_tools import get_optimize_iteration_tool, _active_config
                    extra_tools = [get_optimize_iteration_tool()]
                    # Pass the tool executor to the active config so evaluator can dispatch
                    if _active_config is not None:
                        from ..tools.executor_utils import (
                            MainThreadToolExecutor, _HAS_QT,
                        )
                        if _HAS_QT:
                            from ..tools.executor_utils import QtMainThreadToolExecutor
                            executor = QtMainThreadToolExecutor()
                        else:
                            executor = MainThreadToolExecutor()
                        executor.set_registry(None)  # will be set after registry creation
                        _active_config["_tool_executor"] = executor
                except ImportError:
                    pass

            self._tool_registry = create_default_registry(include_mcp=True, extra_tools=extra_tools)

            # Update executor registry if optimization active
            if self._optimization_active and extra_tools:
                try:
                    from ..tools.optimize_tools import _active_config
                    if _active_config and "_tool_executor" in _active_config:
                        _active_config["_tool_executor"].set_registry(self._tool_registry)
                except ImportError:
                    pass

            # Search for vision fallback after registry (with MCP tools) is created
            if not cfg.supports_vision and self._vision_fallback_tool is None:
                from ..mcp.manager import find_vision_fallback
                self._vision_fallback_tool = find_vision_fallback(self._tool_registry)
                self._refresh_image_controls()
            api_style = get_api_style(cfg.provider.name)

            # Optional tool reranking: filter schemas down to the top-N
            # relevant tools (+ pinned) based on the latest user message.
            filter_names = None
            if cfg.rerank_method in ("keyword", "llm"):
                user_text = _extract_latest_user_text(self.conversation)
                pairs = self._tool_registry.list_name_description_pairs()
                ranked = _run_reranker(cfg, pairs, user_text)
                filter_names = set(ranked)
                try:
                    import FreeCAD as _App
                    _App.Console.PrintMessage(
                        "[FreeCAD AI] Reranker ({}): {} of {} tools -> {}\n".format(
                            cfg.rerank_method, len(ranked), len(pairs),
                            ", ".join(ranked))
                    )
                except Exception:
                    pass

            if api_style == "anthropic":
                tools_schema = self._tool_registry.to_anthropic_schema(filter_names)
            else:
                tools_schema = self._tool_registry.to_openai_schema(filter_names)
            system_prompt = build_system_prompt(
                mode=mode, tools_enabled=True,
                override=cfg.system_prompt_override)
        else:
            self._tool_registry = None
            system_prompt = build_system_prompt(
                mode=mode, override=cfg.system_prompt_override)

        # Build describe_fn for non-vision LLMs
        describe_fn = None
        conversation_ref = None
        if not cfg.supports_vision:
            fallback = getattr(self, '_vision_fallback_tool', None)
            if fallback and self._tool_registry:
                _reg = self._tool_registry
                _tool = fallback
                def _make_describe(reg, tool_name):
                    def describe(b64_data):
                        result = reg.execute(
                            tool_name, {"image": b64_data, "prompt": "Describe this image in detail."}
                        )
                        if result.success:
                            return result.output
                        raise RuntimeError(result.error or "describe_image failed")
                    return describe
                describe_fn = _make_describe(_reg, _tool)
                conversation_ref = self.conversation

        # Get messages for API
        from ..llm.client import should_strip_thinking
        strip = should_strip_thinking(
            cfg.provider.model, cfg.strip_thinking_history)
        messages = self.conversation.get_messages_for_api(
            api_style=api_style, strip_thinking=strip)

        # Start streaming
        self._set_loading(True)
        self._streaming_html = ""
        self._append_html(
            '<div style="margin: 8px 0; padding: 8px 12px; '
            'background-color: #f5f5f5; border-radius: 6px;">'
            '<div style="font-weight: bold; color: #2e7d32; margin-bottom: 4px;">AI</div>'
            '<div style="white-space: pre-wrap;">'
        )

        self._in_thinking = False
        self._tool_results_stored = False
        self._summary_rendered = False
        self._worker = _LLMWorker(
            messages, system_prompt,
            tools=tools_schema, registry=self._tool_registry,
            api_style=api_style, conversation=conversation_ref,
            describe_fn=describe_fn, parent=self,
        )
        self._worker.token_received.connect(self._on_token)
        self._worker.thinking_received.connect(self._on_thinking)
        self._worker.response_finished.connect(self._on_response_finished)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.tool_call_started.connect(self._on_tool_call_started)
        self._worker.tool_call_finished.connect(self._on_tool_call_finished)
        self._worker.tool_exec_requested.connect(self._execute_tool_call)
        self._worker.vision_note.connect(self._on_vision_note)
        self._worker.start()

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

            self._append_html(render_message(
                "system",
                translate("ChatDockWidget", "Session log saved to: {}").format(filepath)
            ))
        except Exception as e:
            self._append_html(render_message(
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
