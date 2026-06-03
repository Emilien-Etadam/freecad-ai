"""Image-aware input and attachment preview widgets."""

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

from .message_view import _get_theme_colors
from .chat_constants import TEXT_FILE_EXTENSIONS
from .chat_utils import _is_binary_content

class _ImageAwareTextEdit(QTextEdit):
    """Text input that accepts pasted/dropped images."""

    image_added = Signal(str, str)  # (media_type, base64_data)
    document_added = Signal(str, str)  # (filename, text_content)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._images_enabled = True

    def set_images_enabled(self, enabled: bool):
        """Enable or disable image paste and drag-drop."""
        self._images_enabled = enabled
        self.setAcceptDrops(enabled)


    def _find_chat_dock(self):
        """Walk parent chain to find the hosting ChatDockWidget."""
        parent = self.parent()
        while parent is not None:
            from .chat_widget import ChatDockWidget
            if isinstance(parent, ChatDockWidget):
                return parent
            parent = parent.parent()
        return None

    def insertFromMimeData(self, source):
        """Handle paste — extract image or text file if present."""
        if source.hasImage() and self._images_enabled:
            self._process_image_from_mime(source)
        elif source.hasUrls():
            for url in source.urls():
                path = url.toLocalFile()
                if not path:
                    continue
                if self._is_image_file(path) and self._images_enabled:
                    self._process_image_file(path)
                    return
                # Try any non-image file as text
                if self._process_text_file(path):
                    return
            super().insertFromMimeData(source)
        else:
            super().insertFromMimeData(source)

    def dragEnterEvent(self, event):
        if event.mimeData().hasImage() or event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        mime = event.mimeData()
        if mime.hasImage() and self._images_enabled:
            self._process_image_from_mime(mime)
        elif mime.hasUrls():
            for url in mime.urls():
                path = url.toLocalFile()
                if not path:
                    continue
                if self._is_image_file(path) and self._images_enabled:
                    self._process_image_file(path)
                    return
                # Try any non-image file as text (detect by reading)
                if self._process_text_file(path):
                    return
            # Not handled (binary file etc.) — forward to ChatDockWidget
            dock = self._find_chat_dock()
            if dock is not None:
                dock.dropEvent(event)
        else:
            super().dropEvent(event)

    def _process_image_from_mime(self, source):
        """Extract QImage from mime data, resize, and emit."""
        if not self._images_enabled:
            return
        img = source.imageData()
        if img is None or img.isNull():
            return
        from ..utils.viewport import resize_image_bytes, image_to_base64_png, RESOLUTION_PRESETS
        from ..config import get_config
        w, h = RESOLUTION_PRESETS.get(get_config().viewport_resolution, (800, 600))
        # Convert QImage to bytes
        buf = QtCore.QBuffer()
        buf.open(QtCore.QIODevice.WriteOnly)
        img.save(buf, "PNG")
        raw = bytes(buf.data())
        resized = resize_image_bytes(raw, w, h)
        self.image_added.emit("image/png", image_to_base64_png(resized))

    def _process_image_file(self, path: str):
        """Read an image file, resize, and emit."""
        if not self._images_enabled:
            return
        from ..utils.viewport import resize_image_bytes, image_to_base64_png, RESOLUTION_PRESETS
        from ..config import get_config
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError:
            return
        w, h = RESOLUTION_PRESETS.get(get_config().viewport_resolution, (800, 600))
        resized = resize_image_bytes(raw, w, h)
        self.image_added.emit("image/png", image_to_base64_png(resized))

    @staticmethod
    def _is_image_file(path: str) -> bool:
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        return ext in ("png", "jpg", "jpeg", "bmp", "gif", "webp")

    @staticmethod
    def _is_text_file(path: str) -> bool:
        import os
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        name = os.path.basename(path).lower()
        return ext in TEXT_FILE_EXTENSIONS or name in ("makefile", "dockerfile")

    def _process_text_file(self, path: str) -> bool:
        """Try to read a file as text and emit document_added signal.

        Rejects known binary formats (by magic bytes) and files
        containing null bytes. Returns True if successfully read.
        """
        import os
        try:
            size = os.path.getsize(path)
            if size > 512_000:
                return False
            with open(path, "rb") as f:
                raw = f.read()
            if _is_binary_content(raw):
                return False
            text = raw.decode("utf-8", errors="replace")
            self.document_added.emit(os.path.basename(path), text)
            return True
        except OSError:
            return False


class _AttachmentStrip(QtWidgets.QWidget):
    """Horizontal strip of attachment previews (image thumbnails and document chips)."""

    image_removed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(False)  # Drops handled by ChatDockWidget
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._layout.addStretch()
        # Each item: (widget, kind, data_dict)
        #   kind="image" → data_dict = {"media_type": str, "data": str}
        #   kind="document" → data_dict = {"filename": str, "text": str}
        self._items: list[tuple[QtWidgets.QWidget, str, dict]] = []
        self.hide()

    def add_image(self, media_type: str, base64_data: str):
        """Add an image thumbnail to the strip."""
        import base64 as b64

        container = QtWidgets.QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # Thumbnail
        label = QLabel()
        pixmap = QtGui.QPixmap()
        pixmap.loadFromData(b64.b64decode(base64_data))
        if not pixmap.isNull():
            pixmap = pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        label.setPixmap(pixmap)
        colors = _get_theme_colors()
        label.setStyleSheet(f"border: 1px solid {colors['chat_border']}; border-radius: 3px;")
        container_layout.addWidget(label)

        # Remove button
        remove_btn = QPushButton("x")
        remove_btn.setMaximumSize(16, 16)
        remove_btn.setStyleSheet(f"font-size: 10px; padding: 0; border: none; color: {colors['tool_error_text']};")
        idx = len(self._items)
        remove_btn.clicked.connect(lambda checked=False, i=idx: self._remove(i))
        container_layout.addWidget(remove_btn, alignment=Qt.AlignCenter)

        # Insert before the stretch
        self._layout.insertWidget(self._layout.count() - 1, container)
        self._items.append((container, "image", {"media_type": media_type, "data": base64_data}))
        self.show()

    def add_document(self, filename: str, text: str):
        """Add a document chip (filename badge) to the strip."""
        container = QtWidgets.QWidget()
        container_layout = QHBoxLayout(container)
        container_layout.setContentsMargins(4, 2, 4, 2)
        container_layout.setSpacing(4)

        colors = _get_theme_colors()

        # Filename label with truncation
        display_name = filename if len(filename) <= 24 else filename[:10] + "..." + filename[-10:]
        label = QLabel(display_name)
        label.setToolTip(filename)
        label.setStyleSheet(
            f"font-size: 10px; color: {colors['chat_text']}; "
            f"background: {colors['chat_bg']}; "
            f"border: 1px solid {colors['chat_border']}; "
            f"border-radius: 3px; padding: 2px 6px;"
        )
        container_layout.addWidget(label)

        # Remove button
        remove_btn = QPushButton("x")
        remove_btn.setMaximumSize(16, 16)
        remove_btn.setStyleSheet(f"font-size: 10px; padding: 0; border: none; color: {colors['tool_error_text']};")
        idx = len(self._items)
        remove_btn.clicked.connect(lambda checked=False, i=idx: self._remove(i))
        container_layout.addWidget(remove_btn)

        self._layout.insertWidget(self._layout.count() - 1, container)
        self._items.append((container, "document", {"filename": filename, "text": text}))
        self.show()

    def get_images(self) -> list[dict]:
        """Return list of image content block dicts."""
        return [
            {"type": "image", "source": "base64", "media_type": d["media_type"], "data": d["data"]}
            for _, kind, d in self._items if kind == "image"
        ]

    def get_documents(self) -> list[dict]:
        """Return list of document attachment dicts."""
        return [
            {"filename": d["filename"], "text": d["text"]}
            for _, kind, d in self._items if kind == "document"
        ]

    def clear(self):
        """Remove all attachments."""
        for widget, _, _ in self._items:
            widget.deleteLater()
        self._items.clear()
        self.hide()

    def _remove(self, idx: int):
        if 0 <= idx < len(self._items):
            widget, _, _ = self._items.pop(idx)
            widget.deleteLater()
            self.image_removed.emit(idx)
            # Re-bind remaining remove buttons
            for new_idx, (w, _, _) in enumerate(self._items):
                btn = w.findChild(QPushButton)
                if btn:
                    btn.clicked.disconnect()
                    btn.clicked.connect(lambda checked=False, i=new_idx: self._remove(i))
            if not self._items:
                self.hide()
