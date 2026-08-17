"""Process entrypoint: wires every module into the detect -> prompt -> join ->
live-voice-loop -> leave orchestration described in the design spec.

Run with `python -m mia.main`.

Deviations from the plan's draft code are marked with `NOTE:` comments below;
each one is either a bug in the draft or a mismatch with a dependency module's
actually-committed behaviour (see task-19-report.md for the full rationale).
"""

import json
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

from mia import attendee_client
from mia.audio.attendee_bridge import AttendeeAudioBridge
from mia.audio.vad import FrameVAD
from mia.command_buffer import CommandBuffer
from mia.config import Config
from mia.detection.calendar_enricher import find_current_meeting_title
from mia.detection.mic_monitor import is_mic_active
from mia.detection.tab_detector import find_active_meet_tab
from mia.detection.trigger import decide
from mia.llm import ConversationHistory, dispatch_command
from mia.logging_setup import configure as configure_logging
from mia.logging_setup import safe_log
from mia.notify import NotificationResult, prompt_join
from mia.state import StateStore
from mia.stt import StreamingSTT
from mia.tools.base import ToolRegistry
from mia.tools.calendar_cancel_tool import build_cancel_calendar_event_tool
from mia.tools.calendar_fetch_tool import build_calendar_fetch_tool
from mia.tools.calendar_tool import build_calendar_tool
from mia.tools.calendar_update_tool import build_update_calendar_event_tool
from mia.tools.gmail_tool import build_gmail_search_tool
from mia.tts import synthesize
from mia.turn_state import TurnState, TurnStateMachine
from mia.wakeword import WakeWordMatcher, is_self_echo

_TOKEN_PATH = Path("~/.mia/token.json").expanduser()
_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
]

# Outer detection poll interval.
_POLL_INTERVAL_SECONDS = 5.0

# Audio framing for the live loop. 32ms x 16kHz is exactly the 512-sample
# window silero-vad requires, so every frame reaches the model intact; the
# 30ms the spec suggests would be 480 samples and get zero-padded on every
# single inference for the life of the process.
_FRAME_MS = 32

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
# (24 * 32ms = 768ms, sub-second per the spec's turn-taking requirement, and
# long enough to survive a natural pause after the wake phrase).
_SILENCE_FRAMES_TO_END_COMMAND = 24

_BOT_AVATAR_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "bot_avatar.png"

# States that mean Attendee's bot is no longer usably present in the
# meeting -- checked alongside the existing tab-based leave signal so a
# bot removed by another participant (or a fatal error) is also noticed,
# not just a locally-closed Chrome tab.
_BOT_LEFT_STATES = {"fatal_error", "ended", "data_deleted"}


def _save_credentials(creds: Credentials) -> None:
    _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_PATH.write_text(creds.to_json())
    _TOKEN_PATH.chmod(0o600)


def _authorize_google(config: Config):
    creds = None
    if _TOKEN_PATH.exists():
        cached_info = json.loads(_TOKEN_PATH.read_text())
        if not set(_SCOPES).issubset(set(cached_info.get("scopes") or [])):
            # Scope list has widened since this token was issued (e.g. Gmail
            # search added after the user first authorized). Credentials
            # objects trust whatever scope list they're constructed with, so
            # loading the cached token here would silently claim scopes it
            # was never actually granted -- force a fresh consent instead.
            creds = None
        else:
            creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), _SCOPES)
            # NOTE: the draft used the cached token as-is. A cached access token is
            # expired within an hour of the first run, so without this refresh the
            # calendar enricher, calendar tool, and Gmail search tool would fail on
            # every subsequent run of the process.
            if not creds.valid and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    _save_credentials(creds)
                except Exception as exc:
                    safe_log("warning", "google token refresh failed", error=str(exc))
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

    return creds


def _run_call_loop(
    config: Config,
    registry: ToolRegistry,
    anthropic_client: Anthropic,
    meet_url: str,
    bridge: AttendeeAudioBridge,
    bot_id: str,
) -> None:
    turn_state = TurnStateMachine()
    wake_word = WakeWordMatcher(config.wake_word, threshold=config.fuzzy_threshold)
    command_buffer = CommandBuffer()
    vad = FrameVAD(frame_ms=_FRAME_MS)
    history = ConversationHistory()
    # What mia is currently speaking, so on_transcript can tell her own TTS
    # looping back through capture (BlackHole routes injected audio back into
    # what mia captures, by design) apart from a real barge-in (Finding 1).
    current_speech: list[str | None] = [None]

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
            if turn_state.current() == TurnState.SPEAKING and current_speech[0] is not None:
                if is_self_echo(text, current_speech[0]):
                    return
            if command_buffer.is_capturing():
                command_buffer.append(text + " ")
                return
            if wake_word.matches(text):
                # Stopping playback here is what makes this a real barge-in
                # when the wake word arrives during SPEAKING -- harmless no-op
                # if nothing is currently playing (the normal LISTENING case).
                bridge.stop_playback()
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

                bot_still_in_meeting = True
                try:
                    current_bot_state = attendee_client.bot_state(
                        base_url=config.attendee_base_url,
                        api_key=config.attendee_api_key,
                        bot_id=bot_id,
                    )
                    bot_still_in_meeting = current_bot_state not in _BOT_LEFT_STATES
                except Exception as exc:
                    # A transient status-poll failure must not end the
                    # call -- fall back to the tab-based signal alone
                    # for this iteration.
                    safe_log("warning", "bot status poll failed", meeting_url=meet_url, error=str(exc))

                if not bot_still_in_meeting:
                    safe_log("info", "leave signal", meeting_url=meet_url, reason="bot left meeting")
                    break

                if find_active_meet_tab() == meet_url:
                    missed_tab_checks = 0
                else:
                    missed_tab_checks += 1
                    if missed_tab_checks >= _LEAVE_CONFIRM_CHECKS:
                        safe_log("info", "leave signal", meeting_url=meet_url, reason="tab closed")
                        break

            turn_state.tick()

            # A barge-in wake word already moved the machine out of
            # SPEAKING (and stopped playback) from on_transcript, on the
            # STT listener thread -- this only fires for a response that
            # finished on its own, uninterrupted.
            with lock:
                if turn_state.current() == TurnState.SPEAKING and not bridge.is_playback_active():
                    turn_state.finish_speaking()

            frame = bridge.read_frame(frame_ms=_FRAME_MS)

            if turn_state.should_process_stt():
                stt.send_frame(frame)

            # Called every iteration, gated or not: STT frames are now
            # blocked only during COMMAND_CAPTURED (the Claude + TTS
            # generation window), and Deepgram drops a silent connection
            # after ~10s. This is a no-op except during that window --
            # self-echo during SPEAKING is handled by content-based
            # filtering in on_transcript (is_self_echo), not by blocking
            # STT outright.
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
                            # below. This blocks STT for the
                            # Claude+TTS-generation window; self-echo once
                            # audio starts playing (SPEAKING) is instead
                            # handled by is_self_echo() filtering in
                            # on_transcript, not by blocking STT.
                            turn_state.command_captured()

            if not command_text:
                continue

            # NOTE: dispatch_command() only catches failures inside the
            # tool handler, so a Claude API error (or a TTS error) would
            # otherwise propagate out and end the meeting -- caught below
            # and recovered via abandon_turn() either way.
            #
            # start_speaking() no longer fires unconditionally up front:
            # SPEAKING now means "audio is playing" specifically (so a
            # barge-in wake word during SPEAKING has actual audio to
            # interrupt), so it's called right before start_playback()
            # instead, only on the path that actually produces audio.
            # The bare-wake-phrase path and any exception path use
            # abandon_turn() to recover straight to LISTENING, since
            # neither has audio to speak or cool down from. A normal,
            # uninterrupted response's SPEAKING -> COOLDOWN -> LISTENING
            # transition now happens from the loop's natural-completion
            # check above, not from a `finally` block here.
            try:
                # Spec: a false trigger must stay silent. If nothing was
                # said beyond the wake phrase itself, the wake word fired
                # on stray speech -- skip dispatch_command entirely so a
                # bare trigger costs no Claude call and consumes no slot
                # in the bounded conversation-memory window. A genuine
                # unrecognized command (real words after the wake phrase)
                # still reaches dispatch_command and gets its own spoken
                # fallback from there.
                if not wake_word.strip_wake_phrase(command_text):
                    safe_log(
                        "info",
                        "bare wake phrase ignored",
                        meeting_url=meet_url,
                    )
                    with lock:
                        turn_state.abandon_turn()
                else:
                    result = dispatch_command(anthropic_client, registry, command_text, history)
                    safe_log(
                        "info",
                        "command dispatched",
                        tool=result.tool_name,
                        meeting_url=meet_url,
                    )
                    audio = synthesize(
                        config.elevenlabs_api_key, result.confirmation
                    )
                    with lock:
                        turn_state.start_speaking()
                        current_speech[0] = result.confirmation
                        bridge.start_playback(audio)
            except Exception as exc:
                safe_log(
                    "error",
                    "voice turn failed",
                    meeting_url=meet_url,
                    error=str(exc),
                )
                with lock:
                    turn_state.abandon_turn()
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
    websocket_url = f"ws://host.docker.internal:{config.attendee_websocket_port}/audio"
    with AttendeeAudioBridge(port=config.attendee_websocket_port) as bridge:
        try:
            bot_id = attendee_client.create_bot(
                base_url=config.attendee_base_url,
                api_key=config.attendee_api_key,
                meeting_url=meet_url,
                websocket_url=websocket_url,
                bot_name=config.attendee_bot_name,
            )
            attendee_client.wait_until_joined(
                base_url=config.attendee_base_url,
                api_key=config.attendee_api_key,
                bot_id=bot_id,
            )
        except Exception as exc:
            # Spec ("Can't join"): log and skip; detection keeps running.
            # Leave the URL marked "skipped" so the next poll doesn't
            # immediately re-prompt for the same failing call.
            safe_log("error", "join failed", meeting_url=meet_url, error=str(exc))
            state.set_status(meet_url, "skipped")
            return

        try:
            attendee_client.set_avatar_image(
                base_url=config.attendee_base_url,
                api_key=config.attendee_api_key,
                bot_id=bot_id,
                image_path=_BOT_AVATAR_PATH,
            )
        except Exception as exc:
            safe_log("warning", "avatar image failed", meeting_url=meet_url, error=str(exc))

        safe_log("info", "joined meeting", meeting_url=meet_url)
        try:
            _run_call_loop(config, registry, anthropic_client, meet_url, bridge, bot_id)
        except Exception as exc:
            safe_log("error", "call loop failed", meeting_url=meet_url, error=str(exc))
        finally:
            try:
                attendee_client.leave(
                    base_url=config.attendee_base_url,
                    api_key=config.attendee_api_key,
                    bot_id=bot_id,
                )
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

    creds = _authorize_google(config)
    calendar_service = build("calendar", "v3", credentials=creds)
    gmail_service = build("gmail", "v1", credentials=creds)
    anthropic_client = Anthropic(api_key=config.anthropic_api_key)
    registry = ToolRegistry()
    registry.register(build_calendar_tool(calendar_service))
    registry.register(build_calendar_fetch_tool(calendar_service))
    registry.register(build_cancel_calendar_event_tool(calendar_service))
    registry.register(build_update_calendar_event_tool(calendar_service))
    registry.register(build_gmail_search_tool(gmail_service, anthropic_client))
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
