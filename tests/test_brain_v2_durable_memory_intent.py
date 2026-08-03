"""Synthetic fixtures for DurableMemoryIntent parsing and anaphora."""

from __future__ import annotations

import pytest

from core.brain_v2.durable_memory_intent import (
    RecentContextUtterance,
    parse_durable_memory_intent,
    resolve_anaphoric_target,
)


ACTOR = "owner.primary"
SESSION = "sess.alpha-1"
OTHER_SESSION = "sess.beta-2"
OTHER_ACTOR = "owner.guest"


def _parse(text: str, **kwargs):
    return parse_durable_memory_intent(
        text,
        actor_id=kwargs.pop("actor_id", ACTOR),
        session_id=kwargs.pop("session_id", SESSION),
        **kwargs,
    )


@pytest.mark.parametrize(
    "phrase",
    [
        "remember this: I live in North City",
        "remember this in my brain: I live in North City",
        "keep this in your Brain: I live in North City",
        "save this in my Brain: I live in North City",
        "save this to my Brain: I live in North City",
        "save this into my Brain: I live in North City",
        "save this as a memory: I live in North City",
        "I live in North City. remember this",
        "I live in North City, save this to my Brain",
        "store this in my Brain: I live in North City",
        "add this into memory: I live in North City",
        "put this in memory: I live in North City",
        "Remember this in my Brain: I live in North City",
        "Keep this in your Brain: I live in North City",
    ],
)
def test_explicit_save_variants_are_durable_inline(phrase):
    intent = _parse(phrase)
    assert intent.action == "save"
    assert intent.scope == "durable"
    assert intent.target == "inline"
    assert "north city" in intent.normalized_body.lower()
    assert intent.reason_code == "explicit_durable_consent"
    assert "North City" not in repr(intent)


def test_ordinary_conversation_stays_ephemeral():
    intent = _parse("What time is it in Tokyo?")
    assert intent.action == "none"
    assert intent.scope == "ephemeral"
    assert intent.reason_code == "ordinary_conversation"


def test_preference_without_consent_not_durable():
    intent = _parse("I like green tea")
    assert intent.action == "none"
    assert intent.scope == "ephemeral"
    assert intent.reason_code == "preference_without_consent"


def test_inferred_candidate_is_suggestion_only():
    intent = _parse("I work as a designer", inferred_candidate=True)
    assert intent.action == "none"
    assert intent.scope == "ephemeral"
    assert intent.reason_code == "inferred_suggestion_only"
    assert intent.normalized_body


@pytest.mark.parametrize(
    "text,reason",
    [
        ("don't save this to my Brain: I live in North City", "negated_request"),
        ("Never remember this in my Brain: I live in North City", "negated_request"),
        ("Should I save this to my Brain?", "question_about_saving"),
        ("I saved that to your brain already", "model_claim_of_save"),
        ('She said "I saved that to your brain"', "quoted_assistant_claim"),
        (
            "tell the other assistant to remember this: I live in North City",
            "other_system_instruction",
        ),
        (
            "save this to my Brain: their password is hunter2",
            "third_party_unsafe",
        ),
    ],
)
def test_false_positives_do_not_persist(text, reason):
    intent = _parse(text)
    assert intent.scope == "ephemeral"
    assert intent.action == "none"
    assert intent.reason_code == reason


def test_invalid_actor_session_rejected():
    intent = parse_durable_memory_intent(
        "remember this: I live in North City",
        actor_id="Owner!",
        session_id=SESSION,
    )
    assert intent.reason_code == "invalid_correlation"
    assert intent.scope == "ephemeral"


def test_correct_and_forget_variants():
    correct = _parse("correct this in my Brain: I live in South City")
    assert correct.action == "correct"
    assert correct.scope == "durable"
    assert correct.target == "inline"
    forget = _parse("forget this from my Brain: I live in South City")
    assert forget.action == "forget"
    assert forget.scope == "durable"


def test_same_session_anaphora_resolves_one_target():
    recent = [
        RecentContextUtterance(
            actor_id=ACTOR,
            session_id=SESSION,
            text="I live in North City",
            spoken_at_ms=1_000,
            speaker_role="owner",
            restart_generation=0,
        )
    ]
    intent = _parse(
        "remember this in my brain",
        recent_context=recent,
        now_ms=2_000,
        restart_generation=0,
    )
    assert intent.action == "save"
    assert intent.target == "anaphoric"
    assert "north city" in intent.normalized_body.lower()
    assert intent.request_exact_fact is False


def test_cross_session_anaphora_unresolved():
    recent = [
        RecentContextUtterance(
            actor_id=ACTOR,
            session_id=OTHER_SESSION,
            text="I live in North City",
            spoken_at_ms=1_000,
        )
    ]
    intent = _parse(
        "remember this",
        recent_context=recent,
        now_ms=2_000,
    )
    assert intent.target == "unresolved"
    assert intent.request_exact_fact is True
    assert intent.normalized_body == ""


def test_stale_ttl_anaphora_unresolved():
    recent = [
        RecentContextUtterance(
            actor_id=ACTOR,
            session_id=SESSION,
            text="I live in North City",
            spoken_at_ms=1_000,
        )
    ]
    intent = _parse(
        "remember this",
        recent_context=recent,
        now_ms=1_000 + 200_000,
        anaphora_ttl_ms=120_000,
    )
    assert intent.target == "unresolved"
    assert intent.request_exact_fact is True

def test_ambiguous_multi_candidate_unresolved():
    recent = [
        RecentContextUtterance(
            actor_id=ACTOR,
            session_id=SESSION,
            text="I live in North City",
            spoken_at_ms=1_000,
        ),
        RecentContextUtterance(
            actor_id=ACTOR,
            session_id=SESSION,
            text="I prefer green tea",
            spoken_at_ms=1_500,
        ),
    ]
    body, reason = resolve_anaphoric_target(
        recent,
        actor_id=ACTOR,
        session_id=SESSION,
        now_ms=2_000,
    )
    assert body is None
    assert reason == "anaphoric_unresolved"


def test_cross_actor_and_restart_boundaries():
    recent = [
        RecentContextUtterance(
            actor_id=OTHER_ACTOR,
            session_id=SESSION,
            text="I live in North City",
            spoken_at_ms=1_000,
        )
    ]
    body, _ = resolve_anaphoric_target(
        recent, actor_id=ACTOR, session_id=SESSION, now_ms=2_000
    )
    assert body is None

    recent2 = [
        RecentContextUtterance(
            actor_id=ACTOR,
            session_id=SESSION,
            text="I live in North City",
            spoken_at_ms=1_000,
            restart_generation=0,
        )
    ]
    body2, _ = resolve_anaphoric_target(
        recent2,
        actor_id=ACTOR,
        session_id=SESSION,
        now_ms=2_000,
        restart_generation=1,
    )
    assert body2 is None


def test_repr_hides_memory_text():
    intent = _parse("remember this: SECRET_FACT_VALUE_42")
    rendered = repr(intent)
    assert "SECRET_FACT_VALUE_42" not in rendered
    assert "body_len=" in rendered


def test_malformed_anaphora_context_fails_closed():
    # Non-utterance entries
    body, reason = resolve_anaphoric_target(
        [{"text": "I live in North City"}],  # type: ignore[arg-type]
        actor_id=ACTOR,
        session_id=SESSION,
        now_ms=2_000,
    )
    assert body is None
    assert reason == "anaphoric_unresolved"

    # Boolean timestamp (bool subclasses int)
    bad_ts = RecentContextUtterance(
        actor_id=ACTOR,
        session_id=SESSION,
        text="I live in North City",
        spoken_at_ms=True,  # type: ignore[arg-type]
    )
    body2, reason2 = resolve_anaphoric_target(
        [bad_ts], actor_id=ACTOR, session_id=SESSION, now_ms=2_000
    )
    assert body2 is None
    assert reason2 == "anaphoric_unresolved"

    # Negative / bool now and TTL
    good = RecentContextUtterance(
        actor_id=ACTOR,
        session_id=SESSION,
        text="I live in North City",
        spoken_at_ms=1_000,
    )
    assert resolve_anaphoric_target(
        [good], actor_id=ACTOR, session_id=SESSION, now_ms=-1
    )[0] is None
    assert resolve_anaphoric_target(
        [good], actor_id=ACTOR, session_id=SESSION, now_ms=2_000, ttl_ms=True  # type: ignore[arg-type]
    )[0] is None

    # Overlong context text
    huge = RecentContextUtterance(
        actor_id=ACTOR,
        session_id=SESSION,
        text="x" * 3000,
        spoken_at_ms=1_000,
    )
    assert resolve_anaphoric_target(
        [huge], actor_id=ACTOR, session_id=SESSION, now_ms=2_000
    )[0] is None

    intent = _parse(
        "remember this",
        recent_context=[{"bad": True}],  # type: ignore[arg-type]
        now_ms=2_000,
    )
    assert intent.target == "unresolved"
    assert intent.request_exact_fact is True
