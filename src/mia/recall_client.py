import base64
import time

import requests

_TERMINAL_FAILURE_STATES = {"call_ended", "fatal"}
_SUCCESS_STATE = "in_call_recording"


def create_bot(base_url: str, api_key: str, meeting_url: str, websocket_url: str, bot_name: str) -> str:
    response = requests.post(
        f"{base_url}/api/v1/bot/",
        headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
        json={
            "meeting_url": meeting_url,
            "bot_name": bot_name,
            "recording_config": {
                "realtime_endpoints": [
                    {"type": "websocket", "url": websocket_url, "events": ["audio_mixed_raw.data"]},
                ],
            },
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["id"]


def bot_state(base_url: str, api_key: str, bot_id: str) -> str:
    response = requests.get(
        f"{base_url}/api/v1/bot/{bot_id}/",
        headers={"Authorization": f"Token {api_key}"},
        timeout=15,
    )
    response.raise_for_status()
    status_changes = response.json().get("status_changes", [])
    if not status_changes:
        return ""
    return status_changes[-1]["code"]


def wait_until_joined(
    base_url: str,
    api_key: str,
    bot_id: str,
    timeout_seconds: float = 60.0,
    poll_interval_seconds: float = 2.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = bot_state(base_url, api_key, bot_id)
        if state == _SUCCESS_STATE:
            return
        if state in _TERMINAL_FAILURE_STATES:
            raise RuntimeError(f"bot {bot_id} failed to join: state={state}")
        time.sleep(poll_interval_seconds)
    raise TimeoutError(f"bot {bot_id} did not reach {_SUCCESS_STATE} within {timeout_seconds}s")


def speak(base_url: str, api_key: str, bot_id: str, mp3_bytes: bytes) -> None:
    response = requests.post(
        f"{base_url}/api/v1/bot/{bot_id}/output_audio/",
        headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
        json={"kind": "mp3", "b64_data": base64.b64encode(mp3_bytes).decode("ascii")},
        timeout=30,
    )
    response.raise_for_status()


def leave(base_url: str, api_key: str, bot_id: str) -> None:
    response = requests.post(
        f"{base_url}/api/v1/bot/{bot_id}/leave_call/",
        headers={"Authorization": f"Token {api_key}"},
        timeout=15,
    )
    response.raise_for_status()
