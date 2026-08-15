from unittest.mock import MagicMock

from mia.tools.calendar_update_tool import build_update_calendar_event_tool


def _service_with_event(event: dict) -> MagicMock:
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {"items": [event]}
    return calendar_service


def test_tool_metadata():
    tool = build_update_calendar_event_tool(MagicMock())
    assert tool.name == "update_calendar_event"
    assert tool.input_schema["required"] == ["time_iso"]


def test_handler_returns_not_found_message_when_no_match():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {"items": []}

    tool = build_update_calendar_event_tool(calendar_service)
    result = tool.handler({"time_iso": "2026-08-14T16:00:00-07:00", "new_title": "Budget review"})

    assert result == "I couldn't find anything around 4:00 PM."
    calendar_service.events.return_value.patch.assert_not_called()


def test_handler_asks_for_clarification_on_multiple_matches():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {"id": "evt1", "summary": "Standup", "start": {"dateTime": "2026-08-14T15:55:00-07:00"}},
            {"id": "evt2", "summary": "1:1 with Bob", "start": {"dateTime": "2026-08-14T16:10:00-07:00"}},
        ]
    }

    tool = build_update_calendar_event_tool(calendar_service)
    result = tool.handler({"time_iso": "2026-08-14T16:00:00-07:00", "new_title": "x"})

    assert result == (
        "I found 2 meetings around 4:00 PM: 'Standup' at 3:55 PM and "
        "'1:1 with Bob' at 4:10 PM — which one?"
    )
    calendar_service.events.return_value.patch.assert_not_called()


def test_handler_returns_nothing_to_change_when_no_fields_given():
    calendar_service = _service_with_event({
        "id": "evt1", "summary": "Standup",
        "start": {"dateTime": "2026-08-14T16:00:00-07:00"},
        "end": {"dateTime": "2026-08-14T16:30:00-07:00"},
    })

    tool = build_update_calendar_event_tool(calendar_service)
    result = tool.handler({"time_iso": "2026-08-14T16:00:00-07:00"})

    assert result == "Nothing to change."
    calendar_service.events.return_value.patch.assert_not_called()


def test_handler_moves_event_preserving_original_duration():
    calendar_service = _service_with_event({
        "id": "evt1", "summary": "Standup",
        "start": {"dateTime": "2026-08-14T16:00:00-07:00"},
        "end": {"dateTime": "2026-08-14T16:30:00-07:00"},
    })

    tool = build_update_calendar_event_tool(calendar_service)
    result = tool.handler({
        "time_iso": "2026-08-14T16:00:00-07:00",
        "new_start_iso": "2026-08-14T17:00:00-07:00",
    })

    calendar_service.events.return_value.patch.assert_called_once()
    _, kwargs = calendar_service.events.return_value.patch.call_args
    assert kwargs["calendarId"] == "primary"
    assert kwargs["eventId"] == "evt1"
    assert kwargs["sendUpdates"] == "all"
    assert kwargs["body"]["start"]["dateTime"] == "2026-08-14T17:00:00-07:00"
    assert kwargs["body"]["end"]["dateTime"] == "2026-08-14T17:30:00-07:00"
    assert result == "Moved 'Standup' from 4:00 PM to 5:00 PM."


def test_handler_moves_event_and_changes_duration_together():
    calendar_service = _service_with_event({
        "id": "evt1", "summary": "Standup",
        "start": {"dateTime": "2026-08-14T16:00:00-07:00"},
        "end": {"dateTime": "2026-08-14T16:30:00-07:00"},
    })

    tool = build_update_calendar_event_tool(calendar_service)
    result = tool.handler({
        "time_iso": "2026-08-14T16:00:00-07:00",
        "new_start_iso": "2026-08-14T17:00:00-07:00",
        "new_duration_minutes": 45,
    })

    _, kwargs = calendar_service.events.return_value.patch.call_args
    assert kwargs["body"]["start"]["dateTime"] == "2026-08-14T17:00:00-07:00"
    assert kwargs["body"]["end"]["dateTime"] == "2026-08-14T17:45:00-07:00"
    assert result == "Moved 'Standup' to 5:00 PM and now 45 minutes."


def test_handler_changes_duration_only_keeping_original_start():
    calendar_service = _service_with_event({
        "id": "evt1", "summary": "Standup",
        "start": {"dateTime": "2026-08-14T16:00:00-07:00"},
        "end": {"dateTime": "2026-08-14T16:30:00-07:00"},
    })

    tool = build_update_calendar_event_tool(calendar_service)
    result = tool.handler({
        "time_iso": "2026-08-14T16:00:00-07:00",
        "new_duration_minutes": 45,
    })

    _, kwargs = calendar_service.events.return_value.patch.call_args
    assert kwargs["body"]["start"]["dateTime"] == "2026-08-14T16:00:00-07:00"
    assert kwargs["body"]["end"]["dateTime"] == "2026-08-14T16:45:00-07:00"
    assert result == "'Standup' is now 45 minutes."


def test_handler_renames_event_without_touching_time():
    calendar_service = _service_with_event({
        "id": "evt1", "summary": "Standup",
        "start": {"dateTime": "2026-08-14T16:00:00-07:00"},
        "end": {"dateTime": "2026-08-14T16:30:00-07:00"},
    })

    tool = build_update_calendar_event_tool(calendar_service)
    result = tool.handler({
        "time_iso": "2026-08-14T16:00:00-07:00",
        "new_title": "Budget review",
    })

    _, kwargs = calendar_service.events.return_value.patch.call_args
    assert kwargs["body"] == {"summary": "Budget review"}
    assert result == "Renamed 'Standup' to 'Budget review'."


def test_handler_changes_description_only():
    calendar_service = _service_with_event({
        "id": "evt1", "summary": "Standup",
        "start": {"dateTime": "2026-08-14T16:00:00-07:00"},
        "end": {"dateTime": "2026-08-14T16:30:00-07:00"},
    })

    tool = build_update_calendar_event_tool(calendar_service)
    result = tool.handler({
        "time_iso": "2026-08-14T16:00:00-07:00",
        "new_description": "Quarterly numbers",
    })

    _, kwargs = calendar_service.events.return_value.patch.call_args
    assert kwargs["body"] == {"description": "Quarterly numbers"}
    assert result == "Updated the description for 'Standup'."


def test_handler_accepts_empty_string_description():
    calendar_service = _service_with_event({
        "id": "evt1", "summary": "Standup",
        "start": {"dateTime": "2026-08-14T16:00:00-07:00"},
        "end": {"dateTime": "2026-08-14T16:30:00-07:00"},
    })

    tool = build_update_calendar_event_tool(calendar_service)
    result = tool.handler({
        "time_iso": "2026-08-14T16:00:00-07:00",
        "new_description": "",
    })

    _, kwargs = calendar_service.events.return_value.patch.call_args
    assert kwargs["body"] == {"description": ""}
    assert result == "Updated the description for 'Standup'."


def test_handler_moves_and_renames_together():
    calendar_service = _service_with_event({
        "id": "evt1", "summary": "Standup",
        "start": {"dateTime": "2026-08-14T09:00:00-07:00"},
        "end": {"dateTime": "2026-08-14T09:30:00-07:00"},
    })

    tool = build_update_calendar_event_tool(calendar_service)
    result = tool.handler({
        "time_iso": "2026-08-14T09:00:00-07:00",
        "new_start_iso": "2026-08-14T09:30:00-07:00",
        "new_title": "Budget review",
    })

    assert result == "Moved 'Standup' to 9:30 AM and renamed it to 'Budget review'."


def test_handler_treats_all_day_event_as_no_match():
    # find_events_near excludes all-day events (they can overlap the query
    # window via Google's overlap semantics without actually starting near
    # the target time), so the handler should report "not found" rather than
    # finding and then rejecting a time change on the all-day event.
    calendar_service = _service_with_event({
        "id": "evt1", "summary": "Company Holiday",
        "start": {"date": "2026-08-14"},
        "end": {"date": "2026-08-15"},
    })

    tool = build_update_calendar_event_tool(calendar_service)
    result = tool.handler({
        "time_iso": "2026-08-14T16:00:00-07:00",
        "new_start_iso": "2026-08-15T16:00:00-07:00",
    })

    assert result == "I couldn't find anything around 4:00 PM."
    calendar_service.events.return_value.patch.assert_not_called()


def test_handler_surfaces_calendar_api_error_as_exception():
    calendar_service = _service_with_event({
        "id": "evt1", "summary": "Standup",
        "start": {"dateTime": "2026-08-14T16:00:00-07:00"},
        "end": {"dateTime": "2026-08-14T16:30:00-07:00"},
    })
    calendar_service.events.return_value.patch.return_value.execute.side_effect = RuntimeError("api down")

    tool = build_update_calendar_event_tool(calendar_service)
    try:
        tool.handler({"time_iso": "2026-08-14T16:00:00-07:00", "new_title": "x"})
        assert False, "expected RuntimeError to propagate"
    except RuntimeError:
        pass
