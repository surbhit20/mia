from datetime import datetime, timedelta


def is_declined(event: dict) -> bool:
    for attendee in event.get("attendees", []):
        if attendee.get("self") and attendee.get("responseStatus") == "declined":
            return True
    return False


def find_events_near(calendar_service, target_iso: str, window_minutes: int = 15) -> list[dict]:
    target = datetime.fromisoformat(target_iso)
    window = timedelta(minutes=window_minutes)
    time_min = (target - window).isoformat()
    time_max = (target + window).isoformat()

    response = (
        calendar_service.events()
        .list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return [e for e in response.get("items", []) if not is_declined(e)]


def format_event_time(event: dict) -> str | None:
    start = event.get("start", {})
    if "dateTime" not in start:
        return None
    dt = datetime.fromisoformat(start["dateTime"]).astimezone()
    return dt.strftime("%-I:%M %p")


def format_candidate(event: dict) -> str:
    title = event.get("summary", "(untitled event)")
    time_str = format_event_time(event)
    return f"'{title}' at {time_str}" if time_str else f"'{title}'"


def format_ambiguous_question(events: list[dict], target_iso: str) -> str:
    target_dt = datetime.fromisoformat(target_iso).astimezone()
    target_str = target_dt.strftime("%-I:%M %p")
    listing_parts = [format_candidate(e) for e in events]
    if len(listing_parts) == 2:
        listing = f"{listing_parts[0]} and {listing_parts[1]}"
    else:
        listing = ", ".join(listing_parts[:-1]) + f", and {listing_parts[-1]}"
    return f"I found {len(events)} meetings around {target_str}: {listing} — which one?"
