"""Tests for StreamingSTT's reconnect-on-disconnect behavior.

The rest of StreamingSTT (the actual Deepgram handshake, message dispatch)
is live-only by design -- see stt.py's module docstring -- but the
reconnect logic added here is pure control flow over start()/stop(), so it
is tested by monkeypatching those two methods instead of touching the
network.
"""

from unittest.mock import MagicMock

from mia.stt import StreamingSTT


def _make_stt():
    stt = StreamingSTT("fake-key", lambda text, is_final: None)
    stt.start = MagicMock()
    stt.stop = MagicMock()
    return stt


def test_send_frame_reconnects_when_disconnected():
    stt = _make_stt()
    stt._connected = False

    stt.send_frame(b"\x00\x00")

    stt.stop.assert_called_once()
    stt.start.assert_called_once()


def test_send_keepalive_reconnects_when_disconnected():
    stt = _make_stt()
    stt._connected = False

    stt.send_keepalive_if_idle()

    stt.stop.assert_called_once()
    stt.start.assert_called_once()


def test_no_reconnect_attempt_when_already_connected():
    stt = _make_stt()
    stt._connected = True
    stt._connection = MagicMock()
    stt._last_send = 0.0  # far in the past, so send_keepalive_if_idle would also fire a keepalive

    stt.send_frame(b"\x00\x00")

    stt.stop.assert_not_called()
    stt.start.assert_not_called()


def test_reconnect_attempts_are_backed_off():
    stt = _make_stt()
    stt._connected = False

    stt.send_frame(b"\x00\x00")
    stt.send_frame(b"\x00\x00")  # immediately after; within the backoff window

    stt.stop.assert_called_once()
    stt.start.assert_called_once()


def test_reconnect_failure_is_caught_and_logged_not_raised():
    stt = _make_stt()
    stt._connected = False
    stt.start.side_effect = RuntimeError("deepgram unreachable")

    stt.send_frame(b"\x00\x00")  # must not raise

    assert stt.is_connected() is False
