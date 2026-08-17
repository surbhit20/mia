import ssl
import time

import websockets.sync.client

from mia.audio.attendee_bridge import AttendeeAudioBridge
from mia.audio.tls_cert import ensure_self_signed_cert


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


def test_port_is_released_after_exit():
    # Regression test: __exit__ must close the server's listening socket,
    # not just stop the event loop. Without proper socket closure, the port
    # remains bound and a second bridge on the same fixed port fails with
    # OSError: Address already in use. This matters because Task 5 constructs
    # AttendeeAudioBridge on a fixed configured port (8765) repeatedly across
    # the app's lifetime.
    fixed_port = 18765
    bridge1 = AttendeeAudioBridge(port=fixed_port, sample_rate=16000)

    with bridge1:
        assert bridge1._server is not None

    # After first bridge exits, the port should be released.
    # This second bridge should succeed without OSError.
    bridge2 = AttendeeAudioBridge(port=fixed_port, sample_rate=16000)
    with bridge2:
        assert bridge2._server is not None


def test_serves_wss_and_receives_audio_when_cert_and_key_provided(tmp_path):
    # Attendee's API rejects any websocket_settings.audio.url that isn't
    # wss://, so the bridge must actually terminate TLS when a cert/key
    # are given -- this proves our own server-side TLS setup is correct
    # (a real wss:// handshake, a real message sent, real bytes landing
    # in the frame buffer). Whether Attendee's own remote client trusts
    # this self-signed cert is a separate, live-environment concern this
    # unit test doesn't cover.
    cert_path, key_path = ensure_self_signed_cert(tmp_path)
    bridge = AttendeeAudioBridge(port=0, sample_rate=16000, cert_path=cert_path, key_path=key_path)

    with bridge:
        actual_port = next(iter(bridge._server.sockets)).getsockname()[1]

        client_ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_ssl_context.check_hostname = False
        client_ssl_context.verify_mode = ssl.CERT_NONE

        with websockets.sync.client.connect(f"wss://localhost:{actual_port}/audio", ssl=client_ssl_context) as client:
            # base64("hello") == "aGVsbG8="
            client.send('{"trigger": "realtime_audio.mixed", "data": {"chunk": "aGVsbG8=", "sample_rate": 16000}}')
            time.sleep(0.2)

        frame = bridge.read_frame(frame_ms=1)  # 1ms @ 16kHz*2 bytes = 32 bytes, but we only pushed 5
        assert frame.startswith(b"hello")
