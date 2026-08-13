from mia.wakeword import WakeWordMatcher

def test_exact_match():
    m = WakeWordMatcher("hey bot")
    assert m.matches("hey bot block this slot") is True

def test_phonetic_mishearing_matches():
    m = WakeWordMatcher("hey bot")
    assert m.matches("hay bot can you help") is True
    assert m.matches("a bot please block this") is True

def test_unrelated_text_does_not_match():
    m = WakeWordMatcher("hey bot")
    assert m.matches("let's discuss the roadmap for next quarter") is False

def test_wake_word_embedded_mid_sentence_matches():
    m = WakeWordMatcher("hey bot")
    assert m.matches("so anyway hey bot block my 3pm please") is True

def test_default_wake_word_ignores_ordinary_meeting_speech():
    m = WakeWordMatcher("hey mia")
    assert m.matches("the bottom line is we ship friday") is False
    assert m.matches("they both agreed on the deadline") is False
    assert m.matches("they bought the smaller plan") is False

def test_strip_wake_phrase_leaves_only_the_command():
    m = WakeWordMatcher("hey mia")
    assert m.strip_wake_phrase("hey mia block my 3pm") == "block my 3pm"
    assert m.strip_wake_phrase("so anyway hey mia block my 3pm") == "so anyway block my 3pm"

def test_strip_wake_phrase_is_empty_for_a_bare_trigger():
    m = WakeWordMatcher("hey mia")
    assert m.strip_wake_phrase("hey mia") == ""
    assert m.strip_wake_phrase("hey mia.") == ""

def test_strip_wake_phrase_returns_text_when_nothing_matches():
    m = WakeWordMatcher("hey mia")
    assert m.strip_wake_phrase("block my 3pm") == "block my 3pm"

def test_threshold_is_configurable():
    lenient = WakeWordMatcher("hey bot", threshold=0.4)
    strict = WakeWordMatcher("hey bot", threshold=0.95)
    text = "yo boat"
    assert lenient.matches(text) is True
    assert strict.matches(text) is False
