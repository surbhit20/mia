# Meeting Summary Doc — Design

Date: 2026-08-17
Status: Approved, not yet implemented

## Context

mia currently hears a meeting only well enough to catch commands. Every
transcript Deepgram returns is used for wake-word matching and then
discarded. When the meeting ends she leaves, and nothing survives.

This adds a post-meeting artifact: a Google Doc containing an LLM-written
summary of the meeting and a single Action Items checklist, with items mia
executed during the call already ticked.

This is a deliberate reversal of the project's original "no transcript ever
persisted" principle, and the second such reversal after audio itself moved
off-machine with the Recall.ai migration. The reversal is bounded: the
transcript is held in memory for the duration of the call and discarded once
the summary is written. What persists is the summary, not the record of who
said what.

**Consent is an open matter, deliberately left out of scope.** Capturing
every participant means recording people who are not the user, and some
jurisdictions require all-party consent. A silent bot named "Mia" is not
disclosure. A spoken self-announcement on join is recommended as a follow-up
(see Out of Scope).

## Architecture

Transcript capture reuses the websocket bridge already built and tested for
audio. No new transport, no polling, no webhooks.

`create_bot` gains a `transcript` block and subscribes the existing endpoint
to a second event:

```json
{
  "recording_config": {
    "audio_mixed_raw": {},
    "transcript": {
      "provider": {"recallai_streaming": {"mode": "prioritize_accuracy", "language_code": "en"}},
      "diarization": {"use_separate_streams_when_available": true}
    },
    "realtime_endpoints": [
      {
        "type": "websocket",
        "url": "<wss url>",
        "events": ["audio_mixed_raw.data", "transcript.data"]
      }
    ]
  }
}
```

`transcript.data` payloads share the doubly-nested `data.data` shape already
handled for audio, carrying `words[]` and a `participant` object whose `name`
may be null.

The bridge routes by event type: audio chunks to the existing `FrameBuffer`,
utterances to a new in-memory `TranscriptLog`.

**Ordering at meeting end: leave first, summarize second.** Recall bills for
time in the call, so the bot must not sit in an empty meeting while Claude
writes a summary.

## The Done/open distinction

Whether an action item is done is **not** inferred from the transcript. An
LLM reading "let's book Thursday at 3" cannot know whether the booking
happened, and asking it to guess produces confident fabrication about the
user's real calendar.

mia already knows. `dispatch_command` returns a `ToolCallResult` for every
executed tool call. Those results are accumulated during the call and passed
to the summarizer as ground truth.

They serve two purposes:

1. **Truth** — only executed tool calls may be marked done.
2. **Deduplication** — a commitment discussed in the transcript *and*
   executed by mia is one ticked item, never one ticked item plus a
   duplicate open one.

## Components

### `src/mia/transcript.py` (new)

```python
@dataclass(frozen=True)
class Utterance:
    speaker: str      # participant.name, or "Unknown speaker" when null
    text: str

def extract_transcript_utterance(raw_message: str) -> Utterance | None:
    """Parse one websocket message. Returns None for any other event type,
    an unparseable message, or a malformed shape -- never raises. Mirrors
    the defensive contract of extract_mixed_audio_chunk."""

class TranscriptLog:
    """Thread-safe, in-memory, append-only. Written from the bridge's
    asyncio thread, read once from the main thread after the call ends."""
    def append(self, utterance: Utterance) -> None: ...
    def utterance_count(self) -> int: ...
    def render(self) -> str: ...   # "Speaker: text" lines, consecutive
                                   # utterances from one speaker merged
```

### `src/mia/summary.py` (new)

```python
def summarize(client, transcript_text: str, actions_taken: list[ToolCallResult]) -> str:
    """One Claude call returning the doc body as HTML. Needs its own
    max_tokens (dispatch_command's 256 is sized for a spoken sentence);
    an hour of meeting is on the order of 10k input tokens."""
```

Output structure: a prose summary, then a single `Action Items` checklist
where executed items are ticked and attributed to mia.

### `src/mia/gdoc.py` (new)

```python
def create_doc(drive_service, title: str, html_body: str) -> str:
    """Drive files.create with mimeType application/vnd.google-apps.document
    and an HTML media upload -- Drive converts it to a native Doc. Simpler
    than the Docs API's create-then-batchUpdate. Returns the Doc URL."""
```

### Changes to existing files

- `src/mia/recall_client.py` — transcript block in `create_bot`.
- `src/mia/audio/recall_bridge.py` — route by event type; expose the log.
- `src/mia/main.py` — add the `drive.file` scope, build the Drive service,
  accumulate `ToolCallResult`s in `_run_call_loop`, and generate the doc
  after leaving.

## OAuth

`_SCOPES` gains `https://www.googleapis.com/auth/drive.file`, the narrowest
scope that works: it grants access only to files mia creates, never the
user's existing Drive contents. `_authorize_google` already detects widened
scopes against the cached token and forces fresh consent, so no new
migration logic is needed -- the next run re-prompts.

## Error handling

No failure here may break the meeting or the leave path.

- **Trivial transcript** (fewer than 5 utterances): no doc, one log line.
  Prevents a Doc for every 30-second test call.
- **Summarization fails**: logged; leave already happened.
- **Drive fails**: fall back to writing `~/.mia/summaries/<date>-<title>.md`
  and log the path, so a transient API error never destroys the summary.
- **Null participant names**: rendered as "Unknown speaker" rather than
  dropped -- an unattributed utterance still carries meaning.

## Testing

Unit tests for each new module:

- `transcript.py` — parser accepts a valid `transcript.data`, returns None
  for other events, unparseable JSON, non-dict `data.data`, and missing
  `words`; null `participant.name` becomes "Unknown speaker";
  `TranscriptLog` accumulates in order and merges consecutive same-speaker
  utterances.
- `summary.py` — mocked Claude; assert the transcript and the executed
  actions both reach the prompt, and that the prompt instructs the
  dedup/tick rule.
- `gdoc.py` — mocked Drive service; assert the conversion mimeType and the
  returned URL.

`main.py`'s wiring gets no unit tests, consistent with the rest of that file;
it is verified by the suite passing plus a live meeting.

## Out of Scope

- **Persisting the transcript.** Held in memory, discarded after the summary.
- **The transcript in the doc.** Summary only.
- **Auto-sharing with attendees.** The Doc is private. Sharing distributes a
  record of other people's speech and should be the user's deliberate act.
- **A spoken self-announcement on join.** Recommended as a follow-up now that
  meetings are being summarized, but a separate change to join behavior.
- **Non-English meetings.** `language_code: "en"`; multilingual is a later
  configuration question.
