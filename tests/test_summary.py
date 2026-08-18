from unittest.mock import MagicMock

from mia.llm import ToolCallResult
from mia.summary import summarize


def _client(text="<h1>Budget sync</h1>"):
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


def _prompt_of(client) -> str:
    return client.messages.create.call_args.kwargs["messages"][0]["content"]


def test_returns_the_models_html():
    client = _client("<h1>Budget sync</h1><h2>Action Items</h2>")

    result = summarize(client, "Sarah: hi", ["Sarah"], [], [])

    assert result == "<h1>Budget sync</h1><h2>Action Items</h2>"


def test_prompt_carries_the_transcript_and_both_attendee_lists():
    client = _client()

    summarize(client, "Sarah: we should ship Friday", ["Sarah", "Speaker 2"], ["Sarah Chen", "Raj Patel"], [])

    prompt = _prompt_of(client)
    assert "Sarah: we should ship Friday" in prompt
    assert "Speaker 2" in prompt
    assert "Raj Patel" in prompt


def test_executed_actions_are_passed_as_ground_truth():
    # The Done ticks must come from what mia actually ran, never from the
    # model's reading of the transcript.
    client = _client()

    summarize(
        client,
        "Sarah: book us Thursday at 3",
        ["Sarah"],
        [],
        [ToolCallResult(tool_name="block_calendar_slot", confirmation="Blocked Budget review Thursday 3 PM")],
    )

    prompt = _prompt_of(client)
    assert "block_calendar_slot" in prompt
    assert "Blocked Budget review Thursday 3 PM" in prompt


def test_prompt_states_the_tick_and_dedup_rules():
    client = _client()

    summarize(client, "Sarah: hi", ["Sarah"], [], [])

    prompt = _prompt_of(client)
    assert "only" in prompt.lower()
    assert "once" in prompt.lower()


def test_uses_a_token_budget_large_enough_for_a_summary():
    # dispatch_command's 256 is sized for one spoken sentence; a summary of an
    # hour-long meeting needs far more room.
    client = _client()

    summarize(client, "Sarah: hi", ["Sarah"], [], [])

    assert client.messages.create.call_args.kwargs["max_tokens"] >= 2000


def test_joins_multiple_text_blocks():
    first, second = MagicMock(), MagicMock()
    first.type, first.text = "text", "<h1>A</h1>"
    second.type, second.text = "text", "<h2>B</h2>"
    response = MagicMock()
    response.content = [first, second]
    client = MagicMock()
    client.messages.create.return_value = response

    assert summarize(client, "Sarah: hi", ["Sarah"], [], []) == "<h1>A</h1><h2>B</h2>"
