"""Save-vs-session memory prompt."""

from __future__ import annotations

import pytest

from core.brain_v2.memory_save_prompt import (
    format_memory_scope_question,
    is_save_to_memory_confirmation,
    is_session_only_confirmation,
    should_ask_memory_scope,
)
from core.brain_v2.owner_auto_trust import is_explicit_remember_command


def test_should_ask_for_plain_owner_fact():
    assert should_ask_memory_scope(
        statement="I am a rising senior and I will be graduating in May 2027.",
        candidate_type="education",
        explicit_remember=False,
    )


def test_should_not_ask_for_remember_this():
    assert not should_ask_memory_scope(
        statement="Remember this: I live in City A.",
        candidate_type="location",
        explicit_remember=True,
    )


def test_should_not_ask_for_current_location():
    assert not should_ask_memory_scope(
        statement="I am in City B.",
        candidate_type="current_location",
        explicit_remember=False,
    )


@pytest.mark.parametrize(
    "phrase",
    [
        "save in memory",
        "save it",
        "long term",
        "remember it",
        "yes save",
    ],
)
def test_save_confirmations(phrase: str):
    assert is_save_to_memory_confirmation(phrase)


@pytest.mark.parametrize(
    "phrase",
    [
        "session only",
        "just this session",
        "don't save",
        "for this session only",
    ],
)
def test_session_only_confirmations(phrase: str):
    assert is_session_only_confirmation(phrase)


def test_scope_question_is_voice_friendly():
    q = format_memory_scope_question("I live in City A")
    assert "long-term memory" in q.lower()
    assert "session only" in q.lower()
    assert is_explicit_remember_command("remember this: I prefer tea") is True
