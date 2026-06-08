"""Speaker context: guest intro, reset, and privacy boundaries."""

from __future__ import annotations

from core.speaker_context import SpeakerContext, is_temporary_speaker_intro


def test_guest_intro_patterns():
    for phrase in (
        "I am Guest B talking to you now",
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
