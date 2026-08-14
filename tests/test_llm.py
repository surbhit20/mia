from datetime import datetime
from unittest.mock import MagicMock

from mia.llm import ConversationHistory, dispatch_command
from mia.timeutil import local_timezone_label
from mia.tools.base import Tool, ToolRegistry


def _mock_tool_use_response(tool_name, tool_input, block_id="toolu_1"):
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input
    block.id = block_id
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
    history = ConversationHistory()

    result = dispatch_command(client, registry, "block an hour for focus time", history)

    assert result.tool_name == "block_calendar_slot"
    assert result.confirmation == "blocked Focus time"
    _, kwargs = client.messages.create.call_args
    assert kwargs["tools"] == registry.anthropic_tool_specs()
    today = datetime.now().astimezone().date().isoformat()
    assert today in kwargs["system"]
    assert local_timezone_label() in kwargs["system"]


def test_no_tool_use_returns_fallback():
    registry = ToolRegistry()
    client = MagicMock()
    client.messages.create.return_value = _mock_text_only_response()
    history = ConversationHistory()

    result = dispatch_command(client, registry, "what's the weather", history)

    assert result.tool_name is None
    assert "didn't catch" in result.confirmation


def test_handler_exception_returns_failure_notice():
    registry = ToolRegistry()
    def boom(args):
        raise RuntimeError("api down")
    registry.register(Tool(name="block_calendar_slot", description="d", input_schema={}, handler=boom))
    client = MagicMock()
    client.messages.create.return_value = _mock_tool_use_response("block_calendar_slot", {})
    history = ConversationHistory()

    result = dispatch_command(client, registry, "block time", history)

    assert result.tool_name == "block_calendar_slot"
    assert "didn't work" in result.confirmation


def test_second_call_includes_first_turn_in_history():
    registry = ToolRegistry()
    registry.register(Tool(
        name="find_gmail_messages",
        description="d",
        input_schema={"type": "object", "properties": {}},
        handler=lambda args: "found 2 emails about that",
    ))
    client = MagicMock()
    client.messages.create.return_value = _mock_tool_use_response(
        "find_gmail_messages", {"query": "proposal"}, block_id="toolu_1"
    )
    history = ConversationHistory()

    dispatch_command(client, registry, "find that email about the proposal", history)

    client.messages.create.return_value = _mock_tool_use_response(
        "find_gmail_messages", {"query": "proposal from bob"}, block_id="toolu_2"
    )
    dispatch_command(client, registry, "the one from bob", history)

    _, kwargs = client.messages.create.call_args
    messages = kwargs["messages"]
    # first user turn, first assistant turn, first tool_result, second user turn
    assert len(messages) == 4
    assert messages[0] == {"role": "user", "content": "find that email about the proposal"}
    assert messages[1]["role"] == "assistant"
    assert messages[2]["role"] == "user"
    assert messages[2]["content"][0]["type"] == "tool_result"
    assert messages[2]["content"][0]["tool_use_id"] == "toolu_1"
    assert messages[2]["content"][0]["content"] == "found 2 emails about that"
    assert messages[3] == {"role": "user", "content": "the one from bob"}


def test_history_trims_to_bounded_window():
    registry = ToolRegistry()
    registry.register(Tool(
        name="find_gmail_messages",
        description="d",
        input_schema={"type": "object", "properties": {}},
        handler=lambda args: "ok",
    ))
    client = MagicMock()
    history = ConversationHistory(max_exchanges=2)

    for i in range(3):
        client.messages.create.return_value = _mock_tool_use_response(
            "find_gmail_messages", {"query": f"q{i}"}, block_id=f"toolu_{i}"
        )
        dispatch_command(client, registry, f"command {i}", history)

    # only the last 2 exchanges (4 messages) are kept, plus this new call's
    # own user message -- but we only assert on history.as_messages() here,
    # which excludes the not-yet-sent new message.
    kept_messages = history.as_messages()
    assert len(kept_messages) == 6  # 2 exchanges x 3 messages each
    assert kept_messages[0] == {"role": "user", "content": "command 1"}


def test_history_omits_tool_result_when_no_tool_was_used():
    registry = ToolRegistry()
    client = MagicMock()
    client.messages.create.return_value = _mock_text_only_response()
    history = ConversationHistory()

    dispatch_command(client, registry, "what's the weather", history)

    messages = history.as_messages()
    assert len(messages) == 2  # user + assistant only, no tool_result


def test_disables_parallel_tool_use_to_prevent_unpaired_tool_results():
    registry = ToolRegistry()
    client = MagicMock()
    client.messages.create.return_value = _mock_text_only_response()
    history = ConversationHistory()

    dispatch_command(client, registry, "test", history)

    _, kwargs = client.messages.create.call_args
    assert kwargs["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}


def test_tool_use_and_tool_result_counts_match_in_history_across_all_branches():
    def _count_blocks(messages):
        tool_use_count = 0
        tool_result_count = 0
        for m in messages:
            if m["role"] == "assistant" and isinstance(m["content"], list):
                tool_use_count += sum(1 for b in m["content"] if getattr(b, "type", None) == "tool_use")
            if m["role"] == "user" and isinstance(m["content"], list):
                tool_result_count += sum(1 for b in m["content"] if b.get("type") == "tool_result")
        return tool_use_count, tool_result_count

    registry = ToolRegistry()
    registry.register(Tool(name="ok_tool", description="d", input_schema={}, handler=lambda a: "ok"))
    def boom(args):
        raise RuntimeError("boom")
    registry.register(Tool(name="boom_tool", description="d", input_schema={}, handler=boom))

    client = MagicMock()
    history = ConversationHistory()

    client.messages.create.return_value = _mock_tool_use_response("ok_tool", {}, block_id="t1")
    dispatch_command(client, registry, "cmd1", history)

    client.messages.create.return_value = _mock_tool_use_response("missing_tool", {}, block_id="t2")
    dispatch_command(client, registry, "cmd2", history)

    client.messages.create.return_value = _mock_tool_use_response("boom_tool", {}, block_id="t3")
    dispatch_command(client, registry, "cmd3", history)

    tool_use_count, tool_result_count = _count_blocks(history.as_messages())
    assert tool_use_count == tool_result_count == 3
