"""Central memory policy router — fake fixtures only."""

from __future__ import annotations

import pytest

from core.brain_v2.memory_policy import (
    MemoryPolicyRoute,
    is_casual_episode_filler,
    is_uncertain_or_hypothetical,
    policy_route_table,
    route_owner_utterance,
)


@pytest.mark.parametrize(
    ("text", "expected_route", "reason_fragment"),
    [
        ("My name is Owner A.", MemoryPolicyRoute.ACTIVE_MEMORY, "owner_auto_trust"),
        ("I live in City A.", MemoryPolicyRoute.ACTIVE_MEMORY, "owner_auto_trust"),
        (
            "My real name is Owner Legal but you can call me Person C.",
            MemoryPolicyRoute.ACTIVE_MEMORY,
            "owner_auto_trust",
        ),
        ("I prefer Topic A.", MemoryPolicyRoute.ACTIVE_MEMORY, "owner_auto_trust"),
        ("Person C is my sister.", MemoryPolicyRoute.ACTIVE_MEMORY, "owner_auto_trust"),
        ("I am in City B.", MemoryPolicyRoute.SESSION_MEMORY, "trip_or_current"),
        ("haha okay", MemoryPolicyRoute.EPISODE_ONLY, "casual_filler"),
        ("remind me to call Person C tomorrow.", MemoryPolicyRoute.TASK, "task_or_action"),
        (
            "My partner Person B studies at School A.",
            MemoryPolicyRoute.REVIEW_QUEUE,
            "needs_review",
        ),
        (
            "I might move to City C next year.",
            MemoryPolicyRoute.EPISODE_ONLY,
            "uncertain_hypothetical",
        ),
        (
            "Maybe I will switch majors someday.",
            MemoryPolicyRoute.EPISODE_ONLY,
            "uncertain_hypothetical",
        ),
    ],
)
def test_route_owner_utterance(text, expected_route, reason_fragment):
    decision = route_owner_utterance(text)
    assert decision.route == expected_route
    assert reason_fragment in decision.reason


def test_guest_declarative_rejected():
    decision = route_owner_utterance("My name is Guest A.", guest=True)
    assert decision.route == MemoryPolicyRoute.REJECT
    assert decision.reason == "guest_owner_memory"


def test_remember_this_routes_active_or_review():
    decision = route_owner_utterance("Remember this: I live in City A.")
    assert decision.route in (
        MemoryPolicyRoute.ACTIVE_MEMORY,
        MemoryPolicyRoute.REVIEW_QUEUE,
    )


def test_policy_table_covers_buckets():
    table = policy_route_table()
    assert MemoryPolicyRoute.ACTIVE_MEMORY.value in table.values()
    assert MemoryPolicyRoute.SESSION_MEMORY.value in table.values()
    assert MemoryPolicyRoute.EPISODE_ONLY.value in table.values()
    assert MemoryPolicyRoute.TASK.value in table.values()
    assert MemoryPolicyRoute.REVIEW_QUEUE.value in table.values()


def test_uncertain_detector():
    assert is_uncertain_or_hypothetical("Maybe I will switch majors someday.")
    assert not is_uncertain_or_hypothetical("I live in City A.")


def test_casual_filler_detector():
    assert is_casual_episode_filler("haha okay")
    assert not is_casual_episode_filler("what is my name?")
