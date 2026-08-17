import asyncio
import threading

import websockets

from mia.audio.recall_framing import FrameBuffer, extract_mixed_audio_chunk

_READ_TIMEOUT_MULTIPLIER = 2
_BYTES_PER_SAMPLE = 2  # 16-bit PCM


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

    def __enter__(self) -> "RecallAudioBridge":
        ready = threading.Event()
        startup_error: list[BaseException] = []

        def _run_loop() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            async def _setup():
                self._server = await websockets.serve(self._handle_connection, "0.0.0.0", self._port)

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
        async for raw_message in websocket:
            chunk = extract_mixed_audio_chunk(raw_message)
            if chunk is not None:
                self._frame_buffer.push(chunk)

    def read_frame(self, frame_ms: int = 30) -> bytes:
        num_bytes = int(self._sample_rate * frame_ms / 1000) * _BYTES_PER_SAMPLE
        timeout_seconds = (frame_ms * _READ_TIMEOUT_MULTIPLIER) / 1000
        return self._frame_buffer.pull(num_bytes, timeout_seconds)
