# Recall.ai Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace mia's Playwright/BlackHole Meet-join path with a real integration against Recall.ai's cloud meeting-bot API.

**Architecture:** mia's backend (VAD, wake-word, turn-state, Deepgram STT, Claude dispatch, TTS) stays local and unchanged. A new REST client and an input-only local websocket server (reachable via a paid ngrok reserved domain) replace `JoinWorker`, `BlackHoleCapture`, and `audio/injection.py`. Output audio is a single REST POST per response (no streaming/pacing needed), a real architectural simplification over the retired local-audio-device approach.

**Tech Stack:** Python, `requests` (new dependency, Recall REST calls), `websockets` (new dependency, the local input bridge server), pytest.

**Spec:** `docs/superpowers/specs/2026-08-17-recall-client-design.md`

## Global Constraints

- No signed-in bot account, no Google Workspace requirement — confirmed live, `create_bot` needs only `meeting_url` and `bot_name`.
- Sample rate is `16000` for incoming audio (matches Recall's fixed wire format: base64 16-bit signed PCM mono, ~200ms chunks). Outgoing audio is MP3 at a fixed `128000` bits/sec bitrate (`mp3_44100_128`), not PCM.
- Incoming websocket messages have `event: "audio_mixed_raw.data"`, with the PCM buffer at `data.data.buffer` (base64-encoded) — note the doubly-nested `data.data`, different from the top-level `data.chunk` shape a prior, unmerged Attendee-based design used.
- Outgoing audio is a single REST call, `POST {base_url}/api/v1/bot/{id}/output_audio/`, body `{"kind": "mp3", "b64_data": "<base64>"}` — not a streamed/paced connection. No chunking or pacing logic is needed anywhere in this plan.
- Recall has no interrupt/cancel/stop-audio API on any endpoint — once `speak()` is called, the response plays to completion. This is an accepted, deliberate tradeoff (see spec) — do not build chunked/interruptible output.
- Recall's bot `state` comes from `GET /api/v1/bot/{id}/`'s `status_changes` list (chronological events), not a single status field — the current state is `status_changes[-1]["code"]`. Relevant codes: `"joining_call"`, `"in_waiting_room"`, `"in_call_not_recording"`, `"in_call_recording"` (success), `"call_ended"` and `"fatal"` (terminal failures).
- The local websocket bridge is reachable from Recall's cloud infrastructure only via a real, publicly-reachable `wss://` URL (a paid ngrok reserved domain) — `host.docker.internal` is not available here, since Recall's bot does not run in local Docker.
- `join_worker.py`, `audio/capture.py`, `audio/injection.py` are retired outright — no other code in `main.py` depends on them once this plan's Task 6 lands.
- `demo_standalone.py` is unchanged by this plan and must still work afterward (it uses none of the retired files — confirm with a grep before finishing Task 6, since a prior unmerged branch broke this exact thing once already).

---

### Task 1: Config additions

**Files:**
- Modify: `src/mia/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.recall_api_key: str` (default `""`), `Config.recall_base_url: str` (default `"https://us-west-2.recall.ai"`), `Config.recall_websocket_port: int` (default `8765`), `Config.recall_bot_name: str` (default `"Mia"`), `Config.recall_websocket_hostname: str` (default `""`, the paid ngrok reserved domain that makes the local bridge publicly reachable -- e.g. `"mia-bridge.ngrok.app"`, no scheme prefix). Task 6 reads all five fields and builds `f"wss://{config.recall_websocket_hostname}/audio"` from the last one.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
def test_from_env_defaults_recall_settings_when_unset(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("RECALL_API_KEY", raising=False)
    monkeypatch.delenv("RECALL_BASE_URL", raising=False)
    monkeypatch.delenv("RECALL_WEBSOCKET_PORT", raising=False)
    monkeypatch.delenv("RECALL_BOT_NAME", raising=False)
    monkeypatch.delenv("RECALL_WEBSOCKET_HOSTNAME", raising=False)

    config = Config.from_env()

    assert config.recall_api_key == ""
    assert config.recall_base_url == "https://us-west-2.recall.ai"
    assert config.recall_websocket_port == 8765
    assert config.recall_bot_name == "Mia"
    assert config.recall_websocket_hostname == ""


def test_from_env_respects_recall_overrides(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("RECALL_API_KEY", "rc-key")
    monkeypatch.setenv("RECALL_BASE_URL", "https://example.recall.ai")
    monkeypatch.setenv("RECALL_WEBSOCKET_PORT", "9999")
    monkeypatch.setenv("RECALL_BOT_NAME", "Custom Bot")
    monkeypatch.setenv("RECALL_WEBSOCKET_HOSTNAME", "mia-bridge.ngrok.app")

    config = Config.from_env()

    assert config.recall_api_key == "rc-key"
    assert config.recall_base_url == "https://example.recall.ai"
    assert config.recall_websocket_port == 9999
    assert config.recall_bot_name == "Custom Bot"
    assert config.recall_websocket_hostname == "mia-bridge.ngrok.app"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'recall_api_key'`

- [ ] **Step 3: Add the fields to `Config`**

In `src/mia/config.py`, add these fields to the `Config` dataclass, after the existing `state_file` field:

```python
    recall_api_key: str = ""
    recall_base_url: str = "https://us-west-2.recall.ai"
    recall_websocket_port: int = 8765
    recall_bot_name: str = "Mia"
    recall_websocket_hostname: str = ""
```

In `Config.from_env()`, add these lines to the `return cls(...)` call, after the existing `fuzzy_threshold=...` line:

```python
            recall_api_key=os.environ.get("RECALL_API_KEY", ""),
            recall_base_url=os.environ.get("RECALL_BASE_URL", "https://us-west-2.recall.ai"),
            recall_websocket_port=int(os.environ.get("RECALL_WEBSOCKET_PORT", "8765")),
            recall_bot_name=os.environ.get("RECALL_BOT_NAME", "Mia"),
            recall_websocket_hostname=os.environ.get("RECALL_WEBSOCKET_HOSTNAME", ""),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (all tests, including the two new ones)

- [ ] **Step 5: Commit**

```bash
git add src/mia/config.py tests/test_config.py
git commit -m "feat: add Recall.ai config fields"
```

---

### Task 2: Recall REST client

**Files:**
- Create: `src/mia/recall_client.py`
- Test: `tests/test_recall_client.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `create_bot(base_url, api_key, meeting_url, websocket_url, bot_name) -> str`, `bot_state(base_url, api_key, bot_id) -> str`, `wait_until_joined(base_url, api_key, bot_id, timeout_seconds=60.0, poll_interval_seconds=2.0) -> None`, `speak(base_url, api_key, bot_id, mp3_bytes: bytes) -> None`, `leave(base_url, api_key, bot_id) -> None`. Task 6 calls all five.

Add `requests>=2.31` to `pyproject.toml`'s `dependencies` list before starting this task (check first whether it's already present from prior work — the retired, unmerged Attendee branch also added it; if `pyproject.toml` on `main` doesn't have it yet, add it as the new last entry, matching the list's historical append-only order).

- [ ] **Step 1: Add the new dependency if needed**

Run: `grep -c "requests" pyproject.toml`

If it prints `0`, add `"requests>=2.31",` as the new last entry in the `dependencies` list in `pyproject.toml`, then run `pip install -e ".[dev]"`. If it already has a `requests` entry, skip straight to Step 2.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_recall_client.py`:

```python
import base64
from unittest.mock import MagicMock, patch

import pytest

from mia.recall_client import bot_state, create_bot, leave, speak, wait_until_joined


@patch("mia.recall_client.requests.post")
def test_create_bot_posts_correct_payload_and_returns_id(mock_post):
    mock_post.return_value = MagicMock(status_code=200)
    mock_post.return_value.json.return_value = {"id": "bot_abc123"}

    bot_id = create_bot(
        base_url="https://us-west-2.recall.ai",
        api_key="test-key",
        meeting_url="https://meet.google.com/xyz",
        websocket_url="wss://example.ngrok.app/audio",
        bot_name="Mia",
    )

    assert bot_id == "bot_abc123"
    mock_post.assert_called_once_with(
        "https://us-west-2.recall.ai/api/v1/bot/",
        headers={"Authorization": "Token test-key", "Content-Type": "application/json"},
        json={
            "meeting_url": "https://meet.google.com/xyz",
            "bot_name": "Mia",
            "recording_config": {
                "realtime_endpoints": [
                    {"type": "websocket", "url": "wss://example.ngrok.app/audio", "events": ["audio_mixed_raw.data"]},
                ],
            },
        },
        timeout=30,
    )


@patch("mia.recall_client.requests.get")
def test_bot_state_returns_most_recent_status_code(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {
        "status_changes": [
            {"code": "joining_call"},
            {"code": "in_waiting_room"},
        ]
    }

    state = bot_state(base_url="https://us-west-2.recall.ai", api_key="test-key", bot_id="bot_abc123")

    assert state == "in_waiting_room"
    mock_get.assert_called_once_with(
        "https://us-west-2.recall.ai/api/v1/bot/bot_abc123/",
        headers={"Authorization": "Token test-key"},
        timeout=15,
    )


@patch("mia.recall_client.requests.get")
def test_bot_state_returns_empty_string_when_no_status_changes_yet(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {"status_changes": []}

    state = bot_state(base_url="https://us-west-2.recall.ai", api_key="test-key", bot_id="bot_abc123")

    assert state == ""


@patch("mia.recall_client.requests.get")
def test_wait_until_joined_returns_when_state_is_in_call_recording(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {"status_changes": [{"code": "in_call_recording"}]}

    wait_until_joined(
        base_url="https://us-west-2.recall.ai",
        api_key="test-key",
        bot_id="bot_abc123",
        timeout_seconds=5.0,
        poll_interval_seconds=0.01,
    )

    mock_get.assert_called_once()


@patch("mia.recall_client.requests.get")
def test_wait_until_joined_raises_on_call_ended_state(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {"status_changes": [{"code": "call_ended"}]}

    with pytest.raises(RuntimeError, match="call_ended"):
        wait_until_joined(
            base_url="https://us-west-2.recall.ai",
            api_key="test-key",
            bot_id="bot_abc123",
            timeout_seconds=5.0,
            poll_interval_seconds=0.01,
        )


@patch("mia.recall_client.requests.get")
def test_wait_until_joined_raises_on_fatal_state(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {"status_changes": [{"code": "fatal"}]}

    with pytest.raises(RuntimeError, match="fatal"):
        wait_until_joined(
            base_url="https://us-west-2.recall.ai",
            api_key="test-key",
            bot_id="bot_abc123",
            timeout_seconds=5.0,
            poll_interval_seconds=0.01,
        )


@patch("mia.recall_client.requests.get")
def test_wait_until_joined_raises_timeout_error_when_never_joined(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {"status_changes": [{"code": "joining_call"}]}

    with pytest.raises(TimeoutError):
        wait_until_joined(
            base_url="https://us-west-2.recall.ai",
            api_key="test-key",
            bot_id="bot_abc123",
            timeout_seconds=0.05,
            poll_interval_seconds=0.01,
        )


@patch("mia.recall_client.requests.post")
def test_speak_posts_base64_encoded_mp3(mock_post):
    mock_post.return_value = MagicMock(status_code=200)

    speak(base_url="https://us-west-2.recall.ai", api_key="test-key", bot_id="bot_abc123", mp3_bytes=b"fake-mp3-bytes")

    mock_post.assert_called_once_with(
        "https://us-west-2.recall.ai/api/v1/bot/bot_abc123/output_audio/",
        headers={"Authorization": "Token test-key", "Content-Type": "application/json"},
        json={"kind": "mp3", "b64_data": base64.b64encode(b"fake-mp3-bytes").decode("ascii")},
        timeout=30,
    )


@patch("mia.recall_client.requests.post")
def test_leave_posts_to_leave_call_endpoint(mock_post):
    mock_post.return_value = MagicMock(status_code=200)

    leave(base_url="https://us-west-2.recall.ai", api_key="test-key", bot_id="bot_abc123")

    mock_post.assert_called_once_with(
        "https://us-west-2.recall.ai/api/v1/bot/bot_abc123/leave_call/",
        headers={"Authorization": "Token test-key"},
        timeout=15,
    )
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_recall_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.recall_client'`

- [ ] **Step 4: Create `src/mia/recall_client.py`**

```python
import base64
import time

import requests

_TERMINAL_FAILURE_STATES = {"call_ended", "fatal"}
_SUCCESS_STATE = "in_call_recording"


def create_bot(base_url: str, api_key: str, meeting_url: str, websocket_url: str, bot_name: str) -> str:
    response = requests.post(
        f"{base_url}/api/v1/bot/",
        headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
        json={
            "meeting_url": meeting_url,
            "bot_name": bot_name,
            "recording_config": {
                "realtime_endpoints": [
                    {"type": "websocket", "url": websocket_url, "events": ["audio_mixed_raw.data"]},
                ],
            },
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["id"]


def bot_state(base_url: str, api_key: str, bot_id: str) -> str:
    response = requests.get(
        f"{base_url}/api/v1/bot/{bot_id}/",
        headers={"Authorization": f"Token {api_key}"},
        timeout=15,
    )
    response.raise_for_status()
    status_changes = response.json().get("status_changes", [])
    if not status_changes:
        return ""
    return status_changes[-1]["code"]


def wait_until_joined(
    base_url: str,
    api_key: str,
    bot_id: str,
    timeout_seconds: float = 60.0,
    poll_interval_seconds: float = 2.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = bot_state(base_url, api_key, bot_id)
        if state == _SUCCESS_STATE:
            return
        if state in _TERMINAL_FAILURE_STATES:
            raise RuntimeError(f"bot {bot_id} failed to join: state={state}")
        time.sleep(poll_interval_seconds)
    raise TimeoutError(f"bot {bot_id} did not reach {_SUCCESS_STATE} within {timeout_seconds}s")


def speak(base_url: str, api_key: str, bot_id: str, mp3_bytes: bytes) -> None:
    response = requests.post(
        f"{base_url}/api/v1/bot/{bot_id}/output_audio/",
        headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
        json={"kind": "mp3", "b64_data": base64.b64encode(mp3_bytes).decode("ascii")},
        timeout=30,
    )
    response.raise_for_status()


def leave(base_url: str, api_key: str, bot_id: str) -> None:
    response = requests.post(
        f"{base_url}/api/v1/bot/{bot_id}/leave_call/",
        headers={"Authorization": f"Token {api_key}"},
        timeout=15,
    )
    response.raise_for_status()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_recall_client.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/mia/recall_client.py tests/test_recall_client.py
git commit -m "feat: add Recall.ai REST client"
```

---

### Task 3: Pure audio-framing logic

**Files:**
- Create: `src/mia/audio/recall_framing.py`
- Test: `tests/test_recall_framing.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `FrameBuffer` class (`.push(chunk: bytes) -> None`, `.pull(num_bytes: int, timeout_seconds: float) -> bytes`), `extract_mixed_audio_chunk(raw_message: str) -> bytes | None`. Task 4 imports and uses both.

This is the input-side-only equivalent of the pure-logic module a prior, unmerged Attendee-based design used — `FrameBuffer`'s design (silence-padding on timeout via a `threading.Condition`) carries over unchanged, since Recall's incoming audio is the same wire format (base64 16-bit PCM 16kHz mono). Only the message-parsing function differs, since Recall's JSON shape nests the buffer one level deeper than that prior design's target did. There is no chunking/pacing logic in this module at all — Recall's `speak()` is a single REST call, not a paced stream, so nothing here needs to split or pace outgoing audio.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_recall_framing.py`:

```python
import base64
import json
import threading
import time

from mia.audio.recall_framing import FrameBuffer, extract_mixed_audio_chunk


def test_frame_buffer_returns_pushed_bytes_exactly():
    buffer = FrameBuffer()
    buffer.push(b"0123456789")

    result = buffer.pull(num_bytes=10, timeout_seconds=1.0)

    assert result == b"0123456789"


def test_frame_buffer_returns_partial_pull_and_keeps_remainder():
    buffer = FrameBuffer()
    buffer.push(b"0123456789")

    first = buffer.pull(num_bytes=4, timeout_seconds=1.0)
    second = buffer.pull(num_bytes=6, timeout_seconds=1.0)

    assert first == b"0123"
    assert second == b"456789"


def test_frame_buffer_pads_with_silence_on_timeout():
    buffer = FrameBuffer()
    buffer.push(b"01")

    start = time.monotonic()
    result = buffer.pull(num_bytes=4, timeout_seconds=0.05)
    elapsed = time.monotonic() - start

    assert result == b"01\x00\x00"
    assert elapsed < 0.5


def test_frame_buffer_pull_returns_promptly_once_enough_data_pushed():
    buffer = FrameBuffer()

    def _push_after_delay():
        time.sleep(0.02)
        buffer.push(b"01234567")

    threading.Thread(target=_push_after_delay).start()
    start = time.monotonic()
    result = buffer.pull(num_bytes=8, timeout_seconds=1.0)
    elapsed = time.monotonic() - start

    assert result == b"01234567"
    assert elapsed < 0.5


def test_extract_mixed_audio_chunk_decodes_correct_event():
    message = json.dumps(
        {
            "event": "audio_mixed_raw.data",
            "data": {
                "data": {
                    "buffer": base64.b64encode(b"hello").decode("ascii"),
                    "timestamp": {"relative": 1.0, "absolute": "2026-08-17T00:00:00Z"},
                },
                "bot": {"id": "bot_abc123"},
            },
        }
    )

    assert extract_mixed_audio_chunk(message) == b"hello"


def test_extract_mixed_audio_chunk_ignores_other_events():
    message = json.dumps({"event": "participant_events.join", "data": {}})

    assert extract_mixed_audio_chunk(message) is None


def test_extract_mixed_audio_chunk_ignores_unparseable_message():
    assert extract_mixed_audio_chunk("not json") is None


def test_extract_mixed_audio_chunk_returns_none_on_null_data():
    message = json.dumps({"event": "audio_mixed_raw.data", "data": None})

    assert extract_mixed_audio_chunk(message) is None


def test_extract_mixed_audio_chunk_returns_none_on_non_dict_inner_data():
    message = json.dumps({"event": "audio_mixed_raw.data", "data": {"data": [1, 2, 3]}})

    assert extract_mixed_audio_chunk(message) is None


def test_extract_mixed_audio_chunk_returns_none_on_missing_buffer():
    message = json.dumps({"event": "audio_mixed_raw.data", "data": {"data": {}}})

    assert extract_mixed_audio_chunk(message) is None


def test_extract_mixed_audio_chunk_returns_none_on_invalid_base64():
    message = json.dumps({"event": "audio_mixed_raw.data", "data": {"data": {"buffer": "not-valid-base64!!!"}}})

    assert extract_mixed_audio_chunk(message) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_recall_framing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.audio.recall_framing'`

- [ ] **Step 3: Create `src/mia/audio/recall_framing.py`**

```python
import base64
import binascii
import json
import threading
import time


class FrameBuffer:
    """Accumulates arbitrarily-sized incoming PCM chunks and serves
    fixed-size frames on demand, padding with silence when a caller's
    timeout elapses before enough real audio has arrived. A
    threading.Condition lets pull() wake up as soon as enough data is
    pushed, rather than polling."""

    def __init__(self):
        self._buffer = bytearray()
        self._condition = threading.Condition()

    def push(self, chunk: bytes) -> None:
        with self._condition:
            self._buffer.extend(chunk)
            self._condition.notify_all()

    def pull(self, num_bytes: int, timeout_seconds: float) -> bytes:
        with self._condition:
            deadline = time.monotonic() + timeout_seconds
            while len(self._buffer) < num_bytes:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
            if len(self._buffer) >= num_bytes:
                result = bytes(self._buffer[:num_bytes])
                del self._buffer[:num_bytes]
                return result
            result = bytes(self._buffer) + b"\x00" * (num_bytes - len(self._buffer))
            self._buffer.clear()
            return result


def extract_mixed_audio_chunk(raw_message: str) -> bytes | None:
    """Parses one incoming websocket message and returns the decoded PCM
    bytes if it's an audio_mixed_raw.data event with a valid
    data.data.buffer field, else None (any other event type, an
    unparseable message, a malformed data/data.data shape, or invalid
    base64 is safely ignored rather than raised)."""
    try:
        payload = json.loads(raw_message)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("event") != "audio_mixed_raw.data":
        return None
    outer_data = payload.get("data")
    if not isinstance(outer_data, dict):
        return None
    inner_data = outer_data.get("data")
    if not isinstance(inner_data, dict):
        return None
    buffer_b64 = inner_data.get("buffer")
    if not buffer_b64:
        return None
    try:
        return base64.b64decode(buffer_b64)
    except (AttributeError, TypeError, ValueError, binascii.Error):
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_recall_framing.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mia/audio/recall_framing.py tests/test_recall_framing.py
git commit -m "feat: add pure audio-framing logic for the Recall.ai bridge"
```

---

### Task 4: RecallAudioBridge (input-only websocket server)

**Files:**
- Create: `src/mia/audio/recall_bridge.py`
- Test: `tests/test_recall_bridge.py`

**Interfaces:**
- Consumes: `FrameBuffer`, `extract_mixed_audio_chunk` from `mia.audio.recall_framing` (Task 3).
- Produces: `RecallAudioBridge` class — `__init__(port: int, sample_rate: int = 16000)`, usable as a context manager (`__enter__`/`__exit__`), `.read_frame(frame_ms: int = 30) -> bytes`. Task 6 constructs and uses this in place of `BlackHoleCapture`. Note there is **no** `start_playback`/`is_playback_active`/`stop_playback` on this class — output audio is a direct REST call from Task 6's `main.py` changes, not routed through this bridge at all.

Add `websockets>=12.0` to `pyproject.toml`'s `dependencies` list before starting this task, unless it's already present (check the same way Task 2 checked for `requests`).

- [ ] **Step 1: Add the new dependency if needed**

Run: `grep -c "websockets" pyproject.toml`

If it prints `0`, add `"websockets>=12.0",` as the new last entry in the `dependencies` list in `pyproject.toml` (after wherever Task 2 left `requests`, so it becomes the new last entry), then run `pip install -e ".[dev]"`. If it already has a `websockets` entry, skip straight to Step 2.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_recall_bridge.py`. These tests exercise the bridge's logic directly (pushing synthetic data into its internal `FrameBuffer`) without opening a real client connection — the one exception is the last test, which verifies the real server can start and stop cleanly using an OS-assigned port (`port=0`), without sending any real messages over it.

```python
from mia.audio.recall_bridge import RecallAudioBridge


def test_read_frame_returns_pushed_audio_without_a_real_connection():
    bridge = RecallAudioBridge(port=0, sample_rate=16000)
    # 30ms at 16kHz mono 16-bit = 960 bytes
    bridge._frame_buffer.push(b"\x01\x02" * 480)

    frame = bridge.read_frame(frame_ms=30)

    assert frame == b"\x01\x02" * 480
    assert len(frame) == 960


def test_read_frame_pads_silence_when_no_audio_pushed():
    bridge = RecallAudioBridge(port=0, sample_rate=16000)

    frame = bridge.read_frame(frame_ms=30)

    assert frame == b"\x00" * 960


def test_enter_and_exit_start_and_stop_the_server_cleanly():
    # port=0 lets the OS assign any free port -- this test only checks
    # that startup/shutdown of the real asyncio server doesn't raise.
    bridge = RecallAudioBridge(port=0, sample_rate=16000)

    with bridge:
        assert bridge._server is not None


def test_port_is_released_after_exit():
    # Regression test: a prior bridge implementation (built for a
    # different, unmerged integration) left its listening socket open
    # after __exit__, so a second bridge on the same fixed port failed to
    # bind. Use a fixed, unusual port (not 0) so this actually exercises
    # reuse of the same port, not two different OS-assigned ones.
    bridge1 = RecallAudioBridge(port=18766, sample_rate=16000)
    with bridge1:
        pass

    bridge2 = RecallAudioBridge(port=18766, sample_rate=16000)
    with bridge2:
        assert bridge2._server is not None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_recall_bridge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.audio.recall_bridge'`

- [ ] **Step 4: Create `src/mia/audio/recall_bridge.py`**

```python
import asyncio
import threading

import websockets

from mia.audio.recall_framing import FrameBuffer, extract_mixed_audio_chunk

_READ_TIMEOUT_MULTIPLIER = 2
_BYTES_PER_SAMPLE = 2  # 16-bit PCM


class RecallAudioBridge:
    """Local, input-only websocket server that Recall's bot connects out
    to (see the design spec for why the connection direction is inverted
    from the usual client/server relationship, and why this bridge has no
    output-side methods -- Recall's speak() is a single REST call, not a
    streamed connection). Provides the same read_frame() contract
    BlackHoleCapture used to, so main.py's call loop needs minimal
    changes."""

    def __init__(self, port: int, sample_rate: int = 16000):
        self._port = port
        self._sample_rate = sample_rate
        self._frame_buffer = FrameBuffer()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "RecallAudioBridge":
        ready = threading.Event()
        startup_error: list[BaseException] = []

        def _run_loop() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            async def _setup():
                self._server = await websockets.serve(self._handle_connection, "0.0.0.0", self._port)

            try:
                self._loop.run_until_complete(_setup())
            except Exception as exc:
                startup_error.append(exc)
                ready.set()
                return
            ready.set()
            self._loop.run_forever()

        self._thread = threading.Thread(target=_run_loop, daemon=True)
        self._thread.start()
        ready.wait(timeout=10)
        if startup_error:
            raise startup_error[0]
        return self

    def __exit__(self, *exc_info) -> None:
        if self._loop is not None and self._server is not None:

            async def _close() -> None:
                self._server.close()
                await self._server.wait_closed()

            future = asyncio.run_coroutine_threadsafe(_close(), self._loop)
            try:
                future.result(timeout=5)
            except Exception:
                pass
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)

    async def _handle_connection(self, websocket) -> None:
        async for raw_message in websocket:
            chunk = extract_mixed_audio_chunk(raw_message)
            if chunk is not None:
                self._frame_buffer.push(chunk)

    def read_frame(self, frame_ms: int = 30) -> bytes:
        num_bytes = int(self._sample_rate * frame_ms / 1000) * _BYTES_PER_SAMPLE
        timeout_seconds = (frame_ms * _READ_TIMEOUT_MULTIPLIER) / 1000
        return self._frame_buffer.pull(num_bytes, timeout_seconds)
```

This uses the `async def _setup(): ... await websockets.serve(...)` wrapper (rather than calling `websockets.serve(...)` directly as an argument to `run_until_complete`) because `websockets.serve` on modern versions of the `websockets` package (12+) requires a running event loop at construction time — constructing it before the loop is running raises `RuntimeError: no running event loop`. Wrapping it in a coroutine that's itself run via `run_until_complete` ensures the loop is already running when `websockets.serve` is constructed.

Also unlike a prior, unmerged bridge implementation for a different integration, `__exit__` here already closes the server socket (`self._server.close()` + `await self._server.wait_closed()`) before stopping the loop -- that prior implementation shipped without this and had to be fixed after a reviewer caught it leaving the port bound past `__exit__`. Get it right here from the start; Step 2's `test_port_is_released_after_exit` is exactly the regression test that would have caught it.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_recall_bridge.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/mia/audio/recall_bridge.py tests/test_recall_bridge.py
git commit -m "feat: add RecallAudioBridge input-only websocket server"
```

---

### Task 5: `synthesize()` gains an `output_format` parameter

**Files:**
- Modify: `src/mia/tts.py`
- Test: `tests/test_tts.py` (new file -- `synthesize()` has no existing test file)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `synthesize(api_key: str, text: str, voice_id: str = "21m00Tcm4TlvDq8ikWAM", output_format: str = "pcm_24000") -> bytes`. Task 6 calls this with `output_format="mp3_44100_128"` for the Meet-bot path; `demo_standalone.py`'s existing call site (unmodified by this plan) keeps using the default.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tts.py`:

```python
from unittest.mock import MagicMock, patch

from mia.tts import synthesize


@patch("mia.tts.ElevenLabs")
def test_synthesize_defaults_to_pcm_24000(mock_elevenlabs_class):
    mock_client = MagicMock()
    mock_elevenlabs_class.return_value = mock_client
    mock_client.text_to_speech.convert.return_value = [b"chunk1", b"chunk2"]

    result = synthesize(api_key="test-key", text="hello")

    assert result == b"chunk1chunk2"
    mock_client.text_to_speech.convert.assert_called_once_with(
        voice_id="21m00Tcm4TlvDq8ikWAM",
        text="hello",
        output_format="pcm_24000",
    )


@patch("mia.tts.ElevenLabs")
def test_synthesize_respects_output_format_override(mock_elevenlabs_class):
    mock_client = MagicMock()
    mock_elevenlabs_class.return_value = mock_client
    mock_client.text_to_speech.convert.return_value = [b"mp3-bytes"]

    result = synthesize(api_key="test-key", text="hello", output_format="mp3_44100_128")

    assert result == b"mp3-bytes"
    mock_client.text_to_speech.convert.assert_called_once_with(
        voice_id="21m00Tcm4TlvDq8ikWAM",
        text="hello",
        output_format="mp3_44100_128",
    )


@patch("mia.tts.ElevenLabs")
def test_synthesize_respects_voice_id_override(mock_elevenlabs_class):
    mock_client = MagicMock()
    mock_elevenlabs_class.return_value = mock_client
    mock_client.text_to_speech.convert.return_value = [b"chunk"]

    synthesize(api_key="test-key", text="hello", voice_id="custom-voice-id")

    mock_client.text_to_speech.convert.assert_called_once_with(
        voice_id="custom-voice-id",
        text="hello",
        output_format="pcm_24000",
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tts.py -v`
Expected: FAIL with `TypeError: synthesize() got an unexpected keyword argument 'output_format'`

- [ ] **Step 3: Add the parameter**

In `src/mia/tts.py`, change the function signature from:

```python
def synthesize(api_key: str, text: str, voice_id: str = "21m00Tcm4TlvDq8ikWAM") -> bytes:
```

to:

```python
def synthesize(api_key: str, text: str, voice_id: str = "21m00Tcm4TlvDq8ikWAM", output_format: str = "pcm_24000") -> bytes:
```

and change the `client.text_to_speech.convert(...)` call's `output_format="pcm_24000"` argument to `output_format=output_format`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tts.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mia/tts.py tests/test_tts.py
git commit -m "feat: add output_format parameter to synthesize()"
```

---

### Task 6: Wire main.py to Recall.ai, retire the old join path

**Files:**
- Modify: `src/mia/main.py`
- Delete: `src/mia/join_worker.py`, `src/mia/audio/capture.py`, `src/mia/audio/injection.py`
- Delete: any test files for those three (check first — see Step 5)

**Interfaces:**
- Consumes: `create_bot`, `bot_state`, `wait_until_joined`, `speak`, `leave` from `mia.recall_client` (Task 2); `RecallAudioBridge` from `mia.audio.recall_bridge` (Task 4); `synthesize`'s new `output_format` parameter (Task 5); `Config.recall_api_key`, `Config.recall_base_url`, `Config.recall_websocket_port`, `Config.recall_bot_name`, `Config.recall_websocket_hostname` (Task 1).

This task has no new unit tests of its own — `_handle_join` and `_run_call_loop` are orchestration functions already covered by the components they call, and their actual behavior can only be verified against a live Recall.ai bot and a real meeting (manual testing). The full existing test suite must still pass unmodified.

- [ ] **Step 1: Update imports in `src/mia/main.py`**

Remove these two lines:

```python
from mia.audio.capture import BlackHoleCapture
from mia.audio.injection import is_playback_active, start_playback, stop_playback
```

Replace them with (alphabetical position among the `mia.*` imports):

```python
from mia import recall_client
from mia.audio.recall_bridge import RecallAudioBridge
```

Remove this line (no longer used anywhere in `main.py`):

```python
from mia.join_worker import JoinWorker
```

- [ ] **Step 2: Add the bot-left-states constant and playback-duration helper**

Near the top of `src/mia/main.py`, after the existing `_SILENCE_FRAMES_TO_END_COMMAND = 24` constant, add:

```python
# States that mean Recall's bot is no longer usably present in the
# meeting -- checked alongside the existing tab-based leave signal so a
# bot removed by another participant (or a fatal error) is also noticed,
# not just a locally-closed Chrome tab.
_BOT_LEFT_STATES = {"call_ended", "fatal"}

# speak() is a single REST call, not a streamed connection -- there is no
# "audio finished playing" acknowledgment from Recall's API. Estimate
# playback duration from the MP3's byte length at ElevenLabs' fixed
# 128kbps ("mp3_44100_128") constant bitrate.
_MP3_BITRATE_BITS_PER_SECOND = 128_000


def _estimate_playback_seconds(mp3_bytes: bytes) -> float:
    return len(mp3_bytes) * 8 / _MP3_BITRATE_BITS_PER_SECOND
```

- [ ] **Step 3: Replace `_run_call_loop` entirely**

Replace the entire existing `_run_call_loop` function (from `def _run_call_loop(` through the closing `stt.stop()` line) with this complete version. It has: a new signature (`bridge`, `bot_id` parameters), audio input now comes from `bridge.read_frame(...)` instead of `capture.read_frame(...)`, output is a direct `recall_client.speak(...)` call plus the duration-estimate helper instead of `bridge.start_playback(...)`/`bridge.is_playback_active()`, a new bot-status leave-check, and everything that was inside the removed `with BlackHoleCapture(...) as capture:` block is now directly inside the `try:` block (one less indentation level than before):

```python
def _run_call_loop(
    config: Config,
    registry: ToolRegistry,
    anthropic_client: Anthropic,
    meet_url: str,
    bridge: RecallAudioBridge,
    bot_id: str,
) -> None:
    turn_state = TurnStateMachine()
    wake_word = WakeWordMatcher(config.wake_word, threshold=config.fuzzy_threshold)
    command_buffer = CommandBuffer()
    vad = FrameVAD(frame_ms=_FRAME_MS)
    history = ConversationHistory()
    # What mia is currently speaking, so on_transcript can tell her own TTS
    # apart from a real barge-in via content-based comparison (is_self_echo).
    # Whether Recall's audio_mixed_raw stream includes mia's own spoken
    # output is unverified as of this branch -- if it does, this filtering
    # is still load-bearing; if it doesn't, it's a harmless no-op. Confirm
    # during live testing.
    current_speech: list[str | None] = [None]

    # NOTE: StreamingSTT dispatches on_transcript from a background listener
    # thread, while the loop below mutates the same turn_state /
    # command_buffer from this thread. Guard the check-then-act sequences on
    # both sides. The lock is only ever held for state transitions, never
    # across the LLM/TTS/injection calls, so the listener thread never blocks
    # on slow network work.
    lock = threading.Lock()

    # NOTE: TurnStateMachine starts in IDLE, and should_process_stt() is True
    # only in LISTENING. Entering the call is what starts listening, so make
    # that transition here.
    turn_state.wake_word_detected()

    # No streamed/paced playback exists anymore -- speak() is a single REST
    # call, so "is currently speaking" is tracked here as a plain estimated
    # end time rather than queried from a bridge.
    playback_end_time = [0.0]

    def on_transcript(text: str, is_final: bool) -> None:
        if not is_final:
            return
        with lock:
            if not turn_state.should_process_stt():
                return
            if turn_state.current() == TurnState.SPEAKING and current_speech[0] is not None:
                if is_self_echo(text, current_speech[0]):
                    return
            if command_buffer.is_capturing():
                command_buffer.append(text + " ")
                return
            if wake_word.matches(text):
                # Recall has no interrupt/stop-audio API, so a barge-in
                # wake word can no longer truncate audio already sent via
                # speak() -- an accepted, deliberate tradeoff (see the
                # design spec). Wake-word detection during playback still
                # works: mia starts listening to a new command
                # immediately, she just can't be cut off mid-sentence.
                turn_state.wake_word_detected()
                command_buffer.start()
                command_buffer.append(text + " ")
                safe_log("info", "wake word detected", meeting_url=meet_url)

    stt = StreamingSTT(config.deepgram_api_key, on_transcript)
    stt.start()
    try:
        silence_frames = 0
        missed_tab_checks = 0
        last_tab_check = time.monotonic()

        while True:
            now = time.monotonic()
            if now - last_tab_check >= _LEAVE_CHECK_INTERVAL_SECONDS:
                last_tab_check = now

                bot_still_in_meeting = True
                try:
                    current_bot_state = recall_client.bot_state(
                        base_url=config.recall_base_url,
                        api_key=config.recall_api_key,
                        bot_id=bot_id,
                    )
                    bot_still_in_meeting = current_bot_state not in _BOT_LEFT_STATES
                except Exception as exc:
                    # A transient status-poll failure must not end the
                    # call -- fall back to the tab-based signal alone
                    # for this iteration.
                    safe_log("warning", "bot status poll failed", meeting_url=meet_url, error=str(exc))

                if not bot_still_in_meeting:
                    safe_log("info", "leave signal", meeting_url=meet_url, reason="bot left meeting")
                    break

                if find_active_meet_tab() == meet_url:
                    missed_tab_checks = 0
                else:
                    missed_tab_checks += 1
                    if missed_tab_checks >= _LEAVE_CONFIRM_CHECKS:
                        safe_log("info", "leave signal", meeting_url=meet_url, reason="tab closed")
                        break

            turn_state.tick()

            with lock:
                if turn_state.current() == TurnState.SPEAKING and time.monotonic() >= playback_end_time[0]:
                    turn_state.finish_speaking()

            frame = bridge.read_frame(frame_ms=_FRAME_MS)

            if turn_state.should_process_stt():
                stt.send_frame(frame)

            stt.send_keepalive_if_idle()

            is_speech = vad.is_speech(frame)

            command_text = None
            with lock:
                if not command_buffer.is_capturing():
                    silence_frames = 0
                elif is_speech:
                    silence_frames = 0
                else:
                    silence_frames += 1
                    if silence_frames >= _SILENCE_FRAMES_TO_END_COMMAND:
                        silence_frames = 0
                        command_text = command_buffer.on_silence()
                        if command_text:
                            turn_state.command_captured()

            if not command_text:
                continue

            try:
                if not wake_word.strip_wake_phrase(command_text):
                    safe_log(
                        "info",
                        "bare wake phrase ignored",
                        meeting_url=meet_url,
                    )
                    with lock:
                        turn_state.abandon_turn()
                else:
                    result = dispatch_command(anthropic_client, registry, command_text, history)
                    safe_log(
                        "info",
                        "command dispatched",
                        tool=result.tool_name,
                        meeting_url=meet_url,
                    )
                    audio = synthesize(
                        config.elevenlabs_api_key, result.confirmation, output_format="mp3_44100_128"
                    )
                    with lock:
                        turn_state.start_speaking()
                        current_speech[0] = result.confirmation
                        recall_client.speak(
                            base_url=config.recall_base_url,
                            api_key=config.recall_api_key,
                            bot_id=bot_id,
                            mp3_bytes=audio,
                        )
                        playback_end_time[0] = time.monotonic() + _estimate_playback_seconds(audio)
            except Exception as exc:
                safe_log(
                    "error",
                    "voice turn failed",
                    meeting_url=meet_url,
                    error=str(exc),
                )
                with lock:
                    turn_state.abandon_turn()
    finally:
        stt.stop()
```

- [ ] **Step 4: Replace `_handle_join`**

Replace the entire existing `_handle_join` function with:

```python
def _handle_join(
    config: Config,
    registry: ToolRegistry,
    anthropic_client: Anthropic,
    state: StateStore,
    meet_url: str,
) -> None:
    websocket_url = f"wss://{config.recall_websocket_hostname}/audio"
    try:
        with RecallAudioBridge(port=config.recall_websocket_port) as bridge:
            bot_id = None
            try:
                bot_id = recall_client.create_bot(
                    base_url=config.recall_base_url,
                    api_key=config.recall_api_key,
                    meeting_url=meet_url,
                    websocket_url=websocket_url,
                    bot_name=config.recall_bot_name,
                )
                recall_client.wait_until_joined(
                    base_url=config.recall_base_url,
                    api_key=config.recall_api_key,
                    bot_id=bot_id,
                )
            except Exception as exc:
                safe_log("error", "join failed", meeting_url=meet_url, error=str(exc))
                state.set_status(meet_url, "skipped")
                if bot_id is not None:
                    try:
                        recall_client.leave(
                            base_url=config.recall_base_url,
                            api_key=config.recall_api_key,
                            bot_id=bot_id,
                        )
                    except Exception as leave_exc:
                        safe_log("error", "leave failed", meeting_url=meet_url, error=str(leave_exc))
                return

            safe_log("info", "joined meeting", meeting_url=meet_url)
            try:
                _run_call_loop(config, registry, anthropic_client, meet_url, bridge, bot_id)
            except Exception as exc:
                safe_log("error", "call loop failed", meeting_url=meet_url, error=str(exc))
            finally:
                try:
                    recall_client.leave(
                        base_url=config.recall_base_url,
                        api_key=config.recall_api_key,
                        bot_id=bot_id,
                    )
                except Exception as exc:
                    safe_log("error", "leave failed", meeting_url=meet_url, error=str(exc))
                state.clear(meet_url)
                safe_log("info", "left meeting", meeting_url=meet_url)
    except Exception as exc:
        # RecallAudioBridge.__enter__ itself failed (e.g. port already
        # bound). The caller in run() already marked this URL "joined"
        # before calling us, so it must be corrected to "skipped" here, or
        # the URL would never be re-prompted until StateStore's TTL
        # expires.
        safe_log("error", "join failed", meeting_url=meet_url, error=str(exc))
        state.set_status(meet_url, "skipped")
```

**Note on `websocket_url`:** unlike the retired local-only setup, this must be a real, publicly-reachable `wss://` hostname (the paid ngrok reserved domain from the design spec's setup requirements) -- there is no `host.docker.internal` equivalent here, since Recall's bot runs on Recall's own cloud infrastructure, not locally. `config.recall_websocket_hostname` (Task 1) holds this value, set via the `RECALL_WEBSOCKET_HOSTNAME` environment variable at deploy time -- no code change needed here to configure it.

- [ ] **Step 5: Delete the retired files**

```bash
ls tests/ | grep -E "join_worker|audio_capture|audio_injection"
```

Delete `src/mia/join_worker.py`, `src/mia/audio/capture.py`, `src/mia/audio/injection.py`, and any test files the `ls` above found for them.

- [ ] **Step 6: Confirm `demo_standalone.py` is unaffected**

```bash
grep -n "capture\|injection\|join_worker" demo_standalone.py
```

`demo_standalone.py` must not reference any of the three deleted modules. If it does, stop and report this as a blocking finding rather than deleting the files anyway -- a prior, unmerged branch broke `demo_standalone.py` exactly this way once already by deleting these same files without checking first.

- [ ] **Step 7: Run the full test suite**

Run: `pytest -q`
Expected: PASS, same test count as before minus whatever tests existed for the three deleted files, plus this plan's new tests

- [ ] **Step 8: Manual verification against a live Recall.ai bot**

This is the step that actually proves the integration works — none of the automated tests exercise a real Google Meet call.

1. Set `RECALL_API_KEY` and `RECALL_WEBSOCKET_HOSTNAME` in `.env` (the paid ngrok reserved domain must be running and pointed at `localhost:8765`, or whatever port `RECALL_WEBSOCKET_PORT` is set to).
2. Start a real Meet call, have `mia` (via `python -m mia.main`) detect it and prompt to join, accept the prompt.
3. Confirm in the actual meeting: the bot joins, responds to "Hey Mia" commands with real tool calls and spoken confirmations.
4. Confirm leaving the meeting (closing the Meet tab) makes the bot leave within a few seconds.
5. Note whether mia's own spoken output appears to loop back into her own `on_transcript` handling (the open question flagged in Step 3's `current_speech` comment) -- if `is_self_echo()` filtering doesn't seem to be triggering on her own speech, that's useful confirmation it's a harmless no-op rather than a live bug; if you observe her reacting to her own voice, that's a real problem to investigate separately, not part of this plan's scope to fix blind.

- [ ] **Step 9: Commit**

```bash
git add src/mia/main.py src/mia/config.py
git add -u src/mia/join_worker.py src/mia/audio/capture.py src/mia/audio/injection.py
git commit -m "feat: wire main.py to Recall.ai, retire the Playwright/BlackHole join path"
```
