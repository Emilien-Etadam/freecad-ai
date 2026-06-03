"""Attachments: drag-drop, file picker, viewport capture helpers."""
from ..compat import QtWidgets, QtCore, QtGui
from ...config import LOGS_DIR, get_config, prune_oldest_files, save_current_config
from ...i18n import translate
from ..message_view import render_message
from ..chat_utils import _run_reranker, _extract_latest_user_text, _is_binary_content
from ..chat_workers import _LLMWorker, _CompactionWorker
from ..chat_constants import TEXT_FILE_EXTENSIONS

Qt = QtCore.Qt
Slot = QtCore.Slot

class ChatDockFilesMixin:

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
                        self._append_html(self._render_message("system",
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
                self._append_html(self._render_message("system",
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
                self._append_html(self._render_message("system",
                    f"File too large ({size // 1024} KB). Maximum is {max_size // 1024} KB."))
                return None
            with open(path, "rb") as f:
                raw = f.read()
            if _is_binary_content(raw):
                return None  # Binary file — let the hook handle it
            return raw.decode("utf-8", errors="replace")
        except OSError as e:
            self._append_html(self._render_message("system", f"Cannot read file: {e}"))
            return None

    def _process_file_with_hook(self, path: str, filename: str, ext: str):
        """Try to convert a file via the file_attach hook."""
        from ...hooks import fire_hook
        import mimetypes
        mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        result = fire_hook("file_attach", {
            "path": path,
            "filename": filename,
            "extension": ext,
            "mime_type": mime_type,
        })
        if result.get("block"):
            self._append_html(self._render_message("system",
                f"Attachment blocked: {result.get('reason', 'no reason given')}"))
            return
        if result.get("text"):
            self._attachment_strip.add_document(filename, result["text"])
            return
        # No hook handled it
        self._append_html(self._render_message("system",
            f"No converter for .{ext} files. To handle this format, either:\n"
            f"- Add a file_attach hook (see docs/hooks/file-attach-example/)\n"
            f"- Install an MCP server like markdownify-mcp for rich conversion"))

    def _capture_viewport_for_chat(self) -> dict | None:
        """Capture the viewport and return an image content block dict."""
        from ...utils.viewport import capture_viewport_image, make_image_content_block, RESOLUTION_PRESETS
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
        from ...mcp.manager import get_mcp_manager
        manager = get_mcp_manager()
        if manager.connected_servers:
            from ...tools.setup import create_default_registry
            from ...mcp.manager import find_vision_fallback
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

