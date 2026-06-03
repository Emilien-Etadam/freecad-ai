"""Chat bubble HTML: separate blocks so QTextBrowser does not merge labels."""
import re

from freecad_ai.ui.message_view import (
    CHAT_STREAM_END,
    render_assistant_stream_open,
    render_message,
)


def _div_balance(html: str) -> int:
    return html.count("<div") - html.count("</div")


class TestChatMessageLayout:
    def test_user_message_closed_and_right_aligned(self):
        html = render_message("user", "Bonjour cube")
        assert "Bonjour cube" in html
        assert "text-align:right" in html
        assert _div_balance(html) == 0
        assert html.rstrip().endswith("</div>")

    def test_assistant_message_closed_and_left_aligned(self):
        html = render_message("assistant", "Voici le modèle.")
        assert "text-align:left" in html
        assert _div_balance(html) == 0
        assert "Voici" in html

    def test_stream_open_then_close_balanced(self):
        open_html = render_assistant_stream_open()
        assert "AI" in open_html or "IA" in open_html  # i18n may vary
        assert _div_balance(open_html) == 3  # row, bubble, body open
        full = open_html + "réponse" + CHAT_STREAM_END
        assert _div_balance(full) == 0

    def test_user_and_stream_are_separate_bubbles(self):
        user = render_message("user", "prompt")
        stream = render_assistant_stream_open()
        assert user.count("You") >= 1 or "Vous" in user
        assert re.search(r">AI<|>IA<", stream)
        # Label must not be inside the user body div (comes after user closes).
        assert user.strip().endswith("</div></div></div>")
        assert stream.count("text-align:left") >= 1
