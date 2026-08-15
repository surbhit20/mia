from mia.turn_state import TurnState, TurnStateMachine

def test_starts_idle():
    m = TurnStateMachine()
    assert m.current() == TurnState.IDLE

def test_wake_word_moves_idle_to_listening():
    m = TurnStateMachine()
    m.wake_word_detected()
    assert m.current() == TurnState.LISTENING

def test_full_cycle_gates_stt_correctly():
    clock = [0.0]
    m = TurnStateMachine(cooldown_seconds=1.0, clock=lambda: clock[0])
    m.wake_word_detected()
    assert m.should_process_stt() is True

    m.command_captured()
    assert m.current() == TurnState.COMMAND_CAPTURED
    assert m.should_process_stt() is False

    m.start_speaking()
    assert m.current() == TurnState.SPEAKING
    # Changed from the pre-barge-in behavior (was False): STT must keep
    # flowing while mia talks, or a barge-in wake word could never be heard.
    assert m.should_process_stt() is True

    m.finish_speaking()
    assert m.current() == TurnState.COOLDOWN
    assert m.should_process_stt() is False

    clock[0] = 0.5
    m.tick()
    assert m.current() == TurnState.COOLDOWN, "cooldown not elapsed yet"

    clock[0] = 1.1
    m.tick()
    assert m.current() == TurnState.LISTENING
    assert m.should_process_stt() is True

def test_wake_word_during_speaking_is_a_barge_in():
    # Renamed from test_wake_word_detected_is_noop_when_speaking: a wake
    # word heard while SPEAKING is no longer a no-op -- it's the barge-in
    # itself, and now moves the machine back to LISTENING.
    clock = [0.0]
    m = TurnStateMachine(clock=lambda: clock[0])
    m.wake_word_detected()
    m.command_captured()
    m.start_speaking()
    m.wake_word_detected()
    assert m.current() == TurnState.LISTENING

def test_wake_word_is_still_a_noop_during_command_captured():
    # COMMAND_CAPTURED (Claude dispatch + TTS synthesis) stays
    # non-interruptible on purpose -- there's no audio playing yet.
    m = TurnStateMachine()
    m.wake_word_detected()
    m.command_captured()
    m.wake_word_detected()
    assert m.current() == TurnState.COMMAND_CAPTURED

def test_abandon_turn_from_command_captured_returns_to_listening():
    # The bare-wake-phrase path: a command was captured but there's
    # nothing to say, so skip SPEAKING/COOLDOWN entirely.
    m = TurnStateMachine()
    m.wake_word_detected()
    m.command_captured()
    m.abandon_turn()
    assert m.current() == TurnState.LISTENING

def test_abandon_turn_from_speaking_returns_to_listening():
    # Error-recovery path: something failed after start_speaking() was
    # already called (e.g. start_playback() itself raised).
    m = TurnStateMachine()
    m.wake_word_detected()
    m.command_captured()
    m.start_speaking()
    m.abandon_turn()
    assert m.current() == TurnState.LISTENING

def test_abandon_turn_is_a_noop_from_listening():
    m = TurnStateMachine()
    m.wake_word_detected()
    m.abandon_turn()
    assert m.current() == TurnState.LISTENING
