# Meet Live Voice Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python agent that joins a Google Meet call as a live participant (triggered by local mic+tab detection, not calendar polling), listens for a wake word, and executes real Google API calls (starting with blocking calendar time) with a spoken confirmation — no transcript or summary is ever persisted.

**Architecture:** A detection layer (mic activity + Chrome tab scan, optionally enriched by Calendar) fires a macOS Join/Skip notification; accepting it launches a Playwright-driven Chromium join. Once in the call, a live pipeline (VAD → streaming STT → fuzzy wake-word match → command buffer → Claude tool-calling → TTS) executes commands and speaks confirmations back into the call via a virtual microphone. A voice-turn state machine prevents the bot from hearing its own TTS output.

**Tech Stack:** Python 3.11+, Playwright, Deepgram (streaming STT), Anthropic Claude (tool-calling), ElevenLabs (TTS), Google Calendar API (google-api-python-client + google-auth-oauthlib), rapidfuzz (fuzzy matching), pyobjc-framework-CoreAudio (mic activity), silero-vad (turn-taking), Logfire (logging/tracing), pytest.

## Global Constraints

- Never persist a transcript, summary, or any meeting content to disk — speech is transient, used only for wake-word detection and command capture (per spec "Why not a transcript pipeline").
- Logfire calls must be fire-and-forget: a failure or unreachable Logfire backend must never raise into, or block, the live voice loop.
- The bot never auto-joins without an explicit Join tap on the macOS notification — mic+tab detection only ever proposes, never joins directly.
- The Meet Tab Detector's calendar lookup is optional enrichment only; every trigger path must work with zero calendar data.
- Chromium must be launched with `--use-fake-ui-for-media-stream` (no human present to accept the mic/camera permission prompt).
- All source lives under `src/mia/`; all tests under `tests/`, mirroring the source path.

---

## File Structure

```
pyproject.toml
.env.example
SETUP.md
setup_audio.sh
src/mia/
  __init__.py
  config.py                 # env-based settings, Task 1
  state.py                  # meeting dedup store, Task 2
  turn_state.py              # voice-turn state machine, Task 3
  wakeword.py                 # fuzzy wake-word matcher, Task 4
  command_buffer.py           # command capture buffer, Task 5
  logging_setup.py            # Logfire config + safe_log(), Task 13
  tools/
    __init__.py
    base.py                   # Tool/ToolRegistry, Task 6
    calendar_tool.py           # block_calendar_slot tool, Task 7
  detection/
    __init__.py
    trigger.py                 # combinator: signals -> decision, Task 8
    mic_monitor.py              # CoreAudio polling, Task 9
    tab_detector.py             # AppleScript Chrome tab scan, Task 10
    calendar_enricher.py        # Calendar API lookup, Task 11
  notify.py                    # terminal-notifier Join/Skip, Task 12
  audio/
    __init__.py
    vad.py                      # Silero VAD wrapper, Task 14
    capture.py                  # BlackHole capture, Task 14
    injection.py                 # TTS -> virtual mic, Task 16
  stt.py                        # Deepgram streaming wrapper, Task 15
  tts.py                         # ElevenLabs synthesis, Task 16
  llm.py                          # Claude tool-calling dispatch, Task 17
  join_worker.py                  # Playwright join/leave, Task 18
  main.py                          # orchestration entrypoint, Task 19
tests/
  test_config.py
  test_state.py
  test_turn_state.py
  test_wakeword.py
  test_command_buffer.py
  test_tools_base.py
  test_tools_calendar.py
  test_trigger.py
  test_calendar_enricher.py
  test_logging_setup.py
  test_audio_vad.py
  test_llm.py
```

---

### Task 1: Project scaffolding and config

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `src/mia/__init__.py`
- Create: `src/mia/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `mia.config.Config` — a frozen dataclass with fields `deepgram_api_key: str`, `anthropic_api_key: str`, `elevenlabs_api_key: str`, `google_client_id: str`, `google_client_secret: str`, `logfire_token: str`, `wake_word: str = "hey bot"`, `fuzzy_threshold: float = 0.75`, `state_file: Path = Path("~/.mia/state.json").expanduser()`. Class method `Config.from_env() -> Config` reads from `os.environ`, raising `MissingConfigError(key: str)` (subclass of `ValueError`) for any missing required key (all except `wake_word`, `fuzzy_threshold`, `state_file`, which have defaults).

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "mia"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "playwright>=1.45",
    "deepgram-sdk>=3.0",
    "anthropic>=0.34",
    "elevenlabs>=1.5",
    "google-api-python-client>=2.140",
    "google-auth-oauthlib>=1.2",
    "rapidfuzz>=3.9",
    "pyobjc-framework-CoreAudio>=10.3; sys_platform == 'darwin'",
    "silero-vad>=5.1",
    "logfire>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-mock>=3.14"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 2: Write `.env.example`**

```bash
DEEPGRAM_API_KEY=
ANTHROPIC_API_KEY=
ELEVENLABS_API_KEY=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
LOGFIRE_TOKEN=
# Optional overrides:
# WAKE_WORD="hey bot"
# FUZZY_THRESHOLD=0.75
```

- [ ] **Step 3: Create `src/mia/__init__.py`** (empty file)

- [ ] **Step 4: Write the failing test**

```python
# tests/test_config.py
import pytest
from mia.config import Config, MissingConfigError

REQUIRED_ENV = {
    "DEEPGRAM_API_KEY": "dg-key",
    "ANTHROPIC_API_KEY": "an-key",
    "ELEVENLABS_API_KEY": "el-key",
    "GOOGLE_CLIENT_ID": "gc-id",
    "GOOGLE_CLIENT_SECRET": "gc-secret",
    "LOGFIRE_TOKEN": "lf-token",
}

def test_from_env_reads_all_required_keys(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    config = Config.from_env()
    assert config.deepgram_api_key == "dg-key"
    assert config.anthropic_api_key == "an-key"
    assert config.elevenlabs_api_key == "el-key"
    assert config.google_client_id == "gc-id"
    assert config.google_client_secret == "gc-secret"
    assert config.logfire_token == "lf-token"
    assert config.wake_word == "hey bot"
    assert config.fuzzy_threshold == 0.75

def test_from_env_raises_on_missing_key(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("DEEPGRAM_API_KEY")
    with pytest.raises(MissingConfigError, match="DEEPGRAM_API_KEY"):
        Config.from_env()

def test_from_env_respects_wake_word_override(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("WAKE_WORD", "hey mia")
    assert Config.from_env().wake_word == "hey mia"
```

- [ ] **Step 5: Run test to verify it fails**

Run: `pip install -e ".[dev]" && pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.config'`

- [ ] **Step 6: Write `src/mia/config.py`**

```python
import os
from dataclasses import dataclass, field
from pathlib import Path

class MissingConfigError(ValueError):
    def __init__(self, key: str):
        super().__init__(f"missing required environment variable: {key}")
        self.key = key

_REQUIRED_KEYS = (
    "DEEPGRAM_API_KEY",
    "ANTHROPIC_API_KEY",
    "ELEVENLABS_API_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "LOGFIRE_TOKEN",
)

@dataclass(frozen=True)
class Config:
    deepgram_api_key: str
    anthropic_api_key: str
    elevenlabs_api_key: str
    google_client_id: str
    google_client_secret: str
    logfire_token: str
    wake_word: str = "hey bot"
    fuzzy_threshold: float = 0.75
    state_file: Path = field(default_factory=lambda: Path("~/.mia/state.json").expanduser())

    @classmethod
    def from_env(cls) -> "Config":
        values = {}
        for key in _REQUIRED_KEYS:
            value = os.environ.get(key)
            if not value:
                raise MissingConfigError(key)
            values[key] = value
        return cls(
            deepgram_api_key=values["DEEPGRAM_API_KEY"],
            anthropic_api_key=values["ANTHROPIC_API_KEY"],
            elevenlabs_api_key=values["ELEVENLABS_API_KEY"],
            google_client_id=values["GOOGLE_CLIENT_ID"],
            google_client_secret=values["GOOGLE_CLIENT_SECRET"],
            logfire_token=values["LOGFIRE_TOKEN"],
            wake_word=os.environ.get("WAKE_WORD", "hey bot"),
            fuzzy_threshold=float(os.environ.get("FUZZY_THRESHOLD", "0.75")),
        )
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .env.example src/mia/__init__.py src/mia/config.py tests/test_config.py
git commit -m "feat: add project scaffolding and env-based config"
```

---

### Task 2: Meeting dedup state store

**Files:**
- Create: `src/mia/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `mia.state.StateStore(path: Path)` with methods `status(meeting_url: str) -> str | None` (returns `"prompted"`, `"joined"`, `"skipped"`, or `None`), `set_status(meeting_url: str, status: str) -> None`, `clear(meeting_url: str) -> None`. Backed by a JSON file at `path`; creates parent directories and an empty file if missing. Later tasks (Task 8 trigger logic, Task 19 main loop) call `status()` to avoid re-prompting/re-joining the same open call.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_state.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.state'`

- [ ] **Step 3: Write `src/mia/state.py`**

```python
import json
from pathlib import Path

class StateStore:
    def __init__(self, path: Path):
        self._path = path

    def _read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text())

    def _write(self, data: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data))

    def status(self, meeting_url: str) -> str | None:
        return self._read().get(meeting_url)

    def set_status(self, meeting_url: str, status: str) -> None:
        data = self._read()
        data[meeting_url] = status
        self._write(data)

    def clear(self, meeting_url: str) -> None:
        data = self._read()
        data.pop(meeting_url, None)
        self._write(data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_state.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mia/state.py tests/test_state.py
git commit -m "feat: add meeting dedup state store"
```

---

### Task 3: Voice-turn state machine

**Files:**
- Create: `src/mia/turn_state.py`
- Test: `tests/test_turn_state.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `mia.turn_state.TurnState` (str enum): `IDLE`, `LISTENING`, `COMMAND_CAPTURED`, `SPEAKING`, `COOLDOWN`. `mia.turn_state.TurnStateMachine(cooldown_seconds: float = 1.0, clock: Callable[[], float] = time.monotonic)` with methods `current() -> TurnState`, `wake_word_detected() -> None` (IDLE/LISTENING → LISTENING, no-op if not idle/listening), `command_captured() -> None` (LISTENING → COMMAND_CAPTURED), `start_speaking() -> None` (COMMAND_CAPTURED → SPEAKING), `finish_speaking() -> None` (SPEAKING → COOLDOWN, records the clock time), `tick() -> None` (call periodically; transitions COOLDOWN → LISTENING once `cooldown_seconds` have elapsed), `should_process_stt() -> bool` (True only in `LISTENING`; used by Task 5's command buffer and Task 4's wake-word matcher to gate on self-echo per spec's Voice-turn state manager).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_turn_state.py
from mia.turn_state import TurnState, TurnStateMachine

def test_starts_idle():
    m = TurnStateMachine()
    assert m.current() == TurnState.IDLE

def test_wake_word_moves_idle_to_listening():
    m = TurnStateMachine()
    m.wake_word_detected()
    assert m.current() == TurnState.LISTENING

def test_full_cycle_gates_stt_correctly():
    clock = [0.0]
    m = TurnStateMachine(cooldown_seconds=1.0, clock=lambda: clock[0])
    m.wake_word_detected()
    assert m.should_process_stt() is True

    m.command_captured()
    assert m.current() == TurnState.COMMAND_CAPTURED
    assert m.should_process_stt() is False

    m.start_speaking()
    assert m.current() == TurnState.SPEAKING
    assert m.should_process_stt() is False

    m.finish_speaking()
    assert m.current() == TurnState.COOLDOWN
    assert m.should_process_stt() is False

    clock[0] = 0.5
    m.tick()
    assert m.current() == TurnState.COOLDOWN, "cooldown not elapsed yet"

    clock[0] = 1.1
    m.tick()
    assert m.current() == TurnState.LISTENING
    assert m.should_process_stt() is True

def test_wake_word_detected_is_noop_when_speaking():
    clock = [0.0]
    m = TurnStateMachine(clock=lambda: clock[0])
    m.wake_word_detected()
    m.command_captured()
    m.start_speaking()
    m.wake_word_detected()
    assert m.current() == TurnState.SPEAKING
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_turn_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.turn_state'`

- [ ] **Step 3: Write `src/mia/turn_state.py`**

```python
import time
from collections.abc import Callable
from enum import StrEnum

class TurnState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    COMMAND_CAPTURED = "command_captured"
    SPEAKING = "speaking"
    COOLDOWN = "cooldown"

class TurnStateMachine:
    def __init__(self, cooldown_seconds: float = 1.0, clock: Callable[[], float] = time.monotonic):
        self._state = TurnState.IDLE
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._cooldown_started_at: float | None = None

    def current(self) -> TurnState:
        return self._state

    def wake_word_detected(self) -> None:
        if self._state in (TurnState.IDLE, TurnState.LISTENING):
            self._state = TurnState.LISTENING

    def command_captured(self) -> None:
        if self._state == TurnState.LISTENING:
            self._state = TurnState.COMMAND_CAPTURED

    def start_speaking(self) -> None:
        if self._state == TurnState.COMMAND_CAPTURED:
            self._state = TurnState.SPEAKING

    def finish_speaking(self) -> None:
        if self._state == TurnState.SPEAKING:
            self._state = TurnState.COOLDOWN
            self._cooldown_started_at = self._clock()

    def tick(self) -> None:
        if self._state != TurnState.COOLDOWN or self._cooldown_started_at is None:
            return
        if self._clock() - self._cooldown_started_at >= self._cooldown_seconds:
            self._state = TurnState.LISTENING
            self._cooldown_started_at = None

    def should_process_stt(self) -> bool:
        return self._state == TurnState.LISTENING
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_turn_state.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mia/turn_state.py tests/test_turn_state.py
git commit -m "feat: add voice-turn state machine for self-echo gating"
```

---

### Task 4: Fuzzy wake-word matcher

**Files:**
- Create: `src/mia/wakeword.py`
- Test: `tests/test_wakeword.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (threshold comes from `Config.fuzzy_threshold`, Task 1, passed by the caller — this module has no dependency on `Config` itself).
- Produces: `mia.wakeword.WakeWordMatcher(wake_word: str, threshold: float = 0.75)` with method `matches(text: str) -> bool`. Normalizes both sides to lowercase, strips punctuation, and computes a similarity ratio via `rapidfuzz.fuzz.ratio` over a sliding window of `len(wake_word.split())`-word chunks of `text`, returning True if any window scores >= `threshold * 100`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wakeword.py
from mia.wakeword import WakeWordMatcher

def test_exact_match():
    m = WakeWordMatcher("hey bot")
    assert m.matches("hey bot block this slot") is True

def test_phonetic_mishearing_matches():
    m = WakeWordMatcher("hey bot")
    assert m.matches("hay bot can you help") is True
    assert m.matches("a bot please block this") is True

def test_unrelated_text_does_not_match():
    m = WakeWordMatcher("hey bot")
    assert m.matches("let's discuss the roadmap for next quarter") is False

def test_wake_word_embedded_mid_sentence_matches():
    m = WakeWordMatcher("hey bot")
    assert m.matches("so anyway hey bot block my 3pm please") is True

def test_threshold_is_configurable():
    lenient = WakeWordMatcher("hey bot", threshold=0.4)
    strict = WakeWordMatcher("hey bot", threshold=0.95)
    text = "yo boat"
    assert lenient.matches(text) is True
    assert strict.matches(text) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_wakeword.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.wakeword'`

- [ ] **Step 3: Write `src/mia/wakeword.py`**

```python
import re
import string

from rapidfuzz import fuzz

def _normalize(text: str) -> str:
    text = text.lower().translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", text).strip()

class WakeWordMatcher:
    def __init__(self, wake_word: str, threshold: float = 0.75):
        self._wake_word = _normalize(wake_word)
        self._window_size = len(self._wake_word.split())
        self._threshold_pct = threshold * 100

    def matches(self, text: str) -> bool:
        words = _normalize(text).split()
        if len(words) < self._window_size:
            return fuzz.ratio(" ".join(words), self._wake_word) >= self._threshold_pct
        for i in range(len(words) - self._window_size + 1):
            window = " ".join(words[i : i + self._window_size])
            if fuzz.ratio(window, self._wake_word) >= self._threshold_pct:
                return True
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_wakeword.py -v`
Expected: PASS (5 tests). If `test_threshold_is_configurable` is flaky against `rapidfuzz.fuzz.ratio`'s exact score for "yo boat" vs "hey bot", adjust the two threshold values in the test (not the implementation) so one is clearly above and one clearly below the actual computed ratio — print `fuzz.ratio("yo boat", "hey bot")` to find it.

- [ ] **Step 5: Commit**

```bash
git add src/mia/wakeword.py tests/test_wakeword.py
git commit -m "feat: add fuzzy wake-word matcher"
```

---

### Task 5: Command buffer

**Files:**
- Create: `src/mia/command_buffer.py`
- Test: `tests/test_command_buffer.py`

**Interfaces:**
- Consumes: nothing from earlier tasks directly (integrated with `TurnStateMachine` and VAD by Task 19's main loop, not by this module).
- Produces: `mia.command_buffer.CommandBuffer()` with methods `start() -> None` (begin capturing, clears any prior text), `append(text_fragment: str) -> None` (append incoming STT text while capturing; no-op if not capturing), `on_silence() -> str | None` (call when VAD reports silence; stops capturing and returns the accumulated command text, stripped, or `None` if nothing was captured or capture wasn't active), `is_capturing() -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_command_buffer.py
from mia.command_buffer import CommandBuffer

def test_not_capturing_initially():
    buf = CommandBuffer()
    assert buf.is_capturing() is False

def test_start_then_append_then_silence_returns_command():
    buf = CommandBuffer()
    buf.start()
    assert buf.is_capturing() is True
    buf.append("block my")
    buf.append(" three pm slot")
    result = buf.on_silence()
    assert result == "block my three pm slot"
    assert buf.is_capturing() is False

def test_append_before_start_is_ignored():
    buf = CommandBuffer()
    buf.append("ignored text")
    assert buf.on_silence() is None

def test_on_silence_without_start_returns_none():
    buf = CommandBuffer()
    assert buf.on_silence() is None

def test_start_clears_previous_command():
    buf = CommandBuffer()
    buf.start()
    buf.append("first command")
    buf.on_silence()
    buf.start()
    result = buf.on_silence()
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_command_buffer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.command_buffer'`

- [ ] **Step 3: Write `src/mia/command_buffer.py`**

```python
class CommandBuffer:
    def __init__(self):
        self._capturing = False
        self._fragments: list[str] = []

    def is_capturing(self) -> bool:
        return self._capturing

    def start(self) -> None:
        self._capturing = True
        self._fragments = []

    def append(self, text_fragment: str) -> None:
        if self._capturing:
            self._fragments.append(text_fragment)

    def on_silence(self) -> str | None:
        if not self._capturing:
            return None
        self._capturing = False
        text = "".join(self._fragments).strip()
        return text or None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_command_buffer.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mia/command_buffer.py tests/test_command_buffer.py
git commit -m "feat: add command buffer for wake-to-silence capture"
```

---

### Task 6: Tool registry framework

**Files:**
- Create: `src/mia/tools/__init__.py`
- Create: `src/mia/tools/base.py`
- Test: `tests/test_tools_base.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `mia.tools.base.Tool` — a dataclass with `name: str`, `description: str`, `input_schema: dict` (JSON Schema, Anthropic tool-calling format), `handler: Callable[[dict], str]` (takes parsed tool-call args, returns a short confirmation string spoken by TTS). `mia.tools.base.ToolRegistry` with methods `register(tool: Tool) -> None` (raises `ValueError` on duplicate name), `get(name: str) -> Tool | None`, `anthropic_tool_specs() -> list[dict]` (returns `[{"name":, "description":, "input_schema":}, ...]` for every registered tool, in the exact shape the Anthropic Messages API `tools` parameter expects — consumed directly by Task 17's `llm.py`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools_base.py
import pytest
from mia.tools.base import Tool, ToolRegistry

def make_tool(name="noop"):
    return Tool(
        name=name,
        description="does nothing",
        input_schema={"type": "object", "properties": {}},
        handler=lambda args: "done",
    )

def test_register_and_get():
    reg = ToolRegistry()
    tool = make_tool()
    reg.register(tool)
    assert reg.get("noop") is tool

def test_get_unknown_returns_none():
    reg = ToolRegistry()
    assert reg.get("missing") is None

def test_duplicate_registration_raises():
    reg = ToolRegistry()
    reg.register(make_tool())
    with pytest.raises(ValueError, match="noop"):
        reg.register(make_tool())

def test_anthropic_tool_specs_shape():
    reg = ToolRegistry()
    reg.register(make_tool("block_calendar_slot"))
    specs = reg.anthropic_tool_specs()
    assert specs == [{
        "name": "block_calendar_slot",
        "description": "does nothing",
        "input_schema": {"type": "object", "properties": {}},
    }]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tools_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.tools'`

- [ ] **Step 3: Create `src/mia/tools/__init__.py`** (empty file)

- [ ] **Step 4: Write `src/mia/tools/base.py`**

```python
from collections.abc import Callable
from dataclasses import dataclass

@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], str]

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def anthropic_tool_specs(self) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in self._tools.values()
        ]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_tools_base.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add src/mia/tools/__init__.py src/mia/tools/base.py tests/test_tools_base.py
git commit -m "feat: add tool registry framework"
```

---

### Task 7: Calendar tool (`block_calendar_slot`)

**Files:**
- Create: `src/mia/tools/calendar_tool.py`
- Test: `tests/test_tools_calendar.py`

**Interfaces:**
- Consumes: `mia.tools.base.Tool` (Task 6).
- Produces: `mia.tools.calendar_tool.build_calendar_tool(calendar_service) -> Tool`, where `calendar_service` is an authenticated `googleapiclient.discovery.Resource` for the Calendar API v3 (`build("calendar", "v3", credentials=...)`, wired up by Task 19's `main.py`). The returned `Tool.name` is `"block_calendar_slot"`, `input_schema` requires `start_iso: string` (ISO 8601 datetime), `duration_minutes: integer`, `title: string`. The handler calls `calendar_service.events().insert(calendarId="primary", body=...).execute()` and returns a confirmation string like `"Blocked 30 minutes starting 3:00 PM for 'Focus time'."`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools_calendar.py
from unittest.mock import MagicMock

from mia.tools.calendar_tool import build_calendar_tool

def test_tool_metadata():
    tool = build_calendar_tool(MagicMock())
    assert tool.name == "block_calendar_slot"
    assert set(tool.input_schema["required"]) == {"start_iso", "duration_minutes", "title"}

def test_handler_creates_event_and_confirms():
    service = MagicMock()
    execute_mock = service.events.return_value.insert.return_value.execute
    execute_mock.return_value = {"id": "evt123"}

    tool = build_calendar_tool(service)
    result = tool.handler({
        "start_iso": "2026-08-12T15:00:00-07:00",
        "duration_minutes": 30,
        "title": "Focus time",
    })

    service.events.return_value.insert.assert_called_once()
    _, kwargs = service.events.return_value.insert.call_args
    assert kwargs["calendarId"] == "primary"
    assert kwargs["body"]["summary"] == "Focus time"
    assert kwargs["body"]["start"]["dateTime"] == "2026-08-12T15:00:00-07:00"
    assert kwargs["body"]["end"]["dateTime"] == "2026-08-12T15:30:00-07:00"
    assert "Focus time" in result
    assert "30 minutes" in result

def test_handler_surfaces_api_error_as_exception():
    service = MagicMock()
    service.events.return_value.insert.return_value.execute.side_effect = RuntimeError("api down")

    tool = build_calendar_tool(service)
    try:
        tool.handler({"start_iso": "2026-08-12T15:00:00-07:00", "duration_minutes": 30, "title": "x"})
        assert False, "expected RuntimeError to propagate"
    except RuntimeError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tools_calendar.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.tools.calendar_tool'`

- [ ] **Step 3: Write `src/mia/tools/calendar_tool.py`**

```python
from datetime import datetime, timedelta

from mia.tools.base import Tool

_SCHEMA = {
    "type": "object",
    "properties": {
        "start_iso": {"type": "string", "description": "ISO 8601 start datetime, e.g. 2026-08-12T15:00:00-07:00"},
        "duration_minutes": {"type": "integer", "description": "Length of the block in minutes"},
        "title": {"type": "string", "description": "What to call the calendar event"},
    },
    "required": ["start_iso", "duration_minutes", "title"],
}

def build_calendar_tool(calendar_service) -> Tool:
    def handler(args: dict) -> str:
        start = datetime.fromisoformat(args["start_iso"])
        end = start + timedelta(minutes=args["duration_minutes"])
        body = {
            "summary": args["title"],
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        }
        calendar_service.events().insert(calendarId="primary", body=body).execute()
        time_str = start.strftime("%-I:%M %p")
        return f"Blocked {args['duration_minutes']} minutes starting {time_str} for '{args['title']}'."

    return Tool(
        name="block_calendar_slot",
        description="Create a calendar event to block time on the user's primary calendar.",
        input_schema=_SCHEMA,
        handler=handler,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tools_calendar.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mia/tools/calendar_tool.py tests/test_tools_calendar.py
git commit -m "feat: add block_calendar_slot tool"
```

---

### Task 8: Trigger decision logic

**Files:**
- Create: `src/mia/detection/__init__.py`
- Create: `src/mia/detection/trigger.py`
- Test: `tests/test_trigger.py`

**Interfaces:**
- Consumes: `mia.state.StateStore` (Task 2).
- Produces: `mia.detection.trigger.TriggerDecision` — a dataclass with `should_prompt: bool`, `meeting_url: str | None`, `display_title: str | None`. `mia.detection.trigger.decide(*, mic_active: bool, meet_tab_url: str | None, calendar_title: str | None, state: StateStore) -> TriggerDecision`. Logic: if `mic_active` is False or `meet_tab_url` is None → `should_prompt=False`. Else, if `state.status(meet_tab_url)` is already `"prompted"`, `"joined"`, or `"skipped"` → `should_prompt=False` (dedup — the real Mic Monitor (Task 9) and Tab Detector (Task 10) poll continuously, so this prevents re-prompting for a call already handled). Otherwise `should_prompt=True`, `meeting_url=meet_tab_url`, `display_title=calendar_title` if given, else the generic string `f"this Meet call ({meet_tab_url})"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trigger.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trigger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.detection'`

- [ ] **Step 3: Create `src/mia/detection/__init__.py`** (empty file)

- [ ] **Step 4: Write `src/mia/detection/trigger.py`**

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_trigger.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add src/mia/detection/__init__.py src/mia/detection/trigger.py tests/test_trigger.py
git commit -m "feat: add trigger decision combinator"
```

---

### Task 9: Mic Activity Monitor (CoreAudio)

**Files:**
- Create: `src/mia/detection/mic_monitor.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `mia.detection.mic_monitor.is_mic_active() -> bool`. Reads `kAudioDevicePropertyDeviceIsRunningSomewhere` on the default input device via `CoreAudio` (through `pyobjc-framework-CoreAudio`). Consumed by Task 19's main loop as the `mic_active` argument to Task 8's `decide()`. No capture permission is required — this is a status query, not audio access.

This task has no automated test: it's a thin binding to a live macOS system API with no meaningful way to fake `pyobjc`'s `CoreAudio` bridge without the test degenerating into asserting the mock was called, which would tell us nothing about correctness. Validate manually instead.

- [ ] **Step 1: Write `src/mia/detection/mic_monitor.py`**

```python
import CoreAudio

def _default_input_device_id() -> int:
    address = CoreAudio.AudioObjectPropertyAddress(
        CoreAudio.kAudioHardwarePropertyDefaultInputDevice,
        CoreAudio.kAudioObjectPropertyScopeGlobal,
        CoreAudio.kAudioObjectPropertyElementMain,
    )
    _, device_id = CoreAudio.AudioObjectGetPropertyData(
        CoreAudio.kAudioObjectSystemObject, address, 0, None, 4, None
    )
    return device_id

def is_mic_active() -> bool:
    device_id = _default_input_device_id()
    address = CoreAudio.AudioObjectPropertyAddress(
        CoreAudio.kAudioDevicePropertyDeviceIsRunningSomewhere,
        CoreAudio.kAudioObjectPropertyScopeGlobal,
        CoreAudio.kAudioObjectPropertyElementMain,
    )
    _, is_running = CoreAudio.AudioObjectGetPropertyData(
        device_id, address, 0, None, 4, None
    )
    return bool(is_running)
```

- [ ] **Step 2: Manual verification**

Run: `python -c "from mia.detection.mic_monitor import is_mic_active; print(is_mic_active())"` with no app using the mic.
Expected: prints `False`.

Then, while the script is running in a loop (`python -c "import time; from mia.detection.mic_monitor import is_mic_active; [print(is_mic_active()) or time.sleep(1) for _ in range(15)]"`), open QuickTime Player, start a new Audio Recording (don't need to actually record), and confirm the printed value flips to `True` within a couple of polls, then back to `False` after closing it.

- [ ] **Step 3: Commit**

```bash
git add src/mia/detection/mic_monitor.py
git commit -m "feat: add CoreAudio mic activity monitor"
```

---

### Task 10: Meet Tab Detector (AppleScript)

**Files:**
- Create: `src/mia/detection/tab_detector.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `mia.detection.tab_detector.find_active_meet_tab() -> str | None`. Shells out to `osascript` to enumerate open Chrome tab URLs, returns the first one matching the pattern `meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}` (an active-call URL, excluding the bare homepage), or `None` if no Chrome window/tab matches or Chrome isn't running. Consumed by Task 19's main loop as the `meet_tab_url` argument to Task 8's `decide()`.

This task has no automated test for the same reason as Task 9 — it shells out to `osascript`, which requires a real Chrome process and the Automation permission grant to mean anything. Validate manually.

- [ ] **Step 1: Write `src/mia/detection/tab_detector.py`**

```python
import re
import subprocess

_MEET_CALL_URL_RE = re.compile(r"https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}")

_APPLESCRIPT = """
tell application "Google Chrome"
    if not running then return ""
    set urlList to {}
    repeat with w in windows
        repeat with t in tabs of w
            copy (URL of t) to end of urlList
        end repeat
    end repeat
    return urlList
end tell
"""

def find_active_meet_tab() -> str | None:
    result = subprocess.run(
        ["osascript", "-e", _APPLESCRIPT],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        return None
    for url in result.stdout.split(", "):
        match = _MEET_CALL_URL_RE.match(url.strip())
        if match:
            return match.group(0)
    return None
```

- [ ] **Step 2: Manual verification**

Run: `python -c "from mia.detection.tab_detector import find_active_meet_tab; print(find_active_meet_tab())"` with Chrome closed.
Expected: prints `None`.

Grant Automation permission when macOS prompts on first run (System Settings → Privacy & Security → Automation → allow Terminal/your app to control Google Chrome). Open Chrome to `https://meet.google.com` (homepage, no call) and re-run — expect `None`. Start or join an actual Meet call in a tab and re-run — expect the call's URL printed.

- [ ] **Step 3: Commit**

```bash
git add src/mia/detection/tab_detector.py
git commit -m "feat: add AppleScript-based Meet tab detector"
```

---

### Task 11: Calendar Enricher

**Files:**
- Create: `src/mia/detection/calendar_enricher.py`
- Test: `tests/test_calendar_enricher.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (takes an authenticated Calendar `Resource`, same as Task 7).
- Produces: `mia.detection.calendar_enricher.find_current_meeting_title(calendar_service, *, now: datetime, meet_url: str) -> str | None`. Queries `calendar_service.events().list(calendarId="primary", timeMin=<now - 10min>, timeMax=<now + 10min>, singleEvents=True).execute()`, and returns the `summary` of the first event whose `hangoutLink` matches `meet_url`, or `None` if no event matches (including when the Calendar API call itself raises — caught and treated as "no match," per spec: calendar access is optional enrichment, never required). Consumed by Task 19's main loop to build the `calendar_title` argument to Task 8's `decide()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calendar_enricher.py
from datetime import datetime, timezone
from unittest.mock import MagicMock

from mia.detection.calendar_enricher import find_current_meeting_title

NOW = datetime(2026, 8, 12, 15, 0, tzinfo=timezone.utc)
URL = "https://meet.google.com/abc-defg-hij"

def test_returns_title_when_event_matches():
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {
        "items": [{"summary": "Standup", "hangoutLink": URL}]
    }
    assert find_current_meeting_title(service, now=NOW, meet_url=URL) == "Standup"

def test_returns_none_when_no_event_matches():
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {
        "items": [{"summary": "Unrelated", "hangoutLink": "https://meet.google.com/xxx-yyyy-zzz"}]
    }
    assert find_current_meeting_title(service, now=NOW, meet_url=URL) is None

def test_returns_none_when_no_events():
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {"items": []}
    assert find_current_meeting_title(service, now=NOW, meet_url=URL) is None

def test_returns_none_when_api_raises():
    service = MagicMock()
    service.events.return_value.list.return_value.execute.side_effect = RuntimeError("api down")
    assert find_current_meeting_title(service, now=NOW, meet_url=URL) is None

def test_queries_within_ten_minute_window():
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {"items": []}
    find_current_meeting_title(service, now=NOW, meet_url=URL)
    _, kwargs = service.events.return_value.list.call_args
    assert kwargs["timeMin"] == "2026-08-12T14:50:00+00:00"
    assert kwargs["timeMax"] == "2026-08-12T15:10:00+00:00"
    assert kwargs["singleEvents"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_calendar_enricher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.detection.calendar_enricher'`

- [ ] **Step 3: Write `src/mia/detection/calendar_enricher.py`**

```python
from datetime import datetime, timedelta

def find_current_meeting_title(calendar_service, *, now: datetime, meet_url: str) -> str | None:
    try:
        response = calendar_service.events().list(
            calendarId="primary",
            timeMin=(now - timedelta(minutes=10)).isoformat(),
            timeMax=(now + timedelta(minutes=10)).isoformat(),
            singleEvents=True,
        ).execute()
    except Exception:
        return None

    for event in response.get("items", []):
        if event.get("hangoutLink") == meet_url:
            return event.get("summary")
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_calendar_enricher.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mia/detection/calendar_enricher.py tests/test_calendar_enricher.py
git commit -m "feat: add optional calendar title enrichment"
```

---

### Task 12: Join/Skip notification

**Files:**
- Create: `src/mia/notify.py`
- Create: `setup_audio.sh` (extended in this task to also `brew install terminal-notifier`; see Step 3)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `mia.notify.NotificationResult` (str enum: `JOIN`, `SKIP`, `TIMEOUT`). `mia.notify.prompt_join(title: str, timeout_seconds: int = 120) -> NotificationResult`. Shells out to `terminal-notifier -message <title> -actions "Join,Skip" -timeout <timeout_seconds>`, parses stdout (`terminal-notifier` prints the chosen action string, or nothing/`"Timeout"` if none chosen), and maps it to the enum. Consumed by Task 19's main loop after `decide()` returns `should_prompt=True`.

This task has no automated test for the terminal-notifier subprocess call itself (same live-dependency reasoning as Tasks 9–10), but the stdout-parsing logic is pure and is unit tested by extracting it into a small pure function.

- [ ] **Step 1: Write the failing test** (for the pure parsing function only)

```python
# tests/test_notify.py
from mia.notify import NotificationResult, _parse_terminal_notifier_output

def test_parses_join():
    assert _parse_terminal_notifier_output("Join\n") == NotificationResult.JOIN

def test_parses_skip():
    assert _parse_terminal_notifier_output("Skip\n") == NotificationResult.SKIP

def test_parses_empty_as_timeout():
    assert _parse_terminal_notifier_output("") == NotificationResult.TIMEOUT

def test_parses_timeout_string_as_timeout():
    assert _parse_terminal_notifier_output("*Timeout*\n") == NotificationResult.TIMEOUT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_notify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.notify'`

- [ ] **Step 3: Write `src/mia/notify.py`**

```python
import subprocess
from enum import StrEnum

class NotificationResult(StrEnum):
    JOIN = "join"
    SKIP = "skip"
    TIMEOUT = "timeout"

def _parse_terminal_notifier_output(output: str) -> NotificationResult:
    cleaned = output.strip()
    if cleaned == "Join":
        return NotificationResult.JOIN
    if cleaned == "Skip":
        return NotificationResult.SKIP
    return NotificationResult.TIMEOUT

def prompt_join(title: str, timeout_seconds: int = 120) -> NotificationResult:
    result = subprocess.run(
        [
            "terminal-notifier",
            "-title", "mia",
            "-message", f"Join {title}?",
            "-actions", "Join,Skip",
            "-timeout", str(timeout_seconds),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_seconds + 5,
    )
    return _parse_terminal_notifier_output(result.stdout)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_notify.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Append to `setup_audio.sh`** (create the file if it doesn't exist yet, with a shebang; this step only adds the notifier dependency — full BlackHole install content is written in Task 14)

```bash
brew install terminal-notifier
```

- [ ] **Step 6: Manual verification**

Run: `python -c "from mia.notify import prompt_join; print(prompt_join('Standup'))"`, click "Join" on the notification when it appears.
Expected: prints `NotificationResult.JOIN`.

- [ ] **Step 7: Commit**

```bash
git add src/mia/notify.py tests/test_notify.py setup_audio.sh
git commit -m "feat: add Join/Skip desktop notification"
```

---

### Task 13: Logging setup (Logfire)

**Files:**
- Create: `src/mia/logging_setup.py`
- Test: `tests/test_logging_setup.py`

**Interfaces:**
- Consumes: `Config.logfire_token` (Task 1).
- Produces: `mia.logging_setup.configure(config: Config) -> None` (calls `logfire.configure(token=config.logfire_token)`; called once at startup by Task 19's `main.py`). `mia.logging_setup.safe_log(level: str, message: str, **fields) -> None` — calls the matching `logfire.<level>(message, **fields)`, catching and swallowing any exception (printing a one-line warning to stderr instead) so a Logfire outage never propagates into the caller, per the Global Constraints. `level` must be one of `"info"`, `"warning"`, `"error"`; invalid levels raise `ValueError` immediately (a caller bug, not a runtime/network failure, so this should NOT be swallowed).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_logging_setup.py
import pytest

from mia.logging_setup import safe_log

def test_safe_log_calls_logfire(mocker):
    mock_info = mocker.patch("logfire.info")
    safe_log("info", "meeting joined", meeting_id="abc")
    mock_info.assert_called_once_with("meeting joined", meeting_id="abc")

def test_safe_log_swallows_logfire_exception(mocker, capsys):
    mocker.patch("logfire.error", side_effect=RuntimeError("network down"))
    safe_log("error", "tool failed")  # must not raise
    assert "network down" in capsys.readouterr().err

def test_safe_log_rejects_invalid_level():
    with pytest.raises(ValueError, match="debug"):
        safe_log("debug", "not a supported level")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_logging_setup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.logging_setup'`

- [ ] **Step 3: Write `src/mia/logging_setup.py`**

```python
import sys

import logfire

from mia.config import Config

_LEVELS = {"info": logfire.info, "warning": logfire.warn, "error": logfire.error}

def configure(config: Config) -> None:
    logfire.configure(token=config.logfire_token)

def safe_log(level: str, message: str, **fields) -> None:
    if level not in _LEVELS:
        raise ValueError(f"unsupported log level: {level}")
    try:
        _LEVELS[level](message, **fields)
    except Exception as exc:
        print(f"mia: logfire call failed ({exc}); continuing", file=sys.stderr)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_logging_setup.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mia/logging_setup.py tests/test_logging_setup.py
git commit -m "feat: add fail-safe Logfire logging wrapper"
```

---

### Task 14: Local VAD and audio capture

**Files:**
- Create: `src/mia/audio/__init__.py`
- Create: `src/mia/audio/vad.py`
- Create: `src/mia/audio/capture.py`
- Create/overwrite: `setup_audio.sh` (full version)
- Create: `SETUP.md`
- Test: `tests/test_audio_vad.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `mia.audio.vad.FrameVAD(sample_rate: int = 16000, frame_ms: int = 30)` with method `is_speech(frame: bytes) -> bool` (wraps `silero-vad`'s frame-level inference; `frame` is raw 16-bit PCM mono audio of exactly `frame_ms` milliseconds). `mia.audio.capture.BlackHoleCapture(device_name: str = "BlackHole 2ch")` with methods `__enter__`/`__exit__` (opens/closes a `sounddevice.RawInputStream` on the named device) and `read_frame(frame_ms: int = 30) -> bytes`. Task 19's main loop feeds `capture.read_frame()` output into both `vad.is_speech()` and Task 15's STT stream.

- [ ] **Step 1: Write the failing test** (VAD only — capture requires a real BlackHole device, verified manually)

```python
# tests/test_audio_vad.py
import wave
from pathlib import Path

import pytest

from mia.audio.vad import FrameVAD

FIXTURES = Path(__file__).parent / "fixtures"

def _read_frames(wav_path: Path, frame_ms: int = 30):
    with wave.open(str(wav_path), "rb") as wf:
        assert wf.getframerate() == 16000
        assert wf.getsampwidth() == 2
        assert wf.getnchannels() == 1
        frame_bytes = int(16000 * frame_ms / 1000) * 2
        data = wf.readframes(wf.getnframes())
    return [data[i : i + frame_bytes] for i in range(0, len(data) - frame_bytes + 1, frame_bytes)]

@pytest.mark.skipif(not (FIXTURES / "speech.wav").exists(), reason="fixture not recorded yet")
def test_detects_speech_frames():
    vad = FrameVAD()
    frames = _read_frames(FIXTURES / "speech.wav")
    assert any(vad.is_speech(f) for f in frames)

@pytest.mark.skipif(not (FIXTURES / "silence.wav").exists(), reason="fixture not recorded yet")
def test_silence_has_no_speech_frames():
    vad = FrameVAD()
    frames = _read_frames(FIXTURES / "silence.wav")
    assert not any(vad.is_speech(f) for f in frames)
```

- [ ] **Step 2: Record the two fixtures**

Run (from repo root, needs a working mic): `mkdir -p tests/fixtures && python -c "
import sounddevice as sd, scipy.io.wavfile as wav
print('recording 2s of you speaking...')
audio = sd.rec(int(2*16000), samplerate=16000, channels=1, dtype='int16'); sd.wait()
wav.write('tests/fixtures/speech.wav', 16000, audio)
print('recording 2s of silence...')
audio = sd.rec(int(2*16000), samplerate=16000, channels=1, dtype='int16'); sd.wait()
wav.write('tests/fixtures/silence.wav', 16000, audio)
"` — speak clearly during the first recording, stay silent during the second.

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_audio_vad.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.audio'`

- [ ] **Step 4: Create `src/mia/audio/__init__.py`** (empty file)

- [ ] **Step 5: Write `src/mia/audio/vad.py`**

```python
from silero_vad import load_silero_vad, VADIterator

class FrameVAD:
    def __init__(self, sample_rate: int = 16000, frame_ms: int = 30):
        self._model = load_silero_vad()
        self._sample_rate = sample_rate
        self._frame_ms = frame_ms

    def is_speech(self, frame: bytes) -> bool:
        import numpy as np
        import torch

        audio = np.frombuffer(frame, dtype="int16").astype("float32") / 32768.0
        tensor = torch.from_numpy(audio)
        prob = self._model(tensor, self._sample_rate).item()
        return prob > 0.5
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_audio_vad.py -v`
Expected: PASS (2 tests). If `test_silence_has_no_speech_frames` fails because of room-tone false positives, re-record `silence.wav` in a quieter room rather than lowering the `0.5` threshold in the implementation.

- [ ] **Step 7: Write `src/mia/audio/capture.py`** (no test — requires the real BlackHole device; verified manually in Step 9)

```python
import sounddevice as sd

class BlackHoleCapture:
    def __init__(self, device_name: str = "BlackHole 2ch", sample_rate: int = 16000):
        self._device_name = device_name
        self._sample_rate = sample_rate
        self._stream: sd.RawInputStream | None = None

    def __enter__(self) -> "BlackHoleCapture":
        self._stream = sd.RawInputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype="int16",
            device=self._device_name,
        )
        self._stream.start()
        return self

    def __exit__(self, *exc_info) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()

    def read_frame(self, frame_ms: int = 30) -> bytes:
        frame_samples = int(self._sample_rate * frame_ms / 1000)
        data, _overflowed = self._stream.read(frame_samples)
        return bytes(data)
```

- [ ] **Step 8: Write `setup_audio.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "Installing BlackHole (virtual audio driver)..."
brew install blackhole-2ch

echo "Installing terminal-notifier..."
brew install terminal-notifier

echo "Done. Manual steps remain — see SETUP.md."
```

- [ ] **Step 9: Write `SETUP.md`**

```markdown
# One-time setup (macOS)

Run `./setup_audio.sh` first, then complete these manual steps — none of
this can be scripted.

## 1. Audio MIDI Setup routing

1. Open **Audio MIDI Setup** (Spotlight search).
2. Click **+** (bottom left) → **Create Multi-Output Device**.
3. Check both your normal speakers and **BlackHole 2ch**.
4. This routes call audio to both your ears and to BlackHole (which `mia`
   captures from).

**Warning:** do not set this Multi-Output Device as your Mac's system-wide
default output — every system sound (Slack pings, email notifications)
would leak into the call. Only select it as the output *inside Meet's own
in-call settings* (next step). If your macOS version supports per-app audio
output routing (Sonoma+), prefer that instead; otherwise mute other
notification sounds while `mia` is running.

## 2. Bot account login and device selection (one time, in Chromium)

1. Run `python -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch_persistent_context('~/.mia/chrome-profile', headless=False); input('press enter when done'); b.close()"`.
2. Log into the bot's dedicated Google account.
3. Join any Meet call, open in-call device settings, and select
   **BlackHole 2ch** as both the microphone and speaker.
4. Press enter in the terminal to close — this profile is reused on every
   future run, so this is a one-time step.

## 3. Automation permission

The first time `mia` runs `tab_detector.py`, macOS will prompt to allow
Terminal (or whichever app runs `mia`) to control Google Chrome via
Automation. Click **OK**. If missed, grant it manually under
**System Settings → Privacy & Security → Automation**.

## 4. Google Calendar OAuth

Set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in `.env` (from a Google
Cloud project with the Calendar API enabled), then run `mia`'s OAuth flow
once (wired up in Task 19) to store a refresh token locally.
```

- [ ] **Step 10: Manual verification**

Run `./setup_audio.sh`, complete `SETUP.md`'s steps, then run: `python -c "
from mia.audio.capture import BlackHoleCapture
with BlackHoleCapture() as cap:
    frame = cap.read_frame()
    print(len(frame), 'bytes captured')
"` while a Meet call with BlackHole selected as output is playing audio.
Expected: prints a nonzero byte count, no exception.

- [ ] **Step 11: Commit**

```bash
git add src/mia/audio/ tests/test_audio_vad.py tests/fixtures/ setup_audio.sh SETUP.md
git commit -m "feat: add local VAD and BlackHole audio capture"
```

---

### Task 15: Streaming STT (Deepgram)

**Files:**
- Create: `src/mia/stt.py`

**Interfaces:**
- Consumes: `Config.deepgram_api_key` (Task 1).
- Produces: `mia.stt.StreamingSTT(api_key: str, on_transcript: Callable[[str, bool], None])` with methods `start() -> None`, `send_frame(frame: bytes) -> None`, `stop() -> None`. `on_transcript` is invoked by the Deepgram SDK's callback with `(text, is_final)` for every partial/final result. Consumed by Task 19's main loop, which feeds `BlackHoleCapture.read_frame()` output into `send_frame()` and routes `on_transcript` output to Task 4's `WakeWordMatcher` and Task 5's `CommandBuffer`, gated by Task 3's `TurnStateMachine.should_process_stt()`.

No automated test — this is a thin wrapper around a live streaming network connection; faking the Deepgram SDK's async websocket callback machinery would test the mock, not the wrapper. Verified manually.

- [ ] **Step 1: Write `src/mia/stt.py`**

```python
from collections.abc import Callable

from deepgram import DeepgramClient, LiveOptions, LiveTranscriptionEvents

class StreamingSTT:
    def __init__(self, api_key: str, on_transcript: Callable[[str, bool], None]):
        self._client = DeepgramClient(api_key)
        self._on_transcript = on_transcript
        self._connection = None

    def start(self) -> None:
        self._connection = self._client.listen.live.v("1")

        def _handle_transcript(_client, result, **_kwargs):
            transcript = result.channel.alternatives[0].transcript
            if transcript:
                self._on_transcript(transcript, result.is_final)

        self._connection.on(LiveTranscriptionEvents.Transcript, _handle_transcript)
        self._connection.start(LiveOptions(
            model="nova-2",
            language="en-US",
            encoding="linear16",
            sample_rate=16000,
            channels=1,
            interim_results=True,
        ))

    def send_frame(self, frame: bytes) -> None:
        if self._connection is not None:
            self._connection.send(frame)

    def stop(self) -> None:
        if self._connection is not None:
            self._connection.finish()
            self._connection = None
```

- [ ] **Step 2: Manual verification**

Run: `python -c "
from mia.audio.capture import BlackHoleCapture
from mia.stt import StreamingSTT
import os, time

stt = StreamingSTT(os.environ['DEEPGRAM_API_KEY'], lambda text, is_final: print(('FINAL: ' if is_final else 'partial: ') + text))
stt.start()
with BlackHoleCapture() as cap:
    end = time.time() + 10
    while time.time() < end:
        stt.send_frame(cap.read_frame())
stt.stop()
"` while speaking into whatever feeds BlackHole.
Expected: partial and final transcripts print, roughly matching what was said.

- [ ] **Step 3: Commit**

```bash
git add src/mia/stt.py
git commit -m "feat: add Deepgram streaming STT wrapper"
```

---

### Task 16: TTS and audio injection

**Files:**
- Create: `src/mia/tts.py`
- Create: `src/mia/audio/injection.py`

**Interfaces:**
- Consumes: `Config.elevenlabs_api_key` (Task 1).
- Produces: `mia.tts.synthesize(api_key: str, text: str, voice_id: str = "Rachel") -> bytes` (returns raw PCM 16-bit 16kHz mono audio). `mia.audio.injection.inject_into_virtual_mic(pcm_audio: bytes, device_name: str = "BlackHole 2ch", sample_rate: int = 16000) -> None` (plays `pcm_audio` out through the named output device via `sounddevice.play`, blocking until playback completes). Consumed by Task 19's main loop: after a tool executes, `synthesize()` the confirmation string then `inject_into_virtual_mic()` it, wrapped by `TurnStateMachine.start_speaking()`/`finish_speaking()` calls (Task 3) so the bot's own voice doesn't re-trigger STT processing.

No automated test — both functions require live network/audio-hardware access. Verified manually.

- [ ] **Step 1: Write `src/mia/tts.py`**

```python
from elevenlabs import ElevenLabs

def synthesize(api_key: str, text: str, voice_id: str = "Rachel") -> bytes:
    client = ElevenLabs(api_key=api_key)
    audio_chunks = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        output_format="pcm_16000",
    )
    return b"".join(audio_chunks)
```

- [ ] **Step 2: Write `src/mia/audio/injection.py`**

```python
import numpy as np
import sounddevice as sd

def inject_into_virtual_mic(pcm_audio: bytes, device_name: str = "BlackHole 2ch", sample_rate: int = 16000) -> None:
    samples = np.frombuffer(pcm_audio, dtype="int16")
    sd.play(samples, samplerate=sample_rate, device=device_name, blocking=True)
```

- [ ] **Step 3: Manual verification**

Run: `python -c "
import os
from mia.tts import synthesize
from mia.audio.injection import inject_into_virtual_mic
audio = synthesize(os.environ['ELEVENLABS_API_KEY'], 'Blocked thirty minutes for focus time.')
inject_into_virtual_mic(audio)
"` while in a real (or solo test) Meet call with BlackHole selected as the bot's Chromium mic input.
Expected: other participants (or a second device you're watching the call from) hear the synthesized confirmation.

- [ ] **Step 4: Commit**

```bash
git add src/mia/tts.py src/mia/audio/injection.py
git commit -m "feat: add ElevenLabs TTS and virtual mic injection"
```

---

### Task 17: Claude tool-calling dispatch

**Files:**
- Create: `src/mia/llm.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `mia.tools.base.ToolRegistry` (Task 6), `Config.anthropic_api_key` (Task 1).
- Produces: `mia.llm.ToolCallResult` — a dataclass with `tool_name: str | None`, `confirmation: str` (either the tool handler's return value, or a fallback string if no tool matched/execution failed). `mia.llm.dispatch_command(client, registry: ToolRegistry, command_text: str) -> ToolCallResult`, where `client` is an `anthropic.Anthropic` instance. Sends `command_text` as a user message with `tools=registry.anthropic_tool_specs()`; if the response contains a `tool_use` block, looks it up in `registry`, calls its handler with the parsed input, and returns its confirmation string. If no `tool_use` block is present, returns `ToolCallResult(tool_name=None, confirmation="Sorry, I didn't catch a command I can act on.")`. If the handler raises, returns `ToolCallResult(tool_name=<name>, confirmation="Sorry, that didn't work — try again?")` (per spec's error-handling: speak a short failure notice rather than staying silent). Consumed by Task 19's main loop after `CommandBuffer.on_silence()` returns text.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm.py
from unittest.mock import MagicMock

from mia.llm import dispatch_command
from mia.tools.base import Tool, ToolRegistry

def _mock_tool_use_response(tool_name, tool_input):
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input
    response = MagicMock()
    response.content = [block]
    return response

def _mock_text_only_response():
    block = MagicMock()
    block.type = "text"
    response = MagicMock()
    response.content = [block]
    return response

def test_dispatches_to_matching_tool():
    registry = ToolRegistry()
    registry.register(Tool(
        name="block_calendar_slot",
        description="d",
        input_schema={"type": "object", "properties": {}},
        handler=lambda args: f"blocked {args['title']}",
    ))
    client = MagicMock()
    client.messages.create.return_value = _mock_tool_use_response(
        "block_calendar_slot", {"title": "Focus time"}
    )

    result = dispatch_command(client, registry, "block an hour for focus time")

    assert result.tool_name == "block_calendar_slot"
    assert result.confirmation == "blocked Focus time"
    _, kwargs = client.messages.create.call_args
    assert kwargs["tools"] == registry.anthropic_tool_specs()

def test_no_tool_use_returns_fallback():
    registry = ToolRegistry()
    client = MagicMock()
    client.messages.create.return_value = _mock_text_only_response()

    result = dispatch_command(client, registry, "what's the weather")

    assert result.tool_name is None
    assert "didn't catch" in result.confirmation

def test_handler_exception_returns_failure_notice():
    registry = ToolRegistry()
    def boom(args):
        raise RuntimeError("api down")
    registry.register(Tool(name="block_calendar_slot", description="d", input_schema={}, handler=boom))
    client = MagicMock()
    client.messages.create.return_value = _mock_tool_use_response("block_calendar_slot", {})

    result = dispatch_command(client, registry, "block time")

    assert result.tool_name == "block_calendar_slot"
    assert "didn't work" in result.confirmation
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.llm'`

- [ ] **Step 3: Write `src/mia/llm.py`**

```python
from dataclasses import dataclass

from mia.tools.base import ToolRegistry

@dataclass(frozen=True)
class ToolCallResult:
    tool_name: str | None
    confirmation: str

def dispatch_command(client, registry: ToolRegistry, command_text: str) -> ToolCallResult:
    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=256,
        tools=registry.anthropic_tool_specs(),
        messages=[{"role": "user", "content": command_text}],
    )

    tool_use_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use_block is None:
        return ToolCallResult(tool_name=None, confirmation="Sorry, I didn't catch a command I can act on.")

    tool = registry.get(tool_use_block.name)
    if tool is None:
        return ToolCallResult(tool_name=tool_use_block.name, confirmation="Sorry, that didn't work — try again?")

    try:
        confirmation = tool.handler(tool_use_block.input)
    except Exception:
        return ToolCallResult(tool_name=tool.name, confirmation="Sorry, that didn't work — try again?")

    return ToolCallResult(tool_name=tool.name, confirmation=confirmation)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_llm.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mia/llm.py tests/test_llm.py
git commit -m "feat: add Claude tool-calling dispatch"
```

---

### Task 18: Join Worker (Playwright)

**Files:**
- Create: `src/mia/join_worker.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `mia.join_worker.JoinWorker(profile_dir: Path = Path("~/.mia/chrome-profile").expanduser())` with methods `join(meet_url: str) -> None` (launches the persistent Chromium profile with `--use-fake-ui-for-media-stream`, navigates to `meet_url`, clicks "Ask to join"/"Join now" — whichever is present) and `leave() -> None` (clicks the in-call "Leave call" button and closes the browser context). Consumed by Task 19's main loop: `join()` is called after a `NotificationResult.JOIN`, `leave()` when Task 10's tab-closed / Task 9's mic-dropped leave signal fires.

No automated test — requires a live Meet call and the manually-authenticated persistent profile from `SETUP.md`. Verified manually per the spec's testing plan (solo call, then a real low-stakes meeting).

- [ ] **Step 1: Write `src/mia/join_worker.py`**

```python
from pathlib import Path

from playwright.sync_api import sync_playwright

class JoinWorker:
    def __init__(self, profile_dir: Path = Path("~/.mia/chrome-profile").expanduser()):
        self._profile_dir = profile_dir
        self._playwright = None
        self._context = None
        self._page = None

    def join(self, meet_url: str) -> None:
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self._profile_dir),
            headless=False,
            args=["--use-fake-ui-for-media-stream"],
        )
        self._page = self._context.new_page()
        self._page.goto(meet_url)
        join_button = self._page.get_by_role("button", name="Ask to join").or_(
            self._page.get_by_role("button", name="Join now")
        )
        join_button.click(timeout=30_000)

    def leave(self) -> None:
        if self._page is not None:
            self._page.get_by_role("button", name="Leave call").click(timeout=10_000)
        if self._context is not None:
            self._context.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._page = self._context = self._playwright = None
```

- [ ] **Step 2: Manual verification**

Complete `SETUP.md` Step 2 first (bot account login + device selection in the persistent profile). Then run: `python -c "
import time
from mia.join_worker import JoinWorker
worker = JoinWorker()
worker.join('https://meet.google.com/<a-real-test-meeting-url>')
print('joined, waiting 10s...')
time.sleep(10)
worker.leave()
print('left')
"`.
Expected: Chromium opens, the bot account joins the call (or appears in the waiting room — see the spec's open question on admission), and cleanly leaves after 10 seconds without an unhandled exception.

- [ ] **Step 3: Commit**

```bash
git add src/mia/join_worker.py
git commit -m "feat: add Playwright join worker"
```

---

### Task 19: Main orchestration loop

**Files:**
- Create: `src/mia/main.py`

**Interfaces:**
- Consumes: every module from Tasks 1–18.
- Produces: `mia.main.run() -> None`, the process entrypoint (invoked via `python -m mia.main`). Wires: `Config.from_env()` → `logging_setup.configure()` → build the Calendar `Resource` via OAuth (device/local-server flow from `google_auth_oauthlib.flow.InstalledAppFlow`, token cached at `~/.mia/token.json`) → build `ToolRegistry` with `build_calendar_tool(calendar_service)` registered → outer poll loop calling `mic_monitor.is_mic_active()` + `tab_detector.find_active_meet_tab()` + `calendar_enricher.find_current_meeting_title()` → `trigger.decide()` → on `should_prompt`, `state.set_status(url, "prompted")` then `notify.prompt_join()` → on `JOIN`, `state.set_status(url, "joined")` and `join_worker.join(url)`, then enters the inner live-call loop (capture frame → VAD → STT → turn-state-gated wake-word match → command buffer → on silence, `llm.dispatch_command()` → `turn_state.start_speaking()` → `tts.synthesize()` + `injection.inject_into_virtual_mic()` → `turn_state.finish_speaking()`) until the leave signal fires, then `join_worker.leave()` and `state.clear(url)`.

This task has no automated test — it is the live integration of every other module, each already tested or manually verified in its own task. Validated via the spec's own two-stage plan: a manual solo test call, then a real low-stakes meeting.

- [ ] **Step 1: Write `src/mia/main.py`**

```python
import time
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from mia.audio.capture import BlackHoleCapture
from mia.audio.injection import inject_into_virtual_mic
from mia.audio.vad import FrameVAD
from mia.command_buffer import CommandBuffer
from mia.config import Config
from mia.detection.calendar_enricher import find_current_meeting_title
from mia.detection.mic_monitor import is_mic_active
from mia.detection.tab_detector import find_active_meet_tab
from mia.detection.trigger import decide
from mia.join_worker import JoinWorker
from mia.llm import dispatch_command
from mia.logging_setup import configure as configure_logging, safe_log
from mia.notify import NotificationResult, prompt_join
from mia.state import StateStore
from mia.stt import StreamingSTT
from mia.tools.base import ToolRegistry
from mia.tools.calendar_tool import build_calendar_tool
from mia.tts import synthesize
from mia.turn_state import TurnStateMachine
from mia.wakeword import WakeWordMatcher

_TOKEN_PATH = Path("~/.mia/token.json").expanduser()
_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

def _authorize_calendar(config: Config):
    if _TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), _SCOPES)
    else:
        flow = InstalledAppFlow.from_client_config(
            {"installed": {
                "client_id": config.google_client_id,
                "client_secret": config.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }},
            _SCOPES,
        )
        creds = flow.run_local_server(port=0)
        _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_PATH.write_text(creds.to_json())
    return build("calendar", "v3", credentials=creds)

def _run_call_loop(config: Config, registry: ToolRegistry, anthropic_client: Anthropic, meet_url: str, state: StateStore):
    turn_state = TurnStateMachine()
    wake_word = WakeWordMatcher(config.wake_word, threshold=config.fuzzy_threshold)
    command_buffer = CommandBuffer()
    vad = FrameVAD()

    def on_transcript(text: str, is_final: bool) -> None:
        if not is_final or not turn_state.should_process_stt():
            return
        if command_buffer.is_capturing():
            command_buffer.append(text + " ")
            return
        if wake_word.matches(text):
            turn_state.wake_word_detected()
            command_buffer.start()
            safe_log("info", "wake word detected", meeting_url=meet_url)

    stt = StreamingSTT(config.deepgram_api_key, on_transcript)
    stt.start()

    with BlackHoleCapture() as capture:
        while find_active_meet_tab() == meet_url:
            turn_state.tick()
            frame = capture.read_frame()
            if turn_state.should_process_stt():
                stt.send_frame(frame)

            if vad.is_speech(frame) is False and command_buffer.is_capturing():
                command_text = command_buffer.on_silence()
                if command_text:
                    turn_state.command_captured()
                    result = dispatch_command(anthropic_client, registry, command_text)
                    safe_log("info", "command dispatched", tool=result.tool_name, meeting_url=meet_url)
                    turn_state.start_speaking()
                    audio = synthesize(config.elevenlabs_api_key, result.confirmation)
                    inject_into_virtual_mic(audio)
                    turn_state.finish_speaking()

    stt.stop()

def run() -> None:
    config = Config.from_env()
    configure_logging(config)

    calendar_service = _authorize_calendar(config)
    registry = ToolRegistry()
    registry.register(build_calendar_tool(calendar_service))
    anthropic_client = Anthropic(api_key=config.anthropic_api_key)
    state = StateStore(config.state_file)

    safe_log("info", "mia started")

    while True:
        decision = decide(
            mic_active=is_mic_active(),
            meet_tab_url=find_active_meet_tab(),
            calendar_title=(
                find_current_meeting_title(calendar_service, now=datetime.now(timezone.utc), meet_url=url)
                if (url := find_active_meet_tab()) else None
            ),
            state=state,
        )

        if decision.should_prompt:
            state.set_status(decision.meeting_url, "prompted")
            safe_log("info", "prompting to join", meeting_url=decision.meeting_url)
            result = prompt_join(decision.display_title)

            if result == NotificationResult.JOIN:
                state.set_status(decision.meeting_url, "joined")
                worker = JoinWorker()
                worker.join(decision.meeting_url)
                _run_call_loop(config, registry, anthropic_client, decision.meeting_url, state)
                worker.leave()
                state.clear(decision.meeting_url)
            else:
                state.set_status(decision.meeting_url, "skipped")

        time.sleep(5)

if __name__ == "__main__":
    run()
```

- [ ] **Step 2: Manual end-to-end verification**

1. Complete every `SETUP.md` step and every prior task's manual verification.
2. Run `python -m mia.main`.
3. Start a solo test Meet call in Chrome (any account) with the mic active. Confirm the Join/Skip notification appears within a few polls.
4. Click Join. Confirm the bot account joins the call.
5. Say "Hey Bot, block thirty minutes starting at 3 PM for focus time" (adjust to a near-future time). Confirm: the bot speaks a confirmation audible in the call, and the event appears on the bot account's Google Calendar.
6. Say something unrelated without the wake word. Confirm the bot stays silent.
7. End the call. Confirm the bot leaves and the process returns to the outer polling loop without crashing.
8. Repeat step 3–7 in a real, low-stakes meeting (per the spec's dogfooding step) before relying on it further.

- [ ] **Step 3: Commit**

```bash
git add src/mia/main.py
git commit -m "feat: wire up main orchestration loop"
```

---

## Self-Review Notes

- **Spec coverage:** every component in the spec's "Components" list (1–14, including the numbering as revised for local detection) maps to a task above. The five hardening fixes (fake-UI flag, self-echo gating, fuzzy matching, one-time manual device selection, VAD-based turn detection) are implemented in Tasks 18, 3, 4, `SETUP.md` (Task 14), and 14/19 respectively. Logging (Task 13) matches the spec's fire-and-forget requirement. Error-handling bullets (no-calendar-match fallback, automation-permission fail-fast, notification timeout, dedup state) are covered in Tasks 8, 10/14, 12, 2/8.
- **Out-of-scope items** (any tool besides calendar blocking, transcript/notes output, always-listening mode, multi-user, non-Meet platforms, always-on server) are intentionally absent from every task above.
- **Type consistency checked:** `Tool`/`ToolRegistry` (Task 6) signatures match their use in Task 7, 17, 19. `TriggerDecision`/`decide()` (Task 8) signature matches its call in Task 19. `TurnStateMachine` methods (Task 3) match their calls in Task 19. `StateStore` methods (Task 2) match their calls in Tasks 8 and 19.
