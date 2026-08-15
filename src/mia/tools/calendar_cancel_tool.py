from mia.tools.base import Tool
from mia.tools.calendar_lookup import (
    find_events_near,
    format_ambiguous_question,
    format_candidate,
    format_not_found,
)

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


def build_cancel_calendar_event_tool(calendar_service) -> Tool:
    def handler(args: dict) -> str:
        events = find_events_near(calendar_service, args["time_iso"])

        if not events:
            return format_not_found(args["time_iso"])

        if len(events) > 1:
            return format_ambiguous_question(events, args["time_iso"])

        event = events[0]
        calendar_service.events().delete(
            calendarId="primary", eventId=event["id"], sendUpdates="all"
        ).execute()
        return f"Cancelled {format_candidate(event)}."

    return Tool(
        name="cancel_calendar_event",
        description=(
            "Cancel (delete) an event on the user's primary calendar. The user "
            "refers to the event by its time (e.g. 'cancel my 4pm', 'cancel "
            "standup at 3') -- resolve whatever time they mean into an ISO "
            "8601 datetime for time_iso, same convention as block_calendar_slot's "
            "start_iso. Only use this when the user explicitly asks to cancel or "
            "remove an event; use update_calendar_event to move or change one "
            "instead. For a recurring event, this only affects the single "
            "matched occurrence, not the whole series."
        ),
        input_schema=_SCHEMA,
        handler=handler,
    )
