"""Owner self-disclosure auto-trust policy (no hardcoded private facts)."""

from __future__ import annotations

import re
from typing import List, Optional, Sequence

from core.brain_v2.candidate_quality import QUALITY_KEEP, QUALITY_WEAK
from core.brain_v2.schemas import MemoryCandidate

_TRUSTED_TYPES = frozenset(
    {
        "identity",
        "location",
        "birthplace",
        "education",
        "preference",
        "plan",
        "decision",
        "relation",
    }
)

_THIRD_PARTY_EDUCATION = re.compile(
    r"\bmy\s+(?:girlfriend|gf|partner|boyfriend|wife|husband|"
    r"sister|brother|dad|father|mom|mother|parents?)\b",
    re.I,
)

_EXPLICIT_REMEMBER = re.compile(
    r"\b(?:"
    r"remember\s+(?:this|that)(?:\s+(?:in|to)\s+(?:my\s+)?(?:brain|memory))?|"
    r"(?:save|store|add|put)\s+(?:this|that)\s+"
    r"(?:as\s+(?:a\s+)?memory|(?:in|to)\s+(?:my\s+)?(?:brain|memory))"
    r")\b",
    re.I,
)

_EXPLICIT_MEMORY_PREFIX = re.compile(
    r"^\s*(?:please\s+)?(?:"
    r"remember\s+(?:this|that)(?:\s+(?:in|to)\s+(?:my\s+)?(?:brain|memory))?|"
    r"(?:save|store|add|put)\s+(?:this|that)\s+"
    r"(?:as\s+(?:a\s+)?memory|(?:in|to)\s+(?:my\s+)?(?:brain|memory))"
    r")(?:\s*[:;,\-]\s*|\s+)(?P<body>.+?)\s*$",
    re.I,
)

_EXPLICIT_MEMORY_SUFFIX = re.compile(
    r"^(?P<body>.+?)(?:\s*[,;.\-]\s*|\s+)(?:please\s+)?(?:"
    r"remember\s+(?:this|that)(?:\s+(?:in|to)\s+(?:my\s+)?(?:brain|memory))?|"
    r"(?:save|store|add|put)\s+(?:this|that)\s+"
    r"(?:as\s+(?:a\s+)?memory|(?:in|to)\s+(?:my\s+)?(?:brain|memory))"
    r")\s*[.!?]*\s*$",
    re.I,
)


def is_explicit_remember_command(text: str) -> bool:
    from core.brain_statements import is_memory_rejection_statement

    if is_memory_rejection_statement(text):
        return False
    return bool(_EXPLICIT_REMEMBER.search(text or ""))


def strip_explicit_memory_command(text: str) -> str:
    """Return the owner-authored fact without a leading/trailing save command."""
    raw = (text or "").strip()
    if not raw:
        return raw
    for pattern in (_EXPLICIT_MEMORY_PREFIX, _EXPLICIT_MEMORY_SUFFIX):
        match = pattern.match(raw)
        if match:
            return match.group("body").strip()
    return raw


def _quality_eligible(meta: dict, user_text: str) -> bool:
    label = str((meta or {}).get("quality_label", ""))
    if label == QUALITY_KEEP:
        return True
    if label == QUALITY_WEAK and (
        (meta or {}).get("explicit_remember") or is_explicit_remember_command(user_text)
    ):
        return True
    return False


def is_owner_scoped_auto_trust_candidate(
    candidate: MemoryCandidate, user_text: str
) -> bool:
    """True when a candidate may be auto-accepted for the household owner."""
    ctype = candidate.candidate_type
    meta = candidate.metadata or {}

    if ctype not in _TRUSTED_TYPES:
        return False
    if not _quality_eligible(meta, user_text):
        return False

    if ctype == "relation":
        return bool(meta.get("relation") and meta.get("person"))

    if ctype == "education":
        if meta.get("relation") and meta.get("person"):
            return False
        if _THIRD_PARTY_EDUCATION.search(user_text or ""):
            return False
        return True

    if ctype in (
        "identity",
        "location",
        "birthplace",
        "preference",
        "plan",
        "decision",
    ):
        return not (meta.get("relation") and ctype != "relation")

    return False


def pick_trusted_owner_candidate(
    candidates: Sequence[MemoryCandidate], user_text: str
) -> Optional[MemoryCandidate]:
    fallback: Optional[MemoryCandidate] = None
    for cand in candidates:
        if not is_owner_scoped_auto_trust_candidate(cand, user_text):
            continue
        if cand.candidate_type == "identity" and (cand.metadata or {}).get(
            "legal_name"
        ):
            return cand
        if fallback is None:
            fallback = cand
    return fallback
