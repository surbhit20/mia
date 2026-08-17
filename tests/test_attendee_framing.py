import base64
import json
import threading
import time

from mia.audio.attendee_framing import (
    FrameBuffer,
    build_bot_output_message,
    chunk_pcm,
    extract_mixed_audio_chunk,
    paced_send,
)


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


def test_chunk_pcm_splits_into_fixed_size_pieces():
    assert chunk_pcm(b"0123456789", chunk_bytes=4) == [b"0123", b"4567", b"89"]


def test_chunk_pcm_empty_input_returns_empty_list():
    assert chunk_pcm(b"", chunk_bytes=4) == []


def test_build_bot_output_message_shape():
    message = build_bot_output_message(chunk=b"abc", sample_rate=16000)

    payload = json.loads(message)

    assert payload["trigger"] == "realtime_audio.bot_output"
    assert payload["data"]["sample_rate"] == 16000
    assert base64.b64decode(payload["data"]["chunk"]) == b"abc"


def test_extract_mixed_audio_chunk_decodes_correct_trigger():
    message = json.dumps(
        {
            "bot_id": "bot_123",
            "trigger": "realtime_audio.mixed",
            "data": {"chunk": base64.b64encode(b"hello").decode("ascii"), "sample_rate": 16000, "timestamp_ms": 1},
        }
    )

    assert extract_mixed_audio_chunk(message) == b"hello"


def test_extract_mixed_audio_chunk_ignores_other_triggers():
    message = json.dumps({"trigger": "realtime_audio.per_participant", "data": {"chunk": "x"}})

    assert extract_mixed_audio_chunk(message) is None


def test_extract_mixed_audio_chunk_ignores_unparseable_message():
    assert extract_mixed_audio_chunk("not json") is None


def test_paced_send_calls_send_fn_for_every_chunk_in_order():
    sent = []

    paced_send(
        chunks=[b"a", b"b", b"c"],
        chunk_duration_seconds=0.01,
        send_fn=sent.append,
        stop_event=threading.Event(),
        sleep_fn=lambda _seconds: None,
    )

    assert sent == [b"a", b"b", b"c"]


def test_paced_send_stops_early_when_stop_event_set_mid_stream():
    sent = []
    stop_event = threading.Event()

    def _send(chunk):
        sent.append(chunk)
        if chunk == b"b":
            stop_event.set()

    paced_send(
        chunks=[b"a", b"b", b"c", b"d"],
        chunk_duration_seconds=0.01,
        send_fn=_send,
        stop_event=stop_event,
        sleep_fn=lambda _seconds: None,
    )

    assert sent == [b"a", b"b"]
