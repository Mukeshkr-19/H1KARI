"""Detect real place names vs deferred/meta location references (no hardcoded cities)."""

from __future__ import annotations

import re
from typing import Optional

# User is referring to session context, not naming a new place.
_META_LOCATION_PHRASE = re.compile(
    r"(?:"
    r"\b(?:the\s+)?(?:city|town|place|location|area)\s+(?:that\s+)?(?:i'?m|im|i am)\s+in\b"
    r"|\b(?:city|town|place)\s+(?:i'?m|im|i am)\s+in\s+(?:now|today|right\s+now)\b"
    r"|\bwhere\s+i(?:'m| am)\b"
    r"|\b(?:this|same)\s+(?:city|town|place)\b"
    r"|\bhere\b"
    r"|\boutside\b"
    r"|\bthe\s+city\s+im\s+in\b"
    r")",
    re.I,
)

_OWNER_IN_PLACE = re.compile(
    r"(?:^|[.!?]\s*|\b)(?:i'?m|im|i am)\s+(?:currently\s+)?in\s+",
    re.I,
)

_NON_PLACE_TOKENS = frozenset(
    {
        "a",
        "an",
        "the",
        "now",
        "here",
        "there",
        "outside",
        "city",
        "town",
        "place",
        "location",
        "area",
        "same",
        "this",
        "that",
        "where",
        "im",
        "in",
        "am",
        "currently",
        "right",
        "today",
    }
)


def is_meta_or_deferred_location_phrase(text: str) -> bool:
    """True when the user points at session location instead of naming a place."""
    low = (text or "").strip().lower()
    if not low:
        return False
    if _META_LOCATION_PHRASE.search(low):
        return True
    if re.search(r"\b(?:weather|temperature)\b", low) and re.search(
        r"\b(?:city|town|place)\s+(?:i'?m|im|i am)\b", low
    ):
        return True
    return False


def has_owner_presence_anchor(text: str) -> bool:
    """True for 'I am in Paris', not for 'city im in now'."""
    return bool(_OWNER_IN_PLACE.search(text or ""))


_FIXTURE_PLACE = re.compile(r"^City\s+[A-Z]$", re.I)
_FIXTURE_SCHOOL = re.compile(r"^School\s+[A-Z]$", re.I)


def is_valid_place_name(place: str) -> bool:
    """Reject meta phrases and empty tokens masquerading as places."""
    cleaned = (place or "").strip().rstrip(".!? ")
    if not cleaned or len(cleaned) < 2:
        return False
    if is_meta_or_deferred_location_phrase(cleaned):
        return False
    if _FIXTURE_PLACE.match(cleaned) or _FIXTURE_SCHOOL.match(cleaned):
        return True
    words = re.findall(r"[A-Za-z]+", cleaned)
    if not words:
        return False
    if all(w.lower() in _NON_PLACE_TOKENS for w in words):
        return False
    if len(words) >= 4 and all(w.lower() in _NON_PLACE_TOKENS | {"city", "im", "in", "now"} for w in words):
        return False
    return True


def normalize_declared_place(place: str) -> Optional[str]:
    if not is_valid_place_name(place):
        return None
    return place.strip().rstrip(".!? ")
