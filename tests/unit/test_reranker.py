"""Tests for the keyword-based tool reranker and registry filter_names support."""

from unittest.mock import MagicMock

import pytest

from freecad_ai.tools.registry import (
    ToolDefinition,
    ToolParam,
    ToolRegistry,
    ToolResult,
)
from freecad_ai.tools.reranker import (
    rerank_tools,
    rerank_tools_llm,
    _tokenize,
    _expand_snake_case,
    _parse_rerank_response,
    _explicit_tool_mentions,
)


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

class TestTokenize:
    def test_lowercase(self):
        assert _tokenize("Hello WORLD") == ["hello", "world"]

    def test_drops_stopwords(self):
        # "the", "a", "is" are stopwords
        assert _tokenize("the cat is a cat") == ["cat", "cat"]

    def test_drops_short_tokens(self):
        # Single-letter tokens are dropped
        assert _tokenize("a b c cat") == ["cat"]

    def test_keeps_domain_vocabulary(self):
        # Words like "circle", "box" are not stopwords
        tokens = _tokenize("draw a circle and a box")
        assert "circle" in tokens and "box" in tokens

    def test_empty(self):
        assert _tokenize("") == []
        assert _tokenize(None) == []

    def test_strips_punctuation(self):
        assert "hello" in _tokenize("hello, world!")


class TestExpandSnakeCase:
    def test_splits_snake_case(self):
        out = _expand_snake_case(["edit_sketch"])
        assert "edit_sketch" in out
        assert "edit" in out
        assert "sketch" in out

    def test_preserves_non_snake_tokens(self):
        out = _expand_snake_case(["circle", "box"])
        assert out == ["circle", "box"]


# ---------------------------------------------------------------------------
# Reranker behavior
# ---------------------------------------------------------------------------

# Realistic sample tool set — subset of the workbench's actual tools
SAMPLE_TOOLS = [
    ("create_sketch", "Create a new sketch with geometry (lines, circles, rectangles, arcs)"),
    ("edit_sketch", "Modify an existing sketch — add/remove geometry and constraints"),
    ("pad_sketch", "Extrude a sketch into a solid pad"),
    ("pocket_sketch", "Cut material from a body using a sketch (pocket feature)"),
    ("revolve_sketch", "Revolve a sketch around an axis to create a solid"),
    ("create_primitive", "Create a PartDesign primitive (box, cylinder, sphere, cone)"),
    ("fillet_edges", "Round selected edges with a fillet"),
    ("chamfer_edges", "Apply a chamfer to selected edges"),
    ("list_objects", "List all objects in the active document"),
    ("describe_object", "Describe an object's properties and shape"),
    ("export_stl", "Export an object as an STL mesh file"),
    ("take_screenshot", "Capture the viewport as a PNG image"),
]


class TestReranker:
    def test_returns_pinned_tools_first(self):
        result = rerank_tools(
            SAMPLE_TOOLS,
            "draw a circle",
            top_n=3,
            pinned=["list_objects"],
        )
        assert result[0] == "list_objects"

    def test_respects_top_n(self):
        result = rerank_tools(SAMPLE_TOOLS, "create a sketch", top_n=3)
        assert len(result) == 3

    def test_sketch_query_prioritizes_sketch_tools(self):
        result = rerank_tools(SAMPLE_TOOLS, "edit my sketch", top_n=3)
        # edit_sketch should be the top hit for "edit sketch"
        assert result[0] == "edit_sketch"
        # All top 3 should be sketch-related
        assert all("sketch" in name for name in result)

    def test_fillet_query_surfaces_fillet_tool(self):
        result = rerank_tools(
            SAMPLE_TOOLS, "add a fillet to the edge", top_n=2
        )
        assert "fillet_edges" in result

    def test_pinned_tool_always_included_even_when_irrelevant(self):
        # "take_screenshot" is irrelevant to "create a box", but is pinned
        result = rerank_tools(
            SAMPLE_TOOLS,
            "create a box",
            top_n=2,
            pinned=["take_screenshot"],
        )
        assert "take_screenshot" in result

    def test_pinned_tools_not_double_counted(self):
        result = rerank_tools(
            SAMPLE_TOOLS,
            "edit my sketch",
            top_n=3,
            pinned=["edit_sketch"],
        )
        # edit_sketch should appear exactly once, pinned, + 3 non-pinned top picks
        assert result.count("edit_sketch") == 1
        assert len(result) == 4

    def test_empty_query_still_returns_top_n(self):
        # Degrades gracefully — should not crash
        result = rerank_tools(SAMPLE_TOOLS, "", top_n=5)
        assert len(result) == 5

    def test_zero_top_n_returns_only_pinned(self):
        result = rerank_tools(
            SAMPLE_TOOLS,
            "anything",
            top_n=0,
            pinned=["list_objects"],
        )
        assert result == ["list_objects"]

    def test_empty_tool_list(self):
        assert rerank_tools([], "query", top_n=5) == []

    def test_idf_rewards_rare_matches(self):
        # "revolve" appears in only one tool — querying for it should
        # rank revolve_sketch very high
        result = rerank_tools(SAMPLE_TOOLS, "revolve around axis", top_n=1)
        assert result[0] == "revolve_sketch"

    def test_result_is_deterministic(self):
        a = rerank_tools(SAMPLE_TOOLS, "create sketch geometry", top_n=5)
        b = rerank_tools(SAMPLE_TOOLS, "create sketch geometry", top_n=5)
        assert a == b

    def test_explicit_tool_name_is_pinned(self):
        """User naming a tool (e.g. create_primitive) must always include it."""
        result = rerank_tools(
            SAMPLE_TOOLS,
            "Crée un cube 10 mm avec create_primitive",
            top_n=2,
        )
        assert "create_primitive" in result
        assert result[0] == "create_primitive"

    def test_explicit_tool_name_with_config_pinned(self):
        result = rerank_tools(
            SAMPLE_TOOLS,
            "use create_primitive for a box",
            top_n=2,
            pinned=["list_objects"],
        )
        assert result[0] == "create_primitive"
        assert "list_objects" in result


class TestExplicitToolMentions:
    def test_finds_snake_case_name(self):
        names = {"create_primitive", "pad_sketch"}
        assert _explicit_tool_mentions(
            "Crée un cube avec create_primitive", names
        ) == ["create_primitive"]

    def test_ignores_unknown_names(self):
        assert _explicit_tool_mentions("use foo_bar", {"create_primitive"}) == []

    def test_longer_name_matched_first(self):
        names = {"create", "create_primitive"}
        assert _explicit_tool_mentions("call create_primitive", names) == [
            "create_primitive"
        ]


# ---------------------------------------------------------------------------
# Registry schema filter_names integration
# ---------------------------------------------------------------------------

def _make_registry():
    reg = ToolRegistry()
    reg.register(ToolDefinition(
        "tool_a", "first tool", [ToolParam("x", "number", "X")],
        handler=lambda x: ToolResult(True, "ok"),
    ))
    reg.register(ToolDefinition(
        "tool_b", "second tool", [ToolParam("y", "string", "Y")],
        handler=lambda y: ToolResult(True, "ok"),
    ))
    reg.register(ToolDefinition(
        "tool_c", "third tool", [ToolParam("z", "boolean", "Z")],
        handler=lambda z: ToolResult(True, "ok"),
    ))
    return reg


class TestSchemaFilterNames:
    def test_openai_schema_no_filter_includes_all(self):
        reg = _make_registry()
        schemas = reg.to_openai_schema()
        names = [s["function"]["name"] for s in schemas]
        assert set(names) == {"tool_a", "tool_b", "tool_c"}

    def test_openai_schema_with_filter(self):
        reg = _make_registry()
        schemas = reg.to_openai_schema(filter_names={"tool_a", "tool_c"})
        names = {s["function"]["name"] for s in schemas}
        assert names == {"tool_a", "tool_c"}

    def test_anthropic_schema_with_filter(self):
        reg = _make_registry()
        schemas = reg.to_anthropic_schema(filter_names={"tool_b"})
        names = {s["name"] for s in schemas}
        assert names == {"tool_b"}

    def test_mcp_schema_with_filter(self):
        reg = _make_registry()
        schemas = reg.to_mcp_schema(filter_names={"tool_a"})
        names = {s["name"] for s in schemas}
        assert names == {"tool_a"}

    def test_empty_filter_returns_empty(self):
        reg = _make_registry()
        assert reg.to_openai_schema(filter_names=set()) == []

    def test_filter_skips_resolve_for_excluded_tools(self):
        """Excluded tools must NOT have their lazy_params resolved —
        this is the core MCP cost-saving property."""
        reg = ToolRegistry()
        lazy_a = MagicMock(return_value=[ToolParam("a", "string", "a")])
        lazy_b = MagicMock(return_value=[ToolParam("b", "string", "b")])
        reg.register(ToolDefinition(
            "included", "included tool", [],
            handler=lambda a: ToolResult(True, "ok"),
            lazy_params=lazy_a,
        ))
        reg.register(ToolDefinition(
            "excluded", "excluded tool", [],
            handler=lambda b: ToolResult(True, "ok"),
            lazy_params=lazy_b,
        ))

        reg.to_openai_schema(filter_names={"included"})

        lazy_a.assert_called_once()
        lazy_b.assert_not_called()


class TestListNameDescriptionPairs:
    def test_returns_all_pairs(self):
        reg = _make_registry()
        pairs = reg.list_name_description_pairs()
        assert len(pairs) == 3
        names = {n for n, _d in pairs}
        assert names == {"tool_a", "tool_b", "tool_c"}

    def test_does_not_resolve_deferred_params(self):
        """MCP tools with lazy_params must not be resolved by this call."""
        reg = ToolRegistry()
        lazy = MagicMock(return_value=[ToolParam("x", "string", "x")])
        reg.register(ToolDefinition(
            "deferred", "lazy tool", [],
            handler=lambda x: ToolResult(True, "ok"),
            lazy_params=lazy,
        ))
        pairs = reg.list_name_description_pairs()
        assert pairs == [("deferred", "lazy tool")]
        lazy.assert_not_called()


# ---------------------------------------------------------------------------
# LLM rerank response parsing
# ---------------------------------------------------------------------------

VALID = {"create_sketch", "pad_sketch", "fillet_edges", "list_objects"}


class TestParseRerankResponse:
    def test_plain_json_array(self):
        text = '["create_sketch", "pad_sketch"]'
        assert _parse_rerank_response(text, VALID) == ["create_sketch", "pad_sketch"]

    def test_markdown_fenced_json(self):
        text = '```json\n["create_sketch", "fillet_edges"]\n```'
        assert _parse_rerank_response(text, VALID) == ["create_sketch", "fillet_edges"]

    def test_bare_fenced_json(self):
        text = '```\n["list_objects"]\n```'
        assert _parse_rerank_response(text, VALID) == ["list_objects"]

    def test_array_embedded_in_prose(self):
        text = 'Here are the most relevant tools: ["create_sketch", "pad_sketch"]. Hope this helps!'
        assert _parse_rerank_response(text, VALID) == ["create_sketch", "pad_sketch"]

    def test_hallucinated_names_dropped(self):
        # "draw_hole" isn't in VALID — must be filtered out
        text = '["draw_hole", "create_sketch", "invented_tool"]'
        assert _parse_rerank_response(text, VALID) == ["create_sketch"]

    def test_duplicates_deduped_keeping_order(self):
        text = '["create_sketch", "pad_sketch", "create_sketch"]'
        assert _parse_rerank_response(text, VALID) == ["create_sketch", "pad_sketch"]

    def test_non_string_items_dropped(self):
        text = '["create_sketch", 42, null, "pad_sketch"]'
        assert _parse_rerank_response(text, VALID) == ["create_sketch", "pad_sketch"]

    def test_empty_response(self):
        assert _parse_rerank_response("", VALID) == []

    def test_malformed_json(self):
        assert _parse_rerank_response("not json at all", VALID) == []

    def test_truncated_json(self):
        # Unterminated array — must not crash
        assert _parse_rerank_response('["create_sketch",', VALID) == []

    def test_object_wrapping_array_is_recovered(self):
        # LLM returned an object instead of a bare array. The regex
        # fallback finds the inner array — that's better than treating
        # the whole response as a failure and falling through to keyword.
        result = _parse_rerank_response('{"tools": ["create_sketch"]}', VALID)
        assert result == ["create_sketch"]

    def test_object_with_no_array_returns_empty(self):
        # Pure object with no bracketed sequence — nothing to recover
        assert _parse_rerank_response('{"answer": "dunno"}', VALID) == []


# ---------------------------------------------------------------------------
# LLM reranker end-to-end (with mock LLMClient)
# ---------------------------------------------------------------------------

class _FakeClient:
    """Minimal stand-in for LLMClient: records calls, returns a preset string."""

    def __init__(self, response="[]", raise_exc=None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls = []

    def send(self, messages, system=""):
        self.calls.append({"messages": messages, "system": system})
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


class TestRerankToolsLLM:
    def test_returns_parsed_names_when_llm_cooperates(self):
        client = _FakeClient(response='["create_sketch", "pad_sketch"]')
        result = rerank_tools_llm(
            SAMPLE_TOOLS, "extrude a sketch", top_n=2, llm_client=client,
        )
        assert result == ["create_sketch", "pad_sketch"]
        assert len(client.calls) == 1

    def test_pinned_always_first(self):
        client = _FakeClient(response='["create_sketch"]')
        result = rerank_tools_llm(
            SAMPLE_TOOLS, "sketch something", top_n=1,
            pinned=["list_objects"], llm_client=client,
        )
        assert result[0] == "list_objects"
        assert "create_sketch" in result

    def test_falls_back_to_keyword_on_exception(self):
        client = _FakeClient(raise_exc=RuntimeError("connection refused"))
        result = rerank_tools_llm(
            SAMPLE_TOOLS, "edit my sketch", top_n=3, llm_client=client,
        )
        # Keyword fallback should still surface edit_sketch for this query
        assert "edit_sketch" in result
        assert len(result) == 3

    def test_falls_back_when_response_is_unparseable(self):
        client = _FakeClient(response="I don't know what to do")
        result = rerank_tools_llm(
            SAMPLE_TOOLS, "fillet an edge", top_n=2, llm_client=client,
        )
        # No valid names parsed — top-up from keyword should still give us fillet_edges
        assert "fillet_edges" in result
        assert len(result) == 2

    def test_tops_up_from_keyword_when_llm_returns_too_few(self):
        # LLM returns only 1 of 3 requested — keyword fills the other 2
        client = _FakeClient(response='["revolve_sketch"]')
        result = rerank_tools_llm(
            SAMPLE_TOOLS, "revolve a sketch profile", top_n=3,
            llm_client=client,
        )
        assert result[0] == "revolve_sketch"
        assert len(result) == 3
        # The top-up names must also come from SAMPLE_TOOLS
        sample_names = {n for n, _d in SAMPLE_TOOLS}
        assert all(name in sample_names for name in result)

    def test_top_up_does_not_duplicate_llm_picks(self):
        client = _FakeClient(response='["edit_sketch"]')
        result = rerank_tools_llm(
            SAMPLE_TOOLS, "edit my sketch", top_n=3, llm_client=client,
        )
        # edit_sketch should appear exactly once even though keyword would
        # also rank it highly
        assert result.count("edit_sketch") == 1
        assert len(result) == 3

    def test_respects_top_n_cap(self):
        # LLM returns more than requested — function must cap it
        many = '["create_sketch", "pad_sketch", "edit_sketch", "revolve_sketch", "pocket_sketch"]'
        client = _FakeClient(response=many)
        result = rerank_tools_llm(
            SAMPLE_TOOLS, "anything", top_n=2, llm_client=client,
        )
        assert len(result) == 2

    def test_no_client_falls_back_to_keyword(self):
        # Passing llm_client=None should just run keyword rerank
        result = rerank_tools_llm(
            SAMPLE_TOOLS, "edit my sketch", top_n=3, llm_client=None,
        )
        assert result[0] == "edit_sketch"

    def test_zero_top_n_returns_only_pinned_without_calling_llm(self):
        client = _FakeClient(response='["create_sketch"]')
        result = rerank_tools_llm(
            SAMPLE_TOOLS, "anything", top_n=0,
            pinned=["list_objects"], llm_client=client,
        )
        assert result == ["list_objects"]
        assert client.calls == []  # short-circuit, no LLM call

    def test_explicit_tool_name_pinned_in_llm_reranker(self):
        client = _FakeClient(response='["create_sketch"]')
        result = rerank_tools_llm(
            SAMPLE_TOOLS,
            "Crée un cube 10 mm avec create_primitive",
            top_n=2,
            llm_client=client,
        )
        assert result[0] == "create_primitive"
        assert "create_primitive" in result

    def test_hallucinated_names_filtered_then_topped_up(self):
        # LLM invents two names, names one real one. Top-up fills the gap.
        client = _FakeClient(
            response='["invented_one", "fillet_edges", "made_up_tool"]'
        )
        result = rerank_tools_llm(
            SAMPLE_TOOLS, "round some edges", top_n=3, llm_client=client,
        )
        assert "fillet_edges" in result
        # None of the hallucinated names made it through
        assert "invented_one" not in result
        assert "made_up_tool" not in result
        assert len(result) == 3

    def test_system_prompt_is_sent(self):
        client = _FakeClient(response="[]")
        rerank_tools_llm(
            SAMPLE_TOOLS, "anything", top_n=2, llm_client=client,
        )
        # Reranker must pass a non-empty system prompt so the LLM knows
        # what JSON shape is expected
        assert client.calls[0]["system"]
        assert "JSON" in client.calls[0]["system"].upper() or \
               "json" in client.calls[0]["system"]
