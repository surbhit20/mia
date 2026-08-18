import base64
from unittest.mock import MagicMock, patch

import pytest

from mia.recall_client import bot_state, create_bot, leave, speak, wait_until_joined


@patch("mia.recall_client.requests.post")
def test_create_bot_posts_correct_payload_and_returns_id(mock_post):
    mock_post.return_value = MagicMock(status_code=200)
    mock_post.return_value.json.return_value = {"id": "bot_abc123"}

    bot_id = create_bot(
        base_url="https://us-west-2.recall.ai",
        api_key="test-key",
        meeting_url="https://meet.google.com/xyz",
        websocket_url="wss://example.ngrok.app/audio",
        bot_name="Mia",
    )

    assert bot_id == "bot_abc123"
    mock_post.assert_called_once_with(
        "https://us-west-2.recall.ai/api/v1/bot/",
        headers={"Authorization": "Token test-key", "Content-Type": "application/json"},
        json={
            "meeting_url": "https://meet.google.com/xyz",
            "bot_name": "Mia",
            "recording_config": {
                "audio_mixed_raw": {},
                "transcript": {
                    "provider": {
                        "recallai_streaming": {
                            "mode": "prioritize_low_latency",
                            "language_code": "en",
                        }
                    },
                    "diarization": {"use_separate_streams_when_available": True},
                },
                "realtime_endpoints": [
                    {
                        "type": "websocket",
                        "url": "wss://example.ngrok.app/audio",
                        "events": [
                            "audio_mixed_raw.data",
                            "transcript.data",
                            "participant_events.join",
                            "participant_events.update",
                        ],
                    },
                ],
            },
        },
        timeout=30,
    )


@patch("mia.recall_client.requests.get")
def test_bot_state_returns_most_recent_status_code(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {
        "status_changes": [
            {"code": "joining_call"},
            {"code": "in_waiting_room"},
        ]
    }

    state = bot_state(base_url="https://us-west-2.recall.ai", api_key="test-key", bot_id="bot_abc123")

    assert state == "in_waiting_room"
    mock_get.assert_called_once_with(
        "https://us-west-2.recall.ai/api/v1/bot/bot_abc123/",
        headers={"Authorization": "Token test-key"},
        timeout=15,
    )


@patch("mia.recall_client.requests.get")
def test_bot_state_returns_empty_string_when_no_status_changes_yet(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {"status_changes": []}

    state = bot_state(base_url="https://us-west-2.recall.ai", api_key="test-key", bot_id="bot_abc123")

    assert state == ""


@patch("mia.recall_client.requests.get")
def test_wait_until_joined_returns_when_state_is_in_call_recording(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {"status_changes": [{"code": "in_call_recording"}]}

    wait_until_joined(
        base_url="https://us-west-2.recall.ai",
        api_key="test-key",
        bot_id="bot_abc123",
        timeout_seconds=5.0,
        poll_interval_seconds=0.01,
    )

    mock_get.assert_called_once()


@patch("mia.recall_client.requests.get")
def test_wait_until_joined_raises_on_call_ended_state(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {"status_changes": [{"code": "call_ended"}]}

    with pytest.raises(RuntimeError, match="call_ended"):
        wait_until_joined(
            base_url="https://us-west-2.recall.ai",
            api_key="test-key",
            bot_id="bot_abc123",
            timeout_seconds=5.0,
            poll_interval_seconds=0.01,
        )


@patch("mia.recall_client.requests.get")
def test_wait_until_joined_raises_on_fatal_state(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {"status_changes": [{"code": "fatal"}]}

    with pytest.raises(RuntimeError, match="fatal"):
        wait_until_joined(
            base_url="https://us-west-2.recall.ai",
            api_key="test-key",
            bot_id="bot_abc123",
            timeout_seconds=5.0,
            poll_interval_seconds=0.01,
        )


@patch("mia.recall_client.requests.get")
def test_wait_until_joined_raises_timeout_error_when_never_joined(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {"status_changes": [{"code": "joining_call"}]}

    with pytest.raises(TimeoutError):
        wait_until_joined(
            base_url="https://us-west-2.recall.ai",
            api_key="test-key",
            bot_id="bot_abc123",
            timeout_seconds=0.05,
            poll_interval_seconds=0.01,
        )


@patch("mia.recall_client.requests.post")
def test_speak_posts_base64_encoded_mp3(mock_post):
    mock_post.return_value = MagicMock(status_code=200)

    speak(base_url="https://us-west-2.recall.ai", api_key="test-key", bot_id="bot_abc123", mp3_bytes=b"fake-mp3-bytes")

    mock_post.assert_called_once_with(
        "https://us-west-2.recall.ai/api/v1/bot/bot_abc123/output_audio/",
        headers={"Authorization": "Token test-key", "Content-Type": "application/json"},
        json={"kind": "mp3", "b64_data": base64.b64encode(b"fake-mp3-bytes").decode("ascii")},
        timeout=30,
    )


@patch("mia.recall_client.requests.post")
def test_leave_posts_to_leave_call_endpoint(mock_post):
    mock_post.return_value = MagicMock(status_code=200)

    leave(base_url="https://us-west-2.recall.ai", api_key="test-key", bot_id="bot_abc123")

    mock_post.assert_called_once_with(
        "https://us-west-2.recall.ai/api/v1/bot/bot_abc123/leave_call/",
        headers={"Authorization": "Token test-key"},
        timeout=15,
    )


@patch("mia.recall_client.requests.post")
def test_create_bot_error_includes_recall_response_body(mock_post):
    # Recall explains rejections in the body, not the status line. Losing it
    # turned a real 400 into a bare "join failed" in the logs.
    mock_post.return_value = MagicMock(
        status_code=400,
        url="https://us-west-2.recall.ai/api/v1/bot/",
        text='{"recording_config":{"non_field_errors":["Cannot specify realtime endpoint events for artifacts that are not configured: audio_mixed_raw"]}}',
    )

    with pytest.raises(Exception, match="artifacts that are not configured"):
        create_bot(
            base_url="https://us-west-2.recall.ai",
            api_key="test-key",
            meeting_url="https://meet.google.com/xyz",
            websocket_url="wss://example.ngrok.app/audio",
            bot_name="Mia",
        )
