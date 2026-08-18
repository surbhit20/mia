from mia.audio.recall_bridge import RecallAudioBridge


def test_read_frame_returns_pushed_audio_without_a_real_connection():
    bridge = RecallAudioBridge(port=0, sample_rate=16000)
    # 30ms at 16kHz mono 16-bit = 960 bytes
    bridge._frame_buffer.push(b"\x01\x02" * 480)

    frame = bridge.read_frame(frame_ms=30)

    assert frame == b"\x01\x02" * 480
    assert len(frame) == 960


def test_read_frame_pads_silence_when_no_audio_pushed():
    bridge = RecallAudioBridge(port=0, sample_rate=16000)

    frame = bridge.read_frame(frame_ms=30)

    assert frame == b"\x00" * 960


def test_enter_and_exit_start_and_stop_the_server_cleanly():
    # port=0 lets the OS assign any free port -- this test only checks
    # that startup/shutdown of the real asyncio server doesn't raise.
    bridge = RecallAudioBridge(port=0, sample_rate=16000)

    with bridge:
        assert bridge._server is not None


def test_port_is_released_after_exit():
    # Regression test: a prior bridge implementation (built for a
    # different, unmerged integration) left its listening socket open
    # after __exit__, so a second bridge on the same fixed port failed to
    # bind. Use a fixed, unusual port (not 0) so this actually exercises
    # reuse of the same port, not two different OS-assigned ones.
    bridge1 = RecallAudioBridge(port=18766, sample_rate=16000)
    with bridge1:
        pass

    bridge2 = RecallAudioBridge(port=18766, sample_rate=16000)
    with bridge2:
        assert bridge2._server is not None


def test_read_frame_does_not_fabricate_silence_at_recall_chunk_cadence():
    # Regression: read_frame() derived its starvation timeout from frame_ms
    # (32ms -> 64ms), but Recall delivers ~200ms chunks. The buffer therefore
    # ran dry between every chunk and pull() substituted silence, measured at
    # 25% of all frames during continuous speech. That fabricated silence was
    # fed to Deepgram *inside* live utterances, mangling transcription and
    # making the wake word register about one attempt in seven.
    import threading
    import time

    bridge = RecallAudioBridge(port=0, sample_rate=16000)
    # 200ms @ 16kHz 16-bit mono = 0.2 * 16000 * 2 = 6400 bytes. Sizing this
    # correctly matters: a short chunk understates arrival rate and would
    # make this test pass for the wrong reason.
    chunk = b"\xaa\xbb" * 3200
    assert len(chunk) == 6400
    stop = threading.Event()

    def _produce() -> None:
        next_at = time.monotonic()
        while not stop.is_set():
            bridge._frame_buffer.push(chunk)
            next_at += 0.200
            delay = next_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    producer = threading.Thread(target=_produce, daemon=True)
    producer.start()
    try:
        time.sleep(0.05)  # let the first chunk land
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            bridge.read_frame(frame_ms=32)
    finally:
        stop.set()
        producer.join(timeout=1)

    fb = bridge._frame_buffer
    padded_share = fb.pulls_padded / fb.pulls_served
    assert padded_share < 0.05, (
        f"{padded_share:.0%} of frames were silence-padded while audio was "
        f"arriving continuously ({fb.pulls_padded}/{fb.pulls_served})"
    )


import asyncio
import json


def _run_messages(bridge, messages):
    """Drive _handle_connection with a fake websocket yielding `messages`."""

    class _FakeWebsocket:
        def __aiter__(self):
            async def _gen():
                for message in messages:
                    yield message

            return _gen()

    asyncio.run(bridge._handle_connection(_FakeWebsocket()))


def test_transcript_messages_land_in_the_transcript_log():
    bridge = RecallAudioBridge(port=0, sample_rate=16000)
    message = json.dumps(
        {
            "event": "transcript.data",
            "data": {
                "data": {
                    "words": [{"text": "hello"}],
                    "participant": {"id": 1, "name": "Sarah"},
                }
            },
        }
    )

    _run_messages(bridge, [message])

    assert bridge.transcript_log.utterance_count() == 1
    assert bridge.transcript_log.render(bridge.roster) == "Sarah: hello"


def test_participant_events_populate_the_roster():
    bridge = RecallAudioBridge(port=0, sample_rate=16000)
    message = json.dumps(
        {
            "event": "participant_events.join",
            "data": {"data": {"participant": {"id": 4, "name": "Raj"}}},
        }
    )

    _run_messages(bridge, [message])

    assert bridge.roster.name_for(4) == "Raj"


def test_audio_still_reaches_the_frame_buffer():
    # Regression: routing must not break the path the call loop depends on.
    import base64

    bridge = RecallAudioBridge(port=0, sample_rate=16000)
    message = json.dumps(
        {
            "event": "audio_mixed_raw.data",
            "data": {"data": {"buffer": base64.b64encode(b"\x01\x02" * 480).decode("ascii")}},
        }
    )

    _run_messages(bridge, [message])

    assert bridge.read_frame(frame_ms=30) == b"\x01\x02" * 480


def test_unrecognized_events_are_counted_not_raised():
    bridge = RecallAudioBridge(port=0, sample_rate=16000)

    _run_messages(bridge, [json.dumps({"event": "participant_events.leave", "data": {}})])

    assert bridge.stats()["messages_unparsed"] == 1


def test_a_transcript_parsing_failure_does_not_close_the_connection():
    # Regression: before this branch the non-audio fall-through was a plain
    # counter increment, which cannot raise. The transcript/participant
    # parsers introduced here run on the same connection the live audio path
    # depends on, so an exception here must never propagate out of
    # _handle_connection and close the websocket -- that would deafen the
    # bot for the rest of a billed meeting.
    from unittest.mock import patch

    bridge = RecallAudioBridge(port=0, sample_rate=16000)
    message = json.dumps(
        {
            "event": "transcript.data",
            "data": {
                "data": {
                    "words": [{"text": "hello"}],
                    "participant": {"id": 1, "name": "Sarah"},
                }
            },
        }
    )

    with patch(
        "mia.audio.recall_bridge.extract_transcript_utterance",
        side_effect=RuntimeError("boom"),
    ):
        _run_messages(bridge, [message])  # must not raise

    assert bridge.routing_errors == 1
    assert bridge.stats()["routing_errors"] == 1
