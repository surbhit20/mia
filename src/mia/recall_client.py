import base64
import time

import requests

_TERMINAL_FAILURE_STATES = {"call_ended", "fatal"}
_SUCCESS_STATE = "in_call_recording"


def _raise_for_status(response: requests.Response) -> None:
    """raise_for_status() that keeps the response body.

    Recall explains rejections in the body, not the status line, and the
    stock exception drops it -- a real 400 ("Cannot specify realtime
    endpoint events for artifacts that are not configured") surfaced in
    mia's logs as a bare "join failed", which took a manual API replay to
    diagnose.
    """
    if response.status_code < 400:
        return
    raise requests.HTTPError(
        f"{response.status_code} from {response.url}: {response.text[:500]}",
        response=response,
    )


def create_bot(base_url: str, api_key: str, meeting_url: str, websocket_url: str, bot_name: str) -> str:
    response = requests.post(
        f"{base_url}/api/v1/bot/",
        headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
        json={
            "meeting_url": meeting_url,
            "bot_name": bot_name,
            "recording_config": {
                # Declaring the artifact is required, not redundant with the
                # endpoint below: referencing audio_mixed_raw.data without
                # this key is rejected with "Cannot specify realtime endpoint
                # events for artifacts that are not configured".
                "audio_mixed_raw": {},
                # Same rule for transcript.data. prioritize_accuracy over
                # prioritize_low_latency because nothing waits on these --
                # they are summarized after the call, not spoken during it.
                "transcript": {
                    "provider": {
                        "recallai_streaming": {
                            "mode": "prioritize_accuracy",
                            "language_code": "en",
                        }
                    },
                    "diarization": {"use_separate_streams_when_available": True},
                },
                "realtime_endpoints": [
                    {
                        "type": "websocket",
                        "url": websocket_url,
                        "events": [
                            "audio_mixed_raw.data",
                            "transcript.data",
                            # Names are frequently null on transcript.data;
                            # these supply the roster used to resolve them.
                            "participant_events.join",
                            "participant_events.update",
                        ],
                    },
                ],
            },
        },
        timeout=30,
    )
    _raise_for_status(response)
    return response.json()["id"]


def bot_state(base_url: str, api_key: str, bot_id: str, timeout_seconds: float = 15.0) -> str:
    # Called every few seconds from the same thread that reads audio frames
    # during a live call -- in-call callers should pass a smaller
    # timeout_seconds than the default so a hung status GET can't stall
    # audio reading for the full 15s.
    response = requests.get(
        f"{base_url}/api/v1/bot/{bot_id}/",
        headers={"Authorization": f"Token {api_key}"},
        timeout=timeout_seconds,
    )
    _raise_for_status(response)
    status_changes = response.json().get("status_changes", [])
    if not status_changes:
        return ""
    return status_changes[-1].get("code", "")


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
    _raise_for_status(response)


def leave(base_url: str, api_key: str, bot_id: str) -> None:
    response = requests.post(
        f"{base_url}/api/v1/bot/{bot_id}/leave_call/",
        headers={"Authorization": f"Token {api_key}"},
        timeout=15,
    )
    _raise_for_status(response)
