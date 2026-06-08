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
        assert "names a tool" in prompt or "names a tool" in prompt.lower() or "create_primitive" in prompt
