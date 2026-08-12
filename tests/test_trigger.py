from mia.detection.trigger import decide
from mia.state import StateStore

URL = "https://meet.google.com/abc-defg-hij"

def test_no_prompt_when_mic_inactive(tmp_path):
    state = StateStore(tmp_path / "state.json")
    result = decide(mic_active=False, meet_tab_url=URL, calendar_title="Standup", state=state)
    assert result.should_prompt is False

def test_no_prompt_when_no_meet_tab(tmp_path):
    state = StateStore(tmp_path / "state.json")
    result = decide(mic_active=True, meet_tab_url=None, calendar_title=None, state=state)
    assert result.should_prompt is False

def test_prompts_with_calendar_title_when_available(tmp_path):
    state = StateStore(tmp_path / "state.json")
    result = decide(mic_active=True, meet_tab_url=URL, calendar_title="Standup", state=state)
    assert result.should_prompt is True
    assert result.meeting_url == URL
    assert result.display_title == "Standup"

def test_prompts_with_generic_title_when_no_calendar_match(tmp_path):
    state = StateStore(tmp_path / "state.json")
    result = decide(mic_active=True, meet_tab_url=URL, calendar_title=None, state=state)
    assert result.should_prompt is True
    assert URL in result.display_title

def test_does_not_reprompt_already_prompted_meeting(tmp_path):
    state = StateStore(tmp_path / "state.json")
    state.set_status(URL, "prompted")
    result = decide(mic_active=True, meet_tab_url=URL, calendar_title="Standup", state=state)
    assert result.should_prompt is False

def test_does_not_reprompt_joined_or_skipped_meeting(tmp_path):
    state = StateStore(tmp_path / "state.json")
    state.set_status(URL, "joined")
    assert decide(mic_active=True, meet_tab_url=URL, calendar_title=None, state=state).should_prompt is False
    state.set_status(URL, "skipped")
    assert decide(mic_active=True, meet_tab_url=URL, calendar_title=None, state=state).should_prompt is False
