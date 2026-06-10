"""Save-vs-session memory prompt."""

from __future__ import annotations

import pytest

from core.brain_v2.memory_save_prompt import (
    format_memory_scope_question,
    is_save_to_memory_confirmation,
    is_session_only_confirmation,
    should_ask_memory_scope,
)
from core.brain_v2.memory_type import extract_owner_identity_names
from core.brain_v2.owner_auto_trust import is_explicit_remember_command


def test_real_name_but_call_me_splits_legal_and_preferred():
    parsed = extract_owner_identity_names(
        "My real name is Owner Legal but you can call me Person C."
    )
    assert parsed.get("legal_name") == "Owner Legal"
    assert parsed.get("preferred_name") == "Person C"


def test_should_not_ask_for_core_owner_identity():
    assert not should_ask_memory_scope(
        statement="My real name is Owner A but you can call me Person B.",
        candidate_type="identity",
        explicit_remember=False,
    )


def test_should_not_ask_for_stable_home():
    assert not should_ask_memory_scope(
        statement="I live in City A.",
        candidate_type="location",
        explicit_remember=False,
    )


def test_never_asks_save_vs_session_even_for_third_party_education():
    assert not should_ask_memory_scope(
        statement="My partner Person B studies at School A.",
        candidate_type="education",
        explicit_remember=False,
    )


def test_should_not_ask_for_graduation_education():
    assert not should_ask_memory_scope(
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
    assert "save in memory" not in q.lower()
    assert "session only" not in q.lower()
    assert q == 'Got it - "I live in City A".'
    assert is_explicit_remember_command("remember this: I prefer tea") is True
