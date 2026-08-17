# Recall.ai Integration — Design

Date: 2026-08-17
Status: Approved, not yet implemented

## Context

mia's Meet-join path currently targets a self-hosted Attendee.dev instance
(`src/mia/attendee_client.py`, `src/mia/audio/attendee_bridge.py`,
`src/mia/audio/attendee_framing.py`, work paused on the unmerged
`attendee-client` branch / PR #3). Anonymous-guest joins through Attendee
worked, but reliability degraded over the course of a single day of
testing: Google's own anti-abuse detection began auto-denying join
requests without ever surfacing an admission prompt, consistent with the
account being flagged for repeated same-day join attempts from an
anonymous guest — a known, inherent characteristic of anonymous
meeting-bot joins, not a bug in mia's or Attendee's code.

This spec replaces the Attendee integration with Recall.ai, a paid
commercial meeting-bot API, in exchange for professionally-maintained join
reliability. This is an explicit, deliberate departure from mia's original
"no meeting audio ever leaves local control" design principle — the
user confirmed this tradeoff directly, after being shown the alternative
(finishing the previously-abandoned signed-in-bot/SSO path, which stayed
fully local) and choosing reliability over that constraint.

**Verified live before this design was written** (2026-08-17, throwaway
spike, not part of this implementation): a Recall.ai bot created with just
`meeting_url` and `bot_name` (no signed-in account, no SSO, no Google
Workspace requirement at all) joined a real Meet call in ~9 seconds with
no denial. A test MP3 clip POSTed via `output_audio` played in the meeting
"almost immediately" — confirming output latency is not a blocker for
mia's live-conversation use case.

## Architecture

mia's backend does not move. It stays a local Python process on the same
machine as today — VAD, wake-word matching, the turn-state machine,
Deepgram STT, Claude tool-calling, and TTS synthesis are all unchanged.
Only the Meet-join and audio-transport layer changes.

Recall's bot, like Attendee's, connects **out** to a websocket server mia
hosts (`recording_config.realtime_endpoints` in the bot-creation request,
pointed at a `wss://` URL) — the same connection direction and TLS
requirement already solved for Attendee. The difference: Recall's bot runs
on Recall's own cloud infrastructure, not in a local Docker container, so
`host.docker.internal` is no longer available — the bridge must be
reachable from the public internet. This uses a **paid ngrok reserved
domain** (not the free tier used and abandoned during the Attendee work,
whose interstitial warning page blocked automated requests, and whose
random hostname churned on every restart). The reserved domain doesn't
change on restart and has no interstitial, so this is a one-time setup
cost, not an ongoing maintenance burden the way the free-tier tunnel was.

Audio input and output are asymmetric in a way that meaningfully
simplifies the bridge compared to Attendee's:

- **Input** (mixed meeting audio in): Recall pushes
  `audio_mixed_raw.data` websocket events, each containing
  `data.data.buffer` — base64-encoded 16-bit signed PCM, 16kHz mono, in
  ~200ms chunks. Same wire format Attendee used; only the JSON message
  shape differs (nested under `data.data.buffer` rather than
  `data.chunk`).
- **Output** (mia speaking): a single REST call,
  `POST /api/v1/bot/{id}/output_audio/` with
  `{"kind": "mp3", "b64_data": "<base64 mp3>"}`. Not a streamed
  connection — Recall's bot plays the whole clip once it receives it.
  This means the bridge needs **no outbound audio handling at all**:
  no chunking, no pacing, no `paced_send`/`chunk_pcm`/
  `build_bot_output_message` equivalents. `main.py` calls
  `recall_client.speak(bot_id, mp3_bytes)` directly; the bridge is
  input-only.

**Barge-in tradeoff, accepted**: neither `output_audio` nor the more
general `output_media` endpoint supports stopping or interrupting audio
already sent — confirmed via Recall's docs, no interrupt/cancel/clear
parameter exists on either. Once a response is POSTed, it plays to
completion. Wake-word detection during playback still works exactly as
before (mia notices "Hey Mia" and starts capturing a new command
immediately), but she can no longer be cut off mid-sentence the way
Attendee's paced-chunk-streaming allowed. Accepted as-is, per explicit
user decision, on the grounds that mia's spoken responses are already
short confirmations, not long monologues where this would bite often.
Rebuilding chunked output ourselves (splitting each response into several
smaller `output_audio` POSTs) was considered and explicitly rejected as
added complexity with a real risk of audible gaps between clips, for a
capability whose loss is acceptable given how short responses already are.

## Components

### `src/mia/recall_client.py` (new, replaces `attendee_client.py`)

Same shape as the retired `attendee_client.py` — plain synchronous REST
wrapper using `requests`.

```python
def create_bot(base_url: str, api_key: str, meeting_url: str, websocket_url: str, bot_name: str) -> str:
    """POST /api/v1/bot/. Returns the created bot's id."""

def bot_state(base_url: str, api_key: str, bot_id: str) -> str:
    """GET /api/v1/bot/{id}/. Returns the most recent status_changes[-1]["code"]
    (e.g. "joining_call", "in_waiting_room", "in_call_not_recording",
    "in_call_recording", "call_ended", "fatal") -- Recall's status model is a
    chronological event list, not a single enum field like Attendee's."""

def wait_until_joined(base_url: str, api_key: str, bot_id: str, timeout_seconds: float = 60.0, poll_interval_seconds: float = 2.0) -> None:
    """Poll bot_state() until it returns "in_call_recording". Raises
    RuntimeError on "call_ended" or "fatal" (terminal failure states),
    TimeoutError on timeout."""

def speak(base_url: str, api_key: str, bot_id: str, mp3_bytes: bytes) -> None:
    """POST /api/v1/bot/{id}/output_audio/ with {"kind": "mp3", "b64_data": <base64>}."""

def leave(base_url: str, api_key: str, bot_id: str) -> None:
    """POST /api/v1/bot/{id}/leave_call/. Best-effort, same as Attendee's leave()."""
```

`create_bot`'s request body:

```json
{
  "meeting_url": "<meet_url>",
  "bot_name": "<bot_name>",
  "recording_config": {
    "realtime_endpoints": [
      {"type": "websocket", "url": "<websocket_url>", "events": ["audio_mixed_raw.data"]}
    ]
  }
}
```

No `automatic_leave`, `video_mixed_mp4`, or other recording-config fields
are set beyond what's needed for the realtime audio endpoint — mia has no
use for Recall's recording/transcript storage features (out of scope,
same reasoning as Attendee: mia has her own Deepgram STT pipeline).

### `src/mia/audio/recall_bridge.py` (new, replaces `audio/attendee_bridge.py` and `audio/attendee_framing.py`)

Input-only local websocket server. Reuses the exact same `FrameBuffer`
design already built and reviewed for Attendee (push/pull with
silence-padding on timeout, `threading.Condition`-based) — that logic is
untouched. What changes is the incoming-message parsing, since Recall's
JSON shape differs from Attendee's:

```python
def extract_mixed_audio_chunk(raw_message: str) -> bytes | None:
    """Parses one incoming websocket message. Returns the decoded PCM
    bytes for an audio_mixed_raw.data event's data.data.buffer field, or
    None for any other event type or an unparseable message. Mirrors the
    graceful-handling contract of the retired Attendee equivalent
    (extract_mixed_audio_chunk in attendee_framing.py) -- including safe
    handling of a missing/non-dict data.data, and invalid base64."""
```

`RecallAudioBridge` class: same `__init__(port, sample_rate=16000)`,
context-manager (`__enter__`/`__exit__`), and `read_frame(frame_ms=30) ->
bytes` as `AttendeeAudioBridge` -- reusing the same asyncio-event-loop-in-
a-background-thread pattern, including the socket-close-on-`__exit__` fix
already proven necessary for repeated `with` blocks on a fixed port. Does
**not** have `start_playback`/`is_playback_active`/`stop_playback` --
those concepts don't apply to an input-only bridge; playback tracking
moves to `main.py` directly (below).

### `src/mia/tts.py`

Add an `output_format` parameter to the existing `synthesize()`:

```python
def synthesize(api_key: str, text: str, voice_id: str = "21m00Tcm4TlvDq8ikWAM", output_format: str = "pcm_24000") -> bytes:
```

Default stays `pcm_24000` (used by `demo_standalone.py`'s local
sounddevice playback, unchanged). The Meet-bot path passes
`output_format="mp3_44100_128"` -- verified live during the spike, this
produces a real, playable MP3 Recall's `output_audio` accepts. Not a new
function: the two call sites differ only in this one parameter, and
duplicating the function for one string difference would violate DRY for
no benefit.

### `src/mia/main.py`

Same overall shape as the Attendee wiring it replaces. `_handle_join`
swaps `attendee_client.*` calls for `recall_client.*`, and
`AttendeeAudioBridge` for `RecallAudioBridge`. The TTS-output step changes
from `bridge.start_playback(audio)` (non-blocking, bridge-owned) to a
direct `recall_client.speak(bot_id, mp3_bytes)` call plus a local,
main.py-owned playback-duration estimate for the turn-state machine's
`is_playback_active()`-equivalent check:

```python
_MP3_BITRATE_BITS_PER_SECOND = 128_000  # matches "mp3_44100_128"

def _estimate_playback_seconds(mp3_bytes: bytes) -> float:
    return len(mp3_bytes) * 8 / _MP3_BITRATE_BITS_PER_SECOND
```

This mirrors the duration-estimation approach `AttendeeAudioBridge`
already used for `is_playback_active()` (no "audio finished" ack existed
for Attendee either) -- same technique, just computed from MP3 byte
length at a known constant bitrate instead of raw PCM sample count.

### Config

`Config` gains `recall_api_key: str = ""`, `recall_base_url: str =
"https://us-west-2.recall.ai"`, `recall_websocket_port: int = 8765`,
`recall_bot_name: str = "Mia"` -- same optional-with-defaults pattern as
the retired `attendee_*` fields (never a hard dependency for
`demo_standalone.py`, which doesn't use any of this).

### Retired

`attendee_client.py`, `audio/attendee_bridge.py`,
`audio/attendee_framing.py`, and their test files -- replaced outright,
not run alongside Recall.ai. (These currently exist only on the paused,
unmerged `attendee-client` branch, never merged to `main` -- so "retiring"
them here means this new work targets `main` directly and never
reintroduces them, not that anything needs deleting from `main`.)

## Setup Requirements

- A Recall.ai account and API key (already obtained and verified live).
- ngrok upgraded to a paid tier for a reserved domain (not yet done --
  required before this can be tested against a real meeting; the local
  websocket server cannot be reached from Recall's cloud infrastructure
  without it).

## Error Handling

Same pattern as the retired Attendee wiring: `create_bot`/
`wait_until_joined` failures are caught in `_handle_join`, logged, and the
meeting URL marked `"skipped"`, with a best-effort `leave()` if a bot was
already created before the failure. `RecallAudioBridge.__enter__` failures
are caught by an outer try/except in `_handle_join` for the same reason
established during the Attendee final review: `run()` already marks the
URL `"joined"` before calling `_handle_join`, so a bridge-startup failure
must still correct that to `"skipped"` or the URL would never be
re-prompted until `StateStore`'s TTL expires.

## Testing

Same TDD pattern as the retired Attendee modules: `recall_client.py`
tests mock `requests` calls and assert exact request bodies/headers/
timeouts, plus the terminal-failure-state and timeout paths for
`wait_until_joined`. `recall_bridge.py`'s `extract_mixed_audio_chunk` and
the reused `FrameBuffer` get the same fast, real-logic unit tests already
proven for Attendee's equivalents (no real sockets needed for the
message-parsing logic; the one live-socket test is `__enter__`/`__exit__`
starting and stopping cleanly on an OS-assigned port, matching the
existing pattern). `main.py`'s `_handle_join`/`_run_call_loop` changes are
orchestration code with no new unit tests of their own, verified by the
full existing suite passing plus manual live-meeting testing --
consistent with how the equivalent Attendee wiring was verified.

## Explicit Scope

**In**: `recall_client.py` (create/poll/speak/leave), `recall_bridge.py`
(input-only websocket bridge reusing `FrameBuffer`), the `tts.py`
`output_format` parameter, `main.py` rewiring, config additions.

**Out**: Recall's own recording/transcript/summary features (mia has her
own STT pipeline and doesn't need them, same reasoning as Attendee); a
custom bot avatar image (Attendee supported this via `output_image`;
whether/how Recall supports an equivalent hasn't been checked and isn't
needed for this pass -- can be revisited later as a small addition, not
blocking); rebuilt chunked/interruptible audio output (explicitly
rejected -- see Barge-in tradeoff above); Recall's webpage-based
"interactive live avatar" mechanism (considered as an alternative
architecture and rejected -- doesn't avoid the public-hosting requirement
this design already solves, and would require moving audio handling into
JavaScript for no corresponding benefit to the input-side design here).
