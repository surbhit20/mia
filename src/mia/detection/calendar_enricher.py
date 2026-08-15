from datetime import datetime, timedelta

def find_current_meeting_title(calendar_service, *, now: datetime, meet_url: str) -> str | None:
    try:
        response = calendar_service.events().list(
            calendarId="primary",
            timeMin=(now - timedelta(minutes=10)).isoformat(),
            timeMax=(now + timedelta(minutes=10)).isoformat(),
            singleEvents=True,
        ).execute()

        for event in response.get("items", []):
            if event.get("hangoutLink") == meet_url:
                return event.get("summary")
        return None
    except Exception:
        return None
