# AttendeeClient Integration — Design

Date: 2026-08-16
Status: Approved, not yet implemented

## Context

mia's Meet-join path currently uses a retired Playwright-driven `JoinWorker`
that opens its own Chrome instance and clicks through Meet's UI, with audio
routed through a BlackHole virtual device (`audio/capture.py` for mic input,
`audio/injection.py` for TTS output). This was always a workaround for
Google's automation-detection blocking scripted sign-in, and depends on a
fragile, manually-maintained Chrome profile.

mia now runs against a self-hosted Attendee.dev instance (Docker Compose,
`~/Desktop/attendee`), which handles the actual meeting join/audio-transport
layer itself. Live testing (2026-08-16) confirmed **anonymous bot join
works** against a meeting on a Workspace domain we control — no signed-in
bot account or SSO needed, which was the originally-planned harder path.
This spec replaces the Playwright/BlackHole layer with a real integration
against Attendee's REST API and realtime-audio websocket protocol.

## Scope

**In scope**: replacing `join_worker.py`, `audio/capture.py`, and
`audio/injection.py` with an Attendee-backed equivalent that satisfies the
same call sites in `main.py`.

**Explicitly unchanged**: the local mic/Chrome-tab detection and
"want me to join?" prompt flow (`detection/*`, `notify.py`, `trigger.py`,
`state.py`) — mia still detects that the user is in a call on their own
machine and asks before joining, exactly as today. `demo_standalone.py`
(local mic/speakers, no Meet join) is untouched. The turn-state machine,
wake-word matching, Deepgram STT wrapper, Claude tool-calling, and
ElevenLabs TTS content are all unchanged — only where audio comes from and
goes to changes.

## Architecture

Attendee's realtime-audio protocol inverts the usual client/server
relationship: **mia runs a local websocket server, and Attendee's bot
process connects out to it** (configured via `websocket_settings.audio.url`
when creating the bot) to stream mixed meeting audio in and receive bot
speech audio out, both over the same connection. Since Attendee runs in
Docker on the same machine as mia, this connection stays entirely local via
Docker's `host.docker.internal` DNS entry — no public tunnel is needed for
any of this (this is separate from, and does not require, the SSO/tunnel
work explored and abandoned earlier).

Audio format on the wire: base64-encoded 16-bit signed PCM, mono, at a
configurable sample rate (`8000`/`16000`/`24000`; mia uses `16000`
throughout, matching every other audio interface already in the codebase).
This is the same raw format `mia.tts.synthesize()` already produces with
`output_format="pcm_16000"` — no transcoding needed.

Because the existing `_run_call_loop` only depends on `capture.read_frame(frame_ms)`
returning roughly-that-much audio and the injection functions behaving
consistently, the new audio classes are built to match the old ones'
interface exactly. `_run_call_loop` itself does not change.

## Components

### `src/mia/attendee_client.py` (new)

Thin synchronous REST wrapper using the `requests` library (new dependency
— nothing already in `pyproject.toml` is a general-purpose HTTP client).

```python
def create_bot(base_url: str, api_key: str, meeting_url: str, websocket_url: str, bot_name: str, sample_rate: int = 16000) -> str:
    """POST /api/v1/bots. Returns the created bot's object_id."""

def wait_until_joined(base_url: str, api_key: str, bot_id: str, timeout_seconds: float = 60.0, poll_interval_seconds: float = 2.0) -> None:
    """Poll GET /api/v1/bots/{id} until state == "joined_recording".
    Raises TimeoutError on timeout, RuntimeError on a terminal failure
    state (e.g. "fatal_error")."""

def set_avatar_image(base_url: str, api_key: str, bot_id: str, image_path: Path) -> None:
    """POST /api/v1/bots/{id}/output_image with the image base64-encoded.
    Called once, right after wait_until_joined() succeeds."""

def bot_state(base_url: str, api_key: str, bot_id: str) -> str:
    """GET /api/v1/bots/{id}, returns just the state field. Used by the
    call loop's periodic leave-check (see Data Flow)."""

def leave(base_url: str, api_key: str, bot_id: str) -> None:
    """POST /api/v1/bots/{id}/leave. Best-effort -- caller wraps in
    try/except the same way main.py already wraps worker.leave()."""
```

`create_bot`'s request body:

```json
{
  "meeting_url": "<meet_url>",
  "bot_name": "<bot_name>",
  "websocket_settings": {
    "audio": {"url": "<websocket_url>", "sample_rate": 16000}
  }
}
```

### `src/mia/audio/attendee_bridge.py` (new)

Replaces both `capture.py` and `injection.py` for the Meet-join path (kept
as one module since input and output share a single websocket connection,
unlike BlackHole's two independent OS-level audio streams).

**I/O boundary design**: an asyncio event loop runs in a background daemon
thread (same pattern `StreamingSTT` already uses for its listener thread),
owning the actual websocket server and connection. All the logic that needs
unit testing (frame-buffering math, chunk-pacing decisions) is written as
plain, synchronous, dependency-injectable functions operating on
`bytes`/`queue.Queue`, kept separate from the asyncio I/O glue -- so tests
exercise the logic without opening real sockets.

```python
class AttendeeAudioBridge:
    def __init__(self, port: int, sample_rate: int = 16000): ...

    def __enter__(self) -> "AttendeeAudioBridge":
        """Starts the background event-loop thread and binds the
        websocket server to ws://0.0.0.0:{port}/audio. Raises OSError if
        the port is already bound (same failure class BlackHoleCapture's
        device-open already raises for a bad device)."""

    def __exit__(self, *exc_info) -> None:
        """Stops the server and joins the background thread."""

    def read_frame(self, frame_ms: int = 30) -> bytes:
        """Same signature as BlackHoleCapture.read_frame(). Pulls exactly
        frame_ms worth of PCM bytes from an internal buffer fed by
        incoming `realtime_audio.mixed` messages. If fewer than
        frame_ms worth of real bytes have arrived within a
        2 * frame_ms timeout, pads with silence (zero bytes) rather than
        blocking -- keeps the call loop's ~32ms cadence steady even
        through gaps in the incoming stream (meeting silence, a
        reconnect)."""

    def start_playback(self, pcm_audio: bytes) -> None:
        """Non-blocking, same contract as injection.start_playback().
        Spawns a pacing sender: splits pcm_audio into ~40ms chunks and
        sends each as a `realtime_audio.bot_output` message roughly in
        real time (sleeping ~40ms between sends), checking a stop flag
        before each send. Real-time pacing (not sending all chunks
        immediately) is what makes stop_playback() meaningfully cut off
        unsent audio rather than merely updating bookkeeping after
        everything's already been handed to Attendee."""

    def is_playback_active(self) -> bool:
        """True while the pacing sender still has unsent chunks."""

    def stop_playback(self) -> None:
        """Sets the stop flag; the pacing sender exits before its next
        send. Already-sent chunks (at most one ~40ms chunk in flight)
        still play out -- this is the known limitation discussed in the
        design conversation: Attendee's protocol has no server-side
        "cancel audio" message, so this is the closest approximation to
        an instant stop."""
```

Incoming websocket messages are parsed as JSON; only
`trigger == "realtime_audio.mixed"` is handled (its `data.chunk` is
base64-decoded and appended to the read buffer). Any other trigger type is
ignored. The server accepts a new connection if the current one drops --
Attendee's own docs state it retries its side up to 30 times on a 2s
interval, so mia's server does not need to actively reconnect, only accept
whatever connection arrives.

### `src/mia/config.py`

Add `attendee_api_key: str`, `attendee_base_url: str` (default
`"http://localhost:8000"`), `attendee_websocket_port: int` (default
`8765`), `attendee_bot_name: str` (default `"Mia"`).

### `src/mia/main.py`

`_handle_join` replaces `JoinWorker()` / `worker.join()` / `worker.leave()`
with `attendee_client.create_bot(...)`, `wait_until_joined(...)`,
`set_avatar_image(...)`, and `leave(...)`. `_run_call_loop` constructs
`AttendeeAudioBridge` instead of `BlackHoleCapture`, and its
`start_playback`/`is_playback_active`/`stop_playback` calls target the
bridge instance instead of the module-level `injection.py` functions --
every other line of `_run_call_loop` is unchanged.

### New asset

`assets/bot_avatar.png`, copied from
`~/Downloads/Handwritten Script Lash Extensions Name Logo.png`.

### Retired

`join_worker.py`, `audio/capture.py`, `audio/injection.py`. The
`playwright` dependency in `pyproject.toml` becomes unused by `main.py` but
is left in place since `SETUP.md`'s Chrome-profile login step and
`tab_detector.py` (kept, per scope) still reference Playwright/AppleScript
tooling around Chrome -- not this spec's concern to remove.

## Data Flow (one call, start to end)

1. `_handle_join(meet_url)` is called (unchanged trigger: detection + user
   accepted the join prompt).
2. Enter `AttendeeAudioBridge` as a context manager -- starts the local
   websocket server.
3. `attendee_client.create_bot(..., websocket_url=f"ws://host.docker.internal:{port}/audio")`
   → `bot_id`.
4. `attendee_client.wait_until_joined(bot_id)` -- blocks until the bot is
   actually in the meeting.
5. `attendee_client.set_avatar_image(bot_id, ASSET_PATH)` -- one-time.
6. `_run_call_loop(...)` runs exactly as it does today, using the bridge in
   place of `BlackHoleCapture`/`injection.py`. Two leave conditions now
   feed the same break-out-of-loop path: the existing local-tab-based check
   (`find_active_meet_tab() == meet_url`, unchanged, still the primary
   signal) **and** a new periodic `attendee_client.bot_state(bot_id)` poll
   on the same interval -- if Attendee reports the bot is no longer in the
   meeting (removed by another participant, or a fatal error), that's
   treated as a leave signal too. This is a genuinely new failure mode
   that didn't exist with a locally-driven Playwright browser, so it needs
   its own check rather than assuming the local tab-based signal alone
   still covers every way a call can end.
7. On loop exit (either leave condition, or an exception caught by
   `_handle_join`'s existing try/except): `attendee_client.leave(bot_id)`,
   best-effort, same as today's `worker.leave()` handling.
8. Exit the `AttendeeAudioBridge` context manager -- stops the websocket
   server.

## Error Handling

- `create_bot`/`wait_until_joined` failures (network error, bad response,
  timeout, terminal bot state) propagate as exceptions, caught by
  `_handle_join`'s existing except block -- same "log and skip, mark the
  URL skipped" behavior `JoinWorker.join()` failures already have.
- Websocket server port-bind failure raises during `AttendeeAudioBridge.__enter__`,
  same failure class as `BlackHoleCapture`'s device-open failure.
- A dropped Attendee→mia websocket connection mid-call does not crash
  `read_frame()` -- it keeps returning silence-padded frames, and the
  server accepts Attendee's automatic reconnection when it arrives.
- Exceptions inside `_run_call_loop` are already caught by
  `_handle_join`'s surrounding try/except; unchanged.

## Testing

`attendee_client.py`: mock `requests` calls (`responses` library or
manual `unittest.mock.patch`), assert on request bodies/URLs and response
handling for each function, including the timeout and terminal-failure-state
paths for `wait_until_joined`.

`audio/attendee_bridge.py`: the buffering/pacing/chunking logic is tested
directly against plain bytes and a fake send function (no real sockets) --
covering: `read_frame` assembling correctly-sized frames from
arbitrarily-sized pushed chunks, silence-padding on timeout,
`start_playback`'s chunk sizing and pacing, and `stop_playback` actually
truncating a run of chunks partway through (asserting fewer chunks were
"sent" than the full audio would require). The actual websocket
server/connection wiring is exercised live during manual testing, not unit
tested, matching how `StreamingSTT`'s real Deepgram connection is handled
in this codebase already.

## Explicit Scope

**In**: anonymous bot join, mixed (not per-participant) realtime audio,
static avatar image, the four REST calls listed above, config wiring, and
retiring the three Playwright/BlackHole files.

**Out**: signed-in bot mode / SSO (anonymous only, per the live-tested
decision earlier this session), per-participant audio streams, video
output beyond the one-time static avatar image, Attendee's own
chat-messages/participant-events/built-in-transcription features (mia has
her own Deepgram STT pipeline and doesn't need Attendee's), remote/hosted
Attendee deployment (this targets the local self-hosted Docker instance
only), and any hardening (TLS, auth) of the local websocket server, since
it's reachable only from Attendee's own Docker network on this machine,
not the public internet.
