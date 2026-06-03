"""Helper functions for chat reranking and attachments."""

from .chat_constants import _BINARY_MAGIC

def _is_binary_content(data: bytes) -> bool:
    """Detect binary content by magic bytes and null-byte presence."""
    header = data[:8]
    for magic in _BINARY_MAGIC:
        if header[:len(magic)] == magic:
            return True
    if b"\x00" in data[:8192]:
        return True
    return False


def _build_rerank_llm_client(cfg):
    """Construct the LLMClient used for LLM-based reranking.

    Each override field is inherited from the main provider when empty,
    so the common case (same provider, maybe a different model) is a
    one-field change. Full override (different provider entirely) works
    too, for e.g. running reranking on a local Ollama model while the
    main chat uses a cloud provider.

    Model params for the reranker's effective model are always sourced
    from the shared ``cfg.model_params`` dict, keyed by model name.
    This means:
      - Inherited model → same params as main chat (handles provider
        quirks like Moonshot's locked ``temperature=1``)
      - Override model → params configured via the reranker's inline
        params table in Settings (important for small Ollama models
        that need ``num_predict`` / ``top_k`` / ``repeat_penalty`` etc.)
    """
    from ..llm.client import LLMClient
    provider_name = cfg.rerank_llm_provider_name or cfg.provider.name
    base_url = cfg.rerank_llm_base_url or cfg.provider.base_url
    api_key = cfg.rerank_llm_api_key or cfg.provider.api_key
    model = cfg.rerank_llm_model or cfg.provider.model

    # Always look up params for the effective model — sharing the main
    # model_params dict keeps params coherent across main/reranker usage.
    model_params = dict(cfg.model_params.get(model, {}))

    return LLMClient(
        provider_name=provider_name,
        base_url=base_url,
        api_key=api_key,
        model=model,
        max_tokens=1024,
        temperature=model_params.get("temperature", 0.0),
        thinking="off",
        model_params=model_params,
    )


def _freecad_log(msg: str):
    """Print a line to FreeCAD's Report View, if FreeCAD is available."""
    try:
        import FreeCAD as _App
        _App.Console.PrintMessage("[FreeCAD AI] {}\n".format(msg))
    except Exception:
        pass


def _run_reranker(cfg, pairs, user_text):
    """Dispatch to the configured reranker method.

    Returns a list of tool names to include. LLM method falls back to
    keyword on any failure (handled inside ``rerank_tools_llm``).
    """
    from ..tools.reranker import rerank_tools, rerank_tools_llm
    if cfg.rerank_method == "llm":
        try:
            client = _build_rerank_llm_client(cfg)
        except Exception as e:
            _freecad_log("LLM reranker: cannot build client ({}); using keyword".format(e))
            return rerank_tools(
                pairs, user_text,
                top_n=cfg.rerank_top_n,
                pinned=cfg.rerank_pinned_tools,
            )
        return rerank_tools_llm(
            pairs, user_text,
            top_n=cfg.rerank_top_n,
            pinned=cfg.rerank_pinned_tools,
            llm_client=client,
            report=_freecad_log,
        )
    return rerank_tools(
        pairs, user_text,
        top_n=cfg.rerank_top_n,
        pinned=cfg.rerank_pinned_tools,
    )


def _extract_latest_user_text(conversation) -> str:
    """Return the text of the most recent user-authored message.

    Skips "[System] ..." synthetic messages injected by the framework —
    those contain tool/execution chatter, not user intent.
    Handles both plain string content and the block-list form used when
    images or documents are attached.
    """
    for msg in reversed(conversation.messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            if content.startswith("[System] "):
                continue
            return content
        if isinstance(content, list):
            parts = [
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            joined = "\n".join(p for p in parts if p).strip()
            if joined and not joined.startswith("[System] "):
                return joined
    return ""
