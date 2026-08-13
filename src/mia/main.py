"""Process entrypoint: wires every module into the detect -> prompt -> join ->
live-voice-loop -> leave orchestration described in the design spec.

Run with `python -m mia.main`.

Deviations from the plan's draft code are marked with `NOTE:` comments below;
each one is either a bug in the draft or a mismatch with a dependency module's
actually-committed behaviour (see task-19-report.md for the full rationale).
"""

import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from mia.audio.capture import BlackHoleCapture
from mia.audio.injection import inject_into_virtual_mic
from mia.audio.vad import FrameVAD
from mia.command_buffer import CommandBuffer
from mia.config import Config
from mia.detection.calendar_enricher import find_current_meeting_title
from mia.detection.mic_monitor import is_mic_active
from mia.detection.tab_detector import find_active_meet_tab
from mia.detection.trigger import decide
from mia.join_worker import JoinWorker
from mia.llm import dispatch_command
from mia.logging_setup import configure as configure_logging
from mia.logging_setup import safe_log
from mia.notify import NotificationResult, prompt_join
from mia.state import StateStore
from mia.stt import StreamingSTT
from mia.tools.base import ToolRegistry
from mia.tools.calendar_tool import build_calendar_tool
from mia.tts import synthesize
from mia.turn_state import TurnStateMachine
from mia.wakeword import WakeWordMatcher

_TOKEN_PATH = Path("~/.mia/token.json").expanduser()
_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# Outer detection poll interval.
_POLL_INTERVAL_SECONDS = 5.0

# Audio framing for the live loop. 30ms matches the spec's "~20-30ms frames"
# and FrameVAD's own default.
_FRAME_MS = 30

# NOTE: the draft re-ran the AppleScript tab scan on *every* audio frame
# (~33 subprocess spawns/second), which would starve the real-time audio loop.
# The leave check is time-throttled instead.
_LEAVE_CHECK_INTERVAL_SECONDS = 3.0

# tab_detector.find_active_meet_tab() returns None on an osascript timeout or
# non-zero exit, so a single miss is not proof the call ended. Require two
# consecutive misses before leaving.
_LEAVE_CONFIRM_CHECKS = 2

# NOTE: the draft ended command capture on the first non-speech frame (30ms),
# which would cut every command off at its first natural pause -- and, worse,
# CommandBuffer.on_silence() stops capturing even when it returns None, so a
# single silent frame right after the wake word would abandon the command
# entirely. Require a run of consecutive non-speech frames instead
# (25 * 30ms = 750ms, sub-second per the spec's turn-taking requirement, and
# long enough to survive a natural pause after the wake phrase).
_SILENCE_FRAMES_TO_END_COMMAND = 25


def _save_credentials(creds: Credentials) -> None:
    _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_PATH.write_text(creds.to_json())
    _TOKEN_PATH.chmod(0o600)


def _authorize_calendar(config: Config):
    creds = None
    if _TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), _SCOPES)
        # NOTE: the draft used the cached token as-is. A cached access token is
        # expired within an hour of the first run, so without this refresh the
        # calendar enricher and the calendar tool would fail on every
        # subsequent run of the process.
        if not creds.valid and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                _save_credentials(creds)
            except Exception as exc:
                safe_log("warning", "calendar token refresh failed", error=str(exc))
                creds = None

    if creds is None or not creds.valid:
        flow = InstalledAppFlow.from_client_config(
            {
                "installed": {
                    "client_id": config.google_client_id,
                    "client_secret": config.google_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            _SCOPES,
        )
        creds = flow.run_local_server(port=0)
        _save_credentials(creds)

    return build("calendar", "v3", credentials=creds)


def _run_call_loop(
    config: Config,
    registry: ToolRegistry,
    anthropic_client: Anthropic,
    meet_url: str,
) -> None:
    turn_state = TurnStateMachine()
    wake_word = WakeWordMatcher(config.wake_word, threshold=config.fuzzy_threshold)
    command_buffer = CommandBuffer()
    vad = FrameVAD(frame_ms=_FRAME_MS)

    # NOTE: StreamingSTT (Task 15) dispatches on_transcript from a background
    # listener thread, while the loop below mutates the same turn_state /
    # command_buffer from this thread. Guard the check-then-act sequences on
    # both sides. The lock is only ever held for state transitions, never
    # across the LLM/TTS/injection calls, so the listener thread never blocks
    # on slow network work.
    lock = threading.Lock()

    # NOTE: TurnStateMachine starts in IDLE, and should_process_stt() is True
    # only in LISTENING. The draft gated the wake-word check behind
    # should_process_stt(), but the only IDLE -> LISTENING transition the
    # machine exposes is wake_word_detected() -- so nothing would ever have
    # left IDLE and the bot would have been deaf for the whole call. Entering
    # the call is what starts listening, so make that transition here.
    turn_state.wake_word_detected()

    def on_transcript(text: str, is_final: bool) -> None:
        if not is_final:
            return
        with lock:
            if not turn_state.should_process_stt():
                return
            if command_buffer.is_capturing():
                command_buffer.append(text + " ")
                return
            if wake_word.matches(text):
                turn_state.wake_word_detected()
                command_buffer.start()
                # NOTE: the draft dropped the fragment the wake word arrived
                # in. Deepgram emits one final transcript per speech segment,
                # so "Hey Bot, block thirty minutes at 3 PM" said in one
                # breath is a single fragment -- dropping it would discard the
                # command itself and leave an empty buffer. Keep the whole
                # fragment; the leading wake phrase is harmless context for
                # Claude's tool selection.
                command_buffer.append(text + " ")
                safe_log("info", "wake word detected", meeting_url=meet_url)

    # NOTE: the draft started the STT socket before opening the audio device.
    # Opening capture first means a missing/misconfigured BlackHole device
    # fails before a Deepgram connection is opened, and stt.stop() now runs in
    # a finally block so the socket and listener thread are never leaked when
    # the loop raises.
    with BlackHoleCapture(sample_rate=16000) as capture:
        stt = StreamingSTT(config.deepgram_api_key, on_transcript)
        stt.start()
        try:
            silence_frames = 0
            missed_tab_checks = 0
            last_tab_check = time.monotonic()

            while True:
                now = time.monotonic()
                if now - last_tab_check >= _LEAVE_CHECK_INTERVAL_SECONDS:
                    last_tab_check = now
                    if find_active_meet_tab() == meet_url:
                        missed_tab_checks = 0
                    else:
                        missed_tab_checks += 1
                        if missed_tab_checks >= _LEAVE_CONFIRM_CHECKS:
                            safe_log("info", "leave signal", meeting_url=meet_url)
                            break

                turn_state.tick()
                frame = capture.read_frame(frame_ms=_FRAME_MS)

                if turn_state.should_process_stt():
                    stt.send_frame(frame)

                # Called every iteration, gated or not: during a voice turn no
                # frames are sent at all (self-echo gating), and Deepgram drops
                # a silent connection after ~10s. This is a no-op while real
                # audio is flowing.
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
                                # Move out of LISTENING before the slow work
                                # below, so the bot's own TTS is never fed to
                                # the wake-word matcher (self-echo gating).
                                turn_state.command_captured()

                if not command_text:
                    continue

                # NOTE: the draft ran the whole turn unguarded. dispatch_command()
                # only catches failures inside the tool handler, so a Claude API
                # error (or an ElevenLabs/playback error) would propagate out and
                # end the meeting. Worse, an exception between command_captured()
                # and finish_speaking() would strand the state machine outside
                # LISTENING, leaving the bot deaf for the rest of the call.
                # start_speaking() up front plus finish_speaking() in `finally`
                # guarantees the machine always returns via COOLDOWN ->
                # LISTENING. Gating is unaffected: should_process_stt() is
                # already False from COMMAND_CAPTURED onward.
                with lock:
                    turn_state.start_speaking()
                try:
                    result = dispatch_command(anthropic_client, registry, command_text)
                    safe_log(
                        "info",
                        "command dispatched",
                        tool=result.tool_name,
                        meeting_url=meet_url,
                    )
                    # Spec: a false trigger must stay silent. If no tool matched
                    # *and* nothing was said beyond the wake phrase itself, the
                    # wake word fired on stray speech -- speaking "sorry, I
                    # didn't catch that" into a live meeting would be the bug.
                    # A genuine unrecognized command (words after the wake
                    # phrase) still gets the spoken fallback.
                    if result.tool_name is None and not wake_word.strip_wake_phrase(
                        command_text
                    ):
                        safe_log(
                            "info",
                            "bare wake phrase ignored",
                            meeting_url=meet_url,
                        )
                    else:
                        audio = synthesize(
                            config.elevenlabs_api_key, result.confirmation
                        )
                        inject_into_virtual_mic(audio)
                except Exception as exc:
                    safe_log(
                        "error",
                        "voice turn failed",
                        meeting_url=meet_url,
                        error=str(exc),
                    )
                finally:
                    with lock:
                        turn_state.finish_speaking()
        finally:
            stt.stop()


def _prompt_join_safely(title: str) -> NotificationResult:
    """prompt_join() shells out to terminal-notifier, which can raise
    TimeoutExpired (hung notifier) or FileNotFoundError (not installed).

    NOTE: the draft called it bare, so either failure would have killed the
    whole detection loop. Treat a failed prompt as TIMEOUT (i.e. Skip), which
    is exactly how the spec already treats an unactioned notification.
    """
    try:
        return prompt_join(title)
    except Exception as exc:
        safe_log("error", "join prompt failed", error=str(exc))
        return NotificationResult.TIMEOUT


def _handle_join(
    config: Config,
    registry: ToolRegistry,
    anthropic_client: Anthropic,
    state: StateStore,
    meet_url: str,
) -> None:
    worker = JoinWorker()
    try:
        worker.join(meet_url)
    except Exception as exc:
        # Spec ("Can't join"): log and skip; detection keeps running. Leave the
        # URL marked "skipped" so the next poll doesn't immediately re-prompt
        # for the same failing call. JoinWorker.join() tears itself down on
        # failure, so there is nothing to clean up here.
        safe_log("error", "join failed", meeting_url=meet_url, error=str(exc))
        state.set_status(meet_url, "skipped")
        return

    safe_log("info", "joined meeting", meeting_url=meet_url)
    try:
        _run_call_loop(config, registry, anthropic_client, meet_url)
    except Exception as exc:
        safe_log("error", "call loop failed", meeting_url=meet_url, error=str(exc))
    finally:
        try:
            worker.leave()
        except Exception as exc:
            safe_log("error", "leave failed", meeting_url=meet_url, error=str(exc))
        state.clear(meet_url)
        safe_log("info", "left meeting", meeting_url=meet_url)


def run() -> None:
    # SETUP.md tells the user to put their credentials in `.env`, and
    # Config.from_env() only reads os.environ -- so something has to bridge
    # the two. Done here rather than in config.py so importing Config never
    # has the side effect of loading files (which would also make the config
    # tests depend on whatever .env happens to sit in the working directory).
    # load_dotenv() does not override variables already set in the real
    # environment, so an exported value still wins.
    load_dotenv()

    config = Config.from_env()
    configure_logging(config)

    calendar_service = _authorize_calendar(config)
    registry = ToolRegistry()
    registry.register(build_calendar_tool(calendar_service))
    anthropic_client = Anthropic(api_key=config.anthropic_api_key)
    state = StateStore(config.state_file)

    safe_log("info", "mia started")

    was_mic_only = False
    try:
        while True:
            # This is a long-running background process: one bad poll (a
            # disconnected USB mic raising from is_mic_active(), an
            # unreadable state file, a Calendar API hiccup) must cost that
            # iteration, not the whole run. KeyboardInterrupt is a
            # BaseException, so the clean-exit path below still wins.
            try:
                mic_active = is_mic_active()

                # NOTE (bug fix): the draft called find_active_meet_tab() twice
                # per iteration -- once for the meet_tab_url argument and once
                # for the walrus in the calendar_title argument. Each call
                # spawns an osascript subprocess, and the two could observe
                # different tab state. Scan once and reuse.
                meet_url = find_active_meet_tab()

                # Spec: trigger events that don't become meetings are still
                # logged, but the outer loop polls every few seconds, so log the
                # mic-active-without-a-Meet-tab case only on the transition
                # into it.
                mic_only = mic_active and meet_url is None
                if mic_only and not was_mic_only:
                    safe_log("info", "mic active with no meet tab")
                was_mic_only = mic_only

                # Only spend a Calendar API call when this poll could actually
                # produce a prompt. Any status already recorded for this URL
                # means decide() will refuse to prompt regardless of the title,
                # and the enricher's result would be thrown away -- which, for a
                # call the user skipped, would otherwise be one wasted API call
                # every poll for the meeting's whole duration.
                calendar_title = None
                if (
                    mic_active
                    and meet_url is not None
                    and state.status(meet_url) is None
                ):
                    calendar_title = find_current_meeting_title(
                        calendar_service,
                        now=datetime.now(timezone.utc),
                        meet_url=meet_url,
                    )

                decision = decide(
                    mic_active=mic_active,
                    meet_tab_url=meet_url,
                    calendar_title=calendar_title,
                    state=state,
                )

                if decision.should_prompt:
                    state.set_status(decision.meeting_url, "prompted")
                    safe_log(
                        "info", "prompting to join", meeting_url=decision.meeting_url
                    )
                    result = _prompt_join_safely(decision.display_title)

                    if result == NotificationResult.JOIN:
                        state.set_status(decision.meeting_url, "joined")
                        _handle_join(
                            config,
                            registry,
                            anthropic_client,
                            state,
                            decision.meeting_url,
                        )
                    else:
                        safe_log(
                            "info",
                            "join prompt not accepted",
                            meeting_url=decision.meeting_url,
                            result=str(result),
                        )
                        state.set_status(decision.meeting_url, "skipped")
            except Exception as exc:
                safe_log("error", "detection poll failed", error=str(exc))

            time.sleep(_POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        safe_log("info", "mia stopped")


if __name__ == "__main__":
    run()
