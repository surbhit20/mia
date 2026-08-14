"""Streaming speech-to-text via Deepgram's live WebSocket API.

NOTE on deviation from the plan: the plan (task-15-brief.md) was written
against deepgram-sdk's older ("v3-style") API: `DeepgramClient(api_key)`,
`client.listen.live.v("1")`, `LiveOptions`, `LiveTranscriptionEvents`, and a
connection object with `.on()` / `.start()` / `.send()` / `.finish()`.

The actually installed package is deepgram-sdk 7.7.0 (pyproject.toml
originally pinned only `>=3.0`; tightened to `>=7.0` alongside this file,
since the code below targets the v7 `connect()`-based API), which replaced
that surface entirely:

- `DeepgramClient(api_key)` (positional) now raises a TypeError -- the
  underlying `BaseClient` only accepts keyword args, so the key must be
  passed as `DeepgramClient(api_key=...)`.
- `client.listen.live` no longer exists. Live transcription is now
  `client.listen.v1.connect(...)`, a `@contextmanager` that performs the
  actual WebSocket handshake (synchronously, and *raises on auth failure*)
  when entered, and yields a `V1SocketClient`.
- `LiveOptions` and `LiveTranscriptionEvents` no longer exist. Connection
  parameters (model, encoding, sample_rate, ...) are passed directly as
  keyword arguments to `connect()`, and event names come from
  `deepgram.core.events.EventType` (a generic OPEN/MESSAGE/ERROR/CLOSE
  enum shared across Listen/Speak/Agent, not a transcript-specific enum).
- The socket object's `.on(EventType.MESSAGE, callback)` registers a
  handler, but callbacks only fire while `.start_listening()` is running --
  and `start_listening()` blocks the calling thread, reading from the
  socket until it closes. So it must run on a background thread while
  `send_frame()` is called from the caller's thread. There is no separate
  `.start()`; connecting and beginning to listen are two different steps.
- `.send()` -> `.send_media(bytes)`. `.finish()` -> `.send_close_stream()`
  (plus exiting the `connect()` context manager to actually close the
  socket).
- Transcript messages keep the same shape used in the plan
  (`message.channel.alternatives[0].transcript`, `message.is_final`), just
  reached via `client.listen.v1.connect(...)` instead of
  `client.listen.live.v("1")`.

Evidence: inspected the installed package directly
(`/Library/.../site-packages/deepgram/listen/v1/client.py` and
`socket_client.py`, deepgram-sdk 7.7.0) and cross-checked against the
upstream README/reference.md for this version
(github.com/deepgram/deepgram-python-sdk, `Listen V1 Connect` section).

Separately: entering `connect()` with an invalid API key was verified (see
task-15-report.md) to raise `websockets.exceptions.InvalidStatus: server
rejected WebSocket connection: HTTP 401` rather than the SDK's intended
`deepgram.core.api_error.ApiError` -- a genuine bug in this SDK version's
`websocket_compat.py` shim (it catches the legacy, deprecated
`InvalidStatusCode` class, which is not what websockets 16.x actually
raises). The error is still clear and mentions the 401 status, so this
wrapper does not attempt to paper over it.

Liveness: Deepgram closes an idle live connection server-side (confirmed
live: `ConnectionClosedError(code=1011, reason='Deepgram did not receive
audio data or a text message within the timeout window')`), and the main
loop stops sending audio for the COMMAND_CAPTURED portion of a voice turn
(the Claude-dispatch + TTS-generation window). Root-caused live: this window
is fully synchronous/blocking, and the original `send_keepalive_if_idle()`
was only ever called from the *caller's own loop* -- which cannot iterate
again until COMMAND_CAPTURED finishes. So for any turn where Claude dispatch
+ TTS synthesis took longer than Deepgram's real idle timeout (observed
~5s, not the ~10s originally assumed), the connection was *guaranteed* to
die, not just at risk of it -- the Gmail-search tool's extra internal Claude
call plus longer spoken summaries made this easy to hit. Fixed by giving
`StreamingSTT` its own background keepalive thread (started in `start()`,
stopped in `stop()`) that calls `send_keepalive_if_idle()` on a fixed wall-
clock interval, independent of whatever the caller's thread is doing.
CLOSE/ERROR handlers plus try/except around every socket call still make a
dead connection visible and non-fatal instead of an exception that unwinds
the call loop and ejects the bot from the meeting, and `_reconnect_if_needed`
still recovers a connection that dies for some other reason.
"""

import threading
import time
from collections.abc import Callable

from deepgram import DeepgramClient
from deepgram.core.events import EventType

from mia.logging_setup import safe_log

# Deepgram closes a live connection that has received no audio for ~10s. The
# main loop now only stops sending frames for the COMMAND_CAPTURED portion of
# a voice turn (the Claude-dispatch + TTS-generation window) -- audio keeps
# flowing during SPEAKING/playback, since self-echo is filtered by content
# instead. That window alone is short, but a slow Claude call could still
# approach the server-side timeout, so a KeepAlive goes out whenever the
# stream has been idle for this long. Half the server-side timeout leaves
# room for a slow iteration.
_KEEPALIVE_IDLE_SECONDS = 5.0

# Once disconnected (e.g. the server-side idle timeout fires mid-turn, see
# send_keepalive_if_idle's docstring), nothing previously tried to reconnect
# -- is_connected() just stayed False forever, silently deafening the bot
# for the rest of the process's life. Both send_frame() and
# send_keepalive_if_idle() now attempt a reconnect first; this bounds how
# often a reconnect is attempted so a genuinely unreachable Deepgram doesn't
# get hammered every ~32ms from the audio loop.
_RECONNECT_BACKOFF_SECONDS = 2.0


class StreamingSTT:
    def __init__(self, api_key: str, on_transcript: Callable[[str, bool], None]):
        self._client = DeepgramClient(api_key=api_key)
        self._on_transcript = on_transcript
        self._connect_cm = None
        self._connection = None
        self._listen_thread = None
        # Set by the CLOSE/ERROR handlers below, or by a failed send. Without
        # it a server-side close is invisible: the next send_media() raises,
        # the exception unwinds the call loop, and the bot leaves the meeting
        # after answering exactly one command.
        self._connected = False
        self._last_send = 0.0
        self._last_reconnect_attempt = 0.0
        self._keepalive_thread = None
        self._keepalive_stop = None

    def start(self) -> None:
        self._connect_cm = self._client.listen.v1.connect(
            model="nova-2",
            language="en-US",
            encoding="linear16",
            sample_rate=16000,
            channels=1,
            interim_results=True,
        )
        # `connect()` is a @contextmanager: entering it performs the actual
        # WebSocket handshake synchronously, and raises (e.g. on an invalid
        # API key) rather than returning a connection that fails later.
        self._connection = self._connect_cm.__enter__()

        def _handle_message(message) -> None:
            if getattr(message, "type", None) != "Results":
                return
            transcript = message.channel.alternatives[0].transcript
            if transcript:
                self._on_transcript(transcript, bool(message.is_final))

        def _handle_close(event) -> None:
            self._connected = False
            safe_log("warning", "deepgram connection closed", event=repr(event))

        def _handle_error(event) -> None:
            self._connected = False
            safe_log("error", "deepgram connection error", error=repr(event))

        self._connection.on(EventType.MESSAGE, _handle_message)
        self._connection.on(EventType.CLOSE, _handle_close)
        self._connection.on(EventType.ERROR, _handle_error)

        self._connected = True
        self._last_send = time.monotonic()

        # start_listening() blocks the calling thread until the socket
        # closes, dispatching to the callback registered above as messages
        # arrive. Run it on a background thread so send_frame() can keep
        # feeding audio from the caller's thread.
        self._listen_thread = threading.Thread(
            target=self._connection.start_listening, daemon=True
        )
        self._listen_thread.start()

        # Runs on its own clock, independent of the caller's loop -- the
        # caller can be blocked in a synchronous Claude/TTS call for the
        # entire COMMAND_CAPTURED window, during which it cannot call
        # send_keepalive_if_idle() itself (see this module's docstring).
        self._keepalive_stop = threading.Event()
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop, daemon=True
        )
        self._keepalive_thread.start()

    def _keepalive_loop(self) -> None:
        # Event.wait(timeout) returns True once .set() is called (stop
        # requested -- exit promptly) or False once the timeout elapses
        # with no stop signal (send a keepalive, then wait again).
        while not self._keepalive_stop.wait(timeout=_KEEPALIVE_IDLE_SECONDS):
            self.send_keepalive_if_idle()

    def is_connected(self) -> bool:
        return self._connected and self._connection is not None

    def _reconnect_if_needed(self) -> None:
        if self.is_connected():
            return
        now = time.monotonic()
        if now - self._last_reconnect_attempt < _RECONNECT_BACKOFF_SECONDS:
            return
        self._last_reconnect_attempt = now
        safe_log("warning", "deepgram reconnecting")
        try:
            self.stop()
            self.start()
        except Exception as exc:
            # A failed reconnect must not raise into the audio loop -- stay
            # disconnected and let the next backoff window try again.
            safe_log("error", "deepgram reconnect failed", error=str(exc))

    def send_frame(self, frame: bytes) -> None:
        self._reconnect_if_needed()
        if not self.is_connected():
            return
        try:
            self._connection.send_media(frame)
            self._last_send = time.monotonic()
        except Exception as exc:
            # A raise here means the socket is gone. Swallow it: an audio
            # frame failing to reach Deepgram must not tear down the meeting.
            self._connected = False
            safe_log("error", "deepgram frame send failed", error=str(exc))

    def send_keepalive_if_idle(self) -> None:
        """Keep the socket alive while no audio is being sent.

        The caller invokes this every loop iteration regardless of whether the
        turn-state gate lets real frames through; it only puts a KeepAlive on
        the wire once the stream has actually been idle.
        """
        self._reconnect_if_needed()
        if not self.is_connected():
            return
        now = time.monotonic()
        if now - self._last_send < _KEEPALIVE_IDLE_SECONDS:
            return
        try:
            self._connection.send_keep_alive()
            self._last_send = now
        except Exception as exc:
            self._connected = False
            safe_log("error", "deepgram keepalive failed", error=str(exc))

    def stop(self) -> None:
        self._connected = False
        if self._keepalive_stop is not None:
            self._keepalive_stop.set()
        if self._keepalive_thread is not None:
            self._keepalive_thread.join(timeout=_KEEPALIVE_IDLE_SECONDS + 1)
            self._keepalive_thread = None
        self._keepalive_stop = None
        if self._connection is not None:
            try:
                self._connection.send_close_stream()
            except Exception as exc:
                # Already-dead socket; the context manager below still needs
                # to run, so teardown must not raise past this point.
                safe_log("warning", "deepgram close-stream failed", error=str(exc))
            self._connection = None
        if self._listen_thread is not None:
            self._listen_thread.join(timeout=5)
            self._listen_thread = None
        if self._connect_cm is not None:
            try:
                self._connect_cm.__exit__(None, None, None)
            except Exception as exc:
                safe_log("warning", "deepgram disconnect failed", error=str(exc))
            self._connect_cm = None
