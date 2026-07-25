"""Tests for accepted-memory-anchored episodic support selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple

import pytest

from core.brain_v2.episodic_support import (
    EpisodicSupportAnchor,
    EpisodicSupportHit,
    EpisodicSupportPolicy,
    select_episodic_support,
)
from core.brain_v2.memory_lifecycle import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_RETIRED,
    LIFECYCLE_SUPERSEDED,
)
from core.brain_v2.schemas import StructuredEpisode, TranscriptSegment

REF = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def _anchor(**kwargs) -> EpisodicSupportAnchor:
    defaults = dict(
        memory_id="mem-owner-a-001",
        episode_id="ep-primary",
        source_segment_ids=("seg-a1",),
        statement="Owner A studies at School A in City A.",
        lifecycle_status=LIFECYCLE_ACTIVE,
    )
    defaults.update(kwargs)
    return EpisodicSupportAnchor(**defaults)


def _episode(episode_id: str, **kwargs) -> StructuredEpisode:
    return StructuredEpisode(
        episode_id=episode_id,
        session_id=kwargs.pop("session_id", "sess-a"),
        started_at=kwargs.pop("started_at", "2026-01-10T10:00:00+00:00"),
        ended_at=kwargs.pop("ended_at", "2026-01-10T10:30:00+00:00"),
        **kwargs,
    )


def _seg(
    segment_id: str,
    episode_id: str,
    text: str,
    *,
    is_user: bool = True,
    started_at: str = "2026-01-10T10:05:00+00:00",
) -> TranscriptSegment:
    return TranscriptSegment(
        segment_id=segment_id,
        episode_id=episode_id,
        sequence=1,
        text=text,
        is_user=is_user,
        started_at=started_at,
    )


def test_01_empty_without_anchor():
    hits = select_episodic_support(
        "School A",
        None,
        [_episode("ep-primary")],
        [_seg("seg-a1", "ep-primary", "Owner A likes Restaurant A.")],
        reference_time=REF,
    )
    assert hits == ()


def test_02_empty_when_anchor_retired():
    hits = select_episodic_support(
        "School A",
        _anchor(lifecycle_status=LIFECYCLE_RETIRED),
        [_episode("ep-primary")],
        [_seg("seg-a1", "ep-primary", "Owner A likes Restaurant A.")],
        reference_time=REF,
    )
    assert hits == ()


def test_03_empty_when_anchor_superseded():
    hits = select_episodic_support(
        "School A",
        _anchor(lifecycle_status=LIFECYCLE_SUPERSEDED),
        [_episode("ep-primary")],
        [_seg("seg-a1", "ep-primary", "Owner A likes Restaurant A.")],
        reference_time=REF,
    )
    assert hits == ()


def test_04_empty_when_custom_excluded_lifecycle():
    policy = EpisodicSupportPolicy(excluded_lifecycle_statuses=frozenset({"paused"}))
    hits = select_episodic_support(
        "School A",
        _anchor(lifecycle_status="paused"),
        [_episode("ep-primary")],
        [_seg("seg-a1", "ep-primary", "Owner A likes Restaurant A.")],
        policy=policy,
        reference_time=REF,
    )
    assert hits == ()


def test_05_links_episode_by_anchor_episode_id():
    hits = select_episodic_support(
        "Restaurant A lunch",
        _anchor(),
        [_episode("ep-primary")],
        [
            _seg("seg-a1", "ep-primary", "Owner A studies at School A in City A."),
            _seg(
                "seg-a2",
                "ep-primary",
                "Owner A met friends at Restaurant A for lunch in City A.",
            ),
        ],
        reference_time=REF,
    )
    assert hits
    assert all(h.episode_id == "ep-primary" for h in hits)
    assert any("Restaurant A" in h.text for h in hits)


def test_06_links_episode_by_source_segment_overlap():
    hits = select_episodic_support(
        "Restaurant A",
        _anchor(episode_id="ep-other", source_segment_ids=("seg-bridge",)),
        [
            _episode("ep-other"),
            _episode("ep-linked", ended_at="2026-01-12T10:30:00+00:00"),
        ],
        [
            _seg("seg-bridge", "ep-linked", "Owner A visited Restaurant A in City A."),
            _seg("seg-a1", "ep-other", "Owner A studies at School A in City A."),
        ],
        reference_time=REF,
    )
    assert any(h.episode_id == "ep-linked" for h in hits)


def test_07_skips_excluded_episode_ids():
    policy = EpisodicSupportPolicy(excluded_episode_ids=frozenset({"ep-primary"}))
    hits = select_episodic_support(
        "Restaurant A",
        _anchor(),
        [_episode("ep-primary")],
        [
            _seg("seg-a2", "ep-primary", "Owner A met friends at Restaurant A in City A."),
        ],
        policy=policy,
        reference_time=REF,
    )
    assert hits == ()


def test_08_user_segments_only_skips_assistant():
    hits = select_episodic_support(
        "Restaurant A",
        _anchor(),
        [_episode("ep-primary")],
        [
            _seg(
                "seg-bot",
                "ep-primary",
                "Owner A confirmed plans at Restaurant A in City A.",
                is_user=False,
            ),
            _seg(
                "seg-a2",
                "ep-primary",
                "Owner A met friends at Restaurant A in City A.",
            ),
        ],
        reference_time=REF,
    )
    assert all("Restaurant A" in h.text for h in hits)
    assert all(h.segment_id != "seg-bot" for h in hits)


def test_09_assistant_never_factual_even_if_user_segments_only_false():
    policy = EpisodicSupportPolicy(user_segments_only=False)
    hits = select_episodic_support(
        "Restaurant A",
        _anchor(),
        [_episode("ep-primary")],
        [
            _seg(
                "seg-bot",
                "ep-primary",
                "Owner A confirmed plans at Restaurant A in City A.",
                is_user=False,
            )
        ],
        policy=policy,
        reference_time=REF,
    )
    assert hits == ()


def test_10_excludes_filler_exact():
    hits = select_episodic_support(
        "thanks",
        _anchor(),
        [_episode("ep-primary")],
        [
            _seg("seg-a1", "ep-primary", "thanks"),
            _seg(
                "seg-a2",
                "ep-primary",
                "Owner A prefers Restaurant A in City A for dinner.",
            ),
        ],
        reference_time=REF,
    )
    assert all("thanks" not in h.text.lower() or "restaurant" in h.text.lower() for h in hits)
    assert any("Restaurant A" in h.text for h in hits)


def test_11_excludes_trailing_question_mark():
    hits = select_episodic_support(
        "School A",
        _anchor(),
        [_episode("ep-primary")],
        [
            _seg("seg-q", "ep-primary", "Does Owner A study at School A?"),
            _seg(
                "seg-a2",
                "ep-primary",
                "Owner A confirmed School A classes meet in City A.",
            ),
        ],
        reference_time=REF,
    )
    assert all(not h.text.endswith("?") for h in hits)


def test_12_excludes_question_forms_without_mark():
    hits = select_episodic_support(
        "School A",
        _anchor(),
        [_episode("ep-primary")],
        [
            _seg("seg-q", "ep-primary", "what is School A schedule for Owner A"),
            _seg(
                "seg-a2",
                "ep-primary",
                "Owner A confirmed School A classes meet in City A.",
            ),
        ],
        reference_time=REF,
    )
    assert all("what is" not in h.text.lower() for h in hits)


def test_13_truncates_segment_text():
    long_text = "Owner A " + ("really " * 80) + "likes Restaurant A in City A."
    policy = EpisodicSupportPolicy(max_segment_text_length=60, min_score=0.0)
    hits = select_episodic_support(
        "Restaurant A",
        _anchor(source_segment_ids=("seg-long",)),
        [_episode("ep-primary")],
        [_seg("seg-long", "ep-primary", long_text)],
        policy=policy,
        reference_time=REF,
    )
    assert hits
    assert len(hits[0].text) == 60


def test_14_respects_max_segments_per_episode():
    policy = EpisodicSupportPolicy(max_segments_per_episode=1, min_score=0.0)
    hits = select_episodic_support(
        "City A",
        _anchor(source_segment_ids=("seg-a1",)),
        [_episode("ep-primary")],
        [
            _seg("seg-a1", "ep-primary", "Owner A studies at School A in City A."),
            _seg("seg-a2", "ep-primary", "Owner A walks around City A after class."),
            _seg("seg-a3", "ep-primary", "Owner A eats at Restaurant A in City A."),
        ],
        policy=policy,
        reference_time=REF,
    )
    assert len(hits) == 1


def test_15_respects_max_episodes():
    policy = EpisodicSupportPolicy(max_episodes=1, min_score=0.0)
    hits = select_episodic_support(
        "City A",
        _anchor(source_segment_ids=("seg-bridge",)),
        [
            _episode("ep-one", ended_at="2026-01-11T10:30:00+00:00"),
            _episode("ep-two", ended_at="2026-01-12T10:30:00+00:00"),
        ],
        [
            _seg("seg-bridge", "ep-one", "Owner A noted City A weather was mild."),
            _seg("seg-b1", "ep-two", "Owner A visited Restaurant A in City A."),
        ],
        reference_time=REF,
    )
    episode_ids = {h.episode_id for h in hits}
    assert len(episode_ids) <= 1


def test_16_min_score_filters_weak_hits():
    policy = EpisodicSupportPolicy(min_score=0.95)
    hits = select_episodic_support(
        "unrelated topic",
        _anchor(),
        [_episode("ep-primary")],
        [
            _seg(
                "seg-a2",
                "ep-primary",
                "Owner A met friends at Restaurant A in City A.",
            )
        ],
        policy=policy,
        reference_time=REF,
    )
    assert hits == ()


def test_17_recency_uses_reference_time():
    newer = _seg(
        "seg-new",
        "ep-primary",
        "Owner A booked Restaurant A in City A for Friday.",
        started_at="2026-01-14T10:00:00+00:00",
    )
    older = _seg(
        "seg-old",
        "ep-primary",
        "Owner A walked through City A campus near School A.",
        started_at="2025-06-01T10:00:00+00:00",
    )
    hits = select_episodic_support(
        "Restaurant A City A",
        _anchor(source_segment_ids=("seg-a1",)),
        [_episode("ep-primary")],
        [older, newer],
        reference_time=REF,
    )
    by_id = {h.segment_id: h for h in hits}
    assert by_id["seg-new"].breakdown.recency >= by_id["seg-old"].breakdown.recency


def test_18_malformed_timestamp_neutral_recency():
    hits = select_episodic_support(
        "Restaurant A",
        _anchor(source_segment_ids=("seg-bad-ts",)),
        [_episode("ep-primary")],
        [
            _seg(
                "seg-bad-ts",
                "ep-primary",
                "Owner A reserved a table at Restaurant A in City A.",
                started_at="not-a-timestamp",
            )
        ],
        reference_time=REF,
    )
    assert hits
    assert hits[0].breakdown.recency == 0.0


def test_19_deduplicates_by_normalize_statement():
    hits = select_episodic_support(
        "Restaurant A",
        _anchor(source_segment_ids=("seg-a1",)),
        [_episode("ep-primary")],
        [
            _seg("seg-d1", "ep-primary", "Owner A likes Restaurant A in City A."),
            _seg("seg-d2", "ep-primary", "Owner A likes Restaurant A in City A!"),
        ],
        reference_time=REF,
    )
    assert len(hits) == 1


def test_20_stable_sort_score_then_ids():
    policy = EpisodicSupportPolicy(min_score=0.0, max_segments_per_episode=3)
    hits = select_episodic_support(
        "City A School A",
        _anchor(source_segment_ids=("seg-a1",)),
        [
            _episode("ep-b", ended_at="2026-01-12T10:30:00+00:00"),
            _episode("ep-a", ended_at="2026-01-11T10:30:00+00:00"),
        ],
        [
            _seg("seg-b1", "ep-b", "Owner A toured School A campus in City A."),
            _seg("seg-a1", "ep-a", "Owner A studies at School A in City A."),
            _seg("seg-a2", "ep-a", "Owner A eats at Restaurant A in City A."),
        ],
        policy=policy,
        reference_time=REF,
    )
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    for i in range(len(hits) - 1):
        left, right = hits[i], hits[i + 1]
        if left.score == right.score:
            assert (left.episode_id, left.segment_id) <= (right.episode_id, right.segment_id)


def test_21_anchor_strength_increases_score():
    low = select_episodic_support(
        "Restaurant A City A",
        _anchor(strength=0.1, source_segment_ids=("seg-x",)),
        [_episode("ep-primary")],
        [
            _seg(
                "seg-x",
                "ep-primary",
                "Owner A picked Restaurant A in City A for the reunion.",
            )
        ],
        reference_time=REF,
    )
    high = select_episodic_support(
        "Restaurant A City A",
        _anchor(strength=0.95, source_segment_ids=("seg-x",)),
        [_episode("ep-primary")],
        [
            _seg(
                "seg-x",
                "ep-primary",
                "Owner A picked Restaurant A in City A for the reunion.",
            )
        ],
        reference_time=REF,
    )
    assert high[0].score >= low[0].score


def test_22_lexical_overlap_with_query():
    hits = select_episodic_support(
        "Restaurant A dinner City A",
        _anchor(source_segment_ids=("seg-a1",)),
        [_episode("ep-primary")],
        [
            _seg(
                "seg-match",
                "ep-primary",
                "Owner A booked Restaurant A dinner in City A tonight.",
            ),
            _seg(
                "seg-miss",
                "ep-primary",
                "Owner A reviewed School A homework before class.",
            ),
        ],
        reference_time=REF,
    )
    assert hits[0].segment_id == "seg-match"
    assert "query_overlap" in hits[0].breakdown.reasons


def test_23_returns_support_hits_only_not_nl():
    hits = select_episodic_support(
        "School A",
        _anchor(),
        [_episode("ep-primary")],
        [
            _seg(
                "seg-a2",
                "ep-primary",
                "Owner A confirmed School A classes meet in City A.",
            )
        ],
        reference_time=REF,
    )
    assert all(isinstance(h, EpisodicSupportHit) for h in hits)
    assert all(h.is_supplemental for h in hits)
    assert all(h.anchor_memory_id == "mem-owner-a-001" for h in hits)


def test_24_empty_when_no_linked_episodes():
    hits = select_episodic_support(
        "School A",
        _anchor(episode_id="ep-missing", source_segment_ids=("seg-missing",)),
        [_episode("ep-other")],
        [_seg("seg-other", "ep-other", "Owner A visited Restaurant A in City A.")],
        reference_time=REF,
    )
    assert hits == ()


def test_25_empty_inputs_return_empty():
    hits = select_episodic_support(
        "",
        _anchor(),
        [],
        [],
        reference_time=REF,
    )
    assert hits == ()


def test_26_score_bounds_and_provenance():
    hits = select_episodic_support(
        "School A City A",
        _anchor(source_segment_ids=("seg-a2",)),
        [_episode("ep-primary", session_id="sess-prov")],
        [
            _seg(
                "seg-a2",
                "ep-primary",
                "Owner A confirmed School A classes meet in City A.",
                started_at="2026-01-14T09:00:00+00:00",
            )
        ],
        reference_time=REF,
    )
    assert hits
    hit = hits[0]
    assert 0.0 <= hit.score <= 1.0
    assert 0.0 <= hit.breakdown.total <= 1.0
    assert hit.session_id == "sess-prov"
    assert hit.anchor_memory_id == "mem-owner-a-001"
    assert hit.episode_id == "ep-primary"
    assert hit.segment_id == "seg-a2"
    assert hit.started_at is not None
    assert hit.is_supplemental is True


def test_27_no_database_access(monkeypatch):
    import sqlite3

    def _blocked(*_args, **_kwargs):
        raise AssertionError("database access is forbidden")

    monkeypatch.setattr(sqlite3, "connect", _blocked)
    hits = select_episodic_support(
        "Restaurant A",
        _anchor(),
        [_episode("ep-primary")],
        [
            _seg(
                "seg-a2",
                "ep-primary",
                "Owner A met friends at Restaurant A in City A.",
            )
        ],
        reference_time=REF,
    )
    assert hits


def test_28_policy_rejects_invalid_bounds():
    with pytest.raises(ValueError, match="max_episodes"):
        EpisodicSupportPolicy(max_episodes=0)
    with pytest.raises(ValueError, match="min_score"):
        EpisodicSupportPolicy(min_score=1.1)


def test_29_reference_time_must_be_timezone_aware():
    with pytest.raises(ValueError, match="timezone-aware"):
        select_episodic_support(
            "School A",
            _anchor(),
            [_episode("ep-primary")],
            [_seg("seg-a1", "ep-primary", "Owner A studies at School A.")],
            reference_time=datetime(2026, 1, 1),
        )


def test_30_omitted_reference_time_has_deterministic_zero_recency():
    hits = select_episodic_support(
        "School A",
        _anchor(),
        [_episode("ep-primary")],
        [
            _seg(
                "seg-a2",
                "ep-primary",
                "Owner A confirmed School A classes in City A today.",
            )
        ],
        EpisodicSupportPolicy(min_score=0.0),
    )
    assert hits
    assert hits[0].breakdown.recency == 0.0
