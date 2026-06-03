"""Tests for document attachment feature."""

import os
import tempfile

import pytest

from freecad_ai.core.conversation import Conversation
from freecad_ai.hooks.registry import VALID_EVENTS


class TestFileAttachHookEvent:
    def test_file_attach_is_valid_event(self):
        assert "file_attach" in VALID_EVENTS


class TestConversationDocuments:
    def test_add_user_message_text_only(self):
        c = Conversation()
        c.add_user_message("hello")
        assert c.messages[-1]["content"] == "hello"

    def test_add_user_message_with_documents(self):
        c = Conversation()
        docs = [{"filename": "data.csv", "text": "a,b,c\n1,2,3"}]
        c.add_user_message("parse this", documents=docs)
        content = c.messages[-1]["content"]
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": "parse this"}
        assert content[1]["type"] == "text"
        assert "data.csv" in content[1]["text"]
        assert "a,b,c" in content[1]["text"]

    def test_add_user_message_with_images_and_documents(self):
        c = Conversation()
        images = [{"type": "image", "source": "base64", "media_type": "image/png", "data": "abc"}]
        docs = [{"filename": "notes.md", "text": "# Notes"}]
        c.add_user_message("look at this", images=images, documents=docs)
        content = c.messages[-1]["content"]
        assert isinstance(content, list)
        # text block first, then document, then image
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "look at this"
        assert content[1]["type"] == "text"
        assert "notes.md" in content[1]["text"]
        assert content[2]["type"] == "image"

    def test_add_user_message_documents_only_creates_blocks(self):
        c = Conversation()
        docs = [{"filename": "f.txt", "text": "hello"}]
        c.add_user_message("check file", documents=docs)
        content = c.messages[-1]["content"]
        assert isinstance(content, list)
        assert len(content) == 2

    def test_add_user_message_multiple_documents(self):
        c = Conversation()
        docs = [
            {"filename": "a.py", "text": "print('a')"},
            {"filename": "b.py", "text": "print('b')"},
        ]
        c.add_user_message("compare these", documents=docs)
        content = c.messages[-1]["content"]
        assert len(content) == 3  # user text + 2 docs
        assert "a.py" in content[1]["text"]
        assert "b.py" in content[2]["text"]

    def test_document_text_is_labeled(self):
        c = Conversation()
        docs = [{"filename": "config.yaml", "text": "key: value"}]
        c.add_user_message("read this", documents=docs)
        doc_block = c.messages[-1]["content"][1]
        assert doc_block["text"].startswith("--- Attached file: config.yaml ---")
        assert "key: value" in doc_block["text"]


class TestAttachmentStrip:
    """Test the _AttachmentStrip widget for mixed image/document content."""

    @pytest.fixture
    def strip(self):
        """Create an _AttachmentStrip without a running QApplication."""
        pytest.importorskip("PySide6", reason="PySide6 required for widget tests")
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        from freecad_ai.ui.chat_widget import _AttachmentStrip
        return _AttachmentStrip()

    def test_add_document_shows_strip(self, strip):
        strip.add_document("test.csv", "a,b\n1,2")
        assert strip.isVisible()
        assert len(strip._items) == 1
        assert strip._items[0][1] == "document"

    def test_get_documents_returns_docs(self, strip):
        strip.add_document("a.txt", "hello")
        strip.add_document("b.md", "# B")
        docs = strip.get_documents()
        assert len(docs) == 2
        assert docs[0] == {"filename": "a.txt", "text": "hello"}
        assert docs[1] == {"filename": "b.md", "text": "# B"}

    def test_get_images_excludes_documents(self, strip):
        strip.add_document("a.txt", "hello")
        assert strip.get_images() == []

    def test_get_documents_excludes_images(self, strip):
        strip.add_image("image/png", "iVBORw0KGgo=")
        assert strip.get_documents() == []

    def test_mixed_content(self, strip):
        strip.add_image("image/png", "iVBORw0KGgo=")
        strip.add_document("notes.md", "# Notes")
        assert len(strip._items) == 2
        assert len(strip.get_images()) == 1
        assert len(strip.get_documents()) == 1

    def test_clear_removes_all(self, strip):
        strip.add_image("image/png", "iVBORw0KGgo=")
        strip.add_document("a.txt", "hello")
        strip.clear()
        assert len(strip._items) == 0
        assert not strip.isVisible()

    def test_remove_document(self, strip):
        strip.add_document("a.txt", "hello")
        strip.add_document("b.txt", "world")
        strip._remove(0)
        assert len(strip._items) == 1
        assert strip.get_documents()[0]["filename"] == "b.txt"


class TestBinaryDetection:
    """Test the _is_binary_content function."""

    def test_pdf_detected(self):
        from freecad_ai.ui.chat_utils import _is_binary_content
        assert _is_binary_content(b"%PDF-1.5\n%something") is True

    def test_zip_detected(self):
        from freecad_ai.ui.chat_utils import _is_binary_content
        assert _is_binary_content(b"PK\x03\x04rest of zip") is True

    def test_png_detected(self):
        from freecad_ai.ui.chat_utils import _is_binary_content
        assert _is_binary_content(b"\x89PNG\r\n\x1a\n") is True

    def test_jpeg_detected(self):
        from freecad_ai.ui.chat_utils import _is_binary_content
        assert _is_binary_content(b"\xff\xd8\xff\xe0") is True

    def test_ms_office_legacy_detected(self):
        from freecad_ai.ui.chat_utils import _is_binary_content
        assert _is_binary_content(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") is True

    def test_null_bytes_detected(self):
        from freecad_ai.ui.chat_utils import _is_binary_content
        assert _is_binary_content(b"some text\x00more text") is True

    def test_plain_text_allowed(self):
        from freecad_ai.ui.chat_utils import _is_binary_content
        assert _is_binary_content(b"Hello, this is plain text.\n") is False

    def test_utf8_text_allowed(self):
        from freecad_ai.ui.chat_utils import _is_binary_content
        assert _is_binary_content("Grüße! 日本語".encode("utf-8")) is False

    def test_empty_file_allowed(self):
        from freecad_ai.ui.chat_utils import _is_binary_content
        assert _is_binary_content(b"") is False


class TestTextFileDetection:
    """Test the text file extension detection logic."""

    def test_text_extensions(self):
        from freecad_ai.ui.chat_constants import TEXT_FILE_EXTENSIONS
        exts = TEXT_FILE_EXTENSIONS
        for ext in ("txt", "md", "csv", "json", "xml", "yaml", "py", "js", "html"):
            assert ext in exts, f"{ext} should be a text extension"

    def test_image_not_text(self):
        from freecad_ai.ui.chat_constants import TEXT_FILE_EXTENSIONS
        for ext in ("png", "jpg", "jpeg", "gif", "webp"):
            assert ext not in TEXT_FILE_EXTENSIONS

    def test_binary_not_text(self):
        from freecad_ai.ui.chat_constants import TEXT_FILE_EXTENSIONS
        for ext in ("pdf", "docx", "xlsx", "zip", "exe"):
            assert ext not in TEXT_FILE_EXTENSIONS


class TestReadTextFile:
    """Test the _read_text_file helper."""

    @pytest.fixture
    def widget(self):
        pytest.importorskip("PySide6", reason="PySide6 required for widget tests")
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        from freecad_ai.ui.chat_widget import ChatDockWidget
        return ChatDockWidget()

    def test_read_small_text_file(self, widget):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello world")
            f.flush()
            result = widget._read_text_file(f.name)
        os.unlink(f.name)
        assert result == "hello world"

    def test_read_utf8_content(self, widget):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False,
                                         encoding="utf-8") as f:
            f.write("Grüße! 你好")
            f.flush()
            result = widget._read_text_file(f.name)
        os.unlink(f.name)
        assert "Grüße" in result

    def test_reject_large_file(self, widget):
        result = widget._read_text_file("/dev/null", max_size=0)
        # /dev/null is 0 bytes, but max_size=0 means nothing fits
        # Actually os.path.getsize("/dev/null") returns 0 which is not > 0
        # so it should succeed. Let's use a real file.
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("x" * 100)
            f.flush()
            result = widget._read_text_file(f.name, max_size=10)
        os.unlink(f.name)
        assert result is None

    def test_nonexistent_file(self, widget):
        result = widget._read_text_file("/nonexistent/path.txt")
        assert result is None


class TestExampleHook:
    """Test the example file_attach hook logic."""

    def test_hook_has_on_file_attach(self):
        import importlib.util
        hook_path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "docs", "hooks", "file-attach-example", "hook.py",
        )
        spec = importlib.util.spec_from_file_location("example_hook", hook_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert hasattr(module, "on_file_attach")
        assert callable(module.on_file_attach)

    def test_hook_ignores_unknown_extension(self):
        import importlib.util
        hook_path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "docs", "hooks", "file-attach-example", "hook.py",
        )
        spec = importlib.util.spec_from_file_location("example_hook", hook_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.on_file_attach({
            "path": "/tmp/test.xyz",
            "filename": "test.xyz",
            "extension": "xyz",
            "mime_type": "application/octet-stream",
        })
        assert result == {}

    def test_hook_blocks_pdf_without_tools(self):
        import importlib.util
        hook_path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "docs", "hooks", "file-attach-example", "hook.py",
        )
        spec = importlib.util.spec_from_file_location("example_hook", hook_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Temporarily ensure pdftotext and pandoc are not in PATH
        import unittest.mock
        with unittest.mock.patch("shutil.which", return_value=None):
            result = module.on_file_attach({
                "path": "/tmp/test.pdf",
                "filename": "test.pdf",
                "extension": "pdf",
                "mime_type": "application/pdf",
            })
        assert result.get("block") is True
        assert "pdftotext" in result.get("reason", "")
