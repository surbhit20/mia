from dataclasses import dataclass

from mia.state import StateStore

_HANDLED_STATUSES = {"prompted", "joined", "skipped"}

@dataclass(frozen=True)
class TriggerDecision:
    should_prompt: bool
    meeting_url: str | None = None
    display_title: str | None = None

def decide(
    *,
    mic_active: bool,
    meet_tab_url: str | None,
    calendar_title: str | None,
    state: StateStore,
) -> TriggerDecision:
    if not mic_active or meet_tab_url is None:
        return TriggerDecision(should_prompt=False)

    if state.status(meet_tab_url) in _HANDLED_STATUSES:
        return TriggerDecision(should_prompt=False)

    title = calendar_title or f"this Meet call ({meet_tab_url})"
    return TriggerDecision(should_prompt=True, meeting_url=meet_tab_url, display_title=title)
