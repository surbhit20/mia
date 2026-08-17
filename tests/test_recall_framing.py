import base64
import json
import threading
import time

from mia.audio.recall_framing import FrameBuffer, extract_mixed_audio_chunk


def test_frame_buffer_returns_pushed_bytes_exactly():
    buffer = FrameBuffer()
    buffer.push(b"0123456789")

    result = buffer.pull(num_bytes=10, timeout_seconds=1.0)

    assert result == b"0123456789"


def test_frame_buffer_returns_partial_pull_and_keeps_remainder():
    buffer = FrameBuffer()
    buffer.push(b"0123456789")

    first = buffer.pull(num_bytes=4, timeout_seconds=1.0)
    second = buffer.pull(num_bytes=6, timeout_seconds=1.0)

    assert first == b"0123"
    assert second == b"456789"


def test_frame_buffer_pads_with_silence_on_timeout():
    buffer = FrameBuffer()
    buffer.push(b"01")

    start = time.monotonic()
    result = buffer.pull(num_bytes=4, timeout_seconds=0.05)
    elapsed = time.monotonic() - start

    assert result == b"01\x00\x00"
    assert elapsed < 0.5


def test_frame_buffer_pull_returns_promptly_once_enough_data_pushed():
    buffer = FrameBuffer()

    def _push_after_delay():
        time.sleep(0.02)
        buffer.push(b"01234567")

    threading.Thread(target=_push_after_delay).start()
    start = time.monotonic()
    result = buffer.pull(num_bytes=8, timeout_seconds=1.0)
    elapsed = time.monotonic() - start

    assert result == b"01234567"
    assert elapsed < 0.5


def test_frame_buffer_drops_oldest_audio_past_the_cap():
    buffer = FrameBuffer(max_bytes=10)

    buffer.push(b"0123456789")
    buffer.push(b"abcde")

    result = buffer.pull(num_bytes=10, timeout_seconds=1.0)

    assert result == b"56789abcde"


def test_extract_mixed_audio_chunk_decodes_correct_event():
    message = json.dumps(
        {
            "event": "audio_mixed_raw.data",
            "data": {
                "data": {
                    "buffer": base64.b64encode(b"hello").decode("ascii"),
                    "timestamp": {"relative": 1.0, "absolute": "2026-08-17T00:00:00Z"},
                },
                "bot": {"id": "bot_abc123"},
            },
        }
    )

    assert extract_mixed_audio_chunk(message) == b"hello"


def test_extract_mixed_audio_chunk_ignores_other_events():
    message = json.dumps({"event": "participant_events.join", "data": {}})

    assert extract_mixed_audio_chunk(message) is None


def test_extract_mixed_audio_chunk_ignores_unparseable_message():
    assert extract_mixed_audio_chunk("not json") is None


def test_extract_mixed_audio_chunk_returns_none_on_null_data():
    message = json.dumps({"event": "audio_mixed_raw.data", "data": None})

    assert extract_mixed_audio_chunk(message) is None


def test_extract_mixed_audio_chunk_returns_none_on_non_dict_inner_data():
    message = json.dumps({"event": "audio_mixed_raw.data", "data": {"data": [1, 2, 3]}})

    assert extract_mixed_audio_chunk(message) is None


def test_extract_mixed_audio_chunk_returns_none_on_missing_buffer():
    message = json.dumps({"event": "audio_mixed_raw.data", "data": {"data": {}}})

    assert extract_mixed_audio_chunk(message) is None


def test_extract_mixed_audio_chunk_returns_none_on_invalid_base64():
    message = json.dumps({"event": "audio_mixed_raw.data", "data": {"data": {"buffer": "not-valid-base64!!!"}}})

    assert extract_mixed_audio_chunk(message) is None
