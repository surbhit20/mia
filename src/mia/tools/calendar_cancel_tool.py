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
