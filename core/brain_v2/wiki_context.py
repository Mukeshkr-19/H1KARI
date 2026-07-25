"""Accepted-memory-anchored wiki context selection (DB-independent).

Pure, deterministic selector for caller-supplied wiki entries. Requires an
active accepted semantic-memory anchor and returns supplemental hits only.
Never opens databases, files, network, subprocesses, or environment state.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence, Tuple

from core.brain_v2.candidate_scoring import normalize_statement
from core.brain_v2.memory_lifecycle import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_RETIRED,
    LIFECYCLE_SUPERSEDED,
)

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z']+")

_WEIGHT_LEXICAL = 0.30
_WEIGHT_ANCHOR = 0.26
_WEIGHT_QUALITY = 0.16
_WEIGHT_RECENCY = 0.14
_WEIGHT_AGREEMENT = 0.14

_SUPPLEMENTAL_REASON = "accepted_memory_anchored_wiki"
_SOURCE_ASSISTANT = frozenset(
    {
        "assistant",
        "assistant_draft",
        "assistant_generated",
        "model",
        "llm",
    }
)
_REVIEW_ACCEPTED = "accepted"
_INVALID_REVIEW = frozenset({"pending", "rejected", "unknown", ""})

_SCORE_MIN = 0.0
_SCORE_MAX = 1.0


def _clamp(value: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        return _SCORE_MIN
    return max(_SCORE_MIN, min(_SCORE_MAX, number))


@dataclass(frozen=True)
class WikiContextAnchor:
    """Active accepted semantic-memory anchor required for wiki selection."""

    memory_id: str
    statement: str
    lifecycle_status: str = LIFECYCLE_ACTIVE
    review_status: str = _REVIEW_ACCEPTED
    strength: Optional[float] = None
    subject_keys: Tuple[str, ...] = ()


@dataclass(frozen=True)
class WikiContextEntry:
    """Caller-supplied wiki row; never loaded from disk by this module."""

    entry_id: str
    text: str
    section: str = ""
    title: str = ""
    source_type: str = "owner_note"
    source_memory_ids: Tuple[str, ...] = ()
    updated_at: Optional[str] = None
    created_at: Optional[str] = None
    quality: Optional[float] = None
    subject_keys: Tuple[str, ...] = ()
    is_assistant_authored: bool = False


@dataclass(frozen=True)
class WikiContextPolicy:
    """Strict selection and ranking bounds."""

    max_entries_examined: int = 64
    max_hits: int = 5
    max_text_length: int = 240
    max_per_section: int = 2
    max_per_source: int = 3
    min_score: float = 0.20
    excluded_lifecycle_statuses: frozenset[str] = frozenset(
        {LIFECYCLE_RETIRED, LIFECYCLE_SUPERSEDED}
    )
    exclude_assistant_authored: bool = True
    require_subject_compatibility: bool = True

    def __post_init__(self) -> None:
        for name in (
            "max_entries_examined",
            "max_hits",
            "max_text_length",
            "max_per_section",
            "max_per_source",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if isinstance(self.min_score, bool) or not isinstance(
            self.min_score, (int, float)
        ):
            raise ValueError("min_score must be numeric")
        if not _SCORE_MIN <= float(self.min_score) <= _SCORE_MAX:
            raise ValueError("min_score must be in 0.0..1.0")
        if not isinstance(self.excluded_lifecycle_statuses, frozenset):
            raise ValueError("excluded_lifecycle_statuses must be a frozenset")
        if not isinstance(self.exclude_assistant_authored, bool):
            raise ValueError("exclude_assistant_authored must be a boolean")
        if not isinstance(self.require_subject_compatibility, bool):
            raise ValueError("require_subject_compatibility must be a boolean")


@dataclass(frozen=True)
class WikiContextScoreBreakdown:
    lexical: float
    anchor_relevance: float
    entry_quality: float
    recency: float
    source_agreement: float
    penalties: float
    total: float
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class WikiContextHit:
    entry_id: str
    text: str
    score: float
    breakdown: WikiContextScoreBreakdown
    anchor_memory_id: str
    source_memory_ids: Tuple[str, ...]
    source_type: str
    section: str
    title: str
    is_supplemental: bool = True
    supplemental_reason: str = _SUPPLEMENTAL_REASON
    updated_at: Optional[str] = None
    created_at: Optional[str] = None


def select_wiki_context(
    query: str,
    anchor: Optional[WikiContextAnchor],
    entries: Sequence[Any],
    policy: Optional[WikiContextPolicy] = None,
    *,
    reference_time: Optional[datetime] = None,
) -> Tuple[WikiContextHit, ...]:
    """Return ranked supplemental wiki hits for an active accepted-memory anchor."""
    if not isinstance(query, str):
        return ()
    if policy is not None and not isinstance(policy, WikiContextPolicy):
        return ()
    if isinstance(entries, (str, bytes)):
        return ()
    pol = policy or WikiContextPolicy()
    if not _anchor_is_usable(anchor, pol):
        return ()

    assert anchor is not None
    if reference_time is not None:
        if not isinstance(reference_time, datetime):
            raise ValueError("reference_time must be a datetime")
        if reference_time.tzinfo is None:
            raise ValueError("reference_time must be timezone-aware")
    # Without an injected clock, use neutral recency instead of reading wall
    # time so identical inputs always produce identical results.
    ref = reference_time

    q_tokens = _tokens(query)
    anchor_tokens = _tokens(anchor.statement)
    anchor_subjects = _normalize_keys(anchor.subject_keys)
    anchor_norm = normalize_statement(anchor.statement)
    scored: list[tuple[str, WikiContextHit]] = []

    examined = 0
    for raw in entries:
        if examined >= pol.max_entries_examined:
            break
        examined += 1
        entry = _coerce_entry(raw)
        if entry is None:
            continue
        if not _entry_eligible(entry, anchor, pol, anchor_subjects):
            continue

        norm = normalize_statement(entry.text)
        if not norm:
            continue
        if anchor_norm and norm == anchor_norm:
            continue

        breakdown = _score_entry(
            q_tokens=q_tokens,
            anchor_tokens=anchor_tokens,
            anchor=anchor,
            entry=entry,
            reference_time=ref,
        )
        if breakdown.total < pol.min_score:
            continue

        truncated = entry.text[: pol.max_text_length]
        hit = WikiContextHit(
            entry_id=entry.entry_id,
            text=truncated,
            score=breakdown.total,
            breakdown=breakdown,
            anchor_memory_id=anchor.memory_id,
            source_memory_ids=tuple(entry.source_memory_ids),
            source_type=entry.source_type,
            section=entry.section,
            title=entry.title,
            is_supplemental=True,
            supplemental_reason=_SUPPLEMENTAL_REASON,
            updated_at=entry.updated_at,
            created_at=entry.created_at,
        )
        scored.append((norm, hit))

    # Sort before dedupe/caps so results are independent of caller ordering.
    scored.sort(key=lambda item: (-item[1].score, item[1].section, item[1].entry_id))

    seen_norms: set[str] = set()
    section_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    selected: list[WikiContextHit] = []
    for norm, hit in scored:
        if norm in seen_norms:
            continue
        section_key = (hit.section or "").casefold()
        source_key = (hit.source_type or "").casefold()
        if section_counts.get(section_key, 0) >= pol.max_per_section:
            continue
        if source_counts.get(source_key, 0) >= pol.max_per_source:
            continue
        seen_norms.add(norm)
        section_counts[section_key] = section_counts.get(section_key, 0) + 1
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
        selected.append(hit)
        if len(selected) >= pol.max_hits:
            break

    return tuple(selected)


def _anchor_is_usable(
    anchor: Optional[WikiContextAnchor],
    policy: WikiContextPolicy,
) -> bool:
    if anchor is None:
        return False
    memory_id = str(anchor.memory_id or "").strip()
    statement = str(anchor.statement or "").strip()
    if not memory_id or not statement:
        return False
    lifecycle = str(anchor.lifecycle_status or "").strip().casefold()
    if not lifecycle:
        lifecycle = LIFECYCLE_ACTIVE
    if lifecycle in {s.casefold() for s in policy.excluded_lifecycle_statuses}:
        return False
    if lifecycle != LIFECYCLE_ACTIVE:
        return False
    review = str(anchor.review_status or "").strip().casefold()
    if review in _INVALID_REVIEW or review != _REVIEW_ACCEPTED:
        return False
    if anchor.strength is not None:
        if isinstance(anchor.strength, bool) or not isinstance(anchor.strength, (int, float)):
            return False
        if not math.isfinite(float(anchor.strength)):
            return False
    return True


def _normalize_keys(keys: Sequence[str]) -> frozenset[str]:
    out: set[str] = set()
    for key in keys or ():
        text = re.sub(r"\s+", " ", str(key or "").strip().casefold())
        text = re.sub(r"[^\w\s]+", "", text).strip()
        if text:
            out.add(text)
    return frozenset(out)


def _tokens(text: str) -> set[str]:
    return {t.casefold() for t in _TOKEN_RE.findall(text or "") if len(t) > 2}


def _coerce_entry(raw: Any) -> Optional[WikiContextEntry]:
    if isinstance(raw, WikiContextEntry):
        entry = raw
    elif isinstance(raw, Mapping):
        try:
            entry = WikiContextEntry(
                entry_id=str(raw.get("entry_id") or ""),
                text=str(raw.get("text") or ""),
                section=str(raw.get("section") or ""),
                title=str(raw.get("title") or ""),
                source_type=str(raw.get("source_type") or "owner_note"),
                source_memory_ids=tuple(
                    str(x) for x in (raw.get("source_memory_ids") or ()) if str(x)
                ),
                updated_at=_optional_str(raw.get("updated_at")),
                created_at=_optional_str(raw.get("created_at")),
                quality=_optional_float(raw.get("quality")),
                subject_keys=tuple(
                    str(x) for x in (raw.get("subject_keys") or ()) if str(x)
                ),
                is_assistant_authored=bool(raw.get("is_assistant_authored", False)),
            )
        except Exception:
            return None
    else:
        try:
            entry = WikiContextEntry(
                entry_id=str(getattr(raw, "entry_id", "") or ""),
                text=str(getattr(raw, "text", "") or ""),
                section=str(getattr(raw, "section", "") or ""),
                title=str(getattr(raw, "title", "") or ""),
                source_type=str(getattr(raw, "source_type", "owner_note") or "owner_note"),
                source_memory_ids=tuple(
                    str(x)
                    for x in (getattr(raw, "source_memory_ids", ()) or ())
                    if str(x)
                ),
                updated_at=_optional_str(getattr(raw, "updated_at", None)),
                created_at=_optional_str(getattr(raw, "created_at", None)),
                quality=_optional_float(getattr(raw, "quality", None)),
                subject_keys=tuple(
                    str(x)
                    for x in (getattr(raw, "subject_keys", ()) or ())
                    if str(x)
                ),
                is_assistant_authored=bool(
                    getattr(raw, "is_assistant_authored", False)
                ),
            )
        except Exception:
            return None

    if not entry.entry_id.strip() or not entry.text.strip():
        return None
    if len(entry.text) > 20000:
        return None
    return entry


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return _clamp(number)


def _entry_eligible(
    entry: WikiContextEntry,
    anchor: WikiContextAnchor,
    policy: WikiContextPolicy,
    anchor_subjects: frozenset[str],
) -> bool:
    if policy.exclude_assistant_authored:
        if entry.is_assistant_authored:
            return False
        if (entry.source_type or "").casefold() in _SOURCE_ASSISTANT:
            return False

    if policy.require_subject_compatibility and anchor_subjects:
        entry_subjects = _normalize_keys(entry.subject_keys)
        if not entry_subjects or entry_subjects.isdisjoint(anchor_subjects):
            return False
    return True


def _score_entry(
    *,
    q_tokens: set[str],
    anchor_tokens: set[str],
    anchor: WikiContextAnchor,
    entry: WikiContextEntry,
    reference_time: Optional[datetime],
) -> WikiContextScoreBreakdown:
    reasons: list[str] = []
    blob = f"{entry.title} {entry.section} {entry.text}".casefold()
    entry_tokens = _tokens(blob)

    if q_tokens:
        overlap = sum(1 for t in q_tokens if t in blob)
        lexical = _clamp(overlap / max(1, len(q_tokens)))
        if overlap:
            reasons.append("query_overlap")
    else:
        lexical = 0.30
        reasons.append("neutral_query")

    if anchor_tokens and entry_tokens:
        shared = len(anchor_tokens & entry_tokens) / max(1, len(anchor_tokens))
    else:
        shared = 0.0
    base_strength = (
        _clamp(float(anchor.strength)) if anchor.strength is not None else 0.55
    )
    anchor_relevance = _clamp(0.40 * base_strength + 0.60 * shared)
    if shared > 0:
        reasons.append("anchor_overlap")

    if entry.quality is not None:
        entry_quality = _clamp(float(entry.quality))
        reasons.append("explicit_quality")
    else:
        words = len(entry.text.split())
        entry_quality = 0.55
        if words >= 10:
            entry_quality = 0.78
            reasons.append("informative_length")
        elif words < 4:
            entry_quality = 0.28

    stamp = entry.updated_at or entry.created_at
    recency = _recency_component(stamp, reference_time)
    if recency >= 0.75:
        reasons.append("recent_entry")

    source_ids = set(entry.source_memory_ids)
    if anchor.memory_id in source_ids:
        source_agreement = 1.0
        reasons.append("source_memory_agreement")
    elif source_ids:
        source_agreement = 0.35
        reasons.append("linked_other_memories")
    else:
        source_agreement = 0.15

    penalties = 0.0
    words = len(entry.text.split())
    if words < 5 and lexical < 0.25:
        penalties += 0.14
        reasons.append("low_info_penalty")
    if entry.text.strip().endswith("?"):
        penalties += 0.10
        reasons.append("question_penalty")

    total = _clamp(
        lexical * _WEIGHT_LEXICAL
        + anchor_relevance * _WEIGHT_ANCHOR
        + entry_quality * _WEIGHT_QUALITY
        + recency * _WEIGHT_RECENCY
        + source_agreement * _WEIGHT_AGREEMENT
        - penalties
    )

    return WikiContextScoreBreakdown(
        lexical=round(lexical, 4),
        anchor_relevance=round(anchor_relevance, 4),
        entry_quality=round(entry_quality, 4),
        recency=round(recency, 4),
        source_agreement=round(source_agreement, 4),
        penalties=round(penalties, 4),
        total=round(total, 4),
        reasons=tuple(reasons),
    )


def _recency_component(raw: Optional[str], reference_time: Optional[datetime]) -> float:
    if reference_time is None:
        return 0.0
    ts = _parse_timestamp(raw)
    if ts is None:
        return 0.0
    if ts > reference_time:
        return 0.0
    days = max(0.0, (reference_time - ts).total_seconds() / 86400.0)
    if days <= 1.0:
        return 1.0
    if days <= 7.0:
        return 0.75
    if days <= 30.0:
        return 0.45
    if days <= 90.0:
        return 0.25
    return 0.10


def _parse_timestamp(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        text = str(raw).replace("Z", "+00:00")
        ts = datetime.fromisoformat(text)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except (TypeError, ValueError):
        return None
