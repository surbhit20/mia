from mia.tools.calendar_lookup import (
    find_events_near,
    format_ambiguous_question,
    format_candidate,
    format_event_time,
    format_not_found,
    is_declined,
)
from unittest.mock import MagicMock


def test_is_declined_true_for_self_declined_attendee():
    event = {
        "attendees": [
            {"email": "me@example.com", "self": True, "responseStatus": "declined"},
        ]
    }
    assert is_declined(event) is True


def test_is_declined_false_when_no_attendees():
    assert is_declined({}) is False


def test_is_declined_false_when_self_accepted():
    event = {
        "attendees": [
            {"email": "me@example.com", "self": True, "responseStatus": "accepted"},
        ]
    }
    assert is_declined(event) is False


def test_find_events_near_queries_widened_window():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {"items": []}

    find_events_near(calendar_service, "2026-08-14T16:00:00-07:00")

    calendar_service.events.return_value.list.assert_called_once_with(
        calendarId="primary",
        timeMin="2026-08-14T15:45:00-07:00",
        timeMax="2026-08-14T16:15:00-07:00",
        singleEvents=True,
        orderBy="startTime",
        maxResults=5,
    )


def test_find_events_near_respects_custom_window():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {"items": []}

    find_events_near(calendar_service, "2026-08-14T16:00:00-07:00", window_minutes=5)

    calendar_service.events.return_value.list.assert_called_once_with(
        calendarId="primary",
        timeMin="2026-08-14T15:55:00-07:00",
        timeMax="2026-08-14T16:05:00-07:00",
        singleEvents=True,
        orderBy="startTime",
        maxResults=5,
    )


def test_find_events_near_filters_declined_events():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {"summary": "Standup", "start": {"dateTime": "2026-08-14T16:00:00-07:00"}},
            {
                "summary": "Optional Sync",
                "start": {"dateTime": "2026-08-14T16:05:00-07:00"},
                "attendees": [
                    {"email": "me@example.com", "self": True, "responseStatus": "declined"},
                ],
            },
        ]
    }

    result = find_events_near(calendar_service, "2026-08-14T16:00:00-07:00")

    assert [e["summary"] for e in result] == ["Standup"]


def test_find_events_near_returns_empty_list_when_no_events():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {"items": []}

    result = find_events_near(calendar_service, "2026-08-14T16:00:00-07:00")

    assert result == []


def test_find_events_near_excludes_all_day_event_despite_overlap():
    # Google's timeMin/timeMax match on overlap, so an all-day event spanning
    # the whole day comes back from .list() even though it doesn't "start"
    # anywhere near the target time. find_events_near must filter it out.
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {"summary": "Team Offsite", "start": {"date": "2026-08-14"}},
        ]
    }

    result = find_events_near(calendar_service, "2026-08-14T16:00:00-07:00")

    assert result == []


def test_find_events_near_excludes_timed_event_starting_outside_window():
    # A long-running event that started well before the target and is still
    # in progress overlaps the query window (Google's overlap semantics) but
    # doesn't actually start near the target time, so it should be excluded.
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {"summary": "All-Hands", "start": {"dateTime": "2026-08-14T14:00:00-07:00"}},
        ]
    }

    result = find_events_near(calendar_service, "2026-08-14T16:00:00-07:00")

    assert result == []


def test_format_event_time_returns_time_string_for_timed_event():
    event = {"start": {"dateTime": "2026-08-14T16:00:00-07:00"}}
    assert format_event_time(event) == "4:00 PM"


def test_format_event_time_returns_none_for_all_day_event():
    event = {"start": {"date": "2026-08-14"}}
    assert format_event_time(event) is None


def test_format_candidate_includes_time_for_timed_event():
    event = {"summary": "Standup", "start": {"dateTime": "2026-08-14T16:00:00-07:00"}}
    assert format_candidate(event) == "'Standup' at 4:00 PM"


def test_format_candidate_omits_time_for_all_day_event():
    event = {"summary": "Holiday", "start": {"date": "2026-08-14"}}
    assert format_candidate(event) == "'Holiday'"


def test_format_candidate_uses_untitled_fallback():
    event = {"start": {"dateTime": "2026-08-14T16:00:00-07:00"}}
    assert format_candidate(event) == "'(untitled event)' at 4:00 PM"


def test_format_not_found_includes_target_time():
    assert format_not_found("2026-08-14T16:00:00-07:00") == "I couldn't find anything around 4:00 PM."


def test_format_ambiguous_question_two_candidates():
    events = [
        {"summary": "Standup", "start": {"dateTime": "2026-08-14T15:55:00-07:00"}},
        {"summary": "1:1 with Bob", "start": {"dateTime": "2026-08-14T16:10:00-07:00"}},
    ]
    result = format_ambiguous_question(events, "2026-08-14T16:00:00-07:00")
    assert result == (
        "I found 2 meetings around 4:00 PM: 'Standup' at 3:55 PM and "
        "'1:1 with Bob' at 4:10 PM — which one?"
    )


def test_format_ambiguous_question_three_candidates():
    events = [
        {"summary": "A", "start": {"dateTime": "2026-08-14T15:50:00-07:00"}},
        {"summary": "B", "start": {"dateTime": "2026-08-14T16:00:00-07:00"}},
        {"summary": "C", "start": {"dateTime": "2026-08-14T16:10:00-07:00"}},
    ]
    result = format_ambiguous_question(events, "2026-08-14T16:00:00-07:00")
    assert result == (
        "I found 3 meetings around 4:00 PM: 'A' at 3:50 PM, 'B' at 4:00 PM, "
        "and 'C' at 4:10 PM — which one?"
    )
