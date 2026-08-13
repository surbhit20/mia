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
    assert m.should_process_stt() is False

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

def test_wake_word_detected_is_noop_when_speaking():
    clock = [0.0]
    m = TurnStateMachine(clock=lambda: clock[0])
    m.wake_word_detected()
    m.command_captured()
    m.start_speaking()
    m.wake_word_detected()
    assert m.current() == TurnState.SPEAKING
