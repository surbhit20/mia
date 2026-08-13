from unittest.mock import MagicMock

from mia.llm import dispatch_command
from mia.tools.base import Tool, ToolRegistry

def _mock_tool_use_response(tool_name, tool_input):
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input
    response = MagicMock()
    response.content = [block]
    return response

def _mock_text_only_response():
    block = MagicMock()
    block.type = "text"
    response = MagicMock()
    response.content = [block]
    return response

def test_dispatches_to_matching_tool():
    registry = ToolRegistry()
    registry.register(Tool(
        name="block_calendar_slot",
        description="d",
        input_schema={"type": "object", "properties": {}},
        handler=lambda args: f"blocked {args['title']}",
    ))
    client = MagicMock()
    client.messages.create.return_value = _mock_tool_use_response(
        "block_calendar_slot", {"title": "Focus time"}
    )

    result = dispatch_command(client, registry, "block an hour for focus time")

    assert result.tool_name == "block_calendar_slot"
    assert result.confirmation == "blocked Focus time"
    _, kwargs = client.messages.create.call_args
    assert kwargs["tools"] == registry.anthropic_tool_specs()

def test_no_tool_use_returns_fallback():
    registry = ToolRegistry()
    client = MagicMock()
    client.messages.create.return_value = _mock_text_only_response()

    result = dispatch_command(client, registry, "what's the weather")

    assert result.tool_name is None
    assert "didn't catch" in result.confirmation

def test_handler_exception_returns_failure_notice():
    registry = ToolRegistry()
    def boom(args):
        raise RuntimeError("api down")
    registry.register(Tool(name="block_calendar_slot", description="d", input_schema={}, handler=boom))
    client = MagicMock()
    client.messages.create.return_value = _mock_tool_use_response("block_calendar_slot", {})

    result = dispatch_command(client, registry, "block time")

    assert result.tool_name == "block_calendar_slot"
    assert "didn't work" in result.confirmation
