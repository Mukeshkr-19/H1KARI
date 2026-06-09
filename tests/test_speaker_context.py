"""Speaker context: guest intro, reset, and privacy boundaries."""

from __future__ import annotations

from core.speaker_context import SpeakerContext, is_temporary_speaker_intro


def test_guest_intro_patterns():
    for phrase in (
        "I am Guest B talking to you now",
        "i am guest b talking to you",
        "this is Guest B",
        "Guest B here",
    ):
        assert is_temporary_speaker_intro(phrase)


def test_guest_intro_marks_guest_speaker():
    ctx = SpeakerContext(primary_user="Owner A")
    ctx.update_from_input("I am Guest B talking to you now")
    assert ctx.current_speaker == "Guest B"
    assert ctx.is_guest_speaker()
    assert ctx.last_was_session_intro


def test_back_to_owner_resets_guest():
    ctx = SpeakerContext(primary_user="Owner A")
    ctx.update_from_input("I am Guest B talking to you now")
    assert ctx.is_guest_speaker()
    ctx.update_from_input("back to owner")
    assert ctx.consume_speaker_reset()
    assert not ctx.is_guest_speaker()
    assert ctx.current_speaker == "Owner A"


def test_owner_again_resets_guest():
    ctx = SpeakerContext(primary_user="Owner A")
    ctx.update_from_input("Guest B here")
    assert ctx.is_guest_speaker()
    ctx.update_from_input("it's Owner A again")
    assert ctx.consume_speaker_reset()
    assert not ctx.is_guest_speaker()


def test_i_am_doing_is_not_guest_speaker():
    ctx = SpeakerContext(primary_user="Owner A")
    ctx.update_from_input("I am doing my bachelors in Topic A at School A")
    assert ctx.current_speaker is None
    assert not ctx.is_guest_speaker()


def test_guest_intro_without_now_lowercase():
    ctx = SpeakerContext(primary_user="Owner A")
    ctx.update_from_input("i am guest c talking to you")
    assert ctx.current_speaker == "Guest C"
    assert ctx.is_guest_speaker()
    assert ctx.last_was_session_intro


def test_guest_visit_recall_after_owner_reset():
    ctx = SpeakerContext(primary_user="Owner A")
    ctx.update_from_input("I am Guest B talking to you")
    ctx.note_guest_relation_from_input("I am your owner's sister")
    ctx.update_from_input("back to owner")
    assert ctx.last_guest_visit is not None
    assert ctx.last_guest_visit.guest_name == "Guest B"
    assert ctx.last_guest_visit.relation == "sister"
