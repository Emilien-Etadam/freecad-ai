"""Tests for LLMClient HTTP timeout configuration."""

from freecad_ai.llm.client import LLMClient


class TestLLMClientTimeout:
    def test_default_timeout_ollama(self):
        c = LLMClient("ollama", "http://localhost:11434/v1", "", "llama3")
        assert c._request_timeout() == 300

    def test_default_timeout_cloud(self):
        c = LLMClient("openai", "https://api.openai.com/v1", "k", "gpt-4")
        assert c._request_timeout() == 120

    def test_custom_timeout(self):
        c = LLMClient("openai", "https://api.openai.com/v1", "k", "gpt-4",
                      http_timeout=30)
        assert c._request_timeout() == 30
