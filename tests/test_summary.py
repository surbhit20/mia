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
    assert "Mark an item done ONLY if it appears in the ground-truth" in prompt
    assert "Never infer completion from the transcript." in prompt
    assert "Never list the same commitment twice." in prompt


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


def test_conversational_turns_are_not_offered_as_completed_actions():
    # dispatch_command returns tool_name=None when mia only spoke -- a reply or
    # a clarifying question. Those must never reach the model as ground truth,
    # or a question becomes a ticked item in the user's doc.
    client = _client()

    summarize(
        client,
        "Sarah: book us Thursday",
        ["Sarah"],
        [],
        [
            ToolCallResult(tool_name=None, confirmation="Did you mean today or tomorrow?"),
            ToolCallResult(tool_name="block_calendar_slot", confirmation="Blocked Thursday 3 PM"),
        ],
    )

    prompt = _prompt_of(client)
    assert "Blocked Thursday 3 PM" in prompt
    assert "Did you mean today or tomorrow?" not in prompt


def test_only_conversational_turns_reads_as_no_actions():
    client = _client()

    summarize(
        client,
        "Sarah: hi",
        ["Sarah"],
        [],
        [ToolCallResult(tool_name=None, confirmation="Hello")],
    )

    assert "mia executed no tools" in _prompt_of(client)


def test_failed_action_does_not_reach_the_prompt():
    # A handler that raised still returns a ToolCallResult (e.g. a booking
    # that failed on a Google error) -- succeeded=False must be excluded, or
    # a failed booking gets ticked as done in the user's doc.
    client = _client()

    summarize(
        client,
        "Sarah: book us Thursday at 3",
        ["Sarah"],
        [],
        [
            ToolCallResult(
                tool_name="block_calendar_slot",
                confirmation="Sorry, that didn't work — try again?",
                succeeded=False,
                mutated=True,
            ),
            ToolCallResult(
                tool_name="rename_calendar_event",
                confirmation="Renamed to Budget review",
                succeeded=True,
                mutated=True,
            ),
        ],
    )

    prompt = _prompt_of(client)
    assert "Sorry, that didn't work" not in prompt
    assert "Renamed to Budget review" in prompt


def test_readonly_action_does_not_reach_the_prompt():
    # A lookup tool (find_calendar_events, find_gmail_messages) never
    # changed anything, so it must not be offered as a completed action.
    client = _client()

    summarize(
        client,
        "Sarah: what's on my calendar",
        ["Sarah"],
        [],
        [
            ToolCallResult(
                tool_name="find_calendar_events",
                confirmation="You have 1 event: 'Standup' at 9 AM.",
                succeeded=True,
                mutated=False,
            ),
        ],
    )

    prompt = _prompt_of(client)
    assert "Standup" not in prompt
    assert "mia executed no tools" in prompt


def test_prompt_forbids_guessing_speaker_identities():
    # The only enforcement of this rule is the prompt text itself, so deleting
    # it must break a test rather than silently changing behavior.
    client = _client()

    summarize(client, "Speaker 1: hi", ["Speaker 1"], ["Sarah Chen"], [])

    prompt = _prompt_of(client)
    assert "Do NOT use it to" in prompt
    assert "unambiguous" in prompt
