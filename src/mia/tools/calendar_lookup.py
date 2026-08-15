from datetime import datetime, timedelta


def is_declined(event: dict) -> bool:
    for attendee in event.get("attendees", []):
        if attendee.get("self") and attendee.get("responseStatus") == "declined":
            return True
    return False


def find_events_near(calendar_service, target_iso: str, window_minutes: int = 15) -> list[dict]:
    target = datetime.fromisoformat(target_iso)
    if target.tzinfo is None:
        target = target.astimezone()
    window = timedelta(minutes=window_minutes)
    window_start = target - window
    window_end = target + window

    response = (
        calendar_service.events()
        .list(
            calendarId="primary",
            timeMin=window_start.isoformat(),
            timeMax=window_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=5,
        )
        .execute()
    )

    events = []
    for e in response.get("items", []):
        if is_declined(e):
            continue
        start_dt_str = e.get("start", {}).get("dateTime")
        if start_dt_str is None:
            # All-day event: no start time to match a spoken time against.
            # Google's timeMin/timeMax match on overlap, not on start time,
            # so an all-day event can come back here even though it isn't
            # a real match for the time the user said -- exclude it.
            continue
        start_dt = datetime.fromisoformat(start_dt_str)
        if window_start <= start_dt <= window_end:
            events.append(e)
    return events


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


def format_not_found(target_iso: str) -> str:
    target_dt = datetime.fromisoformat(target_iso)
    if target_dt.tzinfo is None:
        target_dt = target_dt.astimezone()
    return f"I couldn't find anything around {target_dt.astimezone().strftime('%-I:%M %p')}."


def format_ambiguous_question(events: list[dict], target_iso: str) -> str:
    target_dt = datetime.fromisoformat(target_iso)
    if target_dt.tzinfo is None:
        target_dt = target_dt.astimezone()
    target_str = target_dt.astimezone().strftime("%-I:%M %p")
    listing_parts = [format_candidate(e) for e in events]
    if len(listing_parts) == 2:
        listing = f"{listing_parts[0]} and {listing_parts[1]}"
    else:
        listing = ", ".join(listing_parts[:-1]) + f", and {listing_parts[-1]}"
    return f"I found {len(events)} meetings around {target_str}: {listing} — which one?"
