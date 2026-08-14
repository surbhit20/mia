from unittest.mock import MagicMock

from mia.tools.calendar_fetch_tool import build_calendar_fetch_tool


def test_tool_metadata():
    tool = build_calendar_fetch_tool(MagicMock())
    assert tool.name == "find_calendar_events"
    assert tool.input_schema["required"] == ["start_iso", "end_iso"]


def test_handler_calls_events_list_with_correct_params():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {"items": []}

    tool = build_calendar_fetch_tool(calendar_service)
    tool.handler({"start_iso": "2026-08-14T00:00:00-07:00", "end_iso": "2026-08-14T23:59:59-07:00"})

    calendar_service.events.return_value.list.assert_called_once_with(
        calendarId="primary",
        timeMin="2026-08-14T00:00:00-07:00",
        timeMax="2026-08-14T23:59:59-07:00",
        singleEvents=True,
        orderBy="startTime",
        maxResults=10,
    )


def test_handler_returns_free_message_when_no_events():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {"items": []}

    tool = build_calendar_fetch_tool(calendar_service)
    result = tool.handler({"start_iso": "2026-08-14T15:00:00-07:00", "end_iso": "2026-08-14T16:00:00-07:00"})

    assert result == "You're free then — nothing scheduled."


def test_handler_formats_single_event():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {"summary": "Standup", "start": {"dateTime": "2026-08-14T09:00:00-07:00"}},
        ]
    }

    tool = build_calendar_fetch_tool(calendar_service)
    result = tool.handler({"start_iso": "2026-08-14T00:00:00-07:00", "end_iso": "2026-08-14T23:59:59-07:00"})

    assert result == "You have 1 event: 'Standup' at 9:00 AM."


def test_handler_formats_multiple_events():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {"summary": "Standup", "start": {"dateTime": "2026-08-14T09:00:00-07:00"}},
            {"summary": "1:1 with Bob", "start": {"dateTime": "2026-08-14T14:00:00-07:00"}},
            {"summary": "Focus time", "start": {"dateTime": "2026-08-14T15:00:00-07:00"}},
        ]
    }

    tool = build_calendar_fetch_tool(calendar_service)
    result = tool.handler({"start_iso": "2026-08-14T00:00:00-07:00", "end_iso": "2026-08-14T23:59:59-07:00"})

    assert result == (
        "You have 3 events: 'Standup' at 9:00 AM, '1:1 with Bob' at 2:00 PM, "
        "and 'Focus time' at 3:00 PM."
    )


def test_handler_formats_two_events_without_oxford_comma():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {"summary": "Standup", "start": {"dateTime": "2026-08-14T09:00:00-07:00"}},
            {"summary": "Focus time", "start": {"dateTime": "2026-08-14T15:00:00-07:00"}},
        ]
    }

    tool = build_calendar_fetch_tool(calendar_service)
    result = tool.handler({"start_iso": "2026-08-14T00:00:00-07:00", "end_iso": "2026-08-14T23:59:59-07:00"})

    assert result == "You have 2 events: 'Standup' at 9:00 AM and 'Focus time' at 3:00 PM."


def test_handler_formats_all_day_event():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {"summary": "Company Holiday", "start": {"date": "2026-08-14"}},
        ]
    }

    tool = build_calendar_fetch_tool(calendar_service)
    result = tool.handler({"start_iso": "2026-08-14T00:00:00-07:00", "end_iso": "2026-08-14T23:59:59-07:00"})

    assert result == "You have 1 event: 'Company Holiday' (all day)."


def test_handler_appends_truncation_note_when_more_results_exist():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {"summary": "Standup", "start": {"dateTime": "2026-08-14T09:00:00-07:00"}},
        ],
        "nextPageToken": "abc123",
    }

    tool = build_calendar_fetch_tool(calendar_service)
    result = tool.handler({"start_iso": "2026-08-14T00:00:00-07:00", "end_iso": "2026-08-14T23:59:59-07:00"})

    assert result == (
        "You have 1 event: 'Standup' at 9:00 AM. "
        "...and there are more beyond that — want me to narrow the time range?"
    )


def test_handler_uses_untitled_fallback_when_summary_missing():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {"start": {"dateTime": "2026-08-14T09:00:00-07:00"}},
        ]
    }

    tool = build_calendar_fetch_tool(calendar_service)
    result = tool.handler({"start_iso": "2026-08-14T00:00:00-07:00", "end_iso": "2026-08-14T23:59:59-07:00"})

    assert result == "You have 1 event: '(untitled event)' at 9:00 AM."


def test_handler_surfaces_calendar_api_error_as_exception():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.side_effect = RuntimeError("api down")

    tool = build_calendar_fetch_tool(calendar_service)
    try:
        tool.handler({"start_iso": "x", "end_iso": "y"})
        assert False, "expected RuntimeError to propagate"
    except RuntimeError:
        pass
