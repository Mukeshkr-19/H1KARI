"""Declarative memory statement detection without legacy neural imports."""

from __future__ import annotations

import re


_TASK_ACTION_PATTERNS = (
    re.compile(r"\bremind\s+me\s+to\s+", re.I),
    re.compile(r"^(?:open|close|run|start|stop|schedule)\s+\w+", re.I),
    re.compile(r"\b(?:write|draft|debug|build|send|email)\s+(?:code|my\b)", re.I),
    re.compile(r"\bschedule\s+(?:my|a|the)\b", re.I),
    re.compile(
        r"\b(?:i\s+will|i'll|need\s+to)\s+"
        r"(?:call|email|send|submit|finish|complete|do|buy|pay|book|schedule|"
        r"remind|clean|pick\s+up|drop\s+off)\b",
        re.I,
    ),
)


def is_task_or_action_statement(text: str) -> bool:
    """Imperative or scheduling phrasing — not durable personal facts."""
    raw = (text or "").strip()
    if not raw:
        return False
    return any(pat.search(raw) for pat in _TASK_ACTION_PATTERNS)


def classify_task_action_kind(text: str) -> str:
    """Coarse task bucket for deterministic non-memory replies."""
    raw = (text or "").strip().lower()
    if not raw:
        return "generic"
    if re.search(r"\bremind\s+me\b", raw):
        return "reminder"
    if re.search(r"\bschedule\b", raw):
        return "schedule"
    if re.search(r"\b(?:write|draft|debug|build)\s+(?:code|my\b)", raw):
        return "code"
    if re.match(r"^(?:open|close|run|start|stop)\s+\w+", raw):
        return "open"
    return "generic"


def _looks_like_question_text(text: str) -> bool:
    q = (text or "").strip().lower()
    if q.endswith("?"):
        return True
    return bool(
        re.match(
            r"^(who|whos|who's|what|whats|what's|where|when|whens|when's|do|does|did|is|are|am|can|could|tell me)\b",
            q,
        )
    )


def is_declarative_memory_statement(text: str) -> bool:
    """Declarative facts to store — not questions (no neural bridge)."""
    raw = (text or "").strip().lower()
    if not raw or _looks_like_question_text(raw):
        return False
    if is_task_or_action_statement(raw):
        return False
    try:
        from core.brain_v2.location_phrases import is_meta_or_deferred_location_phrase

        if is_meta_or_deferred_location_phrase(raw):
            return False
    except ImportError:
        pass
    if re.fullmatch(
        r"(?:my\s+)?(?:sister|brother|mom|mother|dad|father|gf|girlfriend|partner)s?",
        raw.rstrip(" .!"),
    ):
        return False
    patterns = (
        r"\bmy\s+name\s+is\b",
        r"\bcall\s+me\s+[A-Za-z]",
        r"\byou can call me\s+[A-Za-z]",
        r"\b(?:i\s+am|i'm)\s+[A-Za-z]",
        r"\bi\s+live\s+in\b",
        r"\bmy\s+home\s+is\b",
        r"\b(?:i\s+am|i'm|im)\s+in\b",
        r"\b(?:no\s+)?(?:it'?s|it\s+is|i'?m|im|i\s+am)\s+(?:just\s+)?(?:called\s+)?[A-Za-z]",
        r"\b(?:study|studying|student)\s+(?:at|in)\b",
        r"\bmy\s+(?:dad'?s?|father'?s?|mom'?s?|mother'?s?|sister|brother|gf|girlfriend|partner).+\b(?:name|live|lives|stud(?:y|ies|ying|ing)|is)\b",
        r"\bmy\s+sisters?\s+full\s+name\b",
        r"\bis\s+my\s+(?:sister|brother|gf|girlfriend|partner|wife|husband)\b",
        r"\bi\s+prefer\b",
        r"\bi\s+don'?t\s+like\b",
        r"\bi\s+study\s+(?:at|in)\b",
        r"\bremember\s+(?:this|that)\b",
        r"\b(?:return\s+)?flight\b",
        r"\b(?:started|reached|arrive|arrival)\b",
    )
    return any(re.search(p, raw) for p in patterns)
