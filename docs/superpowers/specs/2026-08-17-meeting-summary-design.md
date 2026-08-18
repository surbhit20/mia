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
        "events": [
          "audio_mixed_raw.data",
          "transcript.data",
          "participant_events.join",
          "participant_events.update"
        ]
      }
    ]
  }
}
```

`transcript.data` payloads share the doubly-nested `data.data` shape already
handled for audio, carrying `words[]` and a `participant` object whose `name`
may be null.

The bridge routes by event type: audio chunks to the existing `FrameBuffer`,
utterances to a new in-memory `TranscriptLog`, and participant events to a
`ParticipantRoster`.

### Speaker naming

A summary is only as useful as its attribution. "Sarah agreed to send the
numbers" is actionable; "someone agreed to send the numbers" is not.

`transcript.data` carries `participant.name`, but it may be null. Names are
therefore resolved in three steps, in order:

1. the name on the utterance itself;
2. the `ParticipantRoster`, keyed by `participant.id`, populated from
   `participant_events.join` and `.update`;
3. a stable `Speaker N` label, numbered sequentially in order of first
   appearance -- **not** the raw `participant.id`, which is an opaque integer
   and would render as "Speaker 847293".

**Resolution happens at render time, not on arrival.**
`participant_events.update` fires when a participant's details resolve after
they joined, so deferring lets a real name attach retroactively to lines
they spoke while still anonymous.

Step 3 is not cosmetic. Collapsing every unnamed participant into one
"Unknown speaker" label reads to the summarizing model as a single person
saying everything, which destroys the structure of the conversation. A
stable per-id label keeps speakers distinct even when no name is ever
available.

The roster also gives the summarizer an attendee list, so it can name who
was present rather than inferring it from who happened to speak.

### Invited attendees, as context only

The calendar event behind the meeting already carries an `attendees` array
with names and emails, and mia already fetches that event --
`find_current_meeting_title` matches it by `hangoutLink` and keeps only the
title. Extending it to return attendees costs no extra API call and no new
scope.

That list is passed to the summarizer as **context about who was invited**.
It is deliberately NOT used to resolve a null `participant.name`.

There is no signal linking a Recall participant id to a calendar attendee.
Mapping "id 3" onto one of three invitees would be a guess, and a wrong
guess is worse than the honest fallback: a summary that states "Sarah
committed to sending the numbers" when Raj said it is an error the user will
act on. `Speaker 3` is merely unhelpful; misattribution is harmful. The
three-step resolution above stands unchanged.

What the summarizer may do with the invite list is name a speaker where the
transcript itself makes it unambiguous (an unnamed speaker addressed as
"Sarah" in the next line), while leaving genuinely unidentifiable speakers
under their stable labels. That inference belongs to the model reading the
conversation, not to a fuzzy join on names in our code.

The invite list also covers people who never speak, so the doc can record
who was expected -- something the live roster alone cannot supply.

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
    participant_id: int
    speaker_name: str | None   # as given on the utterance; often null
    text: str

def extract_transcript_utterance(raw_message: str) -> Utterance | None:
    """Parse one websocket message. Returns None for any other event type,
    an unparseable message, or a malformed shape -- never raises. Mirrors
    the defensive contract of extract_mixed_audio_chunk."""

class ParticipantRoster:
    """Thread-safe id -> name map, fed by participant_events.join/.update."""
    def record(self, participant_id: int, name: str | None) -> None: ...
    def name_for(self, participant_id: int) -> str: ...
        # Falls back to "Speaker N", numbered in order of first appearance
        # and stable per participant_id thereafter.
    def attendees(self) -> list[str]: ...

class TranscriptLog:
    """Thread-safe, in-memory, append-only. Written from the bridge's
    asyncio thread, read once from the main thread after the call ends."""
    def append(self, utterance: Utterance) -> None: ...
    def utterance_count(self) -> int: ...
    def render(self, roster: ParticipantRoster) -> str: ...
        # "Name: text" lines, consecutive utterances from one speaker
        # merged. Takes the roster so names resolve at render time, after
        # every .update has landed.
```

### `src/mia/summary.py` (new)

```python
def summarize(
    client,
    transcript_text: str,
    present: list[str],        # live roster, from participant_events
    invited: list[str],        # calendar attendees; context only
    actions_taken: list[ToolCallResult],
) -> str:
    """One Claude call returning the doc body as HTML. Needs its own
    max_tokens (dispatch_command's 256 is sized for a spoken sentence);
    an hour of meeting is on the order of 10k input tokens."""
```

Output structure: a prose summary, then a single `Action Items` checklist
where executed items are ticked and attributed to mia. Rendered shape:

    <h1>Budget sync - 17 Aug 2026</h1>
    <p>Sarah walked through Q3 numbers ...</p>
    <h2>Action Items</h2>
    <ul>
      <li>[x] Create "Budget review", Thursday 3 PM - done by Mia</li>
      <li>[ ] Sarah to send the Q3 spreadsheet</li>
      <li>[ ] Follow up with legal on the vendor contract</li>
    </ul>

One list, not two. An item is ticked only when it corresponds to an executed
tool call.

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
- `src/mia/detection/calendar_enricher.py` — return the matched event's
  attendee display names alongside the title, from the call it already makes.
- `src/mia/main.py` — add the `drive.file` scope, build the Drive service,
  accumulate `ToolCallResult`s in `_run_call_loop`, thread the invited
  attendees through to the summarizer, and generate the doc after leaving.

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
- **Drive fails**: fall back to writing
  `~/.mia/summaries/<date>-<title>.html` and log the path, so a transient API
  error never destroys the summary. The extension is `.html`, matching what
  summarize() returns -- the fallback stores the same bytes that would have
  been uploaded, with no lossy conversion step to get wrong.
- **Null participant names**: resolved via the roster, then as a sequential
  `Speaker N`. Never dropped, and never collapsed into a single shared label
  -- see Speaker naming.

## Testing

Unit tests for each new module:

- `transcript.py` — parser accepts a valid `transcript.data`, returns None
  for other events, unparseable JSON, non-dict `data.data`, and missing
  `words`. `ParticipantRoster` returns a joined participant's name, falls
  back to a sequential "Speaker N" for an unknown id, and lets a later
  `.update` overwrite a null name. `TranscriptLog` accumulates in order,
  merges consecutive same-speaker utterances, and -- the regression that
  matters -- resolves a name that only arrived *after* the utterance was
  appended.
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
- **Auto-sharing with attendees.** The Doc is created private to the user,
  who shares it from Drive if they want to. Automatic sharing would need
  attendee email addresses, which mia does not currently collect.
- **Non-English meetings.** `language_code: "en"`; multilingual is a later
  configuration question.
