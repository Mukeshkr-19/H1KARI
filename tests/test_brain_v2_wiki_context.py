"""Synthetic tests for accepted-memory-anchored wiki context selection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

import pytest

from core.brain_v2.memory_lifecycle import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_RETIRED,
    LIFECYCLE_SUPERSEDED,
)
from core.brain_v2.wiki_context import (
    WikiContextAnchor,
    WikiContextEntry,
    WikiContextHit,
    WikiContextPolicy,
    select_wiki_context,
)

REF = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)


def _anchor(**kwargs) -> WikiContextAnchor:
    defaults = dict(
        memory_id="mem-owner-a-001",
        statement="Owner A studies at School A in City A.",
        lifecycle_status=LIFECYCLE_ACTIVE,
        review_status="accepted",
        subject_keys=("owner a",),
        strength=0.8,
    )
    defaults.update(kwargs)
    return WikiContextAnchor(**defaults)


def _entry(entry_id: str, text: str, **kwargs) -> WikiContextEntry:
    return WikiContextEntry(
        entry_id=entry_id,
        text=text,
        section=kwargs.pop("section", "education"),
        title=kwargs.pop("title", "School notes"),
        source_type=kwargs.pop("source_type", "owner_note"),
        source_memory_ids=kwargs.pop("source_memory_ids", ("mem-owner-a-001",)),
        updated_at=kwargs.pop("updated_at", "2026-03-10T10:00:00+00:00"),
        created_at=kwargs.pop("created_at", "2026-03-01T10:00:00+00:00"),
        quality=kwargs.pop("quality", None),
        subject_keys=kwargs.pop("subject_keys", ("owner a",)),
        is_assistant_authored=kwargs.pop("is_assistant_authored", False),
        **kwargs,
    )


def test_accepted_anchor_returns_support():
    hits = select_wiki_context(
        "School A City A",
        _anchor(),
        [
            _entry(
                "w1",
                "Owner A confirmed School A enrollment paperwork in City A.",
            )
        ],
        reference_time=REF,
    )
    assert hits
    assert all(isinstance(h, WikiContextHit) for h in hits)
    assert all(h.is_supplemental for h in hits)
    assert all(h.anchor_memory_id == "mem-owner-a-001" for h in hits)


@pytest.mark.parametrize(
    "lifecycle",
    [LIFECYCLE_RETIRED, LIFECYCLE_SUPERSEDED, "paused", "pending"],
)
def test_invalid_lifecycle_returns_empty(lifecycle):
    hits = select_wiki_context(
        "School A",
        _anchor(lifecycle_status=lifecycle),
        [_entry("w1", "Owner A studies at School A in City A.")],
        reference_time=REF,
    )
    assert hits == ()


@pytest.mark.parametrize("review", ["pending", "rejected", "", "unknown"])
def test_invalid_review_status_returns_empty(review):
    hits = select_wiki_context(
        "School A",
        _anchor(review_status=review),
        [_entry("w1", "Owner A studies at School A in City A.")],
        reference_time=REF,
    )
    assert hits == ()


def test_missing_anchor_returns_empty():
    assert (
        select_wiki_context(
            "School A",
            None,
            [_entry("w1", "Owner A studies at School A in City A.")],
            reference_time=REF,
        )
        == ()
    )


def test_empty_query_and_empty_entries():
    assert select_wiki_context("", _anchor(), [], reference_time=REF) == ()


def test_missing_reference_time_is_deterministic_and_uses_neutral_recency():
    entries = [_entry("w1", "Owner A noted School A lab hours in City A.")]
    first = select_wiki_context("School A", _anchor(), entries)
    second = select_wiki_context("School A", _anchor(), entries)
    assert first == second
    assert first[0].breakdown.recency == 0.0


def test_subject_is_required_when_anchor_is_subject_scoped():
    entry = _entry(
        "w1",
        "School A lab hours are listed for City A.",
        subject_keys=(),
    )
    assert select_wiki_context("School A", _anchor(), [entry], reference_time=REF) == ()


def test_future_timestamp_does_not_gain_recency():
    entry = _entry(
        "w1",
        "Owner A noted School A lab hours in City A.",
        updated_at="2030-01-01T00:00:00+00:00",
    )
    hits = select_wiki_context("School A", _anchor(), [entry], reference_time=REF)
    assert hits[0].breakdown.recency == 0.0


def test_malformed_top_level_inputs_fail_closed():
    assert select_wiki_context(123, _anchor(), [], reference_time=REF) == ()
    assert select_wiki_context("School A", _anchor(), "not entries", reference_time=REF) == ()


def test_deterministic_ranking_and_tie_break():
    policy = WikiContextPolicy(min_score=0.0, max_hits=5)
    entries = [
        _entry(
            "w-b",
            "Owner A noted School A lab hours in City A.",
            section="labs",
            source_memory_ids=("mem-owner-a-001",),
            updated_at="2026-03-12T10:00:00+00:00",
        ),
        _entry(
            "w-a",
            "Owner A noted School A lab hours in City A.",
            section="labs",
            source_memory_ids=("mem-owner-a-001",),
            updated_at="2026-03-12T10:00:00+00:00",
        ),
        _entry(
            "w-c",
            "Owner A reserved Restaurant A seating in City A.",
            section="food",
            source_memory_ids=("mem-other",),
            updated_at="2025-01-01T10:00:00+00:00",
            quality=0.2,
        ),
    ]
    first = select_wiki_context(
        "School A City A", _anchor(), entries, policy=policy, reference_time=REF
    )
    second = select_wiki_context(
        "School A City A",
        _anchor(),
        list(reversed(entries)),
        policy=policy,
        reference_time=REF,
    )
    assert [h.entry_id for h in first] == [h.entry_id for h in second]
    scores = [h.score for h in first]
    assert scores == sorted(scores, reverse=True)
    for i in range(len(first) - 1):
        left, right = first[i], first[i + 1]
        if left.score == right.score:
            assert (left.section, left.entry_id) <= (right.section, right.entry_id)


def test_input_order_independence_for_dedupe_choice():
    a = _entry("w-z", "Owner A prefers Restaurant A in City A!")
    b = _entry("w-a", "Owner A prefers Restaurant A in City A.")
    policy = WikiContextPolicy(min_score=0.0)
    left = select_wiki_context(
        "Restaurant A", _anchor(), [a, b], policy=policy, reference_time=REF
    )
    right = select_wiki_context(
        "Restaurant A", _anchor(), [b, a], policy=policy, reference_time=REF
    )
    assert len(left) == 1
    assert len(right) == 1


def test_lexical_score_prefers_query_overlap():
    hits = select_wiki_context(
        "Restaurant A dinner City A",
        _anchor(),
        [
            _entry(
                "match",
                "Owner A booked Restaurant A dinner reservations in City A.",
                section="food",
            ),
            _entry(
                "miss",
                "Owner A reviewed School A homework before class.",
                section="school",
            ),
        ],
        reference_time=REF,
    )
    assert hits[0].entry_id == "match"
    assert "query_overlap" in hits[0].breakdown.reasons


def test_anchor_relevance_and_strength():
    low = select_wiki_context(
        "Restaurant A City A",
        _anchor(strength=0.1),
        [
            _entry(
                "w1",
                "Owner A picked Restaurant A in City A for the reunion dinner.",
                section="food",
            )
        ],
        reference_time=REF,
    )
    high = select_wiki_context(
        "Restaurant A City A",
        _anchor(strength=0.95),
        [
            _entry(
                "w1",
                "Owner A picked Restaurant A in City A for the reunion dinner.",
                section="food",
            )
        ],
        reference_time=REF,
    )
    assert high[0].breakdown.anchor_relevance >= low[0].breakdown.anchor_relevance
    assert high[0].score >= low[0].score


def test_source_agreement_boost():
    agreed = select_wiki_context(
        "School A",
        _anchor(),
        [
            _entry(
                "agreed",
                "Owner A finished School A orientation in City A.",
                source_memory_ids=("mem-owner-a-001",),
            )
        ],
        reference_time=REF,
    )
    other = select_wiki_context(
        "School A",
        _anchor(),
        [
            _entry(
                "other",
                "Owner A finished School A orientation in City A.",
                source_memory_ids=("mem-other",),
            )
        ],
        reference_time=REF,
    )
    assert agreed[0].breakdown.source_agreement > other[0].breakdown.source_agreement


def test_recency_uses_reference_time():
    newer = _entry(
        "new",
        "Owner A confirmed School A spring schedule in City A.",
        updated_at="2026-03-14T10:00:00+00:00",
    )
    older = _entry(
        "old",
        "Owner A confirmed School A spring schedule in City A campus.",
        updated_at="2025-01-01T10:00:00+00:00",
    )
    hits = select_wiki_context(
        "School A City A",
        _anchor(),
        [older, newer],
        policy=WikiContextPolicy(min_score=0.0, max_hits=5),
        reference_time=REF,
    )
    by_id = {h.entry_id: h for h in hits}
    assert by_id["new"].breakdown.recency >= by_id["old"].breakdown.recency


def test_low_information_penalty():
    hits = select_wiki_context(
        "unrelated topic",
        _anchor(),
        [_entry("short", "ok note", section="misc", quality=0.2)],
        policy=WikiContextPolicy(min_score=0.0),
        reference_time=REF,
    )
    assert hits
    assert hits[0].breakdown.penalties > 0.0
    assert "low_info_penalty" in hits[0].breakdown.reasons


def test_deduplication_of_normalized_equivalents():
    hits = select_wiki_context(
        "Restaurant A",
        _anchor(),
        [
            _entry("a", "Owner A likes Restaurant A in City A."),
            _entry("b", "Owner A likes Restaurant A in City A!"),
        ],
        reference_time=REF,
    )
    assert len(hits) == 1


def test_text_truncation():
    long_text = "Owner A " + ("really " * 80) + "likes Restaurant A in City A."
    policy = WikiContextPolicy(max_text_length=50, min_score=0.0)
    hits = select_wiki_context(
        "Restaurant A",
        _anchor(),
        [_entry("long", long_text, section="food")],
        policy=policy,
        reference_time=REF,
    )
    assert hits
    assert len(hits[0].text) == 50


def test_entry_and_result_caps():
    entries = [
        _entry(
            f"w{i}",
            f"Owner A note {i} about School A campus life in City A.",
            section=f"sec-{i}",
        )
        for i in range(10)
    ]
    policy = WikiContextPolicy(
        max_entries_examined=3,
        max_hits=2,
        min_score=0.0,
        max_per_section=2,
        max_per_source=5,
    )
    hits = select_wiki_context(
        "School A City A", _anchor(), entries, policy=policy, reference_time=REF
    )
    assert len(hits) <= 2


def test_per_section_and_source_caps():
    entries = [
        _entry(
            "s1",
            "Owner A recorded School A lab A details in City A.",
            section="labs",
            source_type="owner_note",
        ),
        _entry(
            "s2",
            "Owner A recorded School A lab B details in City A.",
            section="labs",
            source_type="owner_note",
        ),
        _entry(
            "s3",
            "Owner A recorded School A lab C details in City A.",
            section="labs",
            source_type="owner_note",
        ),
        _entry(
            "o1",
            "Owner A saved School A orientation notes in City A.",
            section="orientation",
            source_type="compiled_note",
        ),
    ]
    policy = WikiContextPolicy(
        max_per_section=1,
        max_per_source=2,
        min_score=0.0,
        max_hits=10,
    )
    hits = select_wiki_context(
        "School A City A", _anchor(), entries, policy=policy, reference_time=REF
    )
    sections = [h.section for h in hits]
    assert sections.count("labs") <= 1
    sources = [h.source_type for h in hits]
    assert sources.count("owner_note") <= 2


def test_provenance_preserved():
    hits = select_wiki_context(
        "School A",
        _anchor(),
        [
            _entry(
                "prov",
                "Owner A confirmed School A classes meet in City A.",
                section="education",
                title="Enrollment",
                source_type="owner_note",
                source_memory_ids=("mem-owner-a-001", "mem-extra"),
                updated_at="2026-03-11T08:00:00+00:00",
                created_at="2026-03-01T08:00:00+00:00",
            )
        ],
        reference_time=REF,
    )
    hit = hits[0]
    assert hit.entry_id == "prov"
    assert hit.section == "education"
    assert hit.title == "Enrollment"
    assert hit.source_type == "owner_note"
    assert hit.source_memory_ids == ("mem-owner-a-001", "mem-extra")
    assert hit.updated_at.startswith("2026-03-11")
    assert hit.created_at.startswith("2026-03-01")
    assert hit.supplemental_reason == "accepted_memory_anchored_wiki"


def test_supplemental_only_guarantees():
    hits = select_wiki_context(
        "School A",
        _anchor(),
        [_entry("w1", "Owner A confirmed School A classes meet in City A.")],
        reference_time=REF,
    )
    assert all(h.is_supplemental is True for h in hits)
    assert all(h.supplemental_reason for h in hits)


def test_unrelated_subject_excluded():
    hits = select_wiki_context(
        "School A",
        _anchor(subject_keys=("owner a",)),
        [
            _entry(
                "other",
                "Guest B studies at School B in City B.",
                subject_keys=("guest b",),
                section="other",
            ),
            _entry(
                "ok",
                "Owner A studies at School A in City A downtown.",
                subject_keys=("owner a",),
            ),
        ],
        reference_time=REF,
    )
    assert all(h.entry_id != "other" for h in hits)
    assert any(h.entry_id == "ok" for h in hits)


def test_assistant_authority_excluded():
    hits = select_wiki_context(
        "School A",
        _anchor(),
        [
            _entry(
                "bot",
                "Owner A studies at School A in City A according to notes.",
                is_assistant_authored=True,
            ),
            _entry(
                "draft",
                "Owner A studies at School A in City A from draft text.",
                source_type="assistant_draft",
            ),
            _entry(
                "ok",
                "Owner A studies at School A in City A campus building.",
            ),
        ],
        reference_time=REF,
    )
    assert [h.entry_id for h in hits] == ["ok"]


def test_invalid_timestamps_neutral_recency_and_score_bounds():
    hits = select_wiki_context(
        "Restaurant A City A",
        _anchor(),
        [
            _entry(
                "bad-ts",
                "Owner A reserved Restaurant A seating in City A tonight.",
                section="food",
                updated_at="not-a-timestamp",
                created_at="also-bad",
            )
        ],
        reference_time=REF,
    )
    assert hits
    assert hits[0].breakdown.recency == 0.0
    assert 0.0 <= hits[0].score <= 1.0
    assert 0.0 <= hits[0].breakdown.total <= 1.0
    assert 0.0 <= hits[0].breakdown.lexical <= 1.0


def test_no_mutation_of_caller_inputs():
    entries: List[WikiContextEntry] = [
        _entry("w1", "Owner A confirmed School A classes meet in City A.")
    ]
    before = (
        entries[0].entry_id,
        entries[0].text,
        entries[0].source_memory_ids,
        entries[0].subject_keys,
    )
    select_wiki_context("School A", _anchor(), entries, reference_time=REF)
    after = (
        entries[0].entry_id,
        entries[0].text,
        entries[0].source_memory_ids,
        entries[0].subject_keys,
    )
    assert before == after


def test_sqlite_and_environment_blocked(monkeypatch):
    import os
    import sqlite3
    import subprocess

    def _blocked(*_a, **_k):
        raise AssertionError("side effect forbidden")

    monkeypatch.setattr(sqlite3, "connect", _blocked)
    monkeypatch.setattr(os, "environ", {"SHOULD_NOT_READ": "1"})
    monkeypatch.setattr(subprocess, "Popen", _blocked)
    monkeypatch.setattr(subprocess, "run", _blocked)

    hits = select_wiki_context(
        "School A",
        _anchor(),
        [_entry("w1", "Owner A confirmed School A classes meet in City A.")],
        reference_time=REF,
    )
    assert hits


def test_malformed_rows_ignored():
    class Broken:
        entry_id = None
        text = None

    hits = select_wiki_context(
        "School A",
        _anchor(),
        [
            Broken(),
            {"entry_id": "", "text": "Owner A studies at School A."},
            {"entry_id": "ok", "text": "Owner A studies at School A in City A."},
            {"entry_id": "huge", "text": "x" * 25000},
        ],
        policy=WikiContextPolicy(min_score=0.0),
        reference_time=REF,
    )
    assert all(h.entry_id == "ok" for h in hits)


def test_naive_reference_time_rejected():
    with pytest.raises(ValueError):
        select_wiki_context(
            "School A",
            _anchor(),
            [_entry("w1", "Owner A studies at School A in City A.")],
            reference_time=datetime(2026, 3, 15, 12, 0),
        )


def test_policy_validation():
    with pytest.raises(ValueError):
        WikiContextPolicy(max_hits=0)
    with pytest.raises(ValueError):
        WikiContextPolicy(min_score=1.5)
