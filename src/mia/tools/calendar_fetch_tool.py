from datetime import datetime

from mia.tools.base import Tool
from mia.tools.calendar_lookup import is_declined

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
        # The API's returned `dateTime` can carry any offset, including UTC
        # `Z` for some imported/cross-platform events -- convert to the
        # system's local timezone before formatting so the spoken time is
        # what the user actually experiences, not the raw source offset.
        dt = datetime.fromisoformat(start["dateTime"]).astimezone()
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
        # Filter out events the user has personally declined before anything
        # downstream (truncation note, empty-result check) sees the list, so
        # a range that's all-declined correctly reads as empty.
        events = [e for e in response.get("items", []) if not is_declined(e)]
        if not events:
            return "Nothing scheduled in that time."

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
            "they're free or busy at a specific time. For an open-ended question "
            "like 'when's my next meeting', use a forward-looking range starting "
            "from the current moment (e.g. the next 7 days) rather than just "
            "today -- results are returned in chronological order, so the first "
            "event listed is the next one."
        ),
        input_schema=_SCHEMA,
        handler=handler,
        mutates=False,
    )
