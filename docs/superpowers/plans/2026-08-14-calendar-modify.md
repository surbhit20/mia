# Cancel/Modify Calendar Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `cancel_calendar_event` and `update_calendar_event` tools so mia can cancel, move, resize, rename, or re-describe an existing calendar event by voice.

**Architecture:** A new shared lookup module (`calendar_lookup.py`) turns a spoken time into a matched event (with disambiguation when more than one event is nearby), reused by two new tool modules that each perform one mutation (`delete` for cancel, `patch` for update). Both are wired into `main.py` and `demo_standalone.py` the same way the existing three tools are.

**Tech Stack:** Python, Google Calendar API (`googleapiclient`), pytest, `unittest.mock.MagicMock`.

## Global Constraints

- All calendar operations target `calendarId="primary"` only — no other calendars.
- Event lookup uses a fixed ±15 minute window around the stated time (`find_events_near`'s `window_minutes` default of 15).
- Every calendar mutation (`delete`, `patch`) passes `sendUpdates="all"` to notify other attendees, matching Google Calendar's own UI behavior.
- Attendee add/remove is explicitly out of scope — `update_calendar_event` never touches the `attendees` field.
- No new Google OAuth scope is required — the existing `calendar.events` scope already covers delete/patch.
- Disambiguation questions stay wake-word-gated like every other mia response — no new turn-state behavior.
- Spoken times always convert to local time via `.astimezone()` then `strftime("%-I:%M %p")`, matching the existing convention in `calendar_fetch_tool.py`.
- Recurring events are only affected as their single matched instance (`singleEvents=True`), same as `find_calendar_events`.
- All-day events (date-only `start`) can have their title/description changed but not their time/duration.

---

### Task 1: Shared event lookup module

**Files:**
- Create: `src/mia/tools/calendar_lookup.py`
- Modify: `src/mia/tools/calendar_fetch_tool.py:1-2` (add import), `src/mia/tools/calendar_fetch_tool.py:35-39` (remove `_is_declined`, now shared), `src/mia/tools/calendar_fetch_tool.py:77` (use the shared `is_declined`)
- Test: `tests/test_calendar_lookup.py`

**Interfaces:**
- Produces: `is_declined(event: dict) -> bool`, `find_events_near(calendar_service, target_iso: str, window_minutes: int = 15) -> list[dict]`, `format_event_time(event: dict) -> str | None`, `format_candidate(event: dict) -> str`, `format_ambiguous_question(events: list[dict], target_iso: str) -> str`. Tasks 2 and 3 import all five from `mia.tools.calendar_lookup`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_calendar_lookup.py`:

```python
from mia.tools.calendar_lookup import (
    find_events_near,
    format_ambiguous_question,
    format_candidate,
    format_event_time,
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_calendar_lookup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.tools.calendar_lookup'`

- [ ] **Step 3: Create `src/mia/tools/calendar_lookup.py`**

```python
from datetime import datetime, timedelta


def is_declined(event: dict) -> bool:
    for attendee in event.get("attendees", []):
        if attendee.get("self") and attendee.get("responseStatus") == "declined":
            return True
    return False


def find_events_near(calendar_service, target_iso: str, window_minutes: int = 15) -> list[dict]:
    target = datetime.fromisoformat(target_iso)
    window = timedelta(minutes=window_minutes)
    time_min = (target - window).isoformat()
    time_max = (target + window).isoformat()

    response = (
        calendar_service.events()
        .list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return [e for e in response.get("items", []) if not is_declined(e)]


def format_event_time(event: dict) -> str | None:
    start = event.get("start", {})
    if "dateTime" not in start:
        return None
    dt = datetime.fromisoformat(start["dateTime"]).astimezone()
    return dt.strftime("%-I:%M %p")


def format_candidate(event: dict) -> str:
    title = event.get("summary", "(untitled event)")
    time_str = format_event_time(event)
    return f"'{title}' at {time_str}" if time_str else f"'{title}'"


def format_ambiguous_question(events: list[dict], target_iso: str) -> str:
    target_dt = datetime.fromisoformat(target_iso).astimezone()
    target_str = target_dt.strftime("%-I:%M %p")
    listing_parts = [format_candidate(e) for e in events]
    if len(listing_parts) == 2:
        listing = f"{listing_parts[0]} and {listing_parts[1]}"
    else:
        listing = ", ".join(listing_parts[:-1]) + f", and {listing_parts[-1]}"
    return f"I found {len(events)} meetings around {target_str}: {listing} — which one?"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_calendar_lookup.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Switch `calendar_fetch_tool.py` to the shared `is_declined`**

In `src/mia/tools/calendar_fetch_tool.py`, add this import after the existing `from mia.tools.base import Tool` line (line 3):

```python
from mia.tools.calendar_lookup import is_declined
```

Delete the local `_is_declined` function (currently lines 35-39):

```python
def _is_declined(event: dict) -> bool:
    for attendee in event.get("attendees", []):
        if attendee.get("self") and attendee.get("responseStatus") == "declined":
            return True
    return False
```

In the handler (currently line 77), change:

```python
        events = [e for e in response.get("items", []) if not _is_declined(e)]
```

to:

```python
        events = [e for e in response.get("items", []) if not is_declined(e)]
```

- [ ] **Step 6: Run the full existing calendar-fetch test suite to confirm no regression**

Run: `pytest tests/test_tools_calendar_fetch.py -v`
Expected: PASS (all pre-existing tests still pass — they exercise the handler, not the private function directly, so this refactor is behavior-preserving)

- [ ] **Step 7: Commit**

```bash
git add src/mia/tools/calendar_lookup.py src/mia/tools/calendar_fetch_tool.py tests/test_calendar_lookup.py
git commit -m "feat: add shared calendar event lookup module"
```

---

### Task 2: `cancel_calendar_event` tool

**Files:**
- Create: `src/mia/tools/calendar_cancel_tool.py`
- Test: `tests/test_tools_calendar_cancel.py`
- Modify: `src/mia/main.py:40-43` (add import), `src/mia/main.py:394-396` (register tool)
- Modify: `demo_standalone.py:46-48` (add import), `demo_standalone.py:66-68` (register tool)

**Interfaces:**
- Consumes: `find_events_near(calendar_service, target_iso: str, window_minutes: int = 15) -> list[dict]` and `format_ambiguous_question(events: list[dict], target_iso: str) -> str` from `mia.tools.calendar_lookup` (Task 1).
- Produces: `build_cancel_calendar_event_tool(calendar_service) -> Tool`, tool name `"cancel_calendar_event"`. Task 3 does not depend on this tool's internals, only on the same `calendar_lookup` functions.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tools_calendar_cancel.py`:

```python
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


def test_handler_cancels_all_day_event_without_time_in_confirmation():
    calendar_service = MagicMock()
    calendar_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {"id": "evt1", "summary": "Company Holiday", "start": {"date": "2026-08-14"}},
        ]
    }

    tool = build_cancel_calendar_event_tool(calendar_service)
    result = tool.handler({"time_iso": "2026-08-14T16:00:00-07:00"})

    assert result == "Cancelled 'Company Holiday'."


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools_calendar_cancel.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.tools.calendar_cancel_tool'`

- [ ] **Step 3: Create `src/mia/tools/calendar_cancel_tool.py`**

```python
from datetime import datetime

from mia.tools.base import Tool
from mia.tools.calendar_lookup import find_events_near, format_ambiguous_question

_SCHEMA = {
    "type": "object",
    "properties": {
        "time_iso": {
            "type": "string",
            "description": "ISO 8601 datetime of the event to cancel, e.g. 2026-08-14T16:00:00-07:00",
        },
    },
    "required": ["time_iso"],
}


def _format_cancel_confirmation(event: dict) -> str:
    title = event.get("summary", "(untitled event)")
    start = event.get("start", {})
    if "dateTime" in start:
        dt = datetime.fromisoformat(start["dateTime"]).astimezone()
        return f"Cancelled '{title}' at {dt.strftime('%-I:%M %p')}."
    return f"Cancelled '{title}'."


def build_cancel_calendar_event_tool(calendar_service) -> Tool:
    def handler(args: dict) -> str:
        events = find_events_near(calendar_service, args["time_iso"])

        if not events:
            target_dt = datetime.fromisoformat(args["time_iso"]).astimezone()
            return f"I couldn't find anything around {target_dt.strftime('%-I:%M %p')}."

        if len(events) > 1:
            return format_ambiguous_question(events, args["time_iso"])

        event = events[0]
        calendar_service.events().delete(
            calendarId="primary", eventId=event["id"], sendUpdates="all"
        ).execute()
        return _format_cancel_confirmation(event)

    return Tool(
        name="cancel_calendar_event",
        description=(
            "Cancel (delete) an event on the user's primary calendar. The user "
            "refers to the event by its time (e.g. 'cancel my 4pm', 'cancel "
            "standup at 3') -- resolve whatever time they mean into an ISO "
            "8601 datetime for time_iso, same convention as block_calendar_slot's "
            "start_iso. Only use this when the user explicitly asks to cancel or "
            "remove an event; use update_calendar_event to move or change one "
            "instead."
        ),
        input_schema=_SCHEMA,
        handler=handler,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools_calendar_cancel.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Wire the tool into `src/mia/main.py`**

Add this import, in alphabetical order right after `from mia.tools.base import ToolRegistry` (line 40) and before `from mia.tools.calendar_fetch_tool import build_calendar_fetch_tool`:

```python
from mia.tools.calendar_cancel_tool import build_cancel_calendar_event_tool
```

Add this registration line right after `registry.register(build_calendar_fetch_tool(calendar_service))` (currently line 395) and before the `find_gmail_messages` registration:

```python
    registry.register(build_cancel_calendar_event_tool(calendar_service))
```

- [ ] **Step 6: Wire the tool into `demo_standalone.py`**

Add this import, in alphabetical order right after `from mia.tools.base import ToolRegistry` (line 45) and before `from mia.tools.calendar_fetch_tool import build_calendar_fetch_tool` (line 46):

```python
from mia.tools.calendar_cancel_tool import build_cancel_calendar_event_tool
```

Add this registration line right after `registry.register(build_calendar_fetch_tool(calendar_service))` and before the `find_gmail_messages` registration:

```python
    registry.register(build_cancel_calendar_event_tool(calendar_service))
```

- [ ] **Step 7: Run the full test suite to confirm nothing broke**

Run: `pytest -v`
Expected: PASS (all tests, including the new ones)

- [ ] **Step 8: Commit**

```bash
git add src/mia/tools/calendar_cancel_tool.py tests/test_tools_calendar_cancel.py src/mia/main.py demo_standalone.py
git commit -m "feat: add cancel_calendar_event tool"
```

---

### Task 3: `update_calendar_event` tool

**Files:**
- Create: `src/mia/tools/calendar_update_tool.py`
- Test: `tests/test_tools_calendar_update.py`
- Modify: `src/mia/main.py` (add import and registration, next to Task 2's — exact lines depend on Task 2's edits, see anchors below)
- Modify: `demo_standalone.py` (add import and registration, next to Task 2's — exact lines depend on Task 2's edits, see anchors below)

**Interfaces:**
- Consumes: `find_events_near(calendar_service, target_iso: str, window_minutes: int = 15) -> list[dict]` and `format_ambiguous_question(events: list[dict], target_iso: str) -> str` from `mia.tools.calendar_lookup` (Task 1).
- Produces: `build_update_calendar_event_tool(calendar_service) -> Tool`, tool name `"update_calendar_event"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tools_calendar_update.py`:

```python
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


def test_handler_rejects_time_change_on_all_day_event():
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

    assert result == "I can't change the time on an all-day event yet."
    calendar_service.events.return_value.patch.assert_not_called()


def test_handler_allows_title_change_on_all_day_event():
    calendar_service = _service_with_event({
        "id": "evt1", "summary": "Company Holiday",
        "start": {"date": "2026-08-14"},
        "end": {"date": "2026-08-15"},
    })

    tool = build_update_calendar_event_tool(calendar_service)
    result = tool.handler({
        "time_iso": "2026-08-14T16:00:00-07:00",
        "new_title": "Office Closed",
    })

    _, kwargs = calendar_service.events.return_value.patch.call_args
    assert kwargs["body"] == {"summary": "Office Closed"}
    assert result == "Renamed 'Company Holiday' to 'Office Closed'."


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools_calendar_update.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.tools.calendar_update_tool'`

- [ ] **Step 3: Create `src/mia/tools/calendar_update_tool.py`**

```python
from datetime import datetime, timedelta

from mia.tools.base import Tool
from mia.tools.calendar_lookup import find_events_near, format_ambiguous_question

_SCHEMA = {
    "type": "object",
    "properties": {
        "time_iso": {
            "type": "string",
            "description": "ISO 8601 datetime of the event to change, e.g. 2026-08-14T16:00:00-07:00",
        },
        "new_start_iso": {
            "type": "string",
            "description": "New ISO 8601 start datetime, if the user wants to move the event",
        },
        "new_duration_minutes": {
            "type": "integer",
            "description": "New length of the event in minutes, if the user wants to change how long it is",
        },
        "new_title": {
            "type": "string",
            "description": "New title for the event, if the user wants to rename it",
        },
        "new_description": {
            "type": "string",
            "description": "New description for the event, if the user wants to change it",
        },
    },
    "required": ["time_iso"],
}


def build_update_calendar_event_tool(calendar_service) -> Tool:
    def handler(args: dict) -> str:
        events = find_events_near(calendar_service, args["time_iso"])

        if not events:
            target_dt = datetime.fromisoformat(args["time_iso"]).astimezone()
            return f"I couldn't find anything around {target_dt.strftime('%-I:%M %p')}."
        if len(events) > 1:
            return format_ambiguous_question(events, args["time_iso"])

        event = events[0]
        title = event.get("summary", "(untitled event)")
        new_start_iso = args.get("new_start_iso")
        new_duration_minutes = args.get("new_duration_minutes")
        new_title = args.get("new_title")
        new_description = args.get("new_description")

        if not any([new_start_iso, new_duration_minutes, new_title, new_description]):
            return "Nothing to change."

        time_changed = new_start_iso is not None
        duration_changed = new_duration_minutes is not None
        title_changed = new_title is not None
        description_changed = new_description is not None

        original_start = event.get("start", {})
        body: dict = {}
        new_time_str = None

        if time_changed or duration_changed:
            if "dateTime" not in original_start:
                return "I can't change the time on an all-day event yet."

            original_start_dt = datetime.fromisoformat(original_start["dateTime"])
            original_end_dt = datetime.fromisoformat(event["end"]["dateTime"])
            original_duration = original_end_dt - original_start_dt

            new_start_dt = (
                datetime.fromisoformat(new_start_iso) if time_changed else original_start_dt
            )
            duration = (
                timedelta(minutes=new_duration_minutes) if duration_changed else original_duration
            )
            new_end_dt = new_start_dt + duration

            body["start"] = {"dateTime": new_start_dt.isoformat()}
            body["end"] = {"dateTime": new_end_dt.isoformat()}
            new_time_str = new_start_dt.astimezone().strftime("%-I:%M %p")

        if title_changed:
            body["summary"] = new_title
        if description_changed:
            body["description"] = new_description

        calendar_service.events().patch(
            calendarId="primary", eventId=event["id"], body=body, sendUpdates="all"
        ).execute()

        if time_changed and not any([duration_changed, title_changed, description_changed]):
            old_time_str = (
                datetime.fromisoformat(original_start["dateTime"]).astimezone().strftime("%-I:%M %p")
            )
            return f"Moved '{title}' from {old_time_str} to {new_time_str}."

        fields = [
            (
                time_changed,
                f"Moved '{title}' to {new_time_str}" if time_changed else None,
                f"moved it to {new_time_str}" if time_changed else None,
            ),
            (
                duration_changed,
                f"'{title}' is now {new_duration_minutes} minutes" if duration_changed else None,
                f"now {new_duration_minutes} minutes" if duration_changed else None,
            ),
            (
                title_changed,
                f"Renamed '{title}' to '{new_title}'" if title_changed else None,
                f"renamed it to '{new_title}'" if title_changed else None,
            ),
            (
                description_changed,
                f"Updated the description for '{title}'" if description_changed else None,
                "updated the description" if description_changed else None,
            ),
        ]
        active = [(opener, cont) for changed, opener, cont in fields if changed]

        if len(active) == 1:
            return f"{active[0][0]}."

        opener_text = active[0][0]
        continuations = [cont for _, cont in active[1:]]
        if len(continuations) == 1:
            return f"{opener_text} and {continuations[0]}."
        return f"{opener_text}, " + ", ".join(continuations[:-1]) + f", and {continuations[-1]}."

    return Tool(
        name="update_calendar_event",
        description=(
            "Change an existing event on the user's primary calendar -- move it "
            "to a new time, change its duration, rename it, or update its "
            "description. The user refers to the event by its current time (e.g. "
            "'move my 4pm to 3pm', 'make my 4pm an hour', 'rename my 4pm to "
            "Budget review') -- resolve that into time_iso, same convention as "
            "block_calendar_slot's start_iso. Only include the fields the user "
            "actually asked to change; leave the rest out. Adding or removing "
            "attendees is not supported. Use cancel_calendar_event to remove an "
            "event entirely instead of updating it."
        ),
        input_schema=_SCHEMA,
        handler=handler,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools_calendar_update.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Wire the tool into `src/mia/main.py`**

Add this import, in alphabetical order right after `from mia.tools.calendar_tool import build_calendar_tool` and before `from mia.tools.gmail_tool import build_gmail_search_tool` (`calendar_update_tool` sorts after `calendar_tool` and before `gmail_tool`):

```python
from mia.tools.calendar_update_tool import build_update_calendar_event_tool
```

Add this registration line right after the `build_cancel_calendar_event_tool` registration Task 2 added, and before the `find_gmail_messages` registration:

```python
    registry.register(build_update_calendar_event_tool(calendar_service))
```

- [ ] **Step 6: Wire the tool into `demo_standalone.py`**

Same two additions as Step 5, in the same relative positions in `demo_standalone.py`:

```python
from mia.tools.calendar_update_tool import build_update_calendar_event_tool
```

```python
    registry.register(build_update_calendar_event_tool(calendar_service))
```

- [ ] **Step 7: Run the full test suite to confirm nothing broke**

Run: `pytest -v`
Expected: PASS (all tests, including every test added in Tasks 1-3)

- [ ] **Step 8: Commit**

```bash
git add src/mia/tools/calendar_update_tool.py tests/test_tools_calendar_update.py src/mia/main.py demo_standalone.py
git commit -m "feat: add update_calendar_event tool"
```
