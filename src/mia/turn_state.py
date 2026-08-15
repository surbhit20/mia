import time
from collections.abc import Callable
from enum import StrEnum

class TurnState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    COMMAND_CAPTURED = "command_captured"
    SPEAKING = "speaking"
    COOLDOWN = "cooldown"

class TurnStateMachine:
    def __init__(self, cooldown_seconds: float = 1.0, clock: Callable[[], float] = time.monotonic):
        self._state = TurnState.IDLE
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._cooldown_started_at: float | None = None

    def current(self) -> TurnState:
        return self._state

    def wake_word_detected(self) -> None:
        # SPEAKING is included so a wake word heard while mia is talking
        # interrupts her (barge-in) rather than being ignored. COMMAND_CAPTURED
        # (Claude dispatch + TTS synthesis in flight) stays excluded on purpose:
        # there's no audio playing yet to interrupt, and canceling an in-flight
        # Claude call is out of scope.
        if self._state in (TurnState.IDLE, TurnState.LISTENING, TurnState.SPEAKING):
            self._state = TurnState.LISTENING

    def command_captured(self) -> None:
        if self._state == TurnState.LISTENING:
            self._state = TurnState.COMMAND_CAPTURED

    def start_speaking(self) -> None:
        if self._state == TurnState.COMMAND_CAPTURED:
            self._state = TurnState.SPEAKING

    def finish_speaking(self) -> None:
        if self._state == TurnState.SPEAKING:
            self._state = TurnState.COOLDOWN
            self._cooldown_started_at = self._clock()

    def abandon_turn(self) -> None:
        """Recover directly to LISTENING with no audio played -- used for a
        bare wake-phrase trigger (nothing to say) or when an error occurs
        before or during synthesis (nothing to play). Skips SPEAKING/COOLDOWN
        entirely since there's no audio to interrupt or cool down from."""
        if self._state in (TurnState.COMMAND_CAPTURED, TurnState.SPEAKING):
            self._state = TurnState.LISTENING

    def tick(self) -> None:
        if self._state != TurnState.COOLDOWN or self._cooldown_started_at is None:
            return
        if self._clock() - self._cooldown_started_at >= self._cooldown_seconds:
            self._state = TurnState.LISTENING
            self._cooldown_started_at = None

    def should_process_stt(self) -> bool:
        # SPEAKING is included so STT keeps flowing to Deepgram while mia
        # talks -- otherwise the wake-word matcher could never see a
        # barge-in attempt in the first place.
        return self._state in (TurnState.LISTENING, TurnState.SPEAKING)
