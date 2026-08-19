from mia.wakeword import WakeWordMatcher, is_self_echo

def test_exact_match():
    m = WakeWordMatcher("hey bot")
    assert m.matches("hey bot block this slot") is True

def test_phonetic_mishearing_matches():
    m = WakeWordMatcher("hey bot")
    assert m.matches("hay bot can you help") is True


def test_dropping_the_first_word_entirely_no_longer_matches():
    # Deliberate trade-off, made after mia woke on "the migration" mid-meeting
    # and talked over the room. "a bot" keeps only one word of the wake phrase,
    # which is too little evidence to justify interrupting a live meeting: a
    # false wake costs everyone in the call, a missed one costs the speaker a
    # repeat. Real mishearings that keep both words ("hay bot", "hemia") still
    # match comfortably.
    m = WakeWordMatcher("hey bot")

    assert m.matches("a bot please block this") is False

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

def test_is_self_echo_detects_fragment_of_spoken_text():
    spoken = "Your 1:1 with Mia is at 2 PM"
    assert is_self_echo("your one on one with mia is at 2pm", spoken) is True

def test_is_self_echo_ignores_unrelated_transcript():
    spoken = "Your 1:1 with Mia is at 2 PM"
    assert is_self_echo("can you block 30 minutes for focus time", spoken) is False

def test_is_self_echo_threshold_is_configurable():
    spoken = "Meeting confirmed for 3pm"
    transcript = "meting confirmd"
    assert is_self_echo(transcript, spoken, threshold=0.5) is True
    assert is_self_echo(transcript, spoken, threshold=0.95) is False


def test_does_not_wake_on_words_that_merely_contain_the_sounds():
    # Found live: "the bigger risk is the migration" woke mia mid-sentence and
    # she started talking over the meeting. fuzz.partial_ratio finds the best
    # matching SUBSTRING and ignores everything around it, so "t(he mi)gration"
    # scored 77 against "hey mia" -- above threshold. No threshold could fix
    # it: a real attempt heard as "hemia" scored 75, i.e. lower than the false
    # positive. The metric had to change, not the number.
    matcher = WakeWordMatcher("hey mia", threshold=0.75)

    assert matcher.matches("the bigger risk is the migration") is False
    assert matcher.matches("someone needs to chase raj about the migration before monday") is False
    assert matcher.matches("media") is False
    assert matcher.matches("the meeting") is False


def test_still_wakes_on_real_attempts_including_mishearings():
    matcher = WakeWordMatcher("hey mia", threshold=0.75)

    assert matcher.matches("hey mia") is True
    assert matcher.matches("Hey, Mia.") is True
    assert matcher.matches("hey mia block thirty minutes at six pm") is True
    assert matcher.matches("hemia") is True          # heard live, run 3
    assert matcher.matches("hey maya") is True


# Every string below was observed live across three real meetings.
_MISHEARD_ATTEMPTS = ["mia", "hemia", "hamia", "mia block thirty minutes at six pm"]
_ORDINARY_SPEECH = [
    "the bigger risk is the migration",
    "we still don't have a rollback plan for the migration",
    "in four three months to help with migration",
    "migration before monday",
    "the big one is database migration",
    "media",
    "the meeting",
    "my main issue",
    "i mean",
    "the mayor said",
]


def _aliased():
    return WakeWordMatcher(
        "hey mia", threshold=0.75, aliases=("mia", "hemia", "hamia", "miya", "maya")
    )


def test_aliases_catch_mishearings_fuzzy_scoring_cannot_reach():
    # "hey mia" comes back as "mia" or "hemia" often enough to cost a repeat
    # every few commands. These are textually far from the phrase -- no
    # threshold reaches them without also admitting "media" and "migration".
    matcher = _aliased()

    for text in _MISHEARD_ATTEMPTS:
        assert matcher.matches(text) is True, text


def test_aliases_do_not_reintroduce_false_wakes():
    matcher = _aliased()

    for text in _ORDINARY_SPEECH:
        assert matcher.matches(text) is False, text


def test_aliases_are_matched_exactly_not_fuzzily():
    # The whole point. Fuzzy-matching the alias "mia" scores "media" at 75 and
    # undoes the false-wake fix; requiring the token to BE the alias does not.
    matcher = _aliased()

    assert matcher.matches("media") is False
    assert matcher.matches("mia") is True


def test_amy_is_deliberately_not_an_alias():
    # Phonetically close and observed live, but it is also a person's name and
    # nothing distinguishes the two. Excluded by choice, not oversight.
    matcher = _aliased()

    assert matcher.matches("amy") is False


def test_strip_removes_the_alias_leaving_the_command():
    # An alias stands in for the whole phrase, so the command must survive it.
    matcher = _aliased()

    assert matcher.strip_wake_phrase("mia block thirty minutes") == "block thirty minutes"


def test_no_aliases_by_default_keeps_previous_behavior():
    matcher = WakeWordMatcher("hey mia", threshold=0.75)

    assert matcher.matches("mia") is False
    assert matcher.matches("hey mia") is True
