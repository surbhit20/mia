# Meeting Summary Doc Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a meeting ends, produce a Google Doc containing an LLM-written summary and a single Action Items checklist, with items mia executed during the call already ticked.

**Architecture:** Transcript capture reuses the existing Recall websocket bridge — the bot subscribes to `transcript.data` and `participant_events.*` alongside the audio it already streams, and the bridge routes each message type to an in-memory `TranscriptLog` or `ParticipantRoster`. mia leaves the call first, then summarizes, then creates the Doc via Drive. The transcript is never written to disk.

**Tech Stack:** Python, `requests`, `websockets`, `google-api-python-client` (Drive v3), Anthropic SDK, pytest.

**Spec:** `docs/superpowers/specs/2026-08-17-meeting-summary-design.md`

## Global Constraints

- Transcript is held in memory only and discarded after the summary is written. Never write the transcript to disk, and never include it in the Doc.
- Realtime endpoint events are exactly: `["audio_mixed_raw.data", "transcript.data", "participant_events.join", "participant_events.update"]`.
- Recall rejects a realtime endpoint referencing an artifact that is not also declared in `recording_config` (error: "Cannot specify realtime endpoint events for artifacts that are not configured"). Every event family used must have its config block.
- Transcript provider is `recallai_streaming` with `mode: "prioritize_accuracy"` and `language_code: "en"`.
- Incoming websocket payloads use a doubly-nested `data.data` shape for every event type.
- Speaker names resolve in this order: the name on the utterance, then the roster by `participant.id`, then a stable `Speaker N` label numbered in order of first appearance. Never render the raw `participant.id` — it is an opaque integer. Resolution happens at **render time**, never on arrival.
- Calendar attendees are passed to the summarizer as context only. Never map a calendar attendee onto a Recall `participant.id`.
- An Action Item may be ticked **only** if it corresponds to an executed `ToolCallResult`. Never infer completion from the transcript.
- Leave the call before summarizing. Recall bills for time in the call.
- No failure in this feature may break the meeting or the leave path.
- All parsers return `None` on malformed input and never raise, matching `extract_mixed_audio_chunk`.

---

### Task 1: Transcript parsing, roster, and log

**Files:**
- Create: `src/mia/transcript.py`
- Test: `tests/test_transcript.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `Utterance` (frozen dataclass: `participant_id: int`, `speaker_name: str | None`, `text: str`); `extract_transcript_utterance(raw_message: str) -> Utterance | None`; `extract_participant_event(raw_message: str) -> tuple[int, str | None] | None`; `ParticipantRoster` (`.record(participant_id: int, name: str | None) -> None`, `.name_for(participant_id: int) -> str`, `.attendees() -> list[str]`); `TranscriptLog` (`.append(utterance: Utterance) -> None`, `.utterance_count() -> int`, `.render(roster: ParticipantRoster) -> str`). Tasks 3, 5, and 7 use these.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_transcript.py`:

```python
import json

from mia.transcript import (
    ParticipantRoster,
    TranscriptLog,
    Utterance,
    extract_participant_event,
    extract_transcript_utterance,
)


def _transcript_message(participant_id=1, name="Sarah", words=("hello", "there")):
    return json.dumps(
        {
            "event": "transcript.data",
            "data": {
                "data": {
                    "words": [{"text": w} for w in words],
                    "participant": {"id": participant_id, "name": name},
                }
            },
        }
    )


def _participant_message(event="participant_events.join", participant_id=1, name="Sarah"):
    return json.dumps(
        {"event": event, "data": {"data": {"participant": {"id": participant_id, "name": name}}}}
    )


def test_extracts_utterance_with_speaker_and_joined_words():
    result = extract_transcript_utterance(_transcript_message())

    assert result == Utterance(participant_id=1, speaker_name="Sarah", text="hello there")


def test_extracts_utterance_with_null_speaker_name():
    result = extract_transcript_utterance(_transcript_message(name=None))

    assert result.participant_id == 1
    assert result.speaker_name is None
    assert result.text == "hello there"


def test_blank_speaker_name_is_treated_as_absent():
    result = extract_transcript_utterance(_transcript_message(name="   "))

    assert result.speaker_name is None


def test_ignores_other_events():
    assert extract_transcript_utterance(_participant_message()) is None


def test_ignores_unparseable_transcript_message():
    assert extract_transcript_utterance("not json") is None


def test_returns_none_on_malformed_transcript_shapes():
    assert extract_transcript_utterance(json.dumps({"event": "transcript.data", "data": None})) is None
    assert extract_transcript_utterance(json.dumps({"event": "transcript.data", "data": {"data": [1]}})) is None
    assert extract_transcript_utterance(
        json.dumps({"event": "transcript.data", "data": {"data": {"words": [{"text": "hi"}]}}})
    ) is None


def test_returns_none_when_no_words_produce_text():
    message = json.dumps(
        {
            "event": "transcript.data",
            "data": {"data": {"words": [], "participant": {"id": 1, "name": "Sarah"}}},
        }
    )

    assert extract_transcript_utterance(message) is None


def test_extracts_participant_join_and_update():
    assert extract_participant_event(_participant_message()) == (1, "Sarah")
    assert extract_participant_event(
        _participant_message(event="participant_events.update", participant_id=4, name="Raj")
    ) == (4, "Raj")


def test_participant_event_ignores_unrelated_events():
    assert extract_participant_event(_transcript_message()) is None
    assert extract_participant_event(_participant_message(event="participant_events.leave")) is None
    assert extract_participant_event("not json") is None


def test_roster_numbers_unnamed_speakers_in_order_of_first_appearance():
    # Labels are sequential, not the raw participant id -- Recall's ids are
    # opaque integers, so interpolating them gives "Speaker 847293".
    roster = ParticipantRoster()

    assert roster.name_for(847293) == "Speaker 1"
    assert roster.name_for(12) == "Speaker 2"


def test_roster_label_is_stable_for_the_same_participant():
    # Distinct, stable labels matter: collapsing every unnamed person into one
    # shared label reads to the summarizing model as a single person saying
    # everything, which destroys the structure of the conversation.
    roster = ParticipantRoster()

    first = roster.name_for(99)
    roster.name_for(100)

    assert roster.name_for(99) == first


def test_a_named_participant_never_consumes_a_speaker_number():
    roster = ParticipantRoster()
    roster.record(1, "Sarah")

    assert roster.name_for(1) == "Sarah"
    assert roster.name_for(2) == "Speaker 1"


def test_roster_returns_recorded_name():
    roster = ParticipantRoster()
    roster.record(1, "Sarah")

    assert roster.name_for(1) == "Sarah"


def test_roster_ignores_null_name_and_keeps_known_one():
    roster = ParticipantRoster()
    roster.record(1, "Sarah")
    roster.record(1, None)

    assert roster.name_for(1) == "Sarah"


def test_roster_lists_known_attendees():
    roster = ParticipantRoster()
    roster.record(2, "Raj")
    roster.record(1, "Sarah")
    roster.record(3, None)

    assert roster.attendees() == ["Raj", "Sarah"]


def test_log_renders_speaker_lines_in_order():
    roster = ParticipantRoster()
    log = TranscriptLog()
    log.append(Utterance(1, "Sarah", "morning"))
    log.append(Utterance(2, "Raj", "morning all"))

    assert log.render(roster) == "Sarah: morning\nRaj: morning all"


def test_log_merges_consecutive_utterances_from_one_speaker():
    roster = ParticipantRoster()
    log = TranscriptLog()
    log.append(Utterance(1, "Sarah", "morning"))
    log.append(Utterance(1, "Sarah", "one thing before we start"))
    log.append(Utterance(2, "Raj", "go ahead"))

    assert log.render(roster) == "Sarah: morning one thing before we start\nRaj: go ahead"


def test_log_resolves_a_name_that_arrived_after_the_utterance():
    # The regression that matters: participant_events.update fires when a
    # participant's details resolve after they joined, so a name learned late
    # must attach retroactively to lines they already spoke. This only works
    # because render() resolves names instead of append().
    roster = ParticipantRoster()
    log = TranscriptLog()
    log.append(Utterance(5, None, "can everyone hear me"))

    roster.record(5, "Priya")

    assert log.render(roster) == "Priya: can everyone hear me"


def test_log_counts_utterances():
    log = TranscriptLog()
    log.append(Utterance(1, "Sarah", "hi"))
    log.append(Utterance(1, "Sarah", "again"))

    assert log.utterance_count() == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_transcript.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.transcript'`

- [ ] **Step 3: Create `src/mia/transcript.py`**

```python
"""In-memory capture of what was said in a meeting, and by whom.

Never written to disk: the log exists for the lifetime of one call and is
discarded once the summary has been generated from it.
"""

import json
import threading
from dataclasses import dataclass

_TRANSCRIPT_EVENT = "transcript.data"
_PARTICIPANT_EVENTS = ("participant_events.join", "participant_events.update")


@dataclass(frozen=True)
class Utterance:
    """One finalized utterance.

    Stores the raw participant_id rather than a rendered speaker name, so a
    name that only arrives later (via participant_events.update) can still be
    applied to it at render time.
    """

    participant_id: int
    speaker_name: str | None
    text: str


def _clean_name(value) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _payload_participant(raw_message: str, expected_events) -> tuple[dict, dict] | None:
    """(inner_data, participant) for a matching event, else None.

    Every Recall realtime payload nests the interesting content under
    data.data, so this walk is shared by both extractors below.
    """
    try:
        payload = json.loads(raw_message)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("event") not in expected_events:
        return None
    outer = payload.get("data")
    if not isinstance(outer, dict):
        return None
    inner = outer.get("data")
    if not isinstance(inner, dict):
        return None
    participant = inner.get("participant")
    if not isinstance(participant, dict) or not isinstance(participant.get("id"), int):
        return None
    return inner, participant


def extract_transcript_utterance(raw_message: str) -> Utterance | None:
    """Parse one websocket message into an Utterance, or None.

    Returns None for any other event type, an unparseable message, or a
    malformed shape -- never raises, matching extract_mixed_audio_chunk.
    """
    found = _payload_participant(raw_message, (_TRANSCRIPT_EVENT,))
    if found is None:
        return None
    inner, participant = found

    words = inner.get("words")
    if not isinstance(words, list):
        return None
    text = " ".join(
        word["text"] for word in words if isinstance(word, dict) and isinstance(word.get("text"), str)
    ).strip()
    if not text:
        return None

    return Utterance(
        participant_id=participant["id"],
        speaker_name=_clean_name(participant.get("name")),
        text=text,
    )


def extract_participant_event(raw_message: str) -> tuple[int, str | None] | None:
    """(participant_id, name) for a join/update event, else None."""
    found = _payload_participant(raw_message, _PARTICIPANT_EVENTS)
    if found is None:
        return None
    _, participant = found
    return participant["id"], _clean_name(participant.get("name"))


class ParticipantRoster:
    """Thread-safe participant_id -> name map.

    Written from the bridge's asyncio thread as participants join and update;
    read from the main thread when the transcript is rendered.
    """

    def __init__(self):
        self._names: dict[int, str] = {}
        self._labels: dict[int, str] = {}
        self._next_label = 1
        self._lock = threading.Lock()

    def record(self, participant_id: int, name: str | None) -> None:
        # A participant often joins with a null name that resolves later, so
        # never let a null overwrite a name already known.
        if not name:
            return
        with self._lock:
            self._names[participant_id] = name

    def name_for(self, participant_id: int) -> str:
        """A display name, falling back to a sequential "Speaker N" label.

        The fallback is per-participant on purpose. One shared "Unknown
        speaker" label would read to the summarizing model as a single person
        saying everything.

        The number counts unnamed speakers in order of first appearance
        rather than interpolating participant_id, which is an opaque integer
        from Recall and would render as "Speaker 847293".
        """
        with self._lock:
            known = self._names.get(participant_id)
            if known:
                return known
            label = self._labels.get(participant_id)
            if label is None:
                label = f"Speaker {self._next_label}"
                self._labels[participant_id] = label
                self._next_label += 1
            return label

    def attendees(self) -> list[str]:
        with self._lock:
            return sorted(self._names.values())


class TranscriptLog:
    """Thread-safe, in-memory, append-only record of the meeting."""

    def __init__(self):
        self._utterances: list[Utterance] = []
        self._lock = threading.Lock()

    def append(self, utterance: Utterance) -> None:
        with self._lock:
            self._utterances.append(utterance)

    def utterance_count(self) -> int:
        with self._lock:
            return len(self._utterances)

    def render(self, roster: ParticipantRoster) -> str:
        """"Name: text" lines, consecutive utterances from one speaker merged.

        Names are resolved here rather than at append time so that a name
        learned late still applies to earlier lines from that speaker.
        """
        with self._lock:
            items = list(self._utterances)

        lines: list[str] = []
        group_id: int | None = None
        group_name = ""
        parts: list[str] = []

        for utterance in items:
            if utterance.participant_id != group_id:
                if group_id is not None:
                    lines.append(f"{group_name}: {' '.join(parts)}")
                    parts = []
                group_id = utterance.participant_id
                group_name = utterance.speaker_name or roster.name_for(utterance.participant_id)
            parts.append(utterance.text)

        if group_id is not None:
            lines.append(f"{group_name}: {' '.join(parts)}")
        return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_transcript.py -v`
Expected: PASS (17 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mia/transcript.py tests/test_transcript.py
git commit -m "feat: add in-memory transcript log and participant roster"
```

---

### Task 2: Subscribe the bot to transcript and participant events

**Files:**
- Modify: `src/mia/recall_client.py`
- Test: `tests/test_recall_client.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: no signature change. `create_bot(base_url, api_key, meeting_url, websocket_url, bot_name) -> str` keeps its signature; only the request body changes.

- [ ] **Step 1: Update the payload assertion in the existing test**

In `tests/test_recall_client.py`, find `test_create_bot_posts_correct_payload_and_returns_id`. Replace the `"recording_config"` value inside its `mock_post.assert_called_once_with(...)` with:

```python
            "recording_config": {
                "audio_mixed_raw": {},
                "transcript": {
                    "provider": {
                        "recallai_streaming": {
                            "mode": "prioritize_accuracy",
                            "language_code": "en",
                        }
                    },
                    "diarization": {"use_separate_streams_when_available": True},
                },
                "realtime_endpoints": [
                    {
                        "type": "websocket",
                        "url": "wss://example.ngrok.app/audio",
                        "events": [
                            "audio_mixed_raw.data",
                            "transcript.data",
                            "participant_events.join",
                            "participant_events.update",
                        ],
                    },
                ],
            },
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_recall_client.py::test_create_bot_posts_correct_payload_and_returns_id -v`
Expected: FAIL — the asserted payload does not match the call, because the sent body has no `transcript` block and only one event.

- [ ] **Step 3: Update `create_bot`'s request body**

In `src/mia/recall_client.py`, inside `create_bot`, replace the `"recording_config"` value with:

```python
            "recording_config": {
                # Declaring the artifact is required, not redundant with the
                # endpoint below: referencing audio_mixed_raw.data without
                # this key is rejected with "Cannot specify realtime endpoint
                # events for artifacts that are not configured".
                "audio_mixed_raw": {},
                # Same rule for transcript.data. prioritize_accuracy over
                # prioritize_low_latency because nothing waits on these --
                # they are summarized after the call, not spoken during it.
                "transcript": {
                    "provider": {
                        "recallai_streaming": {
                            "mode": "prioritize_accuracy",
                            "language_code": "en",
                        }
                    },
                    "diarization": {"use_separate_streams_when_available": True},
                },
                "realtime_endpoints": [
                    {
                        "type": "websocket",
                        "url": websocket_url,
                        "events": [
                            "audio_mixed_raw.data",
                            "transcript.data",
                            # Names are frequently null on transcript.data;
                            # these supply the roster used to resolve them.
                            "participant_events.join",
                            "participant_events.update",
                        ],
                    },
                ],
            },
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_recall_client.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mia/recall_client.py tests/test_recall_client.py
git commit -m "feat: subscribe the bot to transcript and participant events"
```

---

### Task 3: Route transcript and participant messages in the bridge

**Files:**
- Modify: `src/mia/audio/recall_bridge.py`
- Test: `tests/test_recall_bridge.py`

**Interfaces:**
- Consumes: `TranscriptLog`, `ParticipantRoster`, `extract_transcript_utterance`, `extract_participant_event` from `mia.transcript` (Task 1).
- Produces: `RecallAudioBridge.transcript_log: TranscriptLog` and `RecallAudioBridge.roster: ParticipantRoster` as public attributes. Task 7 reads both after the call ends.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_recall_bridge.py`:

```python
import asyncio
import json


def _run_messages(bridge, messages):
    """Drive _handle_connection with a fake websocket yielding `messages`."""

    class _FakeWebsocket:
        def __aiter__(self):
            async def _gen():
                for message in messages:
                    yield message

            return _gen()

    asyncio.run(bridge._handle_connection(_FakeWebsocket()))


def test_transcript_messages_land_in_the_transcript_log():
    bridge = RecallAudioBridge(port=0, sample_rate=16000)
    message = json.dumps(
        {
            "event": "transcript.data",
            "data": {
                "data": {
                    "words": [{"text": "hello"}],
                    "participant": {"id": 1, "name": "Sarah"},
                }
            },
        }
    )

    _run_messages(bridge, [message])

    assert bridge.transcript_log.utterance_count() == 1
    assert bridge.transcript_log.render(bridge.roster) == "Sarah: hello"


def test_participant_events_populate_the_roster():
    bridge = RecallAudioBridge(port=0, sample_rate=16000)
    message = json.dumps(
        {
            "event": "participant_events.join",
            "data": {"data": {"participant": {"id": 4, "name": "Raj"}}},
        }
    )

    _run_messages(bridge, [message])

    assert bridge.roster.name_for(4) == "Raj"


def test_audio_still_reaches_the_frame_buffer():
    # Regression: routing must not break the path the call loop depends on.
    import base64

    bridge = RecallAudioBridge(port=0, sample_rate=16000)
    message = json.dumps(
        {
            "event": "audio_mixed_raw.data",
            "data": {"data": {"buffer": base64.b64encode(b"\x01\x02" * 480).decode("ascii")}},
        }
    )

    _run_messages(bridge, [message])

    assert bridge.read_frame(frame_ms=30) == b"\x01\x02" * 480


def test_unrecognized_events_are_counted_not_raised():
    bridge = RecallAudioBridge(port=0, sample_rate=16000)

    _run_messages(bridge, [json.dumps({"event": "participant_events.leave", "data": {}})])

    assert bridge.stats()["messages_unparsed"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_recall_bridge.py -v`
Expected: FAIL with `AttributeError: 'RecallAudioBridge' object has no attribute 'transcript_log'`

- [ ] **Step 3: Add the import and the two public attributes**

In `src/mia/audio/recall_bridge.py`, change the `mia.audio.recall_framing` import line so both imports are present:

```python
from mia.audio.recall_framing import FrameBuffer, extract_mixed_audio_chunk
from mia.transcript import (
    ParticipantRoster,
    TranscriptLog,
    extract_participant_event,
    extract_transcript_utterance,
)
```

In `__init__`, after `self.messages_unparsed = 0`, add:

```python
        # Populated from the asyncio thread while the call runs; read once
        # from the main thread after it ends. Both are in-memory only.
        self.transcript_log = TranscriptLog()
        self.roster = ParticipantRoster()
```

- [ ] **Step 4: Replace `_handle_connection` with a routing version**

Replace the whole `_handle_connection` method with:

```python
    async def _handle_connection(self, websocket) -> None:
        self.connections += 1
        async for raw_message in websocket:
            self.messages_received += 1

            chunk = extract_mixed_audio_chunk(raw_message)
            if chunk is not None:
                self._frame_buffer.push(chunk)
                continue

            utterance = extract_transcript_utterance(raw_message)
            if utterance is not None:
                self.transcript_log.append(utterance)
                continue

            participant = extract_participant_event(raw_message)
            if participant is not None:
                self.roster.record(*participant)
                continue

            # Event families we do not subscribe to should never arrive, so
            # this counter staying near zero is the signal that routing is
            # working.
            self.messages_unparsed += 1
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_recall_bridge.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Commit**

```bash
git add src/mia/audio/recall_bridge.py tests/test_recall_bridge.py
git commit -m "feat: route transcript and participant events in the bridge"
```

---

### Task 4: Return calendar attendees alongside the meeting title

**Files:**
- Modify: `src/mia/detection/calendar_enricher.py`
- Modify: `src/mia/main.py` (the one call site)
- Test: `tests/test_calendar_enricher.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `MeetingInfo` (frozen dataclass: `title: str | None`, `attendees: list[str]`) and `find_current_meeting(calendar_service, *, now: datetime, meet_url: str) -> MeetingInfo`. This **replaces** `find_current_meeting_title`, which is removed. Task 7 passes `MeetingInfo.attendees` to the summarizer.

- [ ] **Step 1: Rewrite the test file**

Replace the entire contents of `tests/test_calendar_enricher.py` with:

```python
from datetime import datetime, timezone
from unittest.mock import MagicMock

from mia.detection.calendar_enricher import find_current_meeting

NOW = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)
MEET_URL = "https://meet.google.com/abc-defg-hij"


def _service(items):
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {"items": items}
    return service


def test_returns_title_when_event_matches():
    service = _service([{"hangoutLink": MEET_URL, "summary": "Budget sync"}])

    result = find_current_meeting(service, now=NOW, meet_url=MEET_URL)

    assert result.title == "Budget sync"


def test_returns_attendee_display_names():
    service = _service([
        {
            "hangoutLink": MEET_URL,
            "summary": "Budget sync",
            "attendees": [
                {"displayName": "Sarah Chen", "email": "sarah@example.com"},
                {"displayName": "Raj Patel", "email": "raj@example.com"},
            ],
        }
    ])

    result = find_current_meeting(service, now=NOW, meet_url=MEET_URL)

    assert result.attendees == ["Sarah Chen", "Raj Patel"]


def test_falls_back_to_email_when_display_name_missing():
    # An invitee who never set a display name is still worth naming.
    service = _service([
        {"hangoutLink": MEET_URL, "summary": "Sync", "attendees": [{"email": "raj@example.com"}]}
    ])

    result = find_current_meeting(service, now=NOW, meet_url=MEET_URL)

    assert result.attendees == ["raj@example.com"]


def test_skips_attendees_with_neither_name_nor_email():
    service = _service([
        {"hangoutLink": MEET_URL, "summary": "Sync", "attendees": [{}, {"displayName": "Sarah"}]}
    ])

    result = find_current_meeting(service, now=NOW, meet_url=MEET_URL)

    assert result.attendees == ["Sarah"]


def test_returns_empty_attendees_when_event_has_none():
    service = _service([{"hangoutLink": MEET_URL, "summary": "Solo block"}])

    result = find_current_meeting(service, now=NOW, meet_url=MEET_URL)

    assert result.attendees == []


def test_returns_empty_info_when_no_event_matches():
    service = _service([{"hangoutLink": "https://meet.google.com/zzz-zzzz-zzz", "summary": "Other"}])

    result = find_current_meeting(service, now=NOW, meet_url=MEET_URL)

    assert result.title is None
    assert result.attendees == []


def test_returns_empty_info_when_no_events():
    result = find_current_meeting(_service([]), now=NOW, meet_url=MEET_URL)

    assert result.title is None
    assert result.attendees == []


def test_returns_empty_info_when_api_raises():
    # Detection must survive a Calendar hiccup; it runs on every poll.
    service = MagicMock()
    service.events.return_value.list.return_value.execute.side_effect = RuntimeError("boom")

    result = find_current_meeting(service, now=NOW, meet_url=MEET_URL)

    assert result.title is None
    assert result.attendees == []


def test_queries_within_ten_minute_window():
    service = _service([])

    find_current_meeting(service, now=NOW, meet_url=MEET_URL)

    kwargs = service.events.return_value.list.call_args.kwargs
    assert kwargs["timeMin"] == "2026-08-17T14:50:00+00:00"
    assert kwargs["timeMax"] == "2026-08-17T15:10:00+00:00"
    assert kwargs["singleEvents"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_calendar_enricher.py -v`
Expected: FAIL with `ImportError: cannot import name 'find_current_meeting'`

- [ ] **Step 3: Rewrite `src/mia/detection/calendar_enricher.py`**

Replace the entire file with:

```python
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
```

- [ ] **Step 4: Update the call site in `src/mia/main.py`**

`main.py` and the test file rewritten in Step 1 are the only callers of the
old function — verified by grep, and `demo_standalone.py` does not use the
enricher at all — so this is the last place to change.

Replace the import line with:

```python
from mia.detection.calendar_enricher import MeetingInfo, find_current_meeting
```

Then find the block in `run()` that assigns `calendar_title` and replace it with the version below. Note that `meeting_info` must be initialized before the `if`, because `decide()` is called with it on every poll:

```python
                meeting_info = MeetingInfo()
                if (
                    mic_active
                    and meet_url is not None
                    and state.status(meet_url) is None
                ):
                    meeting_info = find_current_meeting(
                        calendar_service,
                        now=datetime.now(timezone.utc),
                        meet_url=meet_url,
                    )
```

Then change the `decide(...)` call's `calendar_title` argument from `calendar_title` to `meeting_info.title`.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS. Also run `PYTHONPATH=src .venv/bin/python -c "import mia.main"` and expect no error.

- [ ] **Step 6: Commit**

```bash
git add src/mia/detection/calendar_enricher.py tests/test_calendar_enricher.py src/mia/main.py
git commit -m "feat: return calendar attendees alongside the meeting title"
```

---

### Task 5: Summarize the transcript

**Files:**
- Create: `src/mia/summary.py`
- Test: `tests/test_summary.py`

**Interfaces:**
- Consumes: `ToolCallResult` from `mia.llm` (existing: frozen dataclass with `tool_name: str | None` and `confirmation: str`).
- Produces: `summarize(client, transcript_text: str, present: list[str], invited: list[str], actions_taken: list[ToolCallResult]) -> str` returning an HTML document body. Task 7 calls it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_summary.py`:

```python
from unittest.mock import MagicMock

from mia.llm import ToolCallResult
from mia.summary import summarize


def _client(text="<h1>Budget sync</h1>"):
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


def _prompt_of(client) -> str:
    return client.messages.create.call_args.kwargs["messages"][0]["content"]


def test_returns_the_models_html():
    client = _client("<h1>Budget sync</h1><h2>Action Items</h2>")

    result = summarize(client, "Sarah: hi", ["Sarah"], [], [])

    assert result == "<h1>Budget sync</h1><h2>Action Items</h2>"


def test_prompt_carries_the_transcript_and_both_attendee_lists():
    client = _client()

    summarize(client, "Sarah: we should ship Friday", ["Sarah", "Speaker 2"], ["Sarah Chen", "Raj Patel"], [])

    prompt = _prompt_of(client)
    assert "Sarah: we should ship Friday" in prompt
    assert "Speaker 2" in prompt
    assert "Raj Patel" in prompt


def test_executed_actions_are_passed_as_ground_truth():
    # The Done ticks must come from what mia actually ran, never from the
    # model's reading of the transcript.
    client = _client()

    summarize(
        client,
        "Sarah: book us Thursday at 3",
        ["Sarah"],
        [],
        [ToolCallResult(tool_name="block_calendar_slot", confirmation="Blocked Budget review Thursday 3 PM")],
    )

    prompt = _prompt_of(client)
    assert "block_calendar_slot" in prompt
    assert "Blocked Budget review Thursday 3 PM" in prompt


def test_prompt_states_the_tick_and_dedup_rules():
    client = _client()

    summarize(client, "Sarah: hi", ["Sarah"], [], [])

    prompt = _prompt_of(client)
    assert "only" in prompt.lower()
    assert "once" in prompt.lower()


def test_uses_a_token_budget_large_enough_for_a_summary():
    # dispatch_command's 256 is sized for one spoken sentence; a summary of an
    # hour-long meeting needs far more room.
    client = _client()

    summarize(client, "Sarah: hi", ["Sarah"], [], [])

    assert client.messages.create.call_args.kwargs["max_tokens"] >= 2000


def test_joins_multiple_text_blocks():
    first, second = MagicMock(), MagicMock()
    first.type, first.text = "text", "<h1>A</h1>"
    second.type, second.text = "text", "<h2>B</h2>"
    response = MagicMock()
    response.content = [first, second]
    client = MagicMock()
    client.messages.create.return_value = response

    assert summarize(client, "Sarah: hi", ["Sarah"], [], []) == "<h1>A</h1><h2>B</h2>"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_summary.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.summary'`

- [ ] **Step 3: Create `src/mia/summary.py`**

```python
"""Turn a finished meeting's transcript into the body of a summary doc."""

from mia.llm import ToolCallResult

# An hour of meeting is on the order of 10k input tokens, and the output
# carries a prose summary plus a checklist. dispatch_command's 256 is sized
# for a single spoken sentence and is nowhere near enough here.
_MAX_TOKENS = 4000

_SYSTEM = (
    "You write concise post-meeting summaries as HTML fragments. "
    "Output only HTML -- no markdown, no code fences, no commentary before "
    "or after. Use <h1> for the title, <p> for prose, <h2>Action Items</h2> "
    "for the checklist, and a <ul> of <li> items beneath it."
)


def _format_actions(actions_taken: list[ToolCallResult]) -> str:
    if not actions_taken:
        return "(none -- mia executed no tools during this meeting)"
    return "\n".join(
        f"- tool={action.tool_name} result={action.confirmation}" for action in actions_taken
    )


def summarize(
    client,
    transcript_text: str,
    present: list[str],
    invited: list[str],
    actions_taken: list[ToolCallResult],
) -> str:
    """One Claude call returning the doc body as HTML.

    `actions_taken` is ground truth for what was completed: only these may be
    ticked. It also deduplicates -- a commitment discussed in the transcript
    and executed by mia must appear once, ticked, not twice.
    """
    prompt = (
        "Summarize this meeting.\n\n"
        f"People detected speaking: {', '.join(present) or 'unknown'}\n"
        f"People invited on the calendar: {', '.join(invited) or 'unknown'}\n\n"
        "The invited list is context about who was expected. Do NOT use it to "
        "guess the identity of a speaker labelled 'Speaker <number>' -- those "
        "labels mean the platform gave no name, and a confident wrong "
        "attribution is worse than an anonymous one. You may name such a "
        "speaker only if the transcript itself makes it unambiguous, for "
        "example if someone addresses them by name.\n\n"
        "Actions mia already completed during the meeting (ground truth):\n"
        f"{_format_actions(actions_taken)}\n\n"
        "Transcript:\n"
        f"{transcript_text}\n\n"
        "Produce:\n"
        "1. An <h1> title for the meeting.\n"
        "2. A few <p> paragraphs summarizing what was discussed and decided.\n"
        "3. An <h2>Action Items</h2> section with a single <ul> checklist.\n\n"
        "Checklist rules:\n"
        "- One list, not two.\n"
        "- Start a completed item with '[x]' and end it with ' - done by "
        "Mia'. Mark an item done ONLY if it appears in the ground-truth "
        "actions above. Never infer completion from the transcript.\n"
        "- Start every other item with '[ ]'.\n"
        "- If a commitment in the transcript matches a completed action, list "
        "it once as the completed item. Never list the same commitment twice.\n"
        "- Attribute an item to a person when the transcript makes the owner "
        "clear."
    )

    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=_MAX_TOKENS,
        system=_SYSTEM,
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text").strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_summary.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mia/summary.py tests/test_summary.py
git commit -m "feat: summarize a meeting transcript into an HTML doc body"
```

---

### Task 6: Create the Google Doc

**Files:**
- Create: `src/mia/gdoc.py`
- Test: `tests/test_gdoc.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `create_doc(drive_service, title: str, html_body: str) -> str` returning the Doc's `webViewLink`. Task 7 calls it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gdoc.py`:

```python
from unittest.mock import MagicMock, patch

from mia.gdoc import create_doc


def _service(link="https://docs.google.com/document/d/abc123/edit"):
    service = MagicMock()
    service.files.return_value.create.return_value.execute.return_value = {
        "id": "abc123",
        "webViewLink": link,
    }
    return service


@patch("mia.gdoc.MediaInMemoryUpload")
def test_returns_the_doc_url(mock_upload):
    service = _service()

    url = create_doc(service, "Budget sync", "<h1>Budget sync</h1>")

    assert url == "https://docs.google.com/document/d/abc123/edit"


@patch("mia.gdoc.MediaInMemoryUpload")
def test_requests_conversion_to_a_native_google_doc(mock_upload):
    # Without this mimeType Drive stores a raw .html file instead of a Doc.
    service = _service()

    create_doc(service, "Budget sync", "<h1>Budget sync</h1>")

    kwargs = service.files.return_value.create.call_args.kwargs
    assert kwargs["body"] == {
        "name": "Budget sync",
        "mimeType": "application/vnd.google-apps.document",
    }
    assert kwargs["fields"] == "id,webViewLink"


@patch("mia.gdoc.MediaInMemoryUpload")
def test_uploads_the_html_body_as_html(mock_upload):
    service = _service()

    create_doc(service, "Budget sync", "<h1>Budget sync</h1>")

    mock_upload.assert_called_once_with(b"<h1>Budget sync</h1>", mimetype="text/html", resumable=False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_gdoc.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.gdoc'`

- [ ] **Step 3: Create `src/mia/gdoc.py`**

```python
"""Create the meeting summary as a native Google Doc."""

from googleapiclient.http import MediaInMemoryUpload

# Asking Drive to store HTML under this mimeType makes it convert the upload
# into a real Doc. The alternative -- the Docs API's create-then-batchUpdate
# -- would mean translating the summary into styled text runs by hand.
_GOOGLE_DOC_MIMETYPE = "application/vnd.google-apps.document"


def create_doc(drive_service, title: str, html_body: str) -> str:
    """Create a Doc from an HTML body and return its shareable URL.

    Created private to the user: the drive.file scope grants access only to
    files mia creates, and nothing here shares the result.
    """
    media = MediaInMemoryUpload(
        html_body.encode("utf-8"), mimetype="text/html", resumable=False
    )
    created = drive_service.files().create(
        body={"name": title, "mimeType": _GOOGLE_DOC_MIMETYPE},
        media_body=media,
        fields="id,webViewLink",
    ).execute()
    return created["webViewLink"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_gdoc.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mia/gdoc.py tests/test_gdoc.py
git commit -m "feat: create the meeting summary as a Google Doc"
```

---

### Task 7: Generate the summary doc when the meeting ends

**Files:**
- Modify: `src/mia/main.py`

**Interfaces:**
- Consumes: `TranscriptLog`/`ParticipantRoster` via `bridge.transcript_log` and `bridge.roster` (Task 3); `MeetingInfo`/`find_current_meeting` (Task 4); `summarize(...)` (Task 5); `create_doc(...)` (Task 6); existing `ToolCallResult` from `mia.llm`.
- Produces: nothing consumed by later tasks.

This task has no new unit tests. `_run_call_loop` and `_handle_join` are orchestration already covered by the components they call, and the end-to-end path can only be exercised against a real meeting. The full existing suite must still pass.

- [ ] **Step 1: Add the Drive scope and imports**

In `src/mia/main.py`, add `"https://www.googleapis.com/auth/drive.file"` as a third entry in `_SCOPES`:

```python
_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    # Narrowest scope that works: access only to files mia creates, never the
    # user's existing Drive. _authorize_google already detects a widened
    # scope list against the cached token and forces fresh consent, so the
    # next run re-prompts by itself.
    "https://www.googleapis.com/auth/drive.file",
]
```

Add these imports alongside the existing `mia.*` imports:

```python
from mia.gdoc import create_doc
from mia.llm import ToolCallResult
from mia.summary import summarize
```

(`ConversationHistory` and `dispatch_command` are already imported from `mia.llm`; add `ToolCallResult` to that existing import line rather than duplicating it.)

- [ ] **Step 2: Add the summary constants and the doc-writing helper**

After the `_estimate_playback_seconds` function, add:

```python
# Below this, a "meeting" is a test call or a room where nobody spoke, and a
# summary would be noise in the user's Drive.
_MIN_UTTERANCES_FOR_SUMMARY = 5

_SUMMARY_FALLBACK_DIR = Path("~/.mia/summaries").expanduser()


def _write_summary_doc(
    drive_service,
    anthropic_client: Anthropic,
    bridge: RecallAudioBridge,
    meet_url: str,
    title: str,
    invited: list[str],
    actions_taken: list[ToolCallResult],
) -> None:
    """Summarize the meeting and put it in the user's Drive.

    Runs after the bot has already left. Every failure here is logged and
    swallowed: the meeting is over, and nothing downstream depends on this.
    """
    utterances = bridge.transcript_log.utterance_count()
    if utterances < _MIN_UTTERANCES_FOR_SUMMARY:
        safe_log(
            "info",
            "skipping summary, too little was said",
            meeting_url=meet_url,
            utterances=utterances,
        )
        return

    try:
        transcript_text = bridge.transcript_log.render(bridge.roster)
        html = summarize(
            anthropic_client,
            transcript_text,
            bridge.roster.attendees(),
            invited,
            actions_taken,
        )
    except Exception as exc:
        safe_log("error", "summary generation failed", meeting_url=meet_url, error=str(exc))
        return

    try:
        url = create_doc(drive_service, title, html)
        safe_log("info", "summary doc created", meeting_url=meet_url, doc_url=url)
        return
    except Exception as exc:
        safe_log("error", "summary doc creation failed", meeting_url=meet_url, error=str(exc))

    # Drive failed, but the summary already exists -- write the same bytes
    # locally rather than throwing the work away over a transient API error.
    try:
        _SUMMARY_FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip()
        path = _SUMMARY_FALLBACK_DIR / f"{datetime.now().date().isoformat()}-{safe_title}.html"
        path.write_text(html)
        safe_log("info", "summary written locally instead", meeting_url=meet_url, path=str(path))
    except Exception as exc:
        safe_log("error", "summary fallback write failed", meeting_url=meet_url, error=str(exc))
```

- [ ] **Step 3: Have the call loop record what mia executed**

In `_run_call_loop`, add `actions_taken: list[ToolCallResult]` as the final parameter:

```python
def _run_call_loop(
    config: Config,
    registry: ToolRegistry,
    anthropic_client: Anthropic,
    meet_url: str,
    bridge: RecallAudioBridge,
    bot_id: str,
    actions_taken: list[ToolCallResult],
) -> None:
```

The caller owns the list so that a crash mid-meeting still leaves the completed actions available to the summary.

Then, immediately after the existing `result = dispatch_command(...)` line and its `safe_log("info", "command dispatched", ...)` call, append the result:

```python
                    actions_taken.append(result)
```

- [ ] **Step 4: Thread the meeting info and Drive service into `_handle_join`**

Change `_handle_join`'s signature to:

```python
def _handle_join(
    config: Config,
    registry: ToolRegistry,
    anthropic_client: Anthropic,
    drive_service,
    state: StateStore,
    meet_url: str,
    meeting_info: MeetingInfo,
) -> None:
```

Immediately inside the function body, before the `missing = [...]` check, add:

```python
    actions_taken: list[ToolCallResult] = []
    doc_title = meeting_info.title or f"Meeting {datetime.now().strftime('%Y-%m-%d %H:%M')}"
```

Update the `_run_call_loop` call to pass the list:

```python
                _run_call_loop(
                    config, registry, anthropic_client, meet_url, bridge, bot_id, actions_taken
                )
```

- [ ] **Step 5: Generate the doc after leaving**

In `_handle_join`'s `finally` block, after the existing `state.clear(meet_url)` and `safe_log("info", "left meeting", ...)` lines, add:

```python
                    # After leave(), never before: Recall bills for time in
                    # the call, and summarizing takes seconds.
                    _write_summary_doc(
                        drive_service,
                        anthropic_client,
                        bridge,
                        meet_url,
                        doc_title,
                        meeting_info.attendees,
                        actions_taken,
                    )
```

- [ ] **Step 6: Build the Drive service and update the call site**

In `run()`, after the existing `gmail_service = build(...)` line, add:

```python
    drive_service = build("drive", "v3", credentials=creds)
```

Then update the `_handle_join(...)` call to match the new signature:

```python
                        _handle_join(
                            config,
                            registry,
                            anthropic_client,
                            drive_service,
                            state,
                            decision.meeting_url,
                            meeting_info,
                        )
```

- [ ] **Step 7: Run the full test suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, with the same counts as before this task plus Tasks 1-6's new tests.

Then confirm both entrypoints still load:

```bash
PYTHONPATH=src .venv/bin/python -c "import mia.main; print('ok')"
PYTHONPATH=src .venv/bin/python -c "import ast; ast.parse(open('demo_standalone.py').read()); print('ok')"
```

- [ ] **Step 8: Commit**

```bash
git add src/mia/main.py
git commit -m "feat: write a summary doc to Drive when the meeting ends"
```

- [ ] **Step 9: Manual verification against a real meeting**

Automated tests do not exercise Recall, Drive, or a real conversation. This step is what proves the feature.

1. Restart mia. **Expect a Google OAuth prompt** — `_SCOPES` widened, so the cached token is rejected and consent is requested again. Grant it.
2. Join a real Meet call with at least one other person and talk for a minute or two, so more than 5 utterances accumulate.
3. Issue at least one real command ("hey mia, block 30 minutes at 4pm") so there is an executed action to tick.
4. Close the tab and wait for the 15-second grace period to elapse.
5. Confirm in mia's log: `summary doc created` with a `doc_url`.
6. Open the Doc and check it is a native Google Doc (not an .html attachment), that speakers are named rather than all labelled identically, that the command you issued appears **once** in Action Items as `[x] ... - done by Mia`, and that nothing else is ticked.
7. Confirm the transcript itself appears nowhere in the Doc.
