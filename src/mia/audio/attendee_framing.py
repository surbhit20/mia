import base64
import json
import threading
import time
from collections.abc import Callable


class FrameBuffer:
    """Accumulates arbitrarily-sized incoming PCM chunks and serves
    fixed-size frames on demand, padding with silence when a caller's
    timeout elapses before enough real audio has arrived. A
    threading.Condition lets pull() wake up as soon as enough data is
    pushed, rather than polling."""

    def __init__(self):
        self._buffer = bytearray()
        self._condition = threading.Condition()

    def push(self, chunk: bytes) -> None:
        with self._condition:
            self._buffer.extend(chunk)
            self._condition.notify_all()

    def pull(self, num_bytes: int, timeout_seconds: float) -> bytes:
        with self._condition:
            deadline = time.monotonic() + timeout_seconds
            while len(self._buffer) < num_bytes:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
            if len(self._buffer) >= num_bytes:
                result = bytes(self._buffer[:num_bytes])
                del self._buffer[:num_bytes]
                return result
            result = bytes(self._buffer) + b"\x00" * (num_bytes - len(self._buffer))
            self._buffer.clear()
            return result


def chunk_pcm(pcm_audio: bytes, chunk_bytes: int) -> list[bytes]:
    """Splits pcm_audio into chunk_bytes-sized pieces; the last piece may
    be shorter. Empty input returns an empty list."""
    return [pcm_audio[i : i + chunk_bytes] for i in range(0, len(pcm_audio), chunk_bytes)]


def build_bot_output_message(chunk: bytes, sample_rate: int) -> str:
    """The JSON message Attendee's realtime-audio websocket protocol
    expects for audio the bot should speak into the meeting."""
    return json.dumps(
        {
            "trigger": "realtime_audio.bot_output",
            "data": {
                "chunk": base64.b64encode(chunk).decode("ascii"),
                "sample_rate": sample_rate,
            },
        }
    )


def extract_mixed_audio_chunk(raw_message: str) -> bytes | None:
    """Parses one incoming websocket message and returns the decoded PCM
    bytes if it's a realtime_audio.mixed message, else None (any other
    trigger type, or an unparseable message, is ignored)."""
    try:
        payload = json.loads(raw_message)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("trigger") != "realtime_audio.mixed":
        return None
    chunk_b64 = payload.get("data", {}).get("chunk")
    if not chunk_b64:
        return None
    return base64.b64decode(chunk_b64)


def paced_send(
    chunks: list[bytes],
    chunk_duration_seconds: float,
    send_fn: Callable[[bytes], None],
    stop_event: threading.Event,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """Calls send_fn(chunk) for each chunk in order, sleeping
    chunk_duration_seconds between sends via sleep_fn (injectable so
    tests don't pay real wall-clock time). Stops before sending a chunk
    if stop_event is set -- this is what lets stop_playback() actually
    truncate unsent audio instead of only updating bookkeeping after
    everything has already been handed off."""
    for chunk in chunks:
        if stop_event.is_set():
            return
        send_fn(chunk)
        sleep_fn(chunk_duration_seconds)
