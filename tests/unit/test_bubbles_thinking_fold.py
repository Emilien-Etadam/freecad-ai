"""Timestamped bubbles and collapsed-by-default thinking blocks (zones 3-4).

Messages are stamped with a ``ts`` at creation and bubbles show a discreet
HH:MM next to the role label. Thinking/reasoning blocks render as a single
'▸ Thinking (N words)' line whose ``thinktoggle:<digest>`` anchor expands
them in place.
"""

import time
from pathlib import Path

from freecad_ai.core.conversation import Conversation
from freecad_ai.ui.message_view import (
    _EXPANDED_THINK_IDS,
    _format_timestamp,
    render_message,
    render_thinking_block,
    think_block_digest,
    toggle_think_block,
)

_UI = Path(__file__).resolve().parents[2] / "freecad_ai" / "ui"


def _src(relpath: str) -> str:
    return (_UI / relpath).read_text()


class TestMessageTimestamps:
    def test_user_and_assistant_messages_are_stamped(self):
        c = Conversation()
        before = time.time()
        c.add_user_message("hello")
        c.add_assistant_message("hi")
        c.add_system_message("done")
        for msg in c.messages:
            assert before - 1 <= msg["ts"] <= time.time() + 1

    def test_multipart_user_message_is_stamped(self):
        c = Conversation()
        c.add_user_message("look", images=[{
            "type": "image", "source": "clipboard",
            "media_type": "image/png", "data": "aGk=",
        }])
        assert "ts" in c.messages[-1]

    def test_ts_never_leaks_to_api(self):
        c = Conversation()
        c.add_user_message("hello")
        c.add_assistant_message("hi")
        for style in ("openai", "anthropic"):
            for msg in c.get_messages_for_api(api_style=style):
                assert "ts" not in msg

    def test_format_timestamp(self):
        ts = time.mktime((2026, 7, 16, 14, 32, 0, 0, 0, -1))
        assert _format_timestamp(ts) == "14:32"
        assert _format_timestamp(None) == ""
        assert _format_timestamp(0) == ""

    def test_bubble_shows_time_when_ts_given(self):
        ts = time.mktime((2026, 7, 16, 9, 5, 0, 0, 0, -1))
        html = render_message("user", "hello", ts=ts)
        assert "09:05" in html

    def test_bubble_without_ts_unchanged(self):
        html = render_message("user", "hello")
        assert ":" not in html.split("hello")[0].split("YOU")[-1][:10]

    def test_label_is_uppercased(self):
        html = render_message("user", "hello")
        assert "YOU" in html


class TestThinkingFold:
    def setup_method(self):
        _EXPANDED_THINK_IDS.clear()

    def teardown_method(self):
        _EXPANDED_THINK_IDS.clear()

    def test_collapsed_by_default(self):
        text = "Let me reason about the cube dimensions carefully."
        html = render_thinking_block(text)
        digest = think_block_digest(text)
        assert f'href="thinktoggle:{digest}"' in html
        assert "cube dimensions" not in html  # body hidden
        assert "&#9656;" in html  # ▸

    def test_word_count_in_header(self):
        html = render_thinking_block("one two three")
        assert "3" in html

    def test_toggle_expands_and_collapses(self):
        text = "Deep reasoning here."
        digest = think_block_digest(text)
        toggle_think_block(digest)
        expanded = render_thinking_block(text)
        assert "Deep reasoning here." in expanded
        assert "&#9662;" in expanded  # ▾
        toggle_think_block(digest)
        assert "Deep reasoning here." not in render_thinking_block(text)

    def test_digest_stable_and_short(self):
        assert think_block_digest("abc") == think_block_digest("  abc  ")
        assert len(think_block_digest("abc")) == 12

    def test_empty_thinking_renders_nothing(self):
        assert render_thinking_block("   ") == ""

    def test_inline_think_tags_use_fold(self):
        text = "Answer.\n<think>Secret reasoning path.</think>"
        html = render_message("assistant", text)
        assert "thinktoggle:" in html
        assert "Secret reasoning path." not in html

    def test_expanded_body_is_escaped(self):
        text = "reason <b>bold</b>"
        toggle_think_block(think_block_digest(text))
        html = render_thinking_block(text)
        assert "&lt;b&gt;" in html


class TestWiringSourceAudit:
    """Source-level checks for the Qt-dependent glue (no QApplication here)."""

    def test_display_toggles_and_preserves_scroll(self):
        src = _src("chat_dock/display.py")
        assert 'startswith("thinktoggle:")' in src
        assert "toggle_think_block" in src
        body = src.split('startswith("thinktoggle:")')[1].split("elif")[0]
        assert "scrollbar" in body and "_rerender_chat" in body

    def test_rerender_passes_ts_and_reasoning(self):
        src = _src("chat_dock/display.py")
        body = src.split("def _rerender_chat")[1].split("\n    def ")[0]
        assert 'ts=msg.get("ts")' in body
        assert "_render_thinking_block" in body
        assert "reasoning_content" in body

    def test_user_bubbles_pass_ts(self):
        for mod in ("chat_dock/send.py", "chat_dock/code.py"):
            src = _src(mod)
            assert 'ts=self.conversation.messages[-1].get("ts")' in src, mod

    def test_stream_open_bubble_timestamped(self):
        src = _src("chat_dock/send.py")
        assert "render_assistant_stream_open(" in src
        assert "ts=_time.time()" in src
