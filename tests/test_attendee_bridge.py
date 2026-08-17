import time

from mia.audio.attendee_bridge import AttendeeAudioBridge


def test_read_frame_returns_pushed_audio_without_a_real_connection():
    bridge = AttendeeAudioBridge(port=0, sample_rate=16000)
    # 30ms at 16kHz mono 16-bit = 960 bytes
    bridge._frame_buffer.push(b"\x01\x02" * 480)

    frame = bridge.read_frame(frame_ms=30)

    assert frame == b"\x01\x02" * 480
    assert len(frame) == 960


def test_read_frame_pads_silence_when_no_audio_pushed():
    bridge = AttendeeAudioBridge(port=0, sample_rate=16000)

    frame = bridge.read_frame(frame_ms=30)

    assert frame == b"\x00" * 960


def test_is_playback_active_true_immediately_after_start_playback():
    bridge = AttendeeAudioBridge(port=0, sample_rate=16000)
    # 16000 samples/sec * 2 bytes/sample * 1 second = 32000 bytes
    bridge.start_playback(b"\x01\x02" * 16000)

    assert bridge.is_playback_active() is True


def test_is_playback_active_false_after_estimated_duration_elapses():
    bridge = AttendeeAudioBridge(port=0, sample_rate=16000)
    # 2 bytes of audio = 1 sample = 1/16000 second, effectively instant
    bridge.start_playback(b"\x01\x02")

    time.sleep(0.05)

    assert bridge.is_playback_active() is False


def test_stop_playback_marks_playback_inactive_immediately():
    bridge = AttendeeAudioBridge(port=0, sample_rate=16000)
    bridge.start_playback(b"\x01\x02" * 16000)  # ~1 second of audio
    assert bridge.is_playback_active() is True

    bridge.stop_playback()

    assert bridge.is_playback_active() is False


def test_start_playback_with_no_connection_does_not_raise():
    # _send() early-returns when self._connection is None -- start_playback
    # must not crash just because Attendee hasn't connected yet.
    bridge = AttendeeAudioBridge(port=0, sample_rate=16000)

    bridge.start_playback(b"\x01\x02" * 100)
    time.sleep(0.05)

    # No assertion beyond "did not raise" -- reaching this line is the test.


def test_enter_and_exit_start_and_stop_the_server_cleanly():
    # port=0 lets the OS assign any free port -- this test only checks
    # that startup/shutdown of the real asyncio server doesn't raise.
    bridge = AttendeeAudioBridge(port=0, sample_rate=16000)

    with bridge:
        assert bridge._server is not None
