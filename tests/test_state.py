import json
from mia.state import StateStore

def test_status_is_none_for_unknown_meeting(tmp_path):
    store = StateStore(tmp_path / "state.json")
    assert store.status("https://meet.google.com/abc-defg-hij") is None

def test_set_status_then_status_round_trips(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.set_status("https://meet.google.com/abc-defg-hij", "joined")
    assert store.status("https://meet.google.com/abc-defg-hij") == "joined"

def test_state_persists_across_instances(tmp_path):
    path = tmp_path / "state.json"
    StateStore(path).set_status("https://meet.google.com/abc-defg-hij", "prompted")
    reloaded = StateStore(path)
    assert reloaded.status("https://meet.google.com/abc-defg-hij") == "prompted"

def test_clear_removes_meeting(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.set_status("https://meet.google.com/abc-defg-hij", "joined")
    store.clear("https://meet.google.com/abc-defg-hij")
    assert store.status("https://meet.google.com/abc-defg-hij") is None

def test_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "state.json"
    store = StateStore(path)
    store.set_status("https://meet.google.com/abc-defg-hij", "joined")
    assert path.exists()
    assert json.loads(path.read_text()) == {"https://meet.google.com/abc-defg-hij": "joined"}
