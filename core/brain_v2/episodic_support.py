"""Accepted-memory-anchored episodic support (DB-independent selection)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence, Tuple

from core.brain_v2.candidate_quality import _FILLER_EXACT, _is_question_form
from core.brain_v2.candidate_scoring import normalize_statement
from core.brain_v2.memory_lifecycle import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_RETIRED,
    LIFECYCLE_SUPERSEDED,
)

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z']+")

_WEIGHT_LEXICAL = 0.36
_WEIGHT_ANCHOR = 0.24
_WEIGHT_RECENCY = 0.20
_WEIGHT_SOURCE = 0.20

_SUPPLEMENTAL_LINKED_EPISODE = "accepted_memory_linked_episode"
_SUPPLEMENTAL_SEGMENT_OVERLAP = "anchor_source_segment_overlap"


@dataclass(frozen=True)
class EpisodicSupportAnchor:
    memory_id: str
    episode_id: str
    source_segment_ids: Tuple[str, ...]
    statement: str
    lifecycle_status: str = LIFECYCLE_ACTIVE
    strength: Optional[float] = None


@dataclass(frozen=True)
class EpisodicSupportPolicy:
    max_episodes: int = 3
    max_segments_per_episode: int = 2
    max_segment_text_length: int = 200
    min_score: float = 0.18
    user_segments_only: bool = True
    excluded_episode_ids: frozenset[str] = frozenset()
    excluded_lifecycle_statuses: frozenset[str] = frozenset(
        {LIFECYCLE_RETIRED, LIFECYCLE_SUPERSEDED}
    )

    def __post_init__(self) -> None:
        for name in (
            "max_episodes",
            "max_segments_per_episode",
            "max_segment_text_length",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if isinstance(self.min_score, bool) or not isinstance(
            self.min_score, (int, float)
        ):
            raise ValueError("min_score must be numeric")
        if not 0.0 <= float(self.min_score) <= 1.0:
            raise ValueError("min_score must be in 0.0..1.0")
        if not isinstance(self.user_segments_only, bool):
            raise ValueError("user_segments_only must be a boolean")
        if not isinstance(self.excluded_episode_ids, frozenset):
            raise ValueError("excluded_episode_ids must be a frozenset")
        if not isinstance(self.excluded_lifecycle_statuses, frozenset):
            raise ValueError("excluded_lifecycle_statuses must be a frozenset")


@dataclass(frozen=True)
class EpisodicScoreBreakdown:
    lexical: float
    anchor_strength: float
    recency: float
    source_quality: float
    penalties: float
    total: float
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class EpisodicSupportHit:
    episode_id: str
    segment_id: str
    text: str
    score: float
    breakdown: EpisodicScoreBreakdown
    anchor_memory_id: str
    is_supplemental: bool = True
    supplemental_reason: str = _SUPPLEMENTAL_LINKED_EPISODE
    started_at: Optional[str] = None
    session_id: Optional[str] = None
    speaker_label: Optional[str] = None


def select_episodic_support(
    query: str,
    anchor: Optional[EpisodicSupportAnchor],
    episodes: Sequence[Any],
    segments: Sequence[Any],
    policy: Optional[EpisodicSupportPolicy] = None,
    *,
    reference_time: Optional[datetime] = None,
) -> Tuple[EpisodicSupportHit, ...]:
    """Return ranked transcript support hits for an active accepted-memory anchor."""
    pol = policy or EpisodicSupportPolicy()
    if not _anchor_is_usable(anchor, pol):
        return ()

    assert anchor is not None
    if reference_time is not None and reference_time.tzinfo is None:
        raise ValueError("reference_time must be timezone-aware")
    ref = reference_time
    q_tokens = _query_tokens(query)
    anchor_seg_ids = set(anchor.source_segment_ids or ())
    anchor_norm = normalize_statement(anchor.statement)

    segments_by_episode = _group_segments(segments)
    linked = _linked_episodes(anchor, episodes, segments_by_episode, anchor_seg_ids, pol)
    if not linked:
        return ()

    episode_order = _episode_sort_keys(linked, episodes)
    seen_norms: set[str] = set()
    hits: list[EpisodicSupportHit] = []
    episodes_used = 0

    for episode_id in episode_order:
        if episodes_used >= pol.max_episodes:
            break
        if episode_id in pol.excluded_episode_ids:
            continue
        if episode_id not in linked:
            continue

        episode_segments = segments_by_episode.get(episode_id, ())
        candidates: list[EpisodicSupportHit] = []
        session_id = _episode_session_id(episode_id, episodes)

        for seg in episode_segments:
            if not _segment_eligible(seg, pol):
                continue
            raw_text = str(getattr(seg, "text", "") or "").strip()
            if not raw_text:
                continue
            if _is_filler(raw_text):
                continue
            if _is_question_only(raw_text):
                continue

            norm = normalize_statement(raw_text)
            if not norm or norm in seen_norms:
                continue
            if anchor_norm and norm == anchor_norm:
                continue

            breakdown = _score_segment(
                q_tokens,
                raw_text,
                anchor,
                anchor_seg_ids,
                seg,
                ref,
            )
            if breakdown.total < pol.min_score:
                continue

            seg_id = str(getattr(seg, "segment_id", "") or "")
            supplemental_reason = (
                _SUPPLEMENTAL_SEGMENT_OVERLAP
                if seg_id in anchor_seg_ids
                else _SUPPLEMENTAL_LINKED_EPISODE
            )
            truncated = raw_text[: pol.max_segment_text_length]
            candidates.append(
                EpisodicSupportHit(
                    episode_id=episode_id,
                    segment_id=seg_id,
                    text=truncated,
                    score=breakdown.total,
                    breakdown=breakdown,
                    anchor_memory_id=anchor.memory_id,
                    is_supplemental=True,
                    supplemental_reason=supplemental_reason,
                    started_at=getattr(seg, "started_at", None),
                    session_id=session_id,
                    speaker_label=getattr(seg, "speaker_label", None),
                )
            )

        candidates.sort(key=lambda h: (-h.score, h.episode_id, h.segment_id))
        picked = candidates[: pol.max_segments_per_episode]
        if not picked:
            continue

        for hit in picked:
            norm = normalize_statement(hit.text)
            if norm in seen_norms:
                continue
            seen_norms.add(norm)
            hits.append(hit)

        episodes_used += 1

    hits.sort(key=lambda h: (-h.score, h.episode_id, h.segment_id))
    return tuple(hits)


def _anchor_is_usable(
    anchor: Optional[EpisodicSupportAnchor],
    policy: EpisodicSupportPolicy,
) -> bool:
    if anchor is None:
        return False
    status = (anchor.lifecycle_status or LIFECYCLE_ACTIVE).strip().lower()
    if status in policy.excluded_lifecycle_statuses:
        return False
    return status == LIFECYCLE_ACTIVE


def _linked_episodes(
    anchor: EpisodicSupportAnchor,
    episodes: Sequence[Any],
    segments_by_episode: Mapping[str, Tuple[Any, ...]],
    anchor_seg_ids: set[str],
    policy: EpisodicSupportPolicy,
) -> set[str]:
    linked: set[str] = set()
    if anchor.episode_id:
        linked.add(anchor.episode_id)

    for episode_id, segs in segments_by_episode.items():
        for seg in segs:
            seg_id = str(getattr(seg, "segment_id", "") or "")
            if seg_id and seg_id in anchor_seg_ids:
                linked.add(episode_id)

    for ep in episodes:
        eid = str(getattr(ep, "episode_id", "") or "")
        if not eid or eid in policy.excluded_episode_ids:
            continue
        if eid == anchor.episode_id:
            linked.add(eid)
            continue
        for seg in segments_by_episode.get(eid, ()):
            seg_id = str(getattr(seg, "segment_id", "") or "")
            if seg_id in anchor_seg_ids:
                linked.add(eid)
                break

    linked -= policy.excluded_episode_ids
    return linked


def _group_segments(segments: Sequence[Any]) -> dict[str, tuple[Any, ...]]:
    buckets: dict[str, list[Any]] = {}
    for seg in segments:
        eid = str(getattr(seg, "episode_id", "") or "")
        if not eid:
            continue
        buckets.setdefault(eid, []).append(seg)
    return {k: tuple(v) for k, v in buckets.items()}


def _episode_session_id(episode_id: str, episodes: Sequence[Any]) -> Optional[str]:
    for ep in episodes:
        if str(getattr(ep, "episode_id", "") or "") == episode_id:
            sid = getattr(ep, "session_id", None)
            return str(sid) if sid else None
    return None


def _episode_sort_keys(
    linked: set[str],
    episodes: Sequence[Any],
) -> list[str]:
    recency: dict[str, float] = {eid: 0.0 for eid in linked}
    for ep in episodes:
        eid = str(getattr(ep, "episode_id", "") or "")
        if eid not in linked:
            continue
        ts = _parse_timestamp(getattr(ep, "ended_at", None) or getattr(ep, "started_at", None))
        recency[eid] = ts.timestamp() if ts else 0.0
    return sorted(linked, key=lambda eid: (-recency.get(eid, 0.0), eid))


def _segment_eligible(seg: Any, policy: EpisodicSupportPolicy) -> bool:
    is_user = getattr(seg, "is_user", True)
    if policy.user_segments_only and not is_user:
        return False
    if not is_user:
        return False
    return True


def _is_filler(text: str) -> bool:
    low = text.lower().strip().rstrip(".!?")
    return low in _FILLER_EXACT


def _is_question_only(text: str) -> bool:
    stripped = text.strip()
    low = stripped.lower().rstrip(".!?")
    if stripped.endswith("?"):
        return True
    return _is_question_form(low)


def _query_tokens(query: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(query or "") if len(t) > 2}


def _score_segment(
    q_tokens: set[str],
    text: str,
    anchor: EpisodicSupportAnchor,
    anchor_seg_ids: set[str],
    seg: Any,
    reference_time: Optional[datetime],
) -> EpisodicScoreBreakdown:
    reasons: list[str] = []
    low = text.lower()
    seg_tokens = {t.lower() for t in _TOKEN_RE.findall(low) if len(t) > 2}

    if q_tokens:
        overlap = sum(1 for t in q_tokens if t in low)
        lexical = min(1.0, overlap / max(1, len(q_tokens)))
        if overlap:
            reasons.append("query_overlap")
    else:
        lexical = 0.35
        reasons.append("neutral_query")

    anchor_text = (anchor.statement or "").lower()
    anchor_tokens = {t.lower() for t in _TOKEN_RE.findall(anchor_text) if len(t) > 2}
    if anchor_tokens and seg_tokens:
        anchor_overlap = len(anchor_tokens & seg_tokens) / max(1, len(anchor_tokens))
    else:
        anchor_overlap = 0.0

    base_strength = anchor.strength if anchor.strength is not None else 0.55
    base_strength = max(0.0, min(1.0, float(base_strength)))
    anchor_strength = max(0.0, min(1.0, 0.45 * base_strength + 0.55 * anchor_overlap))
    seg_id = str(getattr(seg, "segment_id", "") or "")
    if seg_id in anchor_seg_ids:
        anchor_strength = min(1.0, anchor_strength + 0.08)
        reasons.append("anchor_segment")

    started_at = getattr(seg, "started_at", None)
    recency = _recency_component(started_at, reference_time)
    if recency > 0.5:
        reasons.append("recent_segment")

    source_quality = 0.72 if getattr(seg, "is_user", True) else 0.0
    word_count = len(low.split())
    if word_count >= 8:
        source_quality = min(1.0, source_quality + 0.12)
        reasons.append("informative_length")
    elif word_count < 4:
        source_quality = max(0.0, source_quality - 0.25)

    penalties = 0.0
    if word_count < 5 and lexical < 0.2:
        penalties += 0.12
        reasons.append("low_info_penalty")

    total = (
        lexical * _WEIGHT_LEXICAL
        + anchor_strength * _WEIGHT_ANCHOR
        + recency * _WEIGHT_RECENCY
        + source_quality * _WEIGHT_SOURCE
        - penalties
    )
    total = max(0.0, min(1.0, total))

    return EpisodicScoreBreakdown(
        lexical=round(lexical, 4),
        anchor_strength=round(anchor_strength, 4),
        recency=round(recency, 4),
        source_quality=round(source_quality, 4),
        penalties=round(penalties, 4),
        total=round(total, 4),
        reasons=tuple(reasons),
    )


def _recency_component(
    started_at: Optional[str], reference_time: Optional[datetime]
) -> float:
    if reference_time is None:
        return 0.0
    ts = _parse_timestamp(started_at)
    if ts is None:
        return 0.0
    delta = reference_time - ts
    days = max(0.0, delta.total_seconds() / 86400.0)
    if days <= 1.0:
        return 1.0
    if days <= 7.0:
        return 0.75
    if days <= 30.0:
        return 0.45
    if days <= 90.0:
        return 0.25
    return 0.1


def _parse_timestamp(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        text = str(raw).replace("Z", "+00:00")
        ts = datetime.fromisoformat(text)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None
