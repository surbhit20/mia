# Cancel/Modify Calendar Events — Design

Date: 2026-08-14
Status: Approved, not yet implemented

## Context

mia can currently create events (`block_calendar_slot`) and read them
(`find_calendar_events`), but has no way to change or remove something
already on the calendar. This adds two mutation tools so she can handle
"cancel my 3pm," "move standup to 4pm," "make my 4pm an hour instead of
30 minutes," and "rename my 4pm to 'Budget review.'"

Attendee add/remove (e.g. "add Bob to my 4pm") is explicitly out of scope:
Google Calendar needs an email address, not a first name, and mia has no
Contacts integration or address book to resolve one. That's a separate
sub-project with its own design (name-to-email resolution) once it's
prioritized.

## Design

### Shared lookup: `src/mia/tools/calendar_lookup.py` (new)

Both mutation tools need to turn a spoken time ("my 4pm") into a specific
calendar event, since there's no event ID to say out loud. This module
holds that shared logic so it's written and tested once:

- `find_events_near(calendar_service, target_iso, window_minutes=15) ->
  list[dict]`: queries `calendar_service.events().list(calendarId="primary",
  timeMin=target-window, timeMax=target+window, singleEvents=True,
  orderBy="startTime")`, filters out the user's own declined events, and
  returns whatever's left, in chronological order.
- `is_declined(event: dict) -> bool`: moved here from
  `calendar_fetch_tool.py` (currently a private `_is_declined`), which
  switches to importing it from here instead of keeping its own copy.

**Match handling**, done identically in both tools' handlers:
- **Zero matches** → `"I couldn't find anything around {time}."` No
  further action.
- **Two or more matches** → a spoken clarifying question naming each
  candidate: `"I found 2 meetings around 4:00 PM: 'Standup' at 3:55 PM
  and '1:1 with Bob' at 4:10 PM — which one?"` No action taken. This is
  just mia's normal spoken confirmation for the turn — it's recorded in
  `ConversationHistory` like any other exchange, so the user's next
  wake-worded command ("Hey Mia, the 1:1 with Bob") lets Claude resolve
  the reference using the prior tool result already in conversational
  memory, re-issuing a `time_iso` precise enough to match exactly one
  event. No new turn-state behavior is needed — every command, including
  this follow-up, still requires the wake word.
- **Exactly one match** → act on it.

The 15-minute window is fixed, not user-configurable or exposed as a tool
parameter — Claude always passes the time the user stated, unmodified.

### `cancel_calendar_event`

**Input schema**: `{time_iso: string}`, required. Claude resolves
whatever time phrase the user said ("my 4pm," "the 3 o'clock") into an
ISO 8601 datetime, same convention `block_calendar_slot` already uses for
`start_iso`.

**Handler**:
1. `find_events_near(calendar_service, args["time_iso"])`.
2. Zero/multiple matches → shared handling above.
3. One match → `calendar_service.events().delete(calendarId="primary",
   eventId=event["id"], sendUpdates="all").execute()`. Returns
   `"Cancelled '{title}' at {time}."`

`sendUpdates="all"` notifies any other attendees, matching what happens
when cancelling from Google Calendar's own UI — a PA cancelling a meeting
on the user's behalf would obviously tell the other attendees.

### `update_calendar_event`

**Input schema**: `{time_iso: string, new_start_iso?: string,
new_duration_minutes?: integer, new_title?: string, new_description?:
string}`. Only `time_iso` is required; the tool description tells Claude
to include whichever of the other four fields the user actually asked to
change, and omit the rest.

**Handler**:
1. `find_events_near(calendar_service, args["time_iso"])`.
2. Zero/multiple matches → shared handling above.
3. One match → build a PATCH body from whichever optional fields are
   present:
   - `new_start_iso` given → new `start`. New `end` is `new_start_iso +
     duration`, where `duration` is `new_duration_minutes` if also given,
     else the original event's own duration (`end - start` on the
     matched event).
   - `new_duration_minutes` given without `new_start_iso` → `start`
     stays the matched event's original start; only `end` changes, to
     `start + new_duration_minutes`.
   - `new_title` given → `body["summary"] = new_title`.
   - `new_description` given → `body["description"] = new_description`.
   - **None of the four given** → return `"Nothing to change."`, no API
     call.
4. `calendar_service.events().patch(calendarId="primary",
   eventId=event["id"], body=body, sendUpdates="all").execute()`.
5. Confirmation names whichever fields actually changed, e.g. `"Moved
   'Standup' to 9:30 AM and renamed it to 'Budget review'."` A
   time-only change says `"Moved '{title}' from {old_time} to
   {new_time}."`; a duration-only change says `"'{title}' is now
   {new_duration} minutes."`; title/description-only changes are named
   directly without a time clause.

**All-day events**: the matched event's `start` field carries `date`
instead of `dateTime` for all-day events, so there's no `dateTime` to
compute a duration from. If `new_start_iso` or `new_duration_minutes` is
given and the matched event is all-day, return `"I can't change the time
on an all-day event yet."` without patching. `new_title` /
`new_description` changes still work normally on an all-day event, since
neither needs duration math.

**Auth**: no new OAuth scope. `calendar.events` already grants full
read/write access to events (not read-only), the same reasoning already
established for `find_calendar_events` needing no new scope beyond what
`block_calendar_slot` already required.

## Error handling

Calendar API errors (auth, rate limit, network, deleting/patching an
event that no longer exists) propagate as an exception, caught by
`dispatch_command`'s existing bare try/except around `tool.handler()`,
falling back to the generic "Sorry, that didn't work" response — same
pattern every existing tool already relies on. No new handling needed.

## Testing

Same TDD pattern as the existing calendar tools: mock `calendar_service`.

For `calendar_lookup.py`:
- `find_events_near` queries with the correct widened `timeMin`/`timeMax`
  window around the target time.
- Declined events are filtered out of the result.

For `cancel_calendar_event`:
- Zero matches → correct message, `delete()` not called.
- Multiple matches → correct clarifying-question format naming each
  candidate, `delete()` not called.
- One match → `delete()` called with `sendUpdates="all"` and the matched
  `eventId`, correct confirmation message.

For `update_calendar_event`:
- Zero/multiple matches → same shared behavior as above, `patch()` not
  called.
- One match, `new_start_iso` only → `patch()` body has the new start and
  an end preserving the original duration.
- One match, `new_start_iso` + `new_duration_minutes` → end reflects the
  new duration, not the original.
- One match, `new_duration_minutes` only → start unchanged, end reflects
  new duration.
- One match, `new_title` / `new_description` only → body carries just
  that field, no start/end.
- One match, no optional fields given → `"Nothing to change."`, `patch()`
  not called.
- One match, all-day event, time/duration field given → the all-day
  error message, `patch()` not called.
- One match, all-day event, only `new_title`/`new_description` given →
  patches normally.
- Confirmation message wording for each of the above combinations.

## Explicit scope

**In**: `cancel_calendar_event` and `update_calendar_event` (time,
duration, title, description) as described above, shared time-window
lookup with disambiguation, attendee notifications on for both.

**Out**: attendee add/remove (future sub-project — needs its own
name-to-email resolution design), title-based (rather than time-based)
event lookup, recurring-event series-level edits (only the matched
single instance is affected, consistent with `find_calendar_events`'s
`singleEvents=True` treatment), all-day event time/duration changes
(title/description changes on all-day events are in scope), a
no-wake-word clarification reply flow (every command, including
disambiguation follow-ups, stays wake-word-gated).
