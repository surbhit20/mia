import base64
import binascii
import json
import threading
import time


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


def extract_mixed_audio_chunk(raw_message: str) -> bytes | None:
    """Parses one incoming websocket message and returns the decoded PCM
    bytes if it's an audio_mixed_raw.data event with a valid
    data.data.buffer field, else None (any other event type, an
    unparseable message, a malformed data/data.data shape, or invalid
    base64 is safely ignored rather than raised)."""
    try:
        payload = json.loads(raw_message)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("event") != "audio_mixed_raw.data":
        return None
    outer_data = payload.get("data")
    if not isinstance(outer_data, dict):
        return None
    inner_data = outer_data.get("data")
    if not isinstance(inner_data, dict):
        return None
    buffer_b64 = inner_data.get("buffer")
    if not buffer_b64:
        return None
    try:
        return base64.b64decode(buffer_b64)
    except (AttributeError, TypeError, ValueError, binascii.Error):
        return None
