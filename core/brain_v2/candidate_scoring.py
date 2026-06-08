"""Candidate scoring and safe duplicate detection (no deletes)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from core.brain_v2.candidate_quality import QUALITY_KEEP, QUALITY_WEAK, has_entity_hint
from core.brain_v2.schemas import MemoryCandidate

_NON_WORD = re.compile(r"[^\w\s]+", re.UNICODE)


def normalize_statement(text: str) -> str:
    """Normalized key for duplicate detection."""
    raw = re.sub(r"\s+", " ", (text or "").lower().strip())
    return _NON_WORD.sub("", raw)


def compute_rank_score(
    candidate: MemoryCandidate,
    *,
    duplicate_penalty: float = 0.0,
) -> float:
    """Higher = review sooner. Does not auto-accept."""
    conf = float(candidate.confidence or 0.5)
    sal = float(candidate.salience or 0.5)
    meta = candidate.metadata or {}
    src_boost = min(0.12, 0.04 * len(candidate.source_segment_ids or []))
    dup = float(meta.get("duplicate_count", 0) or 0)
    dup_pen = duplicate_penalty if duplicate_penalty else min(0.25, dup * 0.08)

    quality = meta.get("quality_label", QUALITY_KEEP)
    quality_boost = 0.1 if quality == QUALITY_KEEP else (-0.12 if quality == QUALITY_WEAK else -0.35)

    if meta.get("explicit_remember"):
        quality_boost += 0.12
    if meta.get("duplicate_of_existing_memory"):
        quality_boost -= 0.2

    type_boost = 0.0
    if candidate.candidate_type in (
        "identity",
        "relation",
        "preference",
        "location",
        "current_location",
        "travel",
        "decision",
        "education",
        "plan",
        "event",
    ):
        type_boost = 0.06

    entity_boost = 0.04 if has_entity_hint(candidate.statement) else 0.0
    recency = _recency_boost(candidate.created_at)

    return max(
        0.0,
        min(
            1.0,
            conf * 0.32
            + sal * 0.28
            + src_boost
            + recency
            + quality_boost
            + type_boost
            + entity_boost
            - dup_pen,
        ),
    )


def _recency_boost(created_at: Optional[str]) -> float:
    if not created_at:
        return 0.0
    try:
        ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
        if age_days <= 1:
            return 0.08
        if age_days <= 7:
            return 0.04
    except (ValueError, TypeError):
        pass
    return 0.0


def annotate_and_rank_candidates(
    candidates: List[MemoryCandidate],
    accepted_memories: Optional[List] = None,
) -> List[MemoryCandidate]:
    """Mark duplicate clusters and accepted-memory overlap; sort by rank_score."""
    if not candidates:
        return []

    accepted_memories = accepted_memories or []
    accepted_norms = {
        normalize_statement(getattr(m, "statement", "") or ""): m
        for m in accepted_memories
        if normalize_statement(getattr(m, "statement", "") or "")
    }

    buckets: Dict[str, List[MemoryCandidate]] = {}
    for cand in candidates:
        key = normalize_statement(cand.statement)
        if not key:
            key = f"__empty__:{cand.candidate_id}"
        buckets.setdefault(key, []).append(cand)

    ranked: List[MemoryCandidate] = []
    for _norm, group in buckets.items():
        group.sort(
            key=lambda c: (c.confidence, c.salience, c.created_at or ""),
            reverse=True,
        )
        primary = group[0]
        for i, cand in enumerate(group):
            meta = dict(cand.metadata or {})
            meta["normalized_statement"] = normalize_statement(cand.statement)
            meta["duplicate_count"] = len(group)
            meta["last_seen_at"] = cand.created_at
            if i == 0:
                meta["duplicate_primary"] = True
                meta.pop("duplicate_of", None)
            else:
                meta["duplicate_of"] = primary.candidate_id
                meta["duplicate_primary"] = False

            norm = normalize_statement(cand.statement)
            if norm in accepted_norms:
                existing = accepted_norms[norm]
                meta["duplicate_of_existing_memory"] = getattr(
                    existing, "memory_id", True
                )

            meta["rank_score"] = round(
                compute_rank_score(
                    cand,
                    duplicate_penalty=0.15 if i > 0 else 0.0,
                ),
                4,
            )
            ranked.append(
                MemoryCandidate(
                    candidate_id=cand.candidate_id,
                    episode_id=cand.episode_id,
                    statement=cand.statement,
                    candidate_type=cand.candidate_type,
                    confidence=cand.confidence,
                    salience=cand.salience,
                    review_status=cand.review_status,
                    source_segment_ids=list(cand.source_segment_ids),
                    created_at=cand.created_at,
                    metadata=meta,
                )
            )

    ranked.sort(key=lambda c: float((c.metadata or {}).get("rank_score", 0)), reverse=True)
    return ranked


def find_accepted_duplicate(
    statement: str,
    accepted: List,
) -> Optional[object]:
    """Return existing SourceLinkedMemory with same normalized statement."""
    norm = normalize_statement(statement)
    if not norm:
        return None
    for mem in accepted:
        existing = normalize_statement(getattr(mem, "statement", "") or "")
        if existing == norm:
            return mem
    return None
