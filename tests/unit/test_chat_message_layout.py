"""Chat bubble HTML: table layout for QTextBrowser."""
import re

from freecad_ai.ui.message_view import (
    CHAT_STREAM_END,
    render_assistant_stream_open,
    render_message,
)


class TestChatMessageLayout:
    def test_user_message_uses_table_bubble(self):
        html = render_message("user", "Bonjour cube")
        assert "Bonjour cube" in html
        assert "<table" in html
        assert 'bgcolor="' in html
        assert 'width="26%"' in html  # left spacer → bubble on the right
        assert html.endswith("</table>")

    def test_assistant_message_uses_table_bubble(self):
        html = render_message("assistant", "Voici le modèle.")
        assert "<table" in html
        assert 'width="8%"' in html  # left gutter
        assert "Voici" in html

    def test_stream_open_and_close_table(self):
        open_html = render_assistant_stream_open()
        assert "<table" in open_html
        assert "bgcolor=" in open_html
        full = open_html + "réponse" + CHAT_STREAM_END
        assert full.endswith("</table>")
        assert full.count("<table") == full.count("</table>")

    def test_user_and_ai_labels_in_separate_cells(self):
        user = render_message("user", "prompt")
        stream = render_assistant_stream_open()
        assert user.strip().endswith("</table>")
        assert re.search(r">AI<|>IA<", stream)
        # User bubble closes before AI table starts.
        assert user.count("</table>") == 1
