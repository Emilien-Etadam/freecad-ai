"""Tool reranking — filter the tool set sent to the LLM down to the top-N.

Two strategies share a common signature so they're interchangeable:

- ``rerank_tools`` (keyword): pure-Python IDF-weighted token overlap. Zero
  extra LLM cost, zero latency, but lexical only — no synonyms, no stemming,
  no conceptual matching.
- ``rerank_tools_llm`` (semantic): asks a small/fast LLM to pick the N most
  relevant tool names. Closes keyword's blind spots (synonyms, paraphrase,
  context) at the cost of one extra round-trip per user turn. Falls back to
  the keyword reranker on any failure (timeout, bad JSON, empty output) so
  a broken reranker never breaks the chat.

Both reduce prompt tokens and, via the registry's ``filter_names`` plumbing,
avoid resolving deferred params for excluded MCP tools.
"""

from __future__ import annotations

import json
import logging
import math
import re

logger = logging.getLogger(__name__)


# Conservative stopword list — only drops tokens that carry no intent signal.
# We keep modelling / shape vocabulary like "circle", "box", "edit".
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "could",
    "do", "does", "for", "from", "has", "have", "how", "i", "if", "in", "into",
    "is", "it", "its", "just", "let", "me", "my", "need", "not", "of", "on",
    "one", "or", "our", "please", "so", "some", "than", "that", "the", "then",
    "there", "this", "to", "too", "us", "use", "want", "was", "we", "were",
    "what", "when", "where", "which", "who", "why", "will", "with", "would",
    "you", "your",
})

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def _tokenize(text: str) -> list[str]:
    """Lowercase tokenize, drop stopwords and very short tokens."""
    if not text:
        return []
    tokens = _TOKEN_RE.findall(text.lower())
    return [t for t in tokens if len(t) >= 2 and t not in _STOPWORDS]


def _expand_snake_case(tokens: list[str]) -> list[str]:
    """Split snake_case tokens into constituent words, keeping originals.

    "edit_sketch" → ["edit_sketch", "edit", "sketch"]. Helps match tool
    names against natural-language queries like "edit the sketch".
    """
    out = list(tokens)
    for t in tokens:
        if "_" in t:
            out.extend(p for p in t.split("_") if p and p not in _STOPWORDS)
    return out


def _compute_idf(tool_token_lists: list[list[str]]) -> dict[str, float]:
    """Standard IDF: log((N + 1) / (df + 1)) + 1 (smoothed)."""
    n_docs = len(tool_token_lists)
    df: dict[str, int] = {}
    for tokens in tool_token_lists:
        for tok in set(tokens):
            df[tok] = df.get(tok, 0) + 1
    return {
        tok: math.log((n_docs + 1) / (d + 1)) + 1
        for tok, d in df.items()
    }


def _score(
    query_tokens: list[str],
    doc_tokens: list[str],
    idf: dict[str, float],
) -> float:
    """Sum of IDF weights for query tokens that appear in the doc.

    A simple score, but it behaves well for short docs (tool descriptions).
    Each query token contributes at most once — prevents repetition in the
    query from dominating.
    """
    if not query_tokens:
        return 0.0
    doc_set = set(doc_tokens)
    return sum(idf.get(tok, 1.0) for tok in set(query_tokens) if tok in doc_set)


def _explicit_tool_mentions(
    user_message: str, tool_names: set[str]
) -> list[str]:
    """Return tool names the user explicitly referenced in their message.

    Matches exact tool names as whole tokens (e.g. ``create_primitive`` in
    "avec create_primitive", but not ``create`` inside ``create_primitive``).
    Preserves the order of first appearance in ``user_message``.
    """
    if not user_message or not tool_names:
        return []
    text = user_message.lower()
    found: list[tuple[int, str]] = []
    for name in sorted(tool_names, key=len, reverse=True):
        pattern = re.compile(
            r"(?<![a-z0-9_])" + re.escape(name) + r"(?![a-z0-9_])"
        )
        match = pattern.search(text)
        if match:
            found.append((match.start(), name))
    found.sort(key=lambda p: p[0])
    seen: set[str] = set()
    out: list[str] = []
    for _, name in found:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def rerank_tools(
    tools: list[tuple[str, str]],
    user_message: str,
    top_n: int = 15,
    pinned: list[str] | None = None,
) -> list[str]:
    """Return tool names to include, pinned first then scored top-N.

    ``tools`` is a list of (name, description) pairs.
    ``user_message`` is the raw text of the message being reranked against.
    ``top_n`` is the maximum number of *non-pinned* tools to return.
    ``pinned`` are always included, regardless of score.
    Tools explicitly named in ``user_message`` are also pinned automatically.

    If ``user_message`` is empty or no token matches exist, the top_n
    tools by lexical order (stable) are returned, to keep behavior
    predictable on edge cases.
    """
    all_names = {name for name, _ in tools}
    explicit = _explicit_tool_mentions(user_message, all_names)
    pinned_set = set(pinned or []) | set(explicit)
    pinned_present = [name for name in explicit if name in all_names]
    pinned_present += [name for name, _ in tools if name in pinned_set and name not in pinned_present]

    candidates = [(n, d) for n, d in tools if n not in pinned_set]
    if not candidates or top_n <= 0:
        return pinned_present

    query_tokens = _expand_snake_case(_tokenize(user_message))

    tool_tokens = [
        _expand_snake_case(_tokenize(f"{name} {description}"))
        for name, description in candidates
    ]
    idf = _compute_idf(tool_tokens)

    scored = []
    for (name, _desc), tokens in zip(candidates, tool_tokens):
        scored.append((_score(query_tokens, tokens, idf), name))

    # Sort by score desc, then by name for determinism
    scored.sort(key=lambda p: (-p[0], p[1]))
    top = [name for _s, name in scored[:top_n]]
    return pinned_present + top


# ---------------------------------------------------------------------------
# LLM-based reranker
# ---------------------------------------------------------------------------

_RERANK_SYSTEM_PROMPT = (
    "You are a tool relevance filter. Given a user's request and a list of "
    "available tools, select the tools most relevant to the request. "
    "Respond with ONLY a JSON array of tool names ordered by relevance "
    "(most relevant first). No commentary, no markdown, just the array."
)


def _build_rerank_prompt(
    tools: list[tuple[str, str]], user_message: str, top_n: int
) -> str:
    """Assemble the user-facing prompt for the reranker LLM."""
    tool_lines = "\n".join(f"- {name}: {desc}" for name, desc in tools)
    return (
        f"Available tools:\n{tool_lines}\n\n"
        f"User request: {user_message}\n\n"
        f"Return a JSON array of up to {top_n} tool names, most relevant "
        f"first. Example: [\"create_sketch\", \"pad_sketch\"]"
    )


_ARRAY_RE = re.compile(r"\[[^\[\]]*\]", re.DOTALL)


def _parse_rerank_response(text: str, valid_names: set[str]) -> list[str]:
    """Extract a list of valid tool names from the LLM's response.

    Handles three response shapes in order:
      1. Raw JSON array
      2. JSON array inside a ``` fence
      3. JSON array embedded in prose

    Unknown names (hallucinations) are dropped. Duplicates are removed,
    preserving first-seen order. Returns an empty list on any parse failure.
    """
    if not text:
        return []
    cleaned = text.strip()

    # Strip markdown fence if present
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = lines[1:]  # drop opening fence
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    # Try a direct parse first
    candidates: list = []
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            candidates = parsed
    except json.JSONDecodeError:
        pass

    # Fall back to extracting the first bracketed sequence from the text
    if not candidates:
        match = _ARRAY_RE.search(cleaned)
        if match:
            try:
                parsed = json.loads(match.group())
                if isinstance(parsed, list):
                    candidates = parsed
            except json.JSONDecodeError:
                pass

    # Filter, dedupe (keep first), and drop non-strings
    seen: set[str] = set()
    out: list[str] = []
    for item in candidates:
        if not isinstance(item, str):
            continue
        if item in valid_names and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def rerank_tools_llm(
    tools: list[tuple[str, str]],
    user_message: str,
    top_n: int = 15,
    pinned: list[str] | None = None,
    llm_client=None,
    report=None,
) -> list[str]:
    """Rerank tools by asking an LLM for the top-N most relevant names.

    ``llm_client`` must implement ``.send(messages, system) -> str``.
    ``report`` is an optional ``Callable[[str], None]`` that receives
    diagnostic messages at each decision point — useful for surfacing
    what the LLM actually returned to users debugging the feature.

    Robustness contract:
      - Any exception from the LLM call → fall back to keyword rerank
      - Unparseable response → fall back to keyword rerank
      - LLM returns fewer than ``top_n`` valid names → top up from keyword
      - LLM hallucinates tool names → silently dropped (filtered against
        the real tool set)

    The goal is that enabling the LLM reranker never makes the experience
    *worse* than keyword reranking alone.
    """
    if llm_client is None:
        return rerank_tools(tools, user_message, top_n, pinned)

    pinned_list = list(pinned or [])
    valid_all = {n for n, _ in tools}
    explicit = _explicit_tool_mentions(user_message, valid_all)
    pinned_list = explicit + [n for n in pinned_list if n not in explicit]
    pinned_set = set(pinned_list)
    candidates = [(n, d) for n, d in tools if n not in pinned_set]
    pinned_present = [n for n, _ in tools if n in pinned_set]

    if not candidates or top_n <= 0:
        return pinned_present

    valid_names = {n for n, _ in candidates}

    if report:
        report("LLM reranker: sending {} candidates, asking for top {}".format(
            len(candidates), top_n))

    try:
        prompt = _build_rerank_prompt(candidates, user_message, top_n)
        response = llm_client.send(
            [{"role": "user", "content": prompt}],
            system=_RERANK_SYSTEM_PROMPT,
        )
    except Exception as e:
        msg = "LLM reranker: call failed ({}); falling back to keyword".format(e)
        logger.warning(msg)
        if report:
            report(msg)
        return rerank_tools(tools, user_message, top_n, pinned)

    if report:
        preview = (response or "").strip().replace("\n", " ")
        if len(preview) > 200:
            preview = preview[:200] + "..."
        report("LLM reranker: raw response ({} chars): {}".format(
            len(response or ""), preview))

    ranked = _parse_rerank_response(response, valid_names)

    if report:
        report("LLM reranker: parsed {} valid names (hallucinations dropped)".format(
            len(ranked)))

    # Top up from keyword if the LLM returned too few valid names
    if len(ranked) < top_n:
        kw_fill = rerank_tools(
            [(n, d) for n, d in candidates if n not in ranked],
            user_message,
            top_n=top_n - len(ranked),
            pinned=None,
        )
        if report:
            report("LLM reranker: topping up {} slots from keyword -> {}".format(
                len(kw_fill), ", ".join(kw_fill)))
        ranked = ranked + kw_fill

    # Enforce top_n cap (in case LLM returned more than requested)
    ranked = ranked[:top_n]

    return pinned_present + ranked
