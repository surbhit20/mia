import json

from mia.transcript import (
    ParticipantRoster,
    TranscriptLog,
    Utterance,
    extract_participant_event,
    extract_transcript_utterance,
)


def _transcript_message(participant_id=1, name="Sarah", words=("hello", "there")):
    return json.dumps(
        {
            "event": "transcript.data",
            "data": {
                "data": {
                    "words": [{"text": w} for w in words],
                    "participant": {"id": participant_id, "name": name},
                }
            },
        }
    )


def _participant_message(event="participant_events.join", participant_id=1, name="Sarah"):
    return json.dumps(
        {"event": event, "data": {"data": {"participant": {"id": participant_id, "name": name}}}}
    )


def test_extracts_utterance_with_speaker_and_joined_words():
    result = extract_transcript_utterance(_transcript_message())

    assert result == Utterance(participant_id=1, speaker_name="Sarah", text="hello there")


def test_extracts_utterance_with_null_speaker_name():
    result = extract_transcript_utterance(_transcript_message(name=None))

    assert result.participant_id == 1
    assert result.speaker_name is None
    assert result.text == "hello there"


def test_blank_speaker_name_is_treated_as_absent():
    result = extract_transcript_utterance(_transcript_message(name="   "))

    assert result.speaker_name is None


def test_ignores_other_events():
    assert extract_transcript_utterance(_participant_message()) is None


def test_ignores_unparseable_transcript_message():
    assert extract_transcript_utterance("not json") is None


def test_returns_none_on_malformed_transcript_shapes():
    assert extract_transcript_utterance(json.dumps({"event": "transcript.data", "data": None})) is None
    assert extract_transcript_utterance(json.dumps({"event": "transcript.data", "data": {"data": [1]}})) is None
    assert extract_transcript_utterance(
        json.dumps({"event": "transcript.data", "data": {"data": {"words": [{"text": "hi"}]}}})
    ) is None


def test_returns_none_when_no_words_produce_text():
    message = json.dumps(
        {
            "event": "transcript.data",
            "data": {"data": {"words": [], "participant": {"id": 1, "name": "Sarah"}}},
        }
    )

    assert extract_transcript_utterance(message) is None


def test_extracts_participant_join_and_update():
    assert extract_participant_event(_participant_message()) == (1, "Sarah")
    assert extract_participant_event(
        _participant_message(event="participant_events.update", participant_id=4, name="Raj")
    ) == (4, "Raj")


def test_participant_event_ignores_unrelated_events():
    assert extract_participant_event(_transcript_message()) is None
    assert extract_participant_event(_participant_message(event="participant_events.leave")) is None
    assert extract_participant_event("not json") is None


def test_roster_numbers_unnamed_speakers_in_order_of_first_appearance():
    # Labels are sequential, not the raw participant id -- Recall's ids are
    # opaque integers, so interpolating them gives "Speaker 847293".
    roster = ParticipantRoster()

    assert roster.name_for(847293) == "Speaker 1"
    assert roster.name_for(12) == "Speaker 2"


def test_roster_label_is_stable_for_the_same_participant():
    # Distinct, stable labels matter: collapsing every unnamed person into one
    # shared label reads to the summarizing model as a single person saying
    # everything, which destroys the structure of the conversation.
    roster = ParticipantRoster()

    first = roster.name_for(99)
    roster.name_for(100)

    assert roster.name_for(99) == first


def test_a_named_participant_never_consumes_a_speaker_number():
    roster = ParticipantRoster()
    roster.record(1, "Sarah")

    assert roster.name_for(1) == "Sarah"
    assert roster.name_for(2) == "Speaker 1"


def test_roster_returns_recorded_name():
    roster = ParticipantRoster()
    roster.record(1, "Sarah")

    assert roster.name_for(1) == "Sarah"


def test_roster_ignores_null_name_and_keeps_known_one():
    roster = ParticipantRoster()
    roster.record(1, "Sarah")
    roster.record(1, None)

    assert roster.name_for(1) == "Sarah"


def test_has_name_does_not_allocate_a_speaker_number():
    # name_for() assigns lazily, so probing with it would consume labels out
    # of order. has_name() must be a pure query.
    roster = ParticipantRoster()

    assert roster.has_name(42) is False
    assert roster.name_for(7) == "Speaker 1"


def test_roster_lists_known_attendees():
    roster = ParticipantRoster()
    roster.record(2, "Raj")
    roster.record(1, "Sarah")
    roster.record(3, None)

    assert roster.attendees() == ["Raj", "Sarah"]


def test_log_renders_speaker_lines_in_order():
    roster = ParticipantRoster()
    log = TranscriptLog()
    log.append(Utterance(1, "Sarah", "morning"))
    log.append(Utterance(2, "Raj", "morning all"))

    assert log.render(roster) == "Sarah: morning\nRaj: morning all"


def test_log_merges_consecutive_utterances_from_one_speaker():
    roster = ParticipantRoster()
    log = TranscriptLog()
    log.append(Utterance(1, "Sarah", "morning"))
    log.append(Utterance(1, "Sarah", "one thing before we start"))
    log.append(Utterance(2, "Raj", "go ahead"))

    assert log.render(roster) == "Sarah: morning one thing before we start\nRaj: go ahead"


def test_log_resolves_a_name_that_arrived_after_the_utterance():
    # The regression that matters: participant_events.update fires when a
    # participant's details resolve after they joined, so a name learned late
    # must attach retroactively to lines they already spoke. This only works
    # because render() resolves names instead of append().
    roster = ParticipantRoster()
    log = TranscriptLog()
    log.append(Utterance(5, None, "can everyone hear me"))

    roster.record(5, "Priya")

    assert log.render(roster) == "Priya: can everyone hear me"


def test_log_reports_distinct_speaker_ids():
    # Used to detect speakers the roster never named -- the signal that
    # participant events are being missed in real meetings.
    log = TranscriptLog()
    log.append(Utterance(1, "Sarah", "hi"))
    log.append(Utterance(1, "Sarah", "again"))
    log.append(Utterance(4, None, "hello"))

    assert log.speaker_ids() == {1, 4}


def test_log_counts_utterances():
    log = TranscriptLog()
    log.append(Utterance(1, "Sarah", "hi"))
    log.append(Utterance(1, "Sarah", "again"))

    assert log.utterance_count() == 2
