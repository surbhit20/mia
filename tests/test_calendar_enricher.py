from datetime import datetime, timezone
from unittest.mock import MagicMock

from mia.detection.calendar_enricher import find_current_meeting

NOW = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)
MEET_URL = "https://meet.google.com/abc-defg-hij"


def _service(items):
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {"items": items}
    return service


def test_returns_title_when_event_matches():
    service = _service([{"hangoutLink": MEET_URL, "summary": "Budget sync"}])

    result = find_current_meeting(service, now=NOW, meet_url=MEET_URL)

    assert result.title == "Budget sync"


def test_returns_attendee_display_names():
    service = _service([
        {
            "hangoutLink": MEET_URL,
            "summary": "Budget sync",
            "attendees": [
                {"displayName": "Sarah Chen", "email": "sarah@example.com"},
                {"displayName": "Raj Patel", "email": "raj@example.com"},
            ],
        }
    ])

    result = find_current_meeting(service, now=NOW, meet_url=MEET_URL)

    assert result.attendees == ["Sarah Chen", "Raj Patel"]


def test_falls_back_to_email_when_display_name_missing():
    # An invitee who never set a display name is still worth naming.
    service = _service([
        {"hangoutLink": MEET_URL, "summary": "Sync", "attendees": [{"email": "raj@example.com"}]}
    ])

    result = find_current_meeting(service, now=NOW, meet_url=MEET_URL)

    assert result.attendees == ["raj@example.com"]


def test_skips_attendees_with_neither_name_nor_email():
    service = _service([
        {"hangoutLink": MEET_URL, "summary": "Sync", "attendees": [{}, {"displayName": "Sarah"}]}
    ])

    result = find_current_meeting(service, now=NOW, meet_url=MEET_URL)

    assert result.attendees == ["Sarah"]


def test_returns_empty_attendees_when_event_has_none():
    service = _service([{"hangoutLink": MEET_URL, "summary": "Solo block"}])

    result = find_current_meeting(service, now=NOW, meet_url=MEET_URL)

    assert result.attendees == []


def test_returns_empty_info_when_no_event_matches():
    service = _service([{"hangoutLink": "https://meet.google.com/zzz-zzzz-zzz", "summary": "Other"}])

    result = find_current_meeting(service, now=NOW, meet_url=MEET_URL)

    assert result.title is None
    assert result.attendees == []


def test_returns_empty_info_when_no_events():
    result = find_current_meeting(_service([]), now=NOW, meet_url=MEET_URL)

    assert result.title is None
    assert result.attendees == []


def test_returns_empty_info_when_api_raises():
    # Detection must survive a Calendar hiccup; it runs on every poll.
    service = MagicMock()
    service.events.return_value.list.return_value.execute.side_effect = RuntimeError("boom")

    result = find_current_meeting(service, now=NOW, meet_url=MEET_URL)

    assert result.title is None
    assert result.attendees == []


def test_queries_within_ten_minute_window():
    service = _service([])

    find_current_meeting(service, now=NOW, meet_url=MEET_URL)

    kwargs = service.events.return_value.list.call_args.kwargs
    assert kwargs["timeMin"] == "2026-08-17T14:50:00+00:00"
    assert kwargs["timeMax"] == "2026-08-17T15:10:00+00:00"
    assert kwargs["singleEvents"] is True
