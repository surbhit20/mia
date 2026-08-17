import base64
import time
from pathlib import Path

import requests

_TERMINAL_FAILURE_STATES = {"fatal_error", "ended"}


def create_bot(
    base_url: str,
    api_key: str,
    meeting_url: str,
    websocket_url: str,
    bot_name: str,
    sample_rate: int = 16000,
) -> str:
    response = requests.post(
        f"{base_url}/api/v1/bots",
        headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
        json={
            "meeting_url": meeting_url,
            "bot_name": bot_name,
            "websocket_settings": {
                "audio": {"url": websocket_url, "sample_rate": sample_rate},
            },
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["id"]


def bot_state(base_url: str, api_key: str, bot_id: str) -> str:
    response = requests.get(
        f"{base_url}/api/v1/bots/{bot_id}",
        headers={"Authorization": f"Token {api_key}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()["state"]


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
        if state == "joined_recording":
            return
        if state in _TERMINAL_FAILURE_STATES:
            raise RuntimeError(f"bot {bot_id} failed to join: state={state}")
        time.sleep(poll_interval_seconds)
    raise TimeoutError(f"bot {bot_id} did not reach joined_recording within {timeout_seconds}s")


def set_avatar_image(base_url: str, api_key: str, bot_id: str, image_path: Path) -> None:
    image_bytes = image_path.read_bytes()
    response = requests.post(
        f"{base_url}/api/v1/bots/{bot_id}/output_image",
        headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
        json={"type": "image/png", "data": base64.b64encode(image_bytes).decode("ascii")},
        timeout=30,
    )
    response.raise_for_status()


def leave(base_url: str, api_key: str, bot_id: str) -> None:
    response = requests.post(
        f"{base_url}/api/v1/bots/{bot_id}/leave",
        headers={"Authorization": f"Token {api_key}"},
        timeout=15,
    )
    response.raise_for_status()
