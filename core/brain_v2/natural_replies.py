"""Voice-friendly recall formatting (no robotic source prefixes)."""

from __future__ import annotations

import re
from typing import Optional

_REVIEWED_PREFIX = re.compile(
    r"^from\s+reviewed\s+(?:memory|brain\s+v2\s+memories):\s*",
    re.I,
)
_SESSION_PREFIX = re.compile(
    r"^from\s+recent\s+session\s+context:\s*",
    re.I,
)


def strip_recall_source_prefix(text: str) -> str:
    """Remove legacy debug prefixes if they appear in stored or test strings."""
    raw = (text or "").strip()
    if not raw:
        return raw
    raw = _REVIEWED_PREFIX.sub("", raw)
    raw = _SESSION_PREFIX.sub("", raw)
    return raw.strip()


def format_reviewed_memory_recall(body: str, *, prefix_yes: bool = False) -> str:
    """Natural reviewed-memory answer for chat and voice."""
    stmt = strip_recall_source_prefix(body).rstrip(".")
    if not stmt:
        return ""
    lead = "Yes. " if prefix_yes else ""
    if stmt[0].islower():
        stmt = stmt[0].upper() + stmt[1:]
    return f"{lead}{stmt}."


def format_session_location_recall(place: str) -> str:
    """Natural session-location answer."""
    place = (place or "").strip().rstrip(".")
    if not place:
        return ""
    return f"You're in {place} for this session."


def format_guest_intro_reply(guest_name: str) -> str:
    name = (guest_name or "there").strip() or "there"
    return f"Hi {name}. Guest mode - I won't use the owner's personal memories."


def format_owner_reset_reply(primary_user: Optional[str] = None) -> str:
    if primary_user:
        return f"Back to you, {primary_user}."
    return "Back to owner mode."


def format_owner_pending_note() -> str:
    return "Got it - I noted that. Say 'remember this' if you want it saved right away."


def format_identity_saved(
    *,
    legal: str = "",
    preferred: str = "",
) -> str:
    """Voice-quiet acknowledgment after identity is stored."""
    preferred = (preferred or "").strip()
    legal = (legal or "").strip()
    if preferred:
        return f"Got it, {preferred}."
    if legal:
        return f"Got it, {legal}."
    return "Got it."


def format_fact_saved() -> str:
    return "Got it."


def format_session_location_ack(place: str = "") -> str:
    place = (place or "").strip().rstrip(".")
    if place:
        return f"Got it. I'll use {place} for this session."
    return "Got it. I'll use that for this session."


def format_review_queued_quiet() -> str:
    return "Got it - I noted that for review."


def format_memory_conflict_brief() -> str:
    return (
        "I already have something different saved for that. "
        "I kept your update for review."
    )


def format_guest_visit_recall(
    guest_name: str,
    *,
    relation: Optional[str] = None,
    asked_relation: Optional[str] = None,
) -> str:
    name = (guest_name or "someone").strip() or "someone"
    if asked_relation and relation and asked_relation.lower() == relation.lower():
        return f"Yes - your {relation}, {name}, visited as a guest earlier."
    if relation:
        return f"Yes - {name} visited as a guest. They said they're your {relation}."
    return f"Yes - {name} visited as a guest earlier in this household session."
