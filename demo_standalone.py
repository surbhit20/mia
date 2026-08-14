"""Standalone demo: the voice-agent core (wake word -> command -> Claude tool
call -> spoken confirmation) running against this machine's own mic and
speakers, with no Google Meet join involved.

This exists because live-testing the Meet-join path (Attendee, signed-in
bots) hit a 48-hour Google account-recovery hold mid-session. Every module
used here (VAD, wake-word matching, Deepgram STT, Claude tool dispatch, the
calendar tool, ElevenLabs TTS, the turn-state machine) is already built and
reviewed in Tasks 1-17 -- this script only swaps BlackHole for the system's
default audio devices and drops the meeting-detection/join layer that
`main.py` wraps around the same call loop.

Run with: python demo_standalone.py
Say: "Hey Mia, block 30 minutes for focus time" (or whatever WAKE_WORD is
set to in .env) and wait for the spoken confirmation.
"""

import threading
import time

from anthropic import Anthropic
from dotenv import load_dotenv
from googleapiclient.discovery import build

from mia.audio.capture import BlackHoleCapture
from mia.audio.injection import is_playback_active, start_playback, stop_playback
from mia.audio.vad import FrameVAD
from mia.command_buffer import CommandBuffer
from mia.config import Config
from mia.llm import ConversationHistory, dispatch_command
from mia.logging_setup import configure as configure_logging
from mia.logging_setup import safe_log
from mia.main import _authorize_google
from mia.stt import StreamingSTT
from mia.tools.base import ToolRegistry
from mia.tools.calendar_tool import build_calendar_tool
from mia.tools.gmail_tool import build_gmail_search_tool
from mia.tts import synthesize
from mia.turn_state import TurnState, TurnStateMachine
from mia.wakeword import WakeWordMatcher, is_self_echo

_FRAME_MS = 32
_SILENCE_FRAMES_TO_END_COMMAND = 24


def run() -> None:
    load_dotenv()
    config = Config.from_env()
    configure_logging(config)

    creds = _authorize_google(config)
    calendar_service = build("calendar", "v3", credentials=creds)
    gmail_service = build("gmail", "v1", credentials=creds)
    anthropic_client = Anthropic(api_key=config.anthropic_api_key)
    registry = ToolRegistry()
    registry.register(build_calendar_tool(calendar_service))
    registry.register(build_gmail_search_tool(gmail_service, anthropic_client))

    turn_state = TurnStateMachine()
    wake_word = WakeWordMatcher(config.wake_word, threshold=config.fuzzy_threshold)
    command_buffer = CommandBuffer()
    vad = FrameVAD(frame_ms=_FRAME_MS)
    history = ConversationHistory()
    lock = threading.Lock()
    # What mia is currently speaking, so on_transcript can tell her own TTS
    # looping back through capture (BlackHole routes injected audio back into
    # what mia captures, by design) apart from a real barge-in (Finding 1).
    current_speech: list[str | None] = [None]

    turn_state.wake_word_detected()

    print(f"\nListening for \"{config.wake_word}\" on your default microphone.")
    print("Press Ctrl+C to stop.\n")

    def on_transcript(text: str, is_final: bool) -> None:
        if not is_final:
            return
        with lock:
            if not turn_state.should_process_stt():
                return
            if turn_state.current() == TurnState.SPEAKING and current_speech[0] is not None:
                if is_self_echo(text, current_speech[0]):
                    return
            if command_buffer.is_capturing():
                command_buffer.append(text + " ")
                print(f"  ...captured: {text!r}")
                return
            if wake_word.matches(text):
                # Stopping playback here is what makes this a real barge-in
                # when the wake word arrives during SPEAKING -- harmless
                # no-op if nothing is currently playing.
                if turn_state.current() == TurnState.SPEAKING:
                    print("  [barge-in] interrupted.\n")
                stop_playback()
                turn_state.wake_word_detected()
                command_buffer.start()
                command_buffer.append(text + " ")
                print(f"\n[wake word detected] heard: {text!r}")
                safe_log("info", "wake word detected")

    # device_name=None -> sounddevice's system default input/output, instead
    # of the "BlackHole 2ch" default these modules use for the live-Meet path.
    with BlackHoleCapture(device_name=None, sample_rate=16000) as capture:
        stt = StreamingSTT(config.deepgram_api_key, on_transcript)
        stt.start()
        try:
            silence_frames = 0
            while True:
                turn_state.tick()

                # A barge-in wake word already moved the machine out of
                # SPEAKING (and stopped playback) from on_transcript, on the
                # STT listener thread -- this only fires for a response that
                # finished on its own, uninterrupted.
                with lock:
                    if turn_state.current() == TurnState.SPEAKING and not is_playback_active():
                        turn_state.finish_speaking()
                        print("  done.\n")

                frame = capture.read_frame(frame_ms=_FRAME_MS)

                if turn_state.should_process_stt():
                    stt.send_frame(frame)
                stt.send_keepalive_if_idle()

                is_speech = vad.is_speech(frame)

                command_text = None
                with lock:
                    if not command_buffer.is_capturing():
                        silence_frames = 0
                    elif is_speech:
                        silence_frames = 0
                    else:
                        silence_frames += 1
                        if silence_frames >= _SILENCE_FRAMES_TO_END_COMMAND:
                            silence_frames = 0
                            command_text = command_buffer.on_silence()
                            if command_text:
                                turn_state.command_captured()

                if not command_text:
                    continue

                print(f"[command captured] {command_text!r}")
                try:
                    print("  dispatching to Claude...")
                    result = dispatch_command(anthropic_client, registry, command_text, history)
                    safe_log("info", "command dispatched", tool=result.tool_name)

                    if result.tool_name is None and not wake_word.strip_wake_phrase(
                        command_text
                    ):
                        print("  (bare wake phrase, staying silent)")
                        safe_log("info", "bare wake phrase ignored")
                        with lock:
                            turn_state.abandon_turn()
                    else:
                        print(f"  speaking: {result.confirmation!r}")
                        audio = synthesize(config.elevenlabs_api_key, result.confirmation)
                        with lock:
                            turn_state.start_speaking()
                            current_speech[0] = result.confirmation
                            start_playback(audio, device_name=None)
                except Exception as exc:
                    print(f"  [error] {exc}")
                    safe_log("error", "voice turn failed", error=str(exc))
                    with lock:
                        turn_state.abandon_turn()
        finally:
            stt.stop()


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\nStopped.")
