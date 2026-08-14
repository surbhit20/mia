from datetime import datetime, timedelta

from mia.tools.base import Tool
from mia.tools.calendar_lookup import find_events_near, format_ambiguous_question

_SCHEMA = {
    "type": "object",
    "properties": {
        "time_iso": {
            "type": "string",
            "description": "ISO 8601 datetime of the event to change, e.g. 2026-08-14T16:00:00-07:00",
        },
        "new_start_iso": {
            "type": "string",
            "description": "New ISO 8601 start datetime, if the user wants to move the event",
        },
        "new_duration_minutes": {
            "type": "integer",
            "description": "New length of the event in minutes, if the user wants to change how long it is",
        },
        "new_title": {
            "type": "string",
            "description": "New title for the event, if the user wants to rename it",
        },
        "new_description": {
            "type": "string",
            "description": "New description for the event, if the user wants to change it",
        },
    },
    "required": ["time_iso"],
}


def build_update_calendar_event_tool(calendar_service) -> Tool:
    def handler(args: dict) -> str:
        events = find_events_near(calendar_service, args["time_iso"])

        if not events:
            target_dt = datetime.fromisoformat(args["time_iso"]).astimezone()
            return f"I couldn't find anything around {target_dt.strftime('%-I:%M %p')}."
        if len(events) > 1:
            return format_ambiguous_question(events, args["time_iso"])

        event = events[0]
        title = event.get("summary", "(untitled event)")
        new_start_iso = args.get("new_start_iso")
        new_duration_minutes = args.get("new_duration_minutes")
        new_title = args.get("new_title")
        new_description = args.get("new_description")

        if not any([new_start_iso, new_duration_minutes, new_title, new_description]):
            return "Nothing to change."

        time_changed = new_start_iso is not None
        duration_changed = new_duration_minutes is not None
        title_changed = new_title is not None
        description_changed = new_description is not None

        original_start = event.get("start", {})
        body: dict = {}
        new_time_str = None

        if time_changed or duration_changed:
            if "dateTime" not in original_start:
                return "I can't change the time on an all-day event yet."

            original_start_dt = datetime.fromisoformat(original_start["dateTime"])
            original_end_dt = datetime.fromisoformat(event["end"]["dateTime"])
            original_duration = original_end_dt - original_start_dt

            new_start_dt = (
                datetime.fromisoformat(new_start_iso) if time_changed else original_start_dt
            )
            duration = (
                timedelta(minutes=new_duration_minutes) if duration_changed else original_duration
            )
            new_end_dt = new_start_dt + duration

            body["start"] = {"dateTime": new_start_dt.isoformat()}
            body["end"] = {"dateTime": new_end_dt.isoformat()}
            new_time_str = new_start_dt.astimezone().strftime("%-I:%M %p")

        if title_changed:
            body["summary"] = new_title
        if description_changed:
            body["description"] = new_description

        calendar_service.events().patch(
            calendarId="primary", eventId=event["id"], body=body, sendUpdates="all"
        ).execute()

        if time_changed and not any([duration_changed, title_changed, description_changed]):
            old_time_str = (
                datetime.fromisoformat(original_start["dateTime"]).astimezone().strftime("%-I:%M %p")
            )
            return f"Moved '{title}' from {old_time_str} to {new_time_str}."

        fields = [
            (
                time_changed,
                f"Moved '{title}' to {new_time_str}" if time_changed else None,
                f"moved it to {new_time_str}" if time_changed else None,
            ),
            (
                duration_changed,
                f"'{title}' is now {new_duration_minutes} minutes" if duration_changed else None,
                f"now {new_duration_minutes} minutes" if duration_changed else None,
            ),
            (
                title_changed,
                f"Renamed '{title}' to '{new_title}'" if title_changed else None,
                f"renamed it to '{new_title}'" if title_changed else None,
            ),
            (
                description_changed,
                f"Updated the description for '{title}'" if description_changed else None,
                "updated the description" if description_changed else None,
            ),
        ]
        active = [(opener, cont) for changed, opener, cont in fields if changed]

        if len(active) == 1:
            return f"{active[0][0]}."

        opener_text = active[0][0]
        continuations = [cont for _, cont in active[1:]]
        if len(continuations) == 1:
            return f"{opener_text} and {continuations[0]}."
        return f"{opener_text}, " + ", ".join(continuations[:-1]) + f", and {continuations[-1]}."

    return Tool(
        name="update_calendar_event",
        description=(
            "Change an existing event on the user's primary calendar -- move it "
            "to a new time, change its duration, rename it, or update its "
            "description. The user refers to the event by its current time (e.g. "
            "'move my 4pm to 3pm', 'make my 4pm an hour', 'rename my 4pm to "
            "Budget review') -- resolve that into time_iso, same convention as "
            "block_calendar_slot's start_iso. Only include the fields the user "
            "actually asked to change; leave the rest out. Adding or removing "
            "attendees is not supported. Use cancel_calendar_event to remove an "
            "event entirely instead of updating it."
        ),
        input_schema=_SCHEMA,
        handler=handler,
    )
