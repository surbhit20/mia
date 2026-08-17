# AttendeeClient Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the retired Playwright/BlackHole Meet-join path with a real integration against the self-hosted Attendee instance, using anonymous bot join and Attendee's realtime-audio websocket protocol.

**Architecture:** mia runs its own local websocket server that Attendee's bot connects out to (the connection direction is inverted from a typical client/server setup); mixed meeting audio streams in and bot-speech audio streams out over that one connection. The bridge exposes the exact same `read_frame`/`start_playback`/`is_playback_active`/`stop_playback` interface the retired BlackHole classes had, so `main.py`'s call loop barely changes.

**Tech Stack:** Python, `requests` (new dependency, Attendee REST calls), `websockets` (new dependency, the local audio bridge server), pytest.

## Global Constraints

- Anonymous bot join only — no signed-in bot account, no SSO. Live-tested and confirmed working 2026-08-16.
- Mixed audio only (`websocket_settings.audio`, not `per_participant_audio`) — mia doesn't need to distinguish speakers.
- Sample rate is `16000` everywhere (websocket audio, `synthesize()`'s existing `pcm_16000` output) — no resampling anywhere in this plan.
- Audio wire format: base64-encoded 16-bit signed PCM, mono. Incoming messages have `trigger: "realtime_audio.mixed"`; outgoing messages have `trigger: "realtime_audio.bot_output"`.
- `output_image` only accepts `{"type": "image/png" | "image/jpeg", "data": "<base64>"}`, and only once the bot's state is `joined_recording` (or another "can play media" state) — call it once, right after `wait_until_joined` succeeds.
- Attendee's bot `state` field uses these exact string codes (from its `BotStates` API-code mapping): `"ready"`, `"joining"`, `"joined_not_recording"`, `"joined_recording"`, `"leaving"`, `"post_processing"`, `"fatal_error"`, `"waiting_room"`, `"ended"`, `"data_deleted"`, `"scheduled"`, `"staged"`, `"joined_recording_paused"`.
- Attendee's protocol has no "audio finished playing" acknowledgment — `is_playback_active()` is duration-estimated (`audio byte length ÷ (sample_rate × 2)`), not queried.
- The local websocket server is reachable only from Attendee's own Docker network on this machine (`host.docker.internal`) — no auth or TLS needed on it, and it must not be exposed publicly.
- Detection/prompt/state logic (`detection/*`, `notify.py`, `trigger.py`, `state.py`) is unchanged. `demo_standalone.py` is unchanged.
- `_run_call_loop`'s turn-state machine, wake-word matching, Deepgram STT wrapper, Claude tool-calling, and TTS content are unchanged — only where audio comes from/goes to changes.

---

### Task 1: Config additions

**Files:**
- Modify: `src/mia/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.attendee_api_key: str` (default `""`), `Config.attendee_base_url: str` (default `"http://localhost:8000"`), `Config.attendee_websocket_port: int` (default `8765`), `Config.attendee_bot_name: str` (default `"Mia"`). Tasks 2, 4, and 5 read these fields.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
def test_from_env_defaults_attendee_settings_when_unset(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ATTENDEE_API_KEY", raising=False)
    monkeypatch.delenv("ATTENDEE_BASE_URL", raising=False)
    monkeypatch.delenv("ATTENDEE_WEBSOCKET_PORT", raising=False)
    monkeypatch.delenv("ATTENDEE_BOT_NAME", raising=False)

    config = Config.from_env()

    assert config.attendee_api_key == ""
    assert config.attendee_base_url == "http://localhost:8000"
    assert config.attendee_websocket_port == 8765
    assert config.attendee_bot_name == "Mia"


def test_from_env_respects_attendee_overrides(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ATTENDEE_API_KEY", "att-key")
    monkeypatch.setenv("ATTENDEE_BASE_URL", "http://example.com:9000")
    monkeypatch.setenv("ATTENDEE_WEBSOCKET_PORT", "9999")
    monkeypatch.setenv("ATTENDEE_BOT_NAME", "Custom Bot")

    config = Config.from_env()

    assert config.attendee_api_key == "att-key"
    assert config.attendee_base_url == "http://example.com:9000"
    assert config.attendee_websocket_port == 9999
    assert config.attendee_bot_name == "Custom Bot"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'attendee_api_key'`

- [ ] **Step 3: Add the fields to `Config`**

In `src/mia/config.py`, add these fields to the `Config` dataclass, after the existing `state_file` field:

```python
    attendee_api_key: str = ""
    attendee_base_url: str = "http://localhost:8000"
    attendee_websocket_port: int = 8765
    attendee_bot_name: str = "Mia"
```

In `Config.from_env()`, add these lines to the `return cls(...)` call, after the existing `fuzzy_threshold=...` line:

```python
            attendee_api_key=os.environ.get("ATTENDEE_API_KEY", ""),
            attendee_base_url=os.environ.get("ATTENDEE_BASE_URL", "http://localhost:8000"),
            attendee_websocket_port=int(os.environ.get("ATTENDEE_WEBSOCKET_PORT", "8765")),
            attendee_bot_name=os.environ.get("ATTENDEE_BOT_NAME", "Mia"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (all tests, including the two new ones)

- [ ] **Step 5: Commit**

```bash
git add src/mia/config.py tests/test_config.py
git commit -m "feat: add Attendee config fields"
```

---

### Task 2: Attendee REST client

**Files:**
- Create: `src/mia/attendee_client.py`
- Test: `tests/test_attendee_client.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `create_bot(base_url, api_key, meeting_url, websocket_url, bot_name, sample_rate=16000) -> str`, `bot_state(base_url, api_key, bot_id) -> str`, `wait_until_joined(base_url, api_key, bot_id, timeout_seconds=60.0, poll_interval_seconds=2.0) -> None`, `set_avatar_image(base_url, api_key, bot_id, image_path: Path) -> None`, `leave(base_url, api_key, bot_id) -> None`. Task 5 calls all five.

Add `requests>=2.31` to `pyproject.toml`'s `dependencies` list (alphabetically, after `python-dotenv>=1.0`) before starting this task.

- [ ] **Step 1: Add the new dependency and install it**

In `pyproject.toml`, add `"requests>=2.31",` to the `dependencies` list (keep the list's existing order; insert after `"python-dotenv>=1.0",`).

Run: `pip install -e ".[dev]"`

- [ ] **Step 2: Write the failing tests**

Create `tests/test_attendee_client.py`:

```python
from unittest.mock import MagicMock, patch

import pytest

from mia.attendee_client import bot_state, create_bot, leave, set_avatar_image, wait_until_joined


@patch("mia.attendee_client.requests.post")
def test_create_bot_posts_correct_payload_and_returns_id(mock_post):
    mock_post.return_value = MagicMock(status_code=200)
    mock_post.return_value.json.return_value = {"id": "bot_abc123"}

    bot_id = create_bot(
        base_url="http://localhost:8000",
        api_key="test-key",
        meeting_url="https://meet.google.com/xyz",
        websocket_url="ws://host.docker.internal:8765/audio",
        bot_name="Mia",
    )

    assert bot_id == "bot_abc123"
    mock_post.assert_called_once_with(
        "http://localhost:8000/api/v1/bots",
        headers={"Authorization": "Token test-key", "Content-Type": "application/json"},
        json={
            "meeting_url": "https://meet.google.com/xyz",
            "bot_name": "Mia",
            "websocket_settings": {
                "audio": {"url": "ws://host.docker.internal:8765/audio", "sample_rate": 16000},
            },
        },
        timeout=30,
    )


@patch("mia.attendee_client.requests.get")
def test_bot_state_returns_state_field(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {"state": "joining"}

    state = bot_state(base_url="http://localhost:8000", api_key="test-key", bot_id="bot_abc123")

    assert state == "joining"
    mock_get.assert_called_once_with(
        "http://localhost:8000/api/v1/bots/bot_abc123",
        headers={"Authorization": "Token test-key"},
        timeout=15,
    )


@patch("mia.attendee_client.requests.get")
def test_wait_until_joined_returns_when_state_is_joined_recording(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {"state": "joined_recording"}

    wait_until_joined(
        base_url="http://localhost:8000",
        api_key="test-key",
        bot_id="bot_abc123",
        timeout_seconds=5.0,
        poll_interval_seconds=0.01,
    )

    mock_get.assert_called_once()


@patch("mia.attendee_client.requests.get")
def test_wait_until_joined_raises_on_fatal_error_state(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {"state": "fatal_error"}

    with pytest.raises(RuntimeError, match="fatal_error"):
        wait_until_joined(
            base_url="http://localhost:8000",
            api_key="test-key",
            bot_id="bot_abc123",
            timeout_seconds=5.0,
            poll_interval_seconds=0.01,
        )


@patch("mia.attendee_client.requests.get")
def test_wait_until_joined_raises_timeout_error_when_never_joined(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {"state": "joining"}

    with pytest.raises(TimeoutError):
        wait_until_joined(
            base_url="http://localhost:8000",
            api_key="test-key",
            bot_id="bot_abc123",
            timeout_seconds=0.05,
            poll_interval_seconds=0.01,
        )


@patch("mia.attendee_client.requests.post")
def test_set_avatar_image_posts_base64_encoded_image(mock_post, tmp_path):
    mock_post.return_value = MagicMock(status_code=200)
    image_path = tmp_path / "avatar.png"
    image_path.write_bytes(b"fake-png-bytes")

    set_avatar_image(base_url="http://localhost:8000", api_key="test-key", bot_id="bot_abc123", image_path=image_path)

    mock_post.assert_called_once_with(
        "http://localhost:8000/api/v1/bots/bot_abc123/output_image",
        headers={"Authorization": "Token test-key", "Content-Type": "application/json"},
        json={"type": "image/png", "data": "ZmFrZS1wbmctYnl0ZXM="},
        timeout=30,
    )


@patch("mia.attendee_client.requests.post")
def test_leave_posts_to_leave_endpoint(mock_post):
    mock_post.return_value = MagicMock(status_code=200)

    leave(base_url="http://localhost:8000", api_key="test-key", bot_id="bot_abc123")

    mock_post.assert_called_once_with(
        "http://localhost:8000/api/v1/bots/bot_abc123/leave",
        headers={"Authorization": "Token test-key"},
        timeout=15,
    )
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_attendee_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.attendee_client'`

- [ ] **Step 4: Create `src/mia/attendee_client.py`**

```python
import base64
import time
from pathlib import Path

import requests

_TERMINAL_FAILURE_STATES = {"fatal_error", "ended"}


def create_bot(
    base_url: str,
    api_key: str,
    meeting_url: str,
    websocket_url: str,
    bot_name: str,
    sample_rate: int = 16000,
) -> str:
    response = requests.post(
        f"{base_url}/api/v1/bots",
        headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
        json={
            "meeting_url": meeting_url,
            "bot_name": bot_name,
            "websocket_settings": {
                "audio": {"url": websocket_url, "sample_rate": sample_rate},
            },
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["id"]


def bot_state(base_url: str, api_key: str, bot_id: str) -> str:
    response = requests.get(
        f"{base_url}/api/v1/bots/{bot_id}",
        headers={"Authorization": f"Token {api_key}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()["state"]


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
        if state == "joined_recording":
            return
        if state in _TERMINAL_FAILURE_STATES:
            raise RuntimeError(f"bot {bot_id} failed to join: state={state}")
        time.sleep(poll_interval_seconds)
    raise TimeoutError(f"bot {bot_id} did not reach joined_recording within {timeout_seconds}s")


def set_avatar_image(base_url: str, api_key: str, bot_id: str, image_path: Path) -> None:
    image_bytes = image_path.read_bytes()
    response = requests.post(
        f"{base_url}/api/v1/bots/{bot_id}/output_image",
        headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
        json={"type": "image/png", "data": base64.b64encode(image_bytes).decode("ascii")},
        timeout=30,
    )
    response.raise_for_status()


def leave(base_url: str, api_key: str, bot_id: str) -> None:
    response = requests.post(
        f"{base_url}/api/v1/bots/{bot_id}/leave",
        headers={"Authorization": f"Token {api_key}"},
        timeout=15,
    )
    response.raise_for_status()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_attendee_client.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/mia/attendee_client.py tests/test_attendee_client.py
git commit -m "feat: add Attendee REST client"
```

---

### Task 3: Pure audio-framing logic

**Files:**
- Create: `src/mia/audio/attendee_framing.py`
- Test: `tests/test_attendee_framing.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `FrameBuffer` class (`.push(chunk: bytes) -> None`, `.pull(num_bytes: int, timeout_seconds: float) -> bytes`), `chunk_pcm(pcm_audio: bytes, chunk_bytes: int) -> list[bytes]`, `build_bot_output_message(chunk: bytes, sample_rate: int) -> str`, `extract_mixed_audio_chunk(raw_message: str) -> bytes | None`, `paced_send(chunks, chunk_duration_seconds, send_fn, stop_event, sleep_fn=time.sleep) -> None`. Task 4 imports and uses all five.

This module contains every piece of the audio bridge's logic that doesn't need a real network connection to test, kept separate from the asyncio/websocket wiring in Task 4 so it can be tested fast and directly.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_attendee_framing.py`:

```python
import base64
import json
import threading
import time

from mia.audio.attendee_framing import (
    FrameBuffer,
    build_bot_output_message,
    chunk_pcm,
    extract_mixed_audio_chunk,
    paced_send,
)


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


def test_chunk_pcm_splits_into_fixed_size_pieces():
    assert chunk_pcm(b"0123456789", chunk_bytes=4) == [b"0123", b"4567", b"89"]


def test_chunk_pcm_empty_input_returns_empty_list():
    assert chunk_pcm(b"", chunk_bytes=4) == []


def test_build_bot_output_message_shape():
    message = build_bot_output_message(chunk=b"abc", sample_rate=16000)

    payload = json.loads(message)

    assert payload["trigger"] == "realtime_audio.bot_output"
    assert payload["data"]["sample_rate"] == 16000
    assert base64.b64decode(payload["data"]["chunk"]) == b"abc"


def test_extract_mixed_audio_chunk_decodes_correct_trigger():
    message = json.dumps(
        {
            "bot_id": "bot_123",
            "trigger": "realtime_audio.mixed",
            "data": {"chunk": base64.b64encode(b"hello").decode("ascii"), "sample_rate": 16000, "timestamp_ms": 1},
        }
    )

    assert extract_mixed_audio_chunk(message) == b"hello"


def test_extract_mixed_audio_chunk_ignores_other_triggers():
    message = json.dumps({"trigger": "realtime_audio.per_participant", "data": {"chunk": "x"}})

    assert extract_mixed_audio_chunk(message) is None


def test_extract_mixed_audio_chunk_ignores_unparseable_message():
    assert extract_mixed_audio_chunk("not json") is None


def test_paced_send_calls_send_fn_for_every_chunk_in_order():
    sent = []

    paced_send(
        chunks=[b"a", b"b", b"c"],
        chunk_duration_seconds=0.01,
        send_fn=sent.append,
        stop_event=threading.Event(),
        sleep_fn=lambda _seconds: None,
    )

    assert sent == [b"a", b"b", b"c"]


def test_paced_send_stops_early_when_stop_event_set_mid_stream():
    sent = []
    stop_event = threading.Event()

    def _send(chunk):
        sent.append(chunk)
        if chunk == b"b":
            stop_event.set()

    paced_send(
        chunks=[b"a", b"b", b"c", b"d"],
        chunk_duration_seconds=0.01,
        send_fn=_send,
        stop_event=stop_event,
        sleep_fn=lambda _seconds: None,
    )

    assert sent == [b"a", b"b"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_attendee_framing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.audio.attendee_framing'`

- [ ] **Step 3: Create `src/mia/audio/attendee_framing.py`**

```python
import base64
import json
import threading
import time
from collections.abc import Callable


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


def chunk_pcm(pcm_audio: bytes, chunk_bytes: int) -> list[bytes]:
    """Splits pcm_audio into chunk_bytes-sized pieces; the last piece may
    be shorter. Empty input returns an empty list."""
    return [pcm_audio[i : i + chunk_bytes] for i in range(0, len(pcm_audio), chunk_bytes)]


def build_bot_output_message(chunk: bytes, sample_rate: int) -> str:
    """The JSON message Attendee's realtime-audio websocket protocol
    expects for audio the bot should speak into the meeting."""
    return json.dumps(
        {
            "trigger": "realtime_audio.bot_output",
            "data": {
                "chunk": base64.b64encode(chunk).decode("ascii"),
                "sample_rate": sample_rate,
            },
        }
    )


def extract_mixed_audio_chunk(raw_message: str) -> bytes | None:
    """Parses one incoming websocket message and returns the decoded PCM
    bytes if it's a realtime_audio.mixed message, else None (any other
    trigger type, or an unparseable message, is ignored)."""
    try:
        payload = json.loads(raw_message)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("trigger") != "realtime_audio.mixed":
        return None
    chunk_b64 = payload.get("data", {}).get("chunk")
    if not chunk_b64:
        return None
    return base64.b64decode(chunk_b64)


def paced_send(
    chunks: list[bytes],
    chunk_duration_seconds: float,
    send_fn: Callable[[bytes], None],
    stop_event: threading.Event,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """Calls send_fn(chunk) for each chunk in order, sleeping
    chunk_duration_seconds between sends via sleep_fn (injectable so
    tests don't pay real wall-clock time). Stops before sending a chunk
    if stop_event is set -- this is what lets stop_playback() actually
    truncate unsent audio instead of only updating bookkeeping after
    everything has already been handed off."""
    for chunk in chunks:
        if stop_event.is_set():
            return
        send_fn(chunk)
        sleep_fn(chunk_duration_seconds)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_attendee_framing.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mia/audio/attendee_framing.py tests/test_attendee_framing.py
git commit -m "feat: add pure audio-framing logic for the Attendee bridge"
```

---

### Task 4: AttendeeAudioBridge (websocket server)

**Files:**
- Create: `src/mia/audio/attendee_bridge.py`
- Test: `tests/test_attendee_bridge.py`

**Interfaces:**
- Consumes: `FrameBuffer`, `chunk_pcm`, `build_bot_output_message`, `extract_mixed_audio_chunk`, `paced_send` from `mia.audio.attendee_framing` (Task 3).
- Produces: `AttendeeAudioBridge` class — `__init__(port: int, sample_rate: int = 16000)`, usable as a context manager (`__enter__`/`__exit__`), `.read_frame(frame_ms: int = 30) -> bytes`, `.start_playback(pcm_audio: bytes) -> None`, `.is_playback_active() -> bool`, `.stop_playback() -> None`. Task 5 constructs and uses this in place of `BlackHoleCapture` and the `audio/injection.py` functions.

Add `websockets>=12.0` to `pyproject.toml`'s `dependencies` list before starting this task.

- [ ] **Step 1: Add the new dependency and install it**

In `pyproject.toml`, add `"websockets>=12.0",` to the `dependencies` list. The list is in historical append order, not alphabetical -- add this as the new last entry (after `"requests>=2.31",`, which Task 2 added as the previous last entry).

Run: `pip install -e ".[dev]"`

- [ ] **Step 2: Write the failing tests**

Create `tests/test_attendee_bridge.py`. These tests exercise the bridge's logic directly (pushing synthetic data into its internal `FrameBuffer`, calling its playback methods) without opening a real client connection — the one exception is the last test, which verifies the real server can start and stop cleanly using an OS-assigned port (`port=0`), without sending any real messages over it. This matches the design's decision not to unit-test the live connection to Attendee itself, which is exercised manually instead.

```python
import time

from mia.audio.attendee_bridge import AttendeeAudioBridge


def test_read_frame_returns_pushed_audio_without_a_real_connection():
    bridge = AttendeeAudioBridge(port=0, sample_rate=16000)
    # 30ms at 16kHz mono 16-bit = 960 bytes
    bridge._frame_buffer.push(b"\x01\x02" * 480)

    frame = bridge.read_frame(frame_ms=30)

    assert frame == b"\x01\x02" * 480
    assert len(frame) == 960


def test_read_frame_pads_silence_when_no_audio_pushed():
    bridge = AttendeeAudioBridge(port=0, sample_rate=16000)

    frame = bridge.read_frame(frame_ms=30)

    assert frame == b"\x00" * 960


def test_is_playback_active_true_immediately_after_start_playback():
    bridge = AttendeeAudioBridge(port=0, sample_rate=16000)
    # 16000 samples/sec * 2 bytes/sample * 1 second = 32000 bytes
    bridge.start_playback(b"\x01\x02" * 16000)

    assert bridge.is_playback_active() is True


def test_is_playback_active_false_after_estimated_duration_elapses():
    bridge = AttendeeAudioBridge(port=0, sample_rate=16000)
    # 2 bytes of audio = 1 sample = 1/16000 second, effectively instant
    bridge.start_playback(b"\x01\x02")

    time.sleep(0.05)

    assert bridge.is_playback_active() is False


def test_stop_playback_marks_playback_inactive_immediately():
    bridge = AttendeeAudioBridge(port=0, sample_rate=16000)
    bridge.start_playback(b"\x01\x02" * 16000)  # ~1 second of audio
    assert bridge.is_playback_active() is True

    bridge.stop_playback()

    assert bridge.is_playback_active() is False


def test_start_playback_with_no_connection_does_not_raise():
    # _send() early-returns when self._connection is None -- start_playback
    # must not crash just because Attendee hasn't connected yet.
    bridge = AttendeeAudioBridge(port=0, sample_rate=16000)

    bridge.start_playback(b"\x01\x02" * 100)
    time.sleep(0.05)

    # No assertion beyond "did not raise" -- reaching this line is the test.


def test_enter_and_exit_start_and_stop_the_server_cleanly():
    # port=0 lets the OS assign any free port -- this test only checks
    # that startup/shutdown of the real asyncio server doesn't raise.
    bridge = AttendeeAudioBridge(port=0, sample_rate=16000)

    with bridge:
        assert bridge._server is not None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_attendee_bridge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.audio.attendee_bridge'`

- [ ] **Step 4: Create `src/mia/audio/attendee_bridge.py`**

```python
import asyncio
import threading
import time

import websockets

from mia.audio.attendee_framing import (
    FrameBuffer,
    build_bot_output_message,
    chunk_pcm,
    extract_mixed_audio_chunk,
    paced_send,
)

_CHUNK_MS = 40
_READ_TIMEOUT_MULTIPLIER = 2
_BYTES_PER_SAMPLE = 2  # 16-bit PCM


class AttendeeAudioBridge:
    """Local websocket server that Attendee's bot connects out to (the
    connection direction is inverted from the usual client/server
    relationship -- see the design spec). Bridges Attendee's realtime-audio
    protocol to the same read_frame()/start_playback()/is_playback_active()/
    stop_playback() interface BlackHoleCapture and audio/injection.py
    already provided, so main.py's call loop does not need to change."""

    def __init__(self, port: int, sample_rate: int = 16000):
        self._port = port
        self._sample_rate = sample_rate
        self._frame_buffer = FrameBuffer()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None
        self._thread: threading.Thread | None = None
        self._connection = None
        self._connection_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._playback_end_time = 0.0

    def __enter__(self) -> "AttendeeAudioBridge":
        ready = threading.Event()
        startup_error: list[BaseException] = []

        def _run_loop() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._server = self._loop.run_until_complete(
                    websockets.serve(self._handle_connection, "0.0.0.0", self._port)
                )
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
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)

    async def _handle_connection(self, websocket) -> None:
        with self._connection_lock:
            self._connection = websocket
        try:
            async for raw_message in websocket:
                chunk = extract_mixed_audio_chunk(raw_message)
                if chunk is not None:
                    self._frame_buffer.push(chunk)
        finally:
            with self._connection_lock:
                if self._connection is websocket:
                    self._connection = None

    def read_frame(self, frame_ms: int = 30) -> bytes:
        num_bytes = int(self._sample_rate * frame_ms / 1000) * _BYTES_PER_SAMPLE
        timeout_seconds = (frame_ms * _READ_TIMEOUT_MULTIPLIER) / 1000
        return self._frame_buffer.pull(num_bytes, timeout_seconds)

    def start_playback(self, pcm_audio: bytes) -> None:
        stop_event = threading.Event()
        self._stop_event = stop_event
        chunk_bytes = int(self._sample_rate * _CHUNK_MS / 1000) * _BYTES_PER_SAMPLE
        chunks = chunk_pcm(pcm_audio, chunk_bytes)
        duration_seconds = len(pcm_audio) / (self._sample_rate * _BYTES_PER_SAMPLE)
        self._playback_end_time = time.monotonic() + duration_seconds

        def _send(chunk: bytes) -> None:
            with self._connection_lock:
                connection = self._connection
            if connection is None or self._loop is None:
                return
            message = build_bot_output_message(chunk, self._sample_rate)
            asyncio.run_coroutine_threadsafe(connection.send(message), self._loop)

        threading.Thread(
            target=paced_send,
            args=(chunks, _CHUNK_MS / 1000, _send, stop_event),
            daemon=True,
        ).start()

    def is_playback_active(self) -> bool:
        return time.monotonic() < self._playback_end_time

    def stop_playback(self) -> None:
        self._stop_event.set()
        self._playback_end_time = time.monotonic()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_attendee_bridge.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/mia/audio/attendee_bridge.py tests/test_attendee_bridge.py
git commit -m "feat: add AttendeeAudioBridge websocket server"
```

---

### Task 5: Wire main.py to Attendee, retire the old join path

**Files:**
- Modify: `src/mia/main.py`
- Create: `assets/bot_avatar.png` (copied from `~/Downloads/Handwritten Script Lash Extensions Name Logo.png`)
- Delete: `src/mia/join_worker.py`, `src/mia/audio/capture.py`, `src/mia/audio/injection.py`
- Delete: `tests/test_join_worker.py`, `tests/test_audio_capture.py`, `tests/test_audio_injection.py` (if present -- check with `ls tests/ | grep -E "join_worker|audio_capture|audio_injection"` first; only delete files that actually exist)

**Interfaces:**
- Consumes: `create_bot`, `bot_state`, `wait_until_joined`, `set_avatar_image`, `leave` from `mia.attendee_client` (Task 2); `AttendeeAudioBridge` from `mia.audio.attendee_bridge` (Task 4); `Config.attendee_api_key`, `Config.attendee_base_url`, `Config.attendee_websocket_port`, `Config.attendee_bot_name` (Task 1).

This task has no new unit tests of its own -- `_handle_join` and `_run_call_loop` are orchestration functions already covered by the components they call, and their actual behavior can only be verified against a live Attendee instance and a real meeting (manual testing, same as how the original `JoinWorker`-based join path was verified). The full existing test suite must still pass unmodified, since this task does not touch any tested unit's public behavior other than what's described here.

- [ ] **Step 1: Copy the avatar asset**

```bash
mkdir -p assets
cp ~/Downloads/"Handwritten Script Lash Extensions Name Logo.png" assets/bot_avatar.png
```

- [ ] **Step 2: Update imports in `src/mia/main.py`**

Remove these two lines:

```python
from mia.audio.capture import BlackHoleCapture
from mia.audio.injection import is_playback_active, start_playback, stop_playback
```

Replace them with (alphabetical position: after `from mia.audio.vad import FrameVAD` stays where it is; these two new imports go where the removed ones were, keeping `mia.*` imports in alphabetical order):

```python
from mia import attendee_client
from mia.audio.attendee_bridge import AttendeeAudioBridge
```

Remove this line (no longer used anywhere in `main.py`):

```python
from mia.join_worker import JoinWorker
```

- [ ] **Step 3: Add the avatar path and bot-left-states constants**

Near the top of `src/mia/main.py`, after the existing `_SILENCE_FRAMES_TO_END_COMMAND = 24` constant, add:

```python
_BOT_AVATAR_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "bot_avatar.png"

# States that mean Attendee's bot is no longer usably present in the
# meeting -- checked alongside the existing tab-based leave signal so a
# bot removed by another participant (or a fatal error) is also noticed,
# not just a locally-closed Chrome tab.
_BOT_LEFT_STATES = {"fatal_error", "ended", "data_deleted"}
```

- [ ] **Step 4: Replace `_run_call_loop` entirely**

Replace the entire existing `_run_call_loop` function (from `def _run_call_loop(` through the closing `stt.stop()` line) with this complete version -- it has the new signature, the new bot-status leave-check, and every `capture`/`start_playback`/`is_playback_active`/`stop_playback` reference switched to the `bridge` parameter, with everything that was inside the removed `with BlackHoleCapture(...) as capture:` block now directly inside the `try:` block instead (one less indentation level than before):

```python
def _run_call_loop(
    config: Config,
    registry: ToolRegistry,
    anthropic_client: Anthropic,
    meet_url: str,
    bridge: AttendeeAudioBridge,
    bot_id: str,
) -> None:
    turn_state = TurnStateMachine()
    wake_word = WakeWordMatcher(config.wake_word, threshold=config.fuzzy_threshold)
    command_buffer = CommandBuffer()
    vad = FrameVAD(frame_ms=_FRAME_MS)
    history = ConversationHistory()
    # What mia is currently speaking, so on_transcript can tell her own TTS
    # looping back through capture (BlackHole routes injected audio back into
    # what mia captures, by design) apart from a real barge-in (Finding 1).
    current_speech: list[str | None] = [None]

    # NOTE: StreamingSTT (Task 15) dispatches on_transcript from a background
    # listener thread, while the loop below mutates the same turn_state /
    # command_buffer from this thread. Guard the check-then-act sequences on
    # both sides. The lock is only ever held for state transitions, never
    # across the LLM/TTS/injection calls, so the listener thread never blocks
    # on slow network work.
    lock = threading.Lock()

    # NOTE: TurnStateMachine starts in IDLE, and should_process_stt() is True
    # only in LISTENING. The draft gated the wake-word check behind
    # should_process_stt(), but the only IDLE -> LISTENING transition the
    # machine exposes is wake_word_detected() -- so nothing would ever have
    # left IDLE and the bot would have been deaf for the whole call. Entering
    # the call is what starts listening, so make that transition here.
    turn_state.wake_word_detected()

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
                # Stopping playback here is what makes this a real barge-in
                # when the wake word arrives during SPEAKING -- harmless no-op
                # if nothing is currently playing (the normal LISTENING case).
                bridge.stop_playback()
                turn_state.wake_word_detected()
                command_buffer.start()
                # NOTE: the draft dropped the fragment the wake word arrived
                # in. Deepgram emits one final transcript per speech segment,
                # so "Hey Bot, block thirty minutes at 3 PM" said in one
                # breath is a single fragment -- dropping it would discard the
                # command itself and leave an empty buffer. Keep the whole
                # fragment; the leading wake phrase is harmless context for
                # Claude's tool selection.
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
                    current_bot_state = attendee_client.bot_state(
                        base_url=config.attendee_base_url,
                        api_key=config.attendee_api_key,
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

            # A barge-in wake word already moved the machine out of
            # SPEAKING (and stopped playback) from on_transcript, on the
            # STT listener thread -- this only fires for a response that
            # finished on its own, uninterrupted.
            with lock:
                if turn_state.current() == TurnState.SPEAKING and not bridge.is_playback_active():
                    turn_state.finish_speaking()

            frame = bridge.read_frame(frame_ms=_FRAME_MS)

            if turn_state.should_process_stt():
                stt.send_frame(frame)

            # Called every iteration, gated or not: STT frames are now
            # blocked only during COMMAND_CAPTURED (the Claude + TTS
            # generation window), and Deepgram drops a silent connection
            # after ~10s. This is a no-op except during that window --
            # self-echo during SPEAKING is handled by content-based
            # filtering in on_transcript (is_self_echo), not by blocking
            # STT outright.
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
                            # Move out of LISTENING before the slow work
                            # below. This blocks STT for the
                            # Claude+TTS-generation window; self-echo once
                            # audio starts playing (SPEAKING) is instead
                            # handled by is_self_echo() filtering in
                            # on_transcript, not by blocking STT.
                            turn_state.command_captured()

            if not command_text:
                continue

            # NOTE: dispatch_command() only catches failures inside the
            # tool handler, so a Claude API error (or a TTS error) would
            # otherwise propagate out and end the meeting -- caught below
            # and recovered via abandon_turn() either way.
            #
            # start_speaking() no longer fires unconditionally up front:
            # SPEAKING now means "audio is playing" specifically (so a
            # barge-in wake word during SPEAKING has actual audio to
            # interrupt), so it's called right before start_playback()
            # instead, only on the path that actually produces audio.
            # The bare-wake-phrase path and any exception path use
            # abandon_turn() to recover straight to LISTENING, since
            # neither has audio to speak or cool down from. A normal,
            # uninterrupted response's SPEAKING -> COOLDOWN -> LISTENING
            # transition now happens from the loop's natural-completion
            # check above, not from a `finally` block here.
            try:
                # Spec: a false trigger must stay silent. If nothing was
                # said beyond the wake phrase itself, the wake word fired
                # on stray speech -- skip dispatch_command entirely so a
                # bare trigger costs no Claude call and consumes no slot
                # in the bounded conversation-memory window. A genuine
                # unrecognized command (real words after the wake phrase)
                # still reaches dispatch_command and gets its own spoken
                # fallback from there.
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
                        config.elevenlabs_api_key, result.confirmation
                    )
                    with lock:
                        turn_state.start_speaking()
                        current_speech[0] = result.confirmation
                        bridge.start_playback(audio)
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

- [ ] **Step 5: Replace `_handle_join`**

Replace the entire existing `_handle_join` function with:

```python
def _handle_join(
    config: Config,
    registry: ToolRegistry,
    anthropic_client: Anthropic,
    state: StateStore,
    meet_url: str,
) -> None:
    websocket_url = f"ws://host.docker.internal:{config.attendee_websocket_port}/audio"
    with AttendeeAudioBridge(port=config.attendee_websocket_port) as bridge:
        try:
            bot_id = attendee_client.create_bot(
                base_url=config.attendee_base_url,
                api_key=config.attendee_api_key,
                meeting_url=meet_url,
                websocket_url=websocket_url,
                bot_name=config.attendee_bot_name,
            )
            attendee_client.wait_until_joined(
                base_url=config.attendee_base_url,
                api_key=config.attendee_api_key,
                bot_id=bot_id,
            )
        except Exception as exc:
            # Spec ("Can't join"): log and skip; detection keeps running.
            # Leave the URL marked "skipped" so the next poll doesn't
            # immediately re-prompt for the same failing call.
            safe_log("error", "join failed", meeting_url=meet_url, error=str(exc))
            state.set_status(meet_url, "skipped")
            return

        attendee_client.set_avatar_image(
            base_url=config.attendee_base_url,
            api_key=config.attendee_api_key,
            bot_id=bot_id,
            image_path=_BOT_AVATAR_PATH,
        )

        safe_log("info", "joined meeting", meeting_url=meet_url)
        try:
            _run_call_loop(config, registry, anthropic_client, meet_url, bridge, bot_id)
        except Exception as exc:
            safe_log("error", "call loop failed", meeting_url=meet_url, error=str(exc))
        finally:
            try:
                attendee_client.leave(
                    base_url=config.attendee_base_url,
                    api_key=config.attendee_api_key,
                    bot_id=bot_id,
                )
            except Exception as exc:
                safe_log("error", "leave failed", meeting_url=meet_url, error=str(exc))
            state.clear(meet_url)
            safe_log("info", "left meeting", meeting_url=meet_url)
```

- [ ] **Step 6: Delete the retired files**

```bash
ls tests/ | grep -E "join_worker|audio_capture|audio_injection"
```

Delete `src/mia/join_worker.py`, `src/mia/audio/capture.py`, `src/mia/audio/injection.py`, and any test files the `ls` above found for them.

- [ ] **Step 7: Run the full test suite**

Run: `pytest -q`
Expected: PASS, same test count as before minus whatever tests existed for the three deleted files (check `git diff --stat` after this task to confirm no other test file's pass/fail count changed)

- [ ] **Step 8: Manual verification against the live local Attendee instance**

This is the step that actually proves the integration works — none of the automated tests exercise a real Google Meet call.

1. Confirm the Attendee Docker stack is running: `docker ps` should show `attendee-attendee-app-local-1` and friends `Up`.
2. Set `ATTENDEE_API_KEY` in `.env` to a real key from the Attendee dashboard (`http://localhost:8000` → API Keys).
3. Start a real Meet call, have `mia` (via `python -m mia.main`) detect it and prompt to join, accept the prompt.
4. Confirm in the actual meeting: the bot joins, shows the avatar image from `assets/bot_avatar.png`, and responds to "Hey Mia" commands with real tool calls and spoken confirmations, including a barge-in test (interrupt mid-response).
5. Confirm leaving the meeting (closing the Meet tab) makes the bot leave within a few seconds.

- [ ] **Step 9: Commit**

```bash
git add src/mia/main.py assets/bot_avatar.png
git add -u src/mia/join_worker.py src/mia/audio/capture.py src/mia/audio/injection.py
git commit -m "feat: wire main.py to Attendee, retire the Playwright/BlackHole join path"
```
