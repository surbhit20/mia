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

    def has_name(self, participant_id: int) -> bool:
        """Whether a real name is known, without allocating a label.

        name_for() lazily assigns the next "Speaker N" as a side effect, so
        callers that only want to *ask* must use this -- probing with
        name_for() would hand out numbers in the caller's iteration order
        rather than in order of first speech.
        """
        with self._lock:
            return participant_id in self._names

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

    def speaker_ids(self) -> set[int]:
        with self._lock:
            return {utterance.participant_id for utterance in self._utterances}

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
