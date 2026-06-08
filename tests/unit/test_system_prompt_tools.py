"""Tests for tool-calling guidance in the system prompt."""

from freecad_ai.core.system_prompt import get_default_system_prompt


class TestSystemPromptTools:
    def test_act_tools_prompt_mentions_create_primitive(self):
        prompt = get_default_system_prompt(mode="act", tools_enabled=True)
        assert "create_primitive" in prompt
        assert "Part::Box" in prompt
        assert "Do NOT generate Python code" in prompt or "never use" in prompt.lower()

    def test_act_tools_prompt_honors_explicit_tool_request(self):
        prompt = get_default_system_prompt(mode="act", tools_enabled=True)
        assert "names a tool" in prompt or "create_primitive" in prompt

    def test_act_tools_prompt_requires_tool_calls_for_modeling(self):
        prompt = get_default_system_prompt(mode="act", tools_enabled=True)
        assert "MUST include tool calls" in prompt
        assert "modélise" in prompt

    def test_act_tools_prompt_includes_dice_pattern(self):
        prompt = get_default_system_prompt(mode="act", tools_enabled=True)
        assert "dé à jouer" in prompt or "Playing die" in prompt
        assert "fillet_edges" in prompt
