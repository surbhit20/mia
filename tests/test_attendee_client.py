from unittest.mock import MagicMock, patch

import pytest

from mia.attendee_client import bot_state, create_bot, leave, set_avatar_image, wait_until_joined


@patch("mia.attendee_client.requests.post")
def test_create_bot_posts_correct_payload_and_returns_id(mock_post):
    mock_post.return_value = MagicMock(status_code=200)
    mock_post.return_value.json.return_value = {"id": "bot_abc123"}

    bot_id = create_bot(
        base_url="http://localhost:8000",
        api_key="test-key",
        meeting_url="https://meet.google.com/xyz",
        websocket_url="ws://host.docker.internal:8765/audio",
        bot_name="Mia",
    )

    assert bot_id == "bot_abc123"
    mock_post.assert_called_once_with(
        "http://localhost:8000/api/v1/bots",
        headers={"Authorization": "Token test-key", "Content-Type": "application/json"},
        json={
            "meeting_url": "https://meet.google.com/xyz",
            "bot_name": "Mia",
            "websocket_settings": {
                "audio": {"url": "ws://host.docker.internal:8765/audio", "sample_rate": 16000},
            },
        },
        timeout=30,
    )


@patch("mia.attendee_client.requests.get")
def test_bot_state_returns_state_field(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {"state": "joining"}

    state = bot_state(base_url="http://localhost:8000", api_key="test-key", bot_id="bot_abc123")

    assert state == "joining"
    mock_get.assert_called_once_with(
        "http://localhost:8000/api/v1/bots/bot_abc123",
        headers={"Authorization": "Token test-key"},
        timeout=15,
    )


@patch("mia.attendee_client.requests.get")
def test_wait_until_joined_returns_when_state_is_joined_recording(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {"state": "joined_recording"}

    wait_until_joined(
        base_url="http://localhost:8000",
        api_key="test-key",
        bot_id="bot_abc123",
        timeout_seconds=5.0,
        poll_interval_seconds=0.01,
    )

    mock_get.assert_called_once()


@patch("mia.attendee_client.requests.get")
def test_wait_until_joined_raises_on_fatal_error_state(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {"state": "fatal_error"}

    with pytest.raises(RuntimeError, match="fatal_error"):
        wait_until_joined(
            base_url="http://localhost:8000",
            api_key="test-key",
            bot_id="bot_abc123",
            timeout_seconds=5.0,
            poll_interval_seconds=0.01,
        )


@patch("mia.attendee_client.requests.get")
def test_wait_until_joined_raises_timeout_error_when_never_joined(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    mock_get.return_value.json.return_value = {"state": "joining"}

    with pytest.raises(TimeoutError):
        wait_until_joined(
            base_url="http://localhost:8000",
            api_key="test-key",
            bot_id="bot_abc123",
            timeout_seconds=0.05,
            poll_interval_seconds=0.01,
        )


@patch("mia.attendee_client.requests.post")
def test_set_avatar_image_posts_base64_encoded_image(mock_post, tmp_path):
    mock_post.return_value = MagicMock(status_code=200)
    image_path = tmp_path / "avatar.png"
    image_path.write_bytes(b"fake-png-bytes")

    set_avatar_image(base_url="http://localhost:8000", api_key="test-key", bot_id="bot_abc123", image_path=image_path)

    mock_post.assert_called_once_with(
        "http://localhost:8000/api/v1/bots/bot_abc123/output_image",
        headers={"Authorization": "Token test-key", "Content-Type": "application/json"},
        json={"type": "image/png", "data": "ZmFrZS1wbmctYnl0ZXM="},
        timeout=30,
    )


@patch("mia.attendee_client.requests.post")
def test_leave_posts_to_leave_endpoint(mock_post):
    mock_post.return_value = MagicMock(status_code=200)

    leave(base_url="http://localhost:8000", api_key="test-key", bot_id="bot_abc123")

    mock_post.assert_called_once_with(
        "http://localhost:8000/api/v1/bots/bot_abc123/leave",
        headers={"Authorization": "Token test-key"},
        timeout=15,
    )
