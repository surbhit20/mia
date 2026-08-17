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

from mia import recall_client
from mia.audio.recall_bridge import RecallAudioBridge
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

# NOTE: the draft ended command capture on the first non-speech frame, which
# cut every command off at its first natural pause -- and worse,
# CommandBuffer.on_silence() stops capturing even when it returns None, so a
# single silent frame right after the wake word abandoned the command
# entirely. A sustained pause is required instead.
#
# How long a speaker must stop talking before their command is considered
# finished.
#
# Measured in wall-clock seconds, NOT in frames. A frame count only equals a
# duration if frames arrive at real time, and on the Recall path they do not:
# the loop blocks every few seconds on a status GET and an AppleScript tab
# scan, and the audio buffered during those stalls then drains at memory
# speed (firing the threshold far too early), while a stalled stream makes
# each read wait out its starvation timeout instead (firing it far too late).
# Wall-clock time is immune to both.
_SILENCE_SECONDS_TO_END_COMMAND = 1.2

# States that mean Recall's bot is no longer usably present in the
# meeting -- checked alongside the existing tab-based leave signal so a
# bot removed by another participant (or a fatal error) is also noticed,
# not just a locally-closed Chrome tab.
_BOT_LEFT_STATES = {"call_ended", "fatal"}

# speak() is a single REST call, not a streamed connection -- there is no
# "audio finished playing" acknowledgment from Recall's API. Estimate
# playback duration from the MP3's byte length at ElevenLabs' fixed
# 128kbps ("mp3_44100_128") constant bitrate.
_MP3_BITRATE_BITS_PER_SECOND = 128_000


def _estimate_playback_seconds(mp3_bytes: bytes) -> float:
    return len(mp3_bytes) * 8 / _MP3_BITRATE_BITS_PER_SECOND


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
    bridge: RecallAudioBridge,
    bot_id: str,
) -> None:
    turn_state = TurnStateMachine()
    wake_word = WakeWordMatcher(config.wake_word, threshold=config.fuzzy_threshold)
    command_buffer = CommandBuffer()
    vad = FrameVAD(frame_ms=_FRAME_MS)
    history = ConversationHistory()
    # What mia is currently speaking, so on_transcript can tell her own TTS
    # apart from a real barge-in via content-based comparison (is_self_echo).
    # Whether Recall's audio_mixed_raw stream includes mia's own spoken
    # output is unverified as of this branch -- if it does, this filtering
    # is still load-bearing; if it doesn't, it's a harmless no-op. Confirm
    # during live testing.
    current_speech: list[str | None] = [None]

    # NOTE: StreamingSTT dispatches on_transcript from a background listener
    # thread, while the loop below mutates the same turn_state /
    # command_buffer from this thread. Guard the check-then-act sequences on
    # both sides. The lock is only ever held for state transitions, never
    # across the LLM/TTS/injection calls, so the listener thread never blocks
    # on slow network work.
    lock = threading.Lock()

    # NOTE: TurnStateMachine starts in IDLE, and should_process_stt() is True
    # only in LISTENING. Entering the call is what starts listening, so make
    # that transition here.
    turn_state.wake_word_detected()

    # No streamed/paced playback exists anymore -- speak() is a single REST
    # call, so "is currently speaking" is tracked here as a plain estimated
    # end time rather than queried from a bridge.
    playback_end_time = [0.0]

    # True from the moment SPEAKING begins until speak()'s POST returns and a
    # real end time is known. The natural-completion check below must not fire
    # in that window: playback_end_time still holds the previous turn's value,
    # so without this the turn would end mid-POST.
    playback_pending = [False]

    def on_transcript(text: str, is_final: bool) -> None:
        if not is_final:
            return
        with lock:
            # DIAGNOSTIC: every final transcript, with the state that decides
            # its fate. Without this a missed wake word is unattributable --
            # Deepgram mishearing, the fuzzy matcher rejecting, and the turn
            # state gating it out all look identical from outside (silence).
            safe_log(
                "info",
                "transcript",
                text=text,
                state=str(turn_state.current()),
                wake_matched=wake_word.matches(text),
                capturing=command_buffer.is_capturing(),
                gated_out=not turn_state.should_process_stt(),
            )
            if not turn_state.should_process_stt():
                return
            if turn_state.current() == TurnState.SPEAKING and current_speech[0] is not None:
                if is_self_echo(text, current_speech[0]):
                    return
            if command_buffer.is_capturing():
                command_buffer.append(text + " ")
                return
            if wake_word.matches(text):
                # Recall has no interrupt/stop-audio API, so a barge-in
                # wake word can no longer truncate audio already sent via
                # speak() -- an accepted, deliberate tradeoff (see the
                # design spec). Wake-word detection during playback still
                # works: mia starts listening to a new command
                # immediately, she just can't be cut off mid-sentence.
                turn_state.wake_word_detected()
                command_buffer.start()
                command_buffer.append(text + " ")
                safe_log("info", "wake word detected", meeting_url=meet_url)

    stt = StreamingSTT(config.deepgram_api_key, on_transcript)
    stt.start()
    try:
        silence_started_at: float | None = None
        missed_tab_checks = 0
        last_tab_check = time.monotonic()

        while True:
            now = time.monotonic()
            if now - last_tab_check >= _LEAVE_CHECK_INTERVAL_SECONDS:
                last_tab_check = now

                bot_still_in_meeting = True
                try:
                    current_bot_state = recall_client.bot_state(
                        base_url=config.recall_base_url,
                        api_key=config.recall_api_key,
                        bot_id=bot_id,
                        timeout_seconds=5.0,
                    )
                    bot_still_in_meeting = current_bot_state not in _BOT_LEFT_STATES
                except Exception as exc:
                    # A transient status-poll failure must not end the
                    # call -- fall back to the tab-based signal alone
                    # for this iteration.
                    safe_log("warning", "bot status poll failed", meeting_url=meet_url, error=str(exc))

                # DIAGNOSTIC: audio-transport health. connections=0 means
                # Recall never dialed the bridge; a pulls_padded ratio near
                # pulls_served means frames are mostly fabricated silence.
                safe_log("info", "audio stats", meeting_url=meet_url, **bridge.stats())

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

            with lock:
                if (
                    turn_state.current() == TurnState.SPEAKING
                    and not playback_pending[0]
                    and time.monotonic() >= playback_end_time[0]
                ):
                    turn_state.finish_speaking()

            frame = bridge.read_frame(frame_ms=_FRAME_MS)

            if turn_state.should_process_stt():
                stt.send_frame(frame)

            stt.send_keepalive_if_idle()

            is_speech = vad.is_speech(frame)

            command_text = None
            with lock:
                if not command_buffer.is_capturing():
                    silence_started_at = None
                elif is_speech:
                    silence_started_at = None
                else:
                    if silence_started_at is None:
                        silence_started_at = time.monotonic()
                    if time.monotonic() - silence_started_at >= _SILENCE_SECONDS_TO_END_COMMAND:
                        silence_started_at = None
                        command_text = command_buffer.on_silence()
                        if command_text:
                            turn_state.command_captured()
                        else:
                            # DIAGNOSTIC: capture ended with nothing. Means the
                            # wake word fired but no transcript followed before
                            # the silence threshold -- a distinct failure from
                            # never waking at all.
                            safe_log("info", "command capture ended empty", meeting_url=meet_url)

            if not command_text:
                continue

            try:
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
                        config.elevenlabs_api_key, result.confirmation, output_format="mp3_44100_128"
                    )
                    playback_seconds = _estimate_playback_seconds(audio)
                    with lock:
                        turn_state.start_speaking()
                        current_speech[0] = result.confirmation
                        playback_pending[0] = True
                    # Deliberately outside the lock: this is a network POST with a 30s
                    # timeout, and on_transcript acquires the same lock from the STT
                    # listener thread -- holding it here would make mia deaf for the whole
                    # request.
                    recall_client.speak(
                        base_url=config.recall_base_url,
                        api_key=config.recall_api_key,
                        bot_id=bot_id,
                        mp3_bytes=audio,
                    )
                    with lock:
                        # Audio starts playing once the POST is accepted, so the estimate
                        # runs from here, not from before the request.
                        playback_end_time[0] = time.monotonic() + playback_seconds
                        playback_pending[0] = False
            except Exception as exc:
                safe_log(
                    "error",
                    "voice turn failed",
                    meeting_url=meet_url,
                    error=str(exc),
                )
                with lock:
                    turn_state.abandon_turn()
                    playback_pending[0] = False
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
    missing = [
        name
        for name, value in (
            ("RECALL_API_KEY", config.recall_api_key),
            ("RECALL_WEBSOCKET_HOSTNAME", config.recall_websocket_hostname),
        )
        if not value
    ]
    if missing:
        # Without these the bot either fails to create or -- worse -- joins
        # successfully against a "wss:///audio" endpoint it can never reach,
        # leaving a billed but permanently deaf bot in the meeting.
        safe_log(
            "error",
            "join failed",
            meeting_url=meet_url,
            error=f"missing required environment variable(s): {', '.join(missing)}",
        )
        state.set_status(meet_url, "skipped")
        return

    websocket_url = f"wss://{config.recall_websocket_hostname}/audio"
    try:
        with RecallAudioBridge(port=config.recall_websocket_port) as bridge:
            bot_id = None
            joined = False
            try:
                bot_id = recall_client.create_bot(
                    base_url=config.recall_base_url,
                    api_key=config.recall_api_key,
                    meeting_url=meet_url,
                    websocket_url=websocket_url,
                    bot_name=config.recall_bot_name,
                )
                recall_client.wait_until_joined(
                    base_url=config.recall_base_url,
                    api_key=config.recall_api_key,
                    bot_id=bot_id,
                )
                joined = True
                safe_log("info", "joined meeting", meeting_url=meet_url)
                _run_call_loop(config, registry, anthropic_client, meet_url, bridge, bot_id)
            except Exception as exc:
                safe_log(
                    "error",
                    "call loop failed" if joined else "join failed",
                    meeting_url=meet_url,
                    error=str(exc),
                )
            finally:
                # A finally, not an except: KeyboardInterrupt during
                # wait_until_joined's poll is a BaseException, and letting it skip
                # this would strand a paid bot in the user's live meeting.
                if bot_id is not None:
                    try:
                        recall_client.leave(
                            base_url=config.recall_base_url,
                            api_key=config.recall_api_key,
                            bot_id=bot_id,
                        )
                    except Exception as leave_exc:
                        safe_log("error", "leave failed", meeting_url=meet_url, error=str(leave_exc))
                if joined:
                    state.clear(meet_url)
                    safe_log("info", "left meeting", meeting_url=meet_url)
                else:
                    state.set_status(meet_url, "skipped")
    except Exception as exc:
        # RecallAudioBridge.__enter__ itself failed (e.g. port already
        # bound). The caller in run() already marked this URL "joined"
        # before calling us, so it must be corrected to "skipped" here, or
        # the URL would never be re-prompted until StateStore's TTL
        # expires.
        safe_log("error", "join failed", meeting_url=meet_url, error=str(exc))
        state.set_status(meet_url, "skipped")


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
