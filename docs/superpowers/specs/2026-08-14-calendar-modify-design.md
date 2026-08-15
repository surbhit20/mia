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
  orderBy="startTime")`, filters out the user's own declined events, **and
  further filters to events whose own `start.dateTime` actually falls within
  `[target-window, target+window]`** -- not just whatever Google's
  overlap-based `timeMin`/`timeMax` query happens to return -- then returns
  at most 5 filtered results in chronological order. The cap is applied to
  the final filtered list (events the user actually meant), not the raw API
  response (which may contain all-day events, declined meetings, and old
  overlaps the user didn't ask for). See "All-day events" below for why the
  start-time filter exists.
- `is_declined(event: dict) -> bool`: moved here from
  `calendar_fetch_tool.py` (currently a private `_is_declined`), which
  switches to importing it from here instead of keeping its own copy.
- `format_not_found(target_iso: str) -> str`: shared `"I couldn't find
  anything around {time}."` message, used by both mutation tools instead
  of each duplicating the formatting inline.

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

**All-day events**: out of scope entirely, for both `cancel_calendar_event`
and `update_calendar_event`. All-day events cannot be found via `time_iso`
lookup at all: Google's `timeMin`/`timeMax` on `events().list()` match on
overlap (`timeMin` bounds an event's end, `timeMax` bounds its start), not
on whether the event's own start falls in that range, so an all-day event
spanning the whole day can come back as a "match" for a spoken time
nowhere near anything the user meant. That overlap-based query can't
distinguish "an all-day event overlaps this time" from "a timed event
starts at this time," and matching on overlap risks silently acting on
the wrong event -- for `cancel_calendar_event` in particular, silently
deleting one. `find_events_near` therefore filters to events with a
`start.dateTime` inside the window, unconditionally excluding anything
with only a `start.date` (all-day events). The original design allowed
`new_title` / `new_description` changes on an all-day event; that
capability was removed as the cost of closing this bug, since there's no
way to keep it without reintroducing overlap-based matching.

This was discovered during a final whole-branch review after initial
implementation -- each task's own tests only ever exercised small,
pre-curated mock lists, never a realistic mixed calendar where a
long-running or all-day event could overlap a query window without
actually starting in it -- not caught at design time.

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
  window around the target time, without a server-side result cap.
- Declined events are filtered out of the result.
- An all-day event returned by the query (via Google's overlap semantics)
  is excluded from the result.
- A timed event whose `start.dateTime` is outside the `±window` (e.g. a
  long event still running from well before the target) is excluded from
  the result, even though the query itself would return it.
- The final filtered result is capped at 5 items (applied after all
  filtering, not on the raw response), ensuring real matches aren't lost
  when the raw response contains many all-day/declined/overlapping events.
- `format_not_found` produces the correct message for a given target
  time.

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
- Confirmation message wording for each of the above combinations.

Since `find_events_near` never returns an all-day event (see "All-day
events" above), neither tool has an all-day-specific code path to test
anymore; the "single match" case for both tools is always a timed event.

## Explicit scope

**In**: `cancel_calendar_event` and `update_calendar_event` (time,
duration, title, description) as described above, shared time-window
lookup with disambiguation, attendee notifications on for both.

**Out**: attendee add/remove (future sub-project — needs its own
name-to-email resolution design), title-based (rather than time-based)
event lookup, recurring-event series-level edits (only the matched
single instance is affected, consistent with `find_calendar_events`'s
`singleEvents=True` treatment), all-day events entirely for both tools
(see "All-day events" above — discovered post-implementation that
time-window matching can't safely reach them at all, not just their
time/duration), a no-wake-word clarification reply flow (every command,
including disambiguation follow-ups, stays wake-word-gated).
