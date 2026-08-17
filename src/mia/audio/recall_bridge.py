import asyncio
import threading

import websockets

from mia.audio.recall_framing import FrameBuffer, extract_mixed_audio_chunk

_BYTES_PER_SAMPLE = 2  # 16-bit PCM

# How long a read waits for real audio before giving up and returning
# silence.
#
# This is deliberately NOT derived from frame_ms. Recall delivers audio in
# ~200ms batches, so a frame-sized timeout (32ms frame -> 64ms) expires
# between every batch: the buffer runs dry, pull() substitutes silence, and
# that fabricated silence is fed to Deepgram *inside* live speech. Measured
# at ~44% of all frames during continuous talking, which chopped words apart
# and made the wake word register about one attempt in seven.
#
# BlackHoleCapture, which this replaced, could never do that -- a hardware
# input stream is paced by the sound card and cannot return a frame before
# that frame's audio has physically elapsed. Reading from a network buffer
# has no such pacing, so the timeout has to supply it.
#
# 0.5s clears Recall's ~200ms cadence plus jitter with room to spare, which
# demotes padding back to what it should be: an emergency fallback for a
# stalled or dead stream, not a routine occurrence. When audio genuinely
# stops the loop simply iterates more slowly, which is correct -- there is
# nothing to process.
_STARVATION_TIMEOUT_SECONDS = 0.5


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
        self.connections = 0
        self.messages_received = 0
        self.messages_unparsed = 0

    def __enter__(self) -> "RecallAudioBridge":
        ready = threading.Event()
        startup_error: list[BaseException] = []

        def _run_loop() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            async def _setup():
                # localhost-only: the ngrok agent that forwards Recall's bot
                # traffic runs on this same machine and connects to
                # localhost, so nothing needs to reach this port from
                # elsewhere on the network. A wider bind (e.g. "0.0.0.0")
                # would let any device on the LAN push audio straight into a
                # pipeline that holds live calendar-write and Gmail-read
                # tool handles.
                self._server = await websockets.serve(self._handle_connection, "127.0.0.1", self._port)

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
        self.connections += 1
        async for raw_message in websocket:
            self.messages_received += 1
            chunk = extract_mixed_audio_chunk(raw_message)
            if chunk is not None:
                self._frame_buffer.push(chunk)
            else:
                # Non-audio events (participant joins, etc.) land here too,
                # so this is only alarming when it accounts for most traffic.
                self.messages_unparsed += 1

    def stats(self) -> dict:
        """Counters for diagnosing a bot that appears deaf. `connections` at 0
        means Recall never reached this bridge at all; a high `pulls_padded`
        ratio means audio is arriving too slowly to fill frames, so the VAD
        sees fabricated silence; `bytes_dropped` above 0 means the buffer
        overflowed and real speech was discarded."""
        fb = self._frame_buffer
        return {
            "connections": self.connections,
            "messages_received": self.messages_received,
            "messages_unparsed": self.messages_unparsed,
            "chunks_pushed": fb.chunks_pushed,
            "bytes_pushed": fb.bytes_pushed,
            "bytes_dropped": fb.bytes_dropped,
            "pulls_served": fb.pulls_served,
            "pulls_padded": fb.pulls_padded,
        }

    def read_frame(self, frame_ms: int = 30) -> bytes:
        num_bytes = int(self._sample_rate * frame_ms / 1000) * _BYTES_PER_SAMPLE
        return self._frame_buffer.pull(num_bytes, _STARVATION_TIMEOUT_SECONDS)
