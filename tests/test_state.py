import json
from mia.state import StateStore

URL = "https://meet.google.com/abc-defg-hij"

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
    store = StateStore(path, clock=lambda: 1000.0)
    store.set_status("https://meet.google.com/abc-defg-hij", "joined")
    assert path.exists()
    assert json.loads(path.read_text()) == {
        "https://meet.google.com/abc-defg-hij": {"status": "joined", "updated_at": 1000.0}
    }

def test_status_expires_after_the_ttl(tmp_path):
    now = [1000.0]
    store = StateStore(tmp_path / "state.json", ttl_seconds=60, clock=lambda: now[0])
    store.set_status(URL, "skipped")
    now[0] += 59
    assert store.status(URL) == "skipped"  # dedup still holds inside the window
    now[0] += 2
    assert store.status(URL) is None  # ...and lapses after it

def test_legacy_bare_string_entries_are_treated_as_expired(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({URL: "skipped"}))
    assert StateStore(path).status(URL) is None

def test_corrupt_state_file_reads_as_empty(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"https://meet.google.com/abc-defg-hij": {"status": "sk')
    store = StateStore(path)
    assert store.status(URL) is None
    store.set_status(URL, "joined")  # and the next write repairs the file
    assert store.status(URL) == "joined"

def test_write_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "state.json"
    StateStore(path).set_status(URL, "joined")
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]
