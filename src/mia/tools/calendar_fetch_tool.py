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
