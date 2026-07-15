"""Reranker params get their own namespace, never the main model's slot.

Issue #30 (AVAVAVA1): editing the main model's temperature and saving reverted
it to 0.3. Root cause: the reranker save path wrote its table into the shared
``cfg.model_params`` dict keyed by model name; in inherit mode that key *is* the
main model, so the (stale) reranker snapshot clobbered the main model's params.

The fix gives the reranker its own ``cfg.rerank_params`` field:
  - override mode (a distinct reranker model is set) → reranker uses
    ``cfg.rerank_params``;
  - inherit mode (override field empty) → reranker uses the main model's
    params (``cfg.model_params[provider.model]``), and saves nothing of its own.

These tests pin both the runtime read path (``_build_rerank_llm_client``) and the
persistence rule (``SettingsDialog._resolve_rerank_params``).
"""

import pytest

# settings_dialog/chat_widget import through ui/compat.py which needs PySide.
# In dev venvs without either, skip — the dialog can't be imported.
try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.config import AppConfig  # noqa: E402
from freecad_ai.ui.chat_widget import _build_rerank_llm_client  # noqa: E402
from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402


def _cfg_with_params():
    cfg = AppConfig()
    cfg.provider.name = "ollama"
    cfg.provider.base_url = "http://localhost:11434/v1"
    cfg.provider.model = "main-model"
    cfg.model_params = {"main-model": {"temperature": 0.8, "top_p": 0.9}}
    cfg.rerank_params = {"temperature": 0.1, "top_k": 20}
    return cfg


class TestBuildRerankClientReadPath:
    def test_override_uses_rerank_params(self):
        """Distinct reranker model → params come from cfg.rerank_params,
        NOT from cfg.model_params (which has nothing for this model)."""
        cfg = _cfg_with_params()
        cfg.rerank_llm_model = "rr-model"
        client = _build_rerank_llm_client(cfg)
        assert client.model == "rr-model"
        assert client.model_params == {"temperature": 0.1, "top_k": 20}

    def test_inherit_uses_main_model_params(self):
        """Empty override → reranker inherits the main model and its params."""
        cfg = _cfg_with_params()
        cfg.rerank_llm_model = ""
        client = _build_rerank_llm_client(cfg)
        assert client.model == "main-model"
        assert client.model_params == {"temperature": 0.8, "top_p": 0.9}


class TestResolveRerankParamsWriteRule:
    def test_override_persists_table(self):
        """With an override model set, the reranker table is persisted."""
        out = SettingsDialog._resolve_rerank_params(
            "rr-model", {"temperature": 0.1, "top_k": 20})
        assert out == {"temperature": 0.1, "top_k": 20}

    def test_inherit_persists_nothing(self):
        """Empty override → reranker stores nothing; the main Model
        Parameters table is the sole owner of the main model's slot. This is
        the exact guard that fixes the issue #30 clobber."""
        out = SettingsDialog._resolve_rerank_params(
            "", {"temperature": 0.1, "top_k": 20})
        assert out == {}

    def test_whitespace_override_treated_as_inherit(self):
        """A blank-but-spaces override field is still inherit mode."""
        out = SettingsDialog._resolve_rerank_params(
            "   ", {"temperature": 0.1})
        assert out == {}
