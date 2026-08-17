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
