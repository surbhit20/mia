import pytest
from mia.tools.base import Tool, ToolRegistry

def make_tool(name="noop"):
    return Tool(
        name=name,
        description="does nothing",
        input_schema={"type": "object", "properties": {}},
        handler=lambda args: "done",
    )

def test_register_and_get():
    reg = ToolRegistry()
    tool = make_tool()
    reg.register(tool)
    assert reg.get("noop") is tool

def test_get_unknown_returns_none():
    reg = ToolRegistry()
    assert reg.get("missing") is None

def test_duplicate_registration_raises():
    reg = ToolRegistry()
    reg.register(make_tool())
    with pytest.raises(ValueError, match="noop"):
        reg.register(make_tool())

def test_anthropic_tool_specs_shape():
    reg = ToolRegistry()
    reg.register(make_tool("block_calendar_slot"))
    specs = reg.anthropic_tool_specs()
    assert specs == [{
        "name": "block_calendar_slot",
        "description": "does nothing",
        "input_schema": {"type": "object", "properties": {}},
    }]
