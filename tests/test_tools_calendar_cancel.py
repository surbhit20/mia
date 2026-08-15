from unittest.mock import MagicMock

from mia.tools.calendar_cancel_tool import build_cancel_calendar_event_tool


def test_tool_metadata():
    tool = build_cancel_calendar_event_tool(MagicMock())
    assert tool.name == "cancel_calendar_event"
    assert tool.input_schema["required"] == ["time_iso"]


def test_handler_returns_not_found_message_when_no_match():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {"items": []}

    tool = build_cancel_calendar_event_tool(calendar_service)
    result = tool.handler({"time_iso": "2026-08-14T16:00:00-07:00"})

    assert result == "I couldn't find anything around 4:00 PM."
    calendar_service.events.return_value.delete.assert_not_called()


def test_handler_asks_for_clarification_on_multiple_matches():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {"id": "evt1", "summary": "Standup", "start": {"dateTime": "2026-08-14T15:55:00-07:00"}},
            {"id": "evt2", "summary": "1:1 with Bob", "start": {"dateTime": "2026-08-14T16:10:00-07:00"}},
        ]
    }

    tool = build_cancel_calendar_event_tool(calendar_service)
    result = tool.handler({"time_iso": "2026-08-14T16:00:00-07:00"})

    assert result == (
        "I found 2 meetings around 4:00 PM: 'Standup' at 3:55 PM and "
        "'1:1 with Bob' at 4:10 PM — which one?"
    )
    calendar_service.events.return_value.delete.assert_not_called()


def test_handler_deletes_single_match_and_confirms():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {"id": "evt1", "summary": "Standup", "start": {"dateTime": "2026-08-14T16:00:00-07:00"}},
        ]
    }

    tool = build_cancel_calendar_event_tool(calendar_service)
    result = tool.handler({"time_iso": "2026-08-14T16:00:00-07:00"})

    calendar_service.events.return_value.delete.assert_called_once_with(
        calendarId="primary", eventId="evt1", sendUpdates="all"
    )
    assert result == "Cancelled 'Standup' at 4:00 PM."


def test_handler_treats_all_day_event_as_no_match():
    # find_events_near excludes all-day events (they can overlap the query
    # window via Google's overlap semantics without actually starting near
    # the target time), so the handler should report "not found" rather than
    # cancelling the all-day event.
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {"id": "evt1", "summary": "Company Holiday", "start": {"date": "2026-08-14"}},
        ]
    }

    tool = build_cancel_calendar_event_tool(calendar_service)
    result = tool.handler({"time_iso": "2026-08-14T16:00:00-07:00"})

    assert result == "I couldn't find anything around 4:00 PM."
    calendar_service.events.return_value.delete.assert_not_called()


def test_handler_surfaces_calendar_api_error_as_exception():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {"id": "evt1", "summary": "Standup", "start": {"dateTime": "2026-08-14T16:00:00-07:00"}},
        ]
    }
    calendar_service.events.return_value.delete.return_value.execute.side_effect = RuntimeError("api down")

    tool = build_cancel_calendar_event_tool(calendar_service)
    try:
        tool.handler({"time_iso": "2026-08-14T16:00:00-07:00"})
        assert False, "expected RuntimeError to propagate"
    except RuntimeError:
        pass
