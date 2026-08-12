from unittest.mock import MagicMock

from mia.tools.calendar_tool import build_calendar_tool

def test_tool_metadata():
    tool = build_calendar_tool(MagicMock())
    assert tool.name == "block_calendar_slot"
    assert set(tool.input_schema["required"]) == {"start_iso", "duration_minutes", "title"}

def test_handler_creates_event_and_confirms():
    service = MagicMock()
    execute_mock = service.events.return_value.insert.return_value.execute
    execute_mock.return_value = {"id": "evt123"}

    tool = build_calendar_tool(service)
    result = tool.handler({
        "start_iso": "2026-08-12T15:00:00-07:00",
        "duration_minutes": 30,
        "title": "Focus time",
    })

    service.events.return_value.insert.assert_called_once()
    _, kwargs = service.events.return_value.insert.call_args
    assert kwargs["calendarId"] == "primary"
    assert kwargs["body"]["summary"] == "Focus time"
    assert kwargs["body"]["start"]["dateTime"] == "2026-08-12T15:00:00-07:00"
    assert kwargs["body"]["end"]["dateTime"] == "2026-08-12T15:30:00-07:00"
    assert "Focus time" in result
    assert "30 minutes" in result

def test_handler_surfaces_api_error_as_exception():
    service = MagicMock()
    service.events.return_value.insert.return_value.execute.side_effect = RuntimeError("api down")

    tool = build_calendar_tool(service)
    try:
        tool.handler({"start_iso": "2026-08-12T15:00:00-07:00", "duration_minutes": 30, "title": "x"})
        assert False, "expected RuntimeError to propagate"
    except RuntimeError:
        pass
