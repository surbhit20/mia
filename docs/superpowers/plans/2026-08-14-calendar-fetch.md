# Fetch Calendar Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `find_calendar_events` tool so mia can answer "what's on my calendar," "when's my next meeting," and "am I free at 3pm" (a conflict check is just an empty-vs-non-empty result for a time range).

**Architecture:** A new `Tool` following the same factory-function pattern as `block_calendar_slot`, registered alongside the existing two tools. Unlike the Gmail search tool, calendar events are already clean structured data, so results are formatted into a spoken sentence with a plain deterministic function — no second internal Claude call needed.

**Tech Stack:** Python, `google-api-python-client` (Calendar API v3, already a project dependency and already authorized — no new OAuth scope needed).

## Global Constraints

- Capped at 10 events per query (`maxResults=10`), with a spoken "...and there are more beyond that" note appended when the API's response includes a `nextPageToken`, rather than silently truncating.
- No new OAuth scope: the existing `calendar.events` scope already covers reading events, not just writing them.
- `singleEvents=True` is required both to expand recurring events into real occurrences and because `orderBy="startTime"` is only valid when set.
- Calendar API errors propagate as exceptions, caught by `dispatch_command`'s existing handler try/except — no new error handling in the tool itself.

---

### Task 1: `find_calendar_events` tool, wired into both entrypoints

**Files:**
- Create: `src/mia/tools/calendar_fetch_tool.py`
- Test: `tests/test_tools_calendar_fetch.py`
- Modify: `src/mia/main.py:42` (import), `src/mia/main.py:394` (registration)
- Modify: `demo_standalone.py:47` (import), `demo_standalone.py:67` (registration)

**Interfaces:**
- Consumes: `Tool` dataclass from `mia.tools.base` (unchanged); the existing `calendar_service` client already built and passed to `build_calendar_tool` in both `main.py` and `demo_standalone.py`.
- Produces: `build_calendar_fetch_tool(calendar_service) -> Tool`, with `tool.name == "find_calendar_events"`. Nothing else depends on this — it's the final integration point for this plan.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tools_calendar_fetch.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools_calendar_fetch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.tools.calendar_fetch_tool'`

- [ ] **Step 3: Write the implementation**

Create `src/mia/tools/calendar_fetch_tool.py`:

```python
from datetime import datetime

from mia.tools.base import Tool

_SCHEMA = {
    "type": "object",
    "properties": {
        "start_iso": {
            "type": "string",
            "description": "ISO 8601 start of the time range to check, e.g. 2026-08-14T00:00:00-07:00",
        },
        "end_iso": {
            "type": "string",
            "description": "ISO 8601 end of the time range to check, e.g. 2026-08-14T23:59:59-07:00",
        },
    },
    "required": ["start_iso", "end_iso"],
}


def _format_event_time(event: dict) -> str:
    start = event.get("start", {})
    if "date" in start:
        return "(all day)"
    if "dateTime" in start:
        dt = datetime.fromisoformat(start["dateTime"])
        return f"at {dt.strftime('%-I:%M %p')}"
    return ""


def _format_events(events: list[dict]) -> str:
    parts = []
    for event in events:
        title = event.get("summary", "(untitled event)")
        time_str = _format_event_time(event)
        parts.append(f"'{title}' {time_str}".strip())

    if len(parts) == 1:
        listing = parts[0]
    elif len(parts) == 2:
        listing = f"{parts[0]} and {parts[1]}"
    else:
        listing = ", ".join(parts[:-1]) + f", and {parts[-1]}"

    count_word = "event" if len(parts) == 1 else "events"
    return f"You have {len(parts)} {count_word}: {listing}."


def build_calendar_fetch_tool(calendar_service) -> Tool:
    def handler(args: dict) -> str:
        response = (
            calendar_service.events()
            .list(
                calendarId="primary",
                timeMin=args["start_iso"],
                timeMax=args["end_iso"],
                singleEvents=True,
                orderBy="startTime",
                maxResults=10,
            )
            .execute()
        )
        events = response.get("items", [])
        if not events:
            return "You're free then — nothing scheduled."

        message = _format_events(events)
        if response.get("nextPageToken"):
            message += " ...and there are more beyond that — want me to narrow the time range?"
        return message

    return Tool(
        name="find_calendar_events",
        description=(
            "Look up what's on the user's Google Calendar in a given time range, "
            "or check whether they're free at a given time. Use this when the user "
            "asks what's on their schedule, when their next meeting is, or whether "
            "they're free or busy at a specific time."
        ),
        input_schema=_SCHEMA,
        handler=handler,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools_calendar_fetch.py -v`
Expected: PASS, 10/10

- [ ] **Step 5: Wire the tool into `main.py`**

Change the import (line 42, right after the existing `build_gmail_search_tool` import):

```python
from mia.tools.gmail_tool import build_gmail_search_tool
```

to:

```python
from mia.tools.calendar_fetch_tool import build_calendar_fetch_tool
from mia.tools.gmail_tool import build_gmail_search_tool
```

Change the registration block (originally lines 393-394):

```python
    registry.register(build_calendar_tool(calendar_service))
    registry.register(build_gmail_search_tool(gmail_service, anthropic_client))
```

to:

```python
    registry.register(build_calendar_tool(calendar_service))
    registry.register(build_calendar_fetch_tool(calendar_service))
    registry.register(build_gmail_search_tool(gmail_service, anthropic_client))
```

- [ ] **Step 6: Wire the tool into `demo_standalone.py`**

Apply the identical two changes to `demo_standalone.py` (import at line 47, registration at line 67):

```python
from mia.tools.gmail_tool import build_gmail_search_tool
```

to:

```python
from mia.tools.calendar_fetch_tool import build_calendar_fetch_tool
from mia.tools.gmail_tool import build_gmail_search_tool
```

```python
    registry.register(build_calendar_tool(calendar_service))
    registry.register(build_gmail_search_tool(gmail_service, anthropic_client))
```

to:

```python
    registry.register(build_calendar_tool(calendar_service))
    registry.register(build_calendar_fetch_tool(calendar_service))
    registry.register(build_gmail_search_tool(gmail_service, anthropic_client))
```

- [ ] **Step 7: Verify both files still import/compile cleanly**

Run: `python3 -m py_compile src/mia/main.py demo_standalone.py`
Expected: no output (success)

Run: `python3 -c "import mia.main"`
Expected: no output (success)

- [ ] **Step 8: Run the full existing test suite to confirm nothing else broke**

Run: `pytest -q`
Expected: all tests pass (the two audio-fixture tests still skip, as before — unrelated to this change)

- [ ] **Step 9: Commit**

```bash
git add src/mia/tools/calendar_fetch_tool.py tests/test_tools_calendar_fetch.py src/mia/main.py demo_standalone.py
git commit -m "feat: add find_calendar_events tool"
```

- [ ] **Step 10: Manual live verification**

Run `python3 demo_standalone.py` and try:
1. **"Hey Mia, what's on my calendar today?"** — confirm a coherent spoken listing, or "You're free then" if nothing's scheduled.
2. **"Hey Mia, am I free at [some time you know is booked]?"** — confirm she names the conflicting event.
3. **"Hey Mia, am I free at [some clearly open time]?"** — confirm "You're free then — nothing scheduled."

This step has no automated pass/fail — record what actually happened, consistent with this project's pattern for live-only verification steps.

---

## Self-Review

**Spec coverage:**
- `find_calendar_events` tool, capped at 10 with truncation note, deterministic (non-LLM) formatting → Step 3. ✅
- No new OAuth scope (reuses existing `calendar_service`) → Steps 5-6 pass the already-built `calendar_service` through, no auth changes anywhere in this plan. ✅
- `singleEvents=True`/`orderBy="startTime"` → Step 3's exact `events().list()` call, verified by Step 1's `test_handler_calls_events_list_with_correct_params`. ✅
- All-day event handling → Step 1's `test_handler_formats_all_day_event`. ✅
- Wired into both `main.py` and `demo_standalone.py` → Steps 5-6. ✅

**Placeholder scan:** No TBD/TODO; every step has literal, complete code or an exact runnable command.

**Type consistency:** `build_calendar_fetch_tool(calendar_service) -> Tool` matches the existing `build_calendar_tool`/`build_gmail_search_tool` factory-function shape exactly; `Tool` fields (`name`, `description`, `input_schema`, `handler`) match `mia.tools.base`'s existing definition, unchanged by this plan.
