from datetime import datetime, timedelta

from mia.timeutil import local_iana_timezone
from mia.tools.base import Tool

_SCHEMA = {
    "type": "object",
    "properties": {
        "start_iso": {"type": "string", "description": "ISO 8601 start datetime, e.g. 2026-08-12T15:00:00-07:00"},
        "duration_minutes": {"type": "integer", "description": "Length of the block in minutes"},
        "title": {"type": "string", "description": "What to call the calendar event"},
    },
    "required": ["start_iso", "duration_minutes", "title"],
}

def build_calendar_tool(calendar_service) -> Tool:
    def handler(args: dict) -> str:
        start = datetime.fromisoformat(args["start_iso"])
        # Google Calendar rejects a timezone-naive `dateTime`. The system
        # prompt tells Claude to include an offset, but a naive value must
        # never reach the API even if it doesn't -- so anchor it to this
        # machine's local zone, which is what the user meant anyway.
        if start.tzinfo is None:
            start = start.astimezone()
        end = start + timedelta(minutes=args["duration_minutes"])
        start_field = {"dateTime": start.isoformat()}
        end_field = {"dateTime": end.isoformat()}
        # `timeZone` must be an IANA name; when it can't be resolved, the RFC
        # 3339 offset carried by `dateTime` is on its own enough for the API.
        timezone_name = local_iana_timezone()
        if timezone_name is not None:
            start_field["timeZone"] = timezone_name
            end_field["timeZone"] = timezone_name
        body = {
            "summary": args["title"],
            "start": start_field,
            "end": end_field,
        }
        calendar_service.events().insert(calendarId="primary", body=body).execute()
        time_str = start.strftime("%-I:%M %p")
        return f"Blocked {args['duration_minutes']} minutes starting {time_str} for '{args['title']}'."

    return Tool(
        name="block_calendar_slot",
        description=(
            "Create a calendar event to block time on the user's primary "
            "calendar. Only use this when the user is asking to schedule or "
            "block time -- not when they're asking what's already on their "
            "calendar or whether a time is free; use find_calendar_events for "
            "those. And not when they want to change an event that already "
            "exists -- use update_calendar_event for that."
        ),
        input_schema=_SCHEMA,
        handler=handler,
    )
