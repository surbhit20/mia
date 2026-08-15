# Fetch Calendar Events — Design

Date: 2026-08-14
Status: Approved, not yet implemented

## Context

mia can currently only create calendar events (`block_calendar_slot`). This
adds a read tool so she can answer "what's on my calendar today," "when's
my next meeting," and "am I free at 3pm" — the last one being a conflict
check, not a distinct capability: an empty result for a time range means
free, a non-empty result means busy. One tool covers both phrasings.

## Design

**New tool**: `src/mia/tools/calendar_fetch_tool.py`,
`build_calendar_fetch_tool(calendar_service) -> Tool`, following the same
factory-function shape as the existing `build_calendar_tool`.

**Input schema**: `{start_iso: string, end_iso: string}`, both required.
Claude translates natural phrasing ("this afternoon," "tomorrow," "at 3pm")
into a start/end range — the same time-handling responsibility Claude
already has for `block_calendar_slot`'s `start_iso`.

**Handler**:
1. `calendar_service.events().list(calendarId="primary", timeMin=start_iso,
   timeMax=end_iso, singleEvents=True, orderBy="startTime", maxResults=10)`.
   `singleEvents=True` is required both to expand recurring events into
   their actual occurrences (otherwise the API returns the recurring
   series' master event, not real instances in the range) and because
   `orderBy="startTime"` is only valid when it's set.
2. **Zero events** → return `"You're free then — nothing scheduled."`
   directly, no further processing.
3. **Events found** → format directly into a spoken sentence with a plain
   Python function — no second Claude call. Unlike Gmail search's noisy
   snippet/subject/sender data, calendar events are already clean
   structured fields (title, start, end), so deterministic formatting
   produces a natural result without needing an LLM pass. Each event
   contributes `"'{title}' at {time}"`, joined into one sentence
   (`"You have 3 things: 'Standup' at 9:00 AM, '1:1 with Bob' at 2:00 PM,
   and 'Focus time' at 3:00 PM."`).
4. **All-day events**: Calendar API returns `{"date": ...}` instead of
   `{"dateTime": ...}` for these — format as `"'{title}' (all day)"`
   instead of a garbled time parse.
5. **Truncation**: if the API response includes a `nextPageToken`, there
   are more events beyond the 10 fetched — append `"...and there are more
   beyond that — want me to narrow the time range?"` to the spoken
   response, mirroring the Gmail tool's pattern of inviting a follow-up
   when results are incomplete rather than silently truncating.

**Auth**: none needed. The existing `calendar.events` OAuth scope already
covers reading events (it grants full read/write access to events, not
write-only), so this needs no new scope and no re-consent — the calendar
service client mia already builds in `main.py`/`demo_standalone.py` is
reused as-is.

## Error handling

Calendar API errors (auth, rate limit, network) propagate as an exception,
caught by `dispatch_command`'s existing bare try/except around
`tool.handler()`, falling back to the generic "Sorry, that didn't work"
response — same pattern `block_calendar_slot` and `find_gmail_messages`
already rely on. No new handling needed.

## Testing

Same TDD pattern as `calendar_tool.py`/`gmail_tool.py`: mock
`calendar_service`, assert the handler calls `events().list()` with the
right params (`singleEvents=True`, `orderBy="startTime"`, `maxResults=10`,
the given time range), and assert the formatting logic for: zero events,
one event, multiple events, an all-day event, and the truncation message
when `nextPageToken` is present.

## Explicit scope

**In**: `find_calendar_events` tool as described above.

**Out**: fetching from calendars other than `"primary"`, editing/deleting
existing events, recurring-event-specific phrasing (a recurring event's
individual occurrence is treated the same as any other event, per
`singleEvents=True`'s expansion) — these are candidates for future
sub-projects, not part of this one.
