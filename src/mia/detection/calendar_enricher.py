from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(frozen=True)
class MeetingInfo:
    """What the calendar knows about the meeting behind a Meet URL.

    Attendees are the invitee list, which is not the same as who actually
    attended: it covers people who never speak, and misses anyone who joined
    without an invite. It is context for the summary, never a way to identify
    a speaker.
    """

    title: str | None = None
    attendees: list[str] = field(default_factory=list)


def _attendee_names(event: dict) -> list[str]:
    names = []
    for attendee in event.get("attendees") or []:
        if not isinstance(attendee, dict):
            continue
        name = attendee.get("displayName") or attendee.get("email")
        if name:
            names.append(name)
    return names


def find_current_meeting(calendar_service, *, now: datetime, meet_url: str) -> MeetingInfo:
    """The calendar event matching `meet_url`, as title plus attendee names.

    Returns an empty MeetingInfo rather than raising: this runs on every
    detection poll, and a Calendar hiccup must cost that poll, not the run.
    """
    try:
        response = calendar_service.events().list(
            calendarId="primary",
            timeMin=(now - timedelta(minutes=10)).isoformat(),
            timeMax=(now + timedelta(minutes=10)).isoformat(),
            singleEvents=True,
        ).execute()

        for event in response.get("items", []):
            if event.get("hangoutLink") == meet_url:
                return MeetingInfo(title=event.get("summary"), attendees=_attendee_names(event))
        return MeetingInfo()
    except Exception:
        return MeetingInfo()
