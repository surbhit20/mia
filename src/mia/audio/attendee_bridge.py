import asyncio
import threading
import time

import websockets

from mia.audio.attendee_framing import (
    FrameBuffer,
    build_bot_output_message,
    chunk_pcm,
    extract_mixed_audio_chunk,
    paced_send,
)

_CHUNK_MS = 40
_READ_TIMEOUT_MULTIPLIER = 2
_BYTES_PER_SAMPLE = 2  # 16-bit PCM


class AttendeeAudioBridge:
    """Local websocket server that Attendee's bot connects out to (the
    connection direction is inverted from the usual client/server
    relationship -- see the design spec). Bridges Attendee's realtime-audio
    protocol to the same read_frame()/start_playback()/is_playback_active()/
    stop_playback() interface BlackHoleCapture and audio/injection.py
    already provided, so main.py's call loop does not need to change."""

    def __init__(self, port: int, sample_rate: int = 16000):
        self._port = port
        self._sample_rate = sample_rate
        self._frame_buffer = FrameBuffer()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None
        self._thread: threading.Thread | None = None
        self._connection = None
        self._connection_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._playback_end_time = 0.0

    def __enter__(self) -> "AttendeeAudioBridge":
        ready = threading.Event()
        startup_error: list[BaseException] = []

        def _run_loop() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                async def setup():
                    self._server = await websockets.serve(self._handle_connection, "0.0.0.0", self._port)

                self._loop.run_until_complete(setup())
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
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)

    async def _handle_connection(self, websocket) -> None:
        with self._connection_lock:
            self._connection = websocket
        try:
            async for raw_message in websocket:
                chunk = extract_mixed_audio_chunk(raw_message)
                if chunk is not None:
                    self._frame_buffer.push(chunk)
        finally:
            with self._connection_lock:
                if self._connection is websocket:
                    self._connection = None

    def read_frame(self, frame_ms: int = 30) -> bytes:
        num_bytes = int(self._sample_rate * frame_ms / 1000) * _BYTES_PER_SAMPLE
        timeout_seconds = (frame_ms * _READ_TIMEOUT_MULTIPLIER) / 1000
        return self._frame_buffer.pull(num_bytes, timeout_seconds)

    def start_playback(self, pcm_audio: bytes) -> None:
        stop_event = threading.Event()
        self._stop_event = stop_event
        chunk_bytes = int(self._sample_rate * _CHUNK_MS / 1000) * _BYTES_PER_SAMPLE
        chunks = chunk_pcm(pcm_audio, chunk_bytes)
        duration_seconds = len(pcm_audio) / (self._sample_rate * _BYTES_PER_SAMPLE)
        self._playback_end_time = time.monotonic() + duration_seconds

        def _send(chunk: bytes) -> None:
            with self._connection_lock:
                connection = self._connection
            if connection is None or self._loop is None:
                return
            message = build_bot_output_message(chunk, self._sample_rate)
            asyncio.run_coroutine_threadsafe(connection.send(message), self._loop)

        threading.Thread(
            target=paced_send,
            args=(chunks, _CHUNK_MS / 1000, _send, stop_event),
            daemon=True,
        ).start()

    def is_playback_active(self) -> bool:
        return time.monotonic() < self._playback_end_time

    def stop_playback(self) -> None:
        self._stop_event.set()
        self._playback_end_time = time.monotonic()
