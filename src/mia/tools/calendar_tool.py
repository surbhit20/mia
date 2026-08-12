from datetime import datetime, timedelta

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
        end = start + timedelta(minutes=args["duration_minutes"])
        body = {
            "summary": args["title"],
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        }
        calendar_service.events().insert(calendarId="primary", body=body).execute()
        time_str = start.strftime("%-I:%M %p")
        return f"Blocked {args['duration_minutes']} minutes starting {time_str} for '{args['title']}'."

    return Tool(
        name="block_calendar_slot",
        description="Create a calendar event to block time on the user's primary calendar.",
        input_schema=_SCHEMA,
        handler=handler,
    )
