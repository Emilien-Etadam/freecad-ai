"""Tests for tool registry, schema generation, and execution."""

import pytest

from freecad_ai.tools.registry import (
    ToolDefinition,
    ToolParam,
    ToolRegistry,
    ToolResult,
    _params_to_json_schema,
)


def _make_tool(name="test_tool", params=None, handler=None):
    """Helper to create a ToolDefinition for testing."""
    if handler is None:
        handler = lambda **kw: ToolResult(success=True, output="ok", data=kw)
    if params is None:
        params = [ToolParam("x", "number", "A number")]
    return ToolDefinition(
        name=name,
        description=f"Test tool: {name}",
        parameters=params,
        handler=handler,
    )


class TestToolParam:
    def test_required_by_default(self):
        p = ToolParam("name", "string", "A name")
        assert p.required is True

    def test_optional_param(self):
        p = ToolParam("opt", "string", "Optional", required=False, default="hi")
        assert p.required is False
        assert p.default == "hi"

    def test_enum_param(self):
        p = ToolParam("color", "string", "Color", enum=["red", "blue"])
        assert p.enum == ["red", "blue"]

    def test_array_param_with_items(self):
        p = ToolParam("names", "array", "Names", items={"type": "string"})
        assert p.items == {"type": "string"}


class TestToolResult:
    def test_success_result(self):
        r = ToolResult(success=True, output="Created box")
        assert r.success is True
        assert r.data == {}
        assert r.error == ""

    def test_error_result(self):
        r = ToolResult(success=False, output="", error="Not found")
        assert r.success is False
        assert r.error == "Not found"


class TestToolRegistry:
    def test_register_and_get(self):
        reg = ToolRegistry()
        tool = _make_tool("my_tool")
        reg.register(tool)
        assert reg.get("my_tool") is tool

    def test_get_missing_returns_none(self):
        reg = ToolRegistry()
        assert reg.get("nonexistent") is None

    def test_list_tools(self):
        reg = ToolRegistry()
        reg.register(_make_tool("a"))
        reg.register(_make_tool("b"))
        names = [t.name for t in reg.list_tools()]
        assert "a" in names
        assert "b" in names

    def test_register_overwrites_same_name(self):
        reg = ToolRegistry()
        t1 = _make_tool("x")
        t2 = _make_tool("x")
        reg.register(t1)
        reg.register(t2)
        assert reg.get("x") is t2
        assert len(reg.list_tools()) == 1


class TestToolExecution:
    def test_execute_success(self):
        reg = ToolRegistry()
        reg.register(_make_tool("add", params=[
            ToolParam("a", "number", "A"),
            ToolParam("b", "number", "B"),
        ], handler=lambda a, b: ToolResult(
            success=True, output=str(a + b), data={"sum": a + b}
        )))
        result = reg.execute("add", {"a": 3, "b": 4})
        assert result.success is True
        assert result.data["sum"] == 7

    def test_execute_unknown_tool(self):
        reg = ToolRegistry()
        result = reg.execute("nonexistent", {})
        assert result.success is False
        assert "Unknown tool" in result.error

    def test_execute_wrong_params(self):
        reg = ToolRegistry()
        reg.register(_make_tool("strict", params=[
            ToolParam("required_arg", "string", "Required"),
        ], handler=lambda required_arg: ToolResult(
            success=True, output=required_arg
        )))
        result = reg.execute("strict", {"wrong_arg": "value"})
        assert result.success is False
        assert "Invalid parameters" in result.error

    def test_execute_handler_exception(self):
        def bad_handler(**kw):
            raise ValueError("Something broke")
        reg = ToolRegistry()
        reg.register(_make_tool("bad", handler=bad_handler))
        result = reg.execute("bad", {"x": 1})
        assert result.success is False
        assert "failed" in result.error
        assert "Something broke" in result.error


class TestParamsToJsonSchema:
    def test_empty_params(self):
        schema = _params_to_json_schema([])
        assert schema == {"type": "object", "properties": {}}
        assert "required" not in schema

    def test_required_param(self):
        schema = _params_to_json_schema([
            ToolParam("name", "string", "The name"),
        ])
        assert "name" in schema["properties"]
        assert schema["required"] == ["name"]
        assert schema["properties"]["name"]["type"] == "string"

    def test_optional_param_not_in_required(self):
        schema = _params_to_json_schema([
            ToolParam("x", "number", "X", required=False, default=0.0),
        ])
        assert "required" not in schema
        assert schema["properties"]["x"]["default"] == 0.0

    def test_enum_in_schema(self):
        schema = _params_to_json_schema([
            ToolParam("op", "string", "Operation", enum=["add", "sub"]),
        ])
        assert schema["properties"]["op"]["enum"] == ["add", "sub"]

    def test_array_items_in_schema(self):
        schema = _params_to_json_schema([
            ToolParam("names", "array", "Names", items={"type": "string"}),
        ])
        assert schema["properties"]["names"]["items"] == {"type": "string"}

    def test_mixed_required_optional(self):
        schema = _params_to_json_schema([
            ToolParam("req", "string", "Required"),
            ToolParam("opt", "number", "Optional", required=False),
        ])
        assert schema["required"] == ["req"]
        assert "req" in schema["properties"]
        assert "opt" in schema["properties"]


class TestOpenAISchema:
    def test_schema_structure(self):
        reg = ToolRegistry()
        reg.register(_make_tool("test", params=[
            ToolParam("msg", "string", "Message"),
        ]))
        schema = reg.to_openai_schema()
        assert len(schema) == 1
        assert schema[0]["type"] == "function"
        assert schema[0]["function"]["name"] == "test"
        assert "parameters" in schema[0]["function"]

    def test_empty_registry(self):
        reg = ToolRegistry()
        assert reg.to_openai_schema() == []


class TestAnthropicSchema:
    def test_schema_structure(self):
        reg = ToolRegistry()
        reg.register(_make_tool("test", params=[
            ToolParam("msg", "string", "Message"),
        ]))
        schema = reg.to_anthropic_schema()
        assert len(schema) == 1
        assert schema[0]["name"] == "test"
        assert "input_schema" in schema[0]

    def test_empty_registry(self):
        reg = ToolRegistry()
        assert reg.to_anthropic_schema() == []


class TestMCPSchema:
    def test_schema_structure(self):
        reg = ToolRegistry()
        reg.register(_make_tool("test", params=[
            ToolParam("msg", "string", "Message"),
        ]))
        schema = reg.to_mcp_schema()
        assert len(schema) == 1
        assert schema[0]["name"] == "test"
        assert "inputSchema" in schema[0]

    def test_empty_registry(self):
        reg = ToolRegistry()
        assert reg.to_mcp_schema() == []


class TestBuiltinToolSchemas:
    """Validate that every shipped tool emits a strict-OpenAI-compliant schema.

    Issue #10: GitHub Models rejected create_assembly because part_names was
    declared as 'array' without 'items'. Anthropic and Ollama silently accept
    this; OpenAI's marketplace API enforces the spec. Walk every built-in tool
    so future regressions surface immediately, not from a user bug report.
    """

    def test_every_array_property_has_items(self):
        from freecad_ai.tools.freecad_tools import ALL_TOOLS

        offenders = []
        for tool in ALL_TOOLS:
            schema = _params_to_json_schema(tool.parameters)
            for prop_name, prop in schema.get("properties", {}).items():
                if prop.get("type") == "array" and "items" not in prop:
                    offenders.append(f"{tool.name}.{prop_name}")
        assert not offenders, (
            "Array-typed properties without 'items' (rejected by strict "
            f"providers like GitHub Models): {offenders}"
        )
