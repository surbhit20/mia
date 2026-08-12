from datetime import datetime, timezone
from unittest.mock import MagicMock

from mia.detection.calendar_enricher import find_current_meeting_title

NOW = datetime(2026, 8, 12, 15, 0, tzinfo=timezone.utc)
URL = "https://meet.google.com/abc-defg-hij"

def test_returns_title_when_event_matches():
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {
        "items": [{"summary": "Standup", "hangoutLink": URL}]
    }
    assert find_current_meeting_title(service, now=NOW, meet_url=URL) == "Standup"

def test_returns_none_when_no_event_matches():
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {
        "items": [{"summary": "Unrelated", "hangoutLink": "https://meet.google.com/xxx-yyyy-zzz"}]
    }
    assert find_current_meeting_title(service, now=NOW, meet_url=URL) is None

def test_returns_none_when_no_events():
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {"items": []}
    assert find_current_meeting_title(service, now=NOW, meet_url=URL) is None

def test_returns_none_when_api_raises():
    service = MagicMock()
    service.events.return_value.list.return_value.execute.side_effect = RuntimeError("api down")
    assert find_current_meeting_title(service, now=NOW, meet_url=URL) is None

def test_queries_within_ten_minute_window():
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {"items": []}
    find_current_meeting_title(service, now=NOW, meet_url=URL)
    _, kwargs = service.events.return_value.list.call_args
    assert kwargs["timeMin"] == "2026-08-12T14:50:00+00:00"
    assert kwargs["timeMax"] == "2026-08-12T15:10:00+00:00"
    assert kwargs["singleEvents"] is True
