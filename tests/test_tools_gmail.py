from unittest.mock import MagicMock

from mia.tools.gmail_tool import build_gmail_search_tool


def test_tool_metadata():
    tool = build_gmail_search_tool(MagicMock(), MagicMock())
    assert tool.name == "find_gmail_messages"
    assert tool.input_schema["required"] == ["query"]


def test_handler_returns_direct_message_when_no_results():
    gmail_service = MagicMock()
    gmail_service.users.return_value.messages.return_value.list.return_value.execute.return_value = {}
    anthropic_client = MagicMock()

    tool = build_gmail_search_tool(gmail_service, anthropic_client)
    result = tool.handler({"query": "nonexistent topic"})

    assert result == "I couldn't find anything matching that."
    anthropic_client.messages.create.assert_not_called()


def test_handler_summarizes_results_via_claude():
    gmail_service = MagicMock()
    list_execute = gmail_service.users.return_value.messages.return_value.list.return_value.execute
    list_execute.return_value = {"messages": [{"id": "msg1", "threadId": "t1"}]}
    get_execute = gmail_service.users.return_value.messages.return_value.get.return_value.execute
    get_execute.return_value = {
        "id": "msg1",
        "snippet": "Here's the proposal draft, let me know what you think.",
        "payload": {
            "headers": [
                {"name": "From", "value": "Bob <bob@example.com>"},
                {"name": "Subject", "value": "Project Proposal"},
                {"name": "Date", "value": "Mon, 10 Aug 2026 09:15:00 -0700"},
            ]
        },
    }

    anthropic_client = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "I found one email from Bob about the project proposal."
    response = MagicMock()
    response.content = [text_block]
    anthropic_client.messages.create.return_value = response

    tool = build_gmail_search_tool(gmail_service, anthropic_client)
    result = tool.handler({"query": "project proposal"})

    assert result == "I found one email from Bob about the project proposal."
    gmail_service.users.return_value.messages.return_value.list.assert_called_once_with(
        userId="me", q="project proposal", maxResults=5
    )
    gmail_service.users.return_value.messages.return_value.get.assert_called_once_with(
        userId="me", id="msg1", format="metadata", metadataHeaders=["From", "Subject", "Date"]
    )
    _, kwargs = anthropic_client.messages.create.call_args
    prompt_text = kwargs["messages"][0]["content"]
    assert "Bob <bob@example.com>" in prompt_text
    assert "Project Proposal" in prompt_text
    assert "project proposal" in prompt_text  # the original query is included
    assert "Mon, 10 Aug 2026 09:15:00 -0700" in prompt_text


def test_handler_surfaces_gmail_api_error_as_exception():
    gmail_service = MagicMock()
    gmail_service.users.return_value.messages.return_value.list.return_value.execute.side_effect = RuntimeError("api down")
    anthropic_client = MagicMock()

    tool = build_gmail_search_tool(gmail_service, anthropic_client)
    try:
        tool.handler({"query": "x"})
        assert False, "expected RuntimeError to propagate"
    except RuntimeError:
        pass


def test_handler_includes_multiple_results_in_summarization_prompt():
    gmail_service = MagicMock()
    list_execute = gmail_service.users.return_value.messages.return_value.list.return_value.execute
    list_execute.return_value = {"messages": [{"id": "msg1", "threadId": "t1"}, {"id": "msg2", "threadId": "t2"}]}

    def _get_side_effect(userId, id, format, metadataHeaders):
        response = MagicMock()
        if id == "msg1":
            response.execute.return_value = {
                "id": "msg1",
                "snippet": "First email preview",
                "payload": {"headers": [
                    {"name": "From", "value": "Bob <bob@example.com>"},
                    {"name": "Subject", "value": "Project Proposal"},
                    {"name": "Date", "value": "Mon, 10 Aug 2026 09:15:00 -0700"},
                ]},
            }
        else:
            response.execute.return_value = {
                "id": "msg2",
                "snippet": "Second email preview",
                "payload": {"headers": [
                    {"name": "From", "value": "Alice <alice@example.com>"},
                    {"name": "Subject", "value": "Also about the proposal"},
                    {"name": "Date", "value": "Tue, 11 Aug 2026 14:00:00 -0700"},
                ]},
            }
        return response

    gmail_service.users.return_value.messages.return_value.get.side_effect = _get_side_effect

    anthropic_client = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "I found two emails about the proposal, one from Bob and one from Alice."
    response = MagicMock()
    response.content = [text_block]
    anthropic_client.messages.create.return_value = response

    tool = build_gmail_search_tool(gmail_service, anthropic_client)
    result = tool.handler({"query": "proposal"})

    assert result == "I found two emails about the proposal, one from Bob and one from Alice."
    assert gmail_service.users.return_value.messages.return_value.get.call_count == 2
    _, kwargs = anthropic_client.messages.create.call_args
    prompt_text = kwargs["messages"][0]["content"]
    assert "Bob <bob@example.com>" in prompt_text
    assert "Alice <alice@example.com>" in prompt_text
