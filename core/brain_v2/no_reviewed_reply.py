"""Helpful, honest Brain v2 replies when no reviewed memory exists."""

from __future__ import annotations

import re

from core.brain_v2.recall_intent import (
    INTENT_BIRTHPLACE,
    INTENT_CURRENT_LOCATION,
    INTENT_EDUCATION,
    INTENT_GENERAL_MEMORY,
    INTENT_IDENTITY_SELF,
    INTENT_LOCATION,
    INTENT_PLAN,
    INTENT_PREFERENCE,
    INTENT_PROFILE_SUMMARY,
    INTENT_TRAVEL,
    classify_recall_intent,
)

_PENDING_HINT = "Check pending candidates with `hikari.py --brain-v2-pending`."


def format_no_reviewed_memory_reply(query: str, intent: str | None = None) -> str:
    """Context-aware missing-memory reply; always includes reviewed-memory honesty cue."""
    label = intent or classify_recall_intent(query)
    q = (query or "").lower().rstrip("?!.").strip()

    if label == INTENT_IDENTITY_SELF:
        if re.search(r"\b(?:real|legal|official)\s+name\b", q):
            return (
                "I don't have a reviewed memory for your legal name yet. "
                "You can say: My real name is Owner A but call me Person B."
            )
        if re.search(r"\b(?:preferred|call)\s+name\b", q) or "call me" in q:
            return (
                "I don't have a reviewed memory for what to call you yet. "
                "You can say: You can call me Person B."
            )
        return (
            "I don't have a reviewed memory for your name yet. "
            "You can say: My name is Owner A."
        )

    if label == INTENT_EDUCATION:
        return (
            "I don't have a reviewed memory about your education yet. "
            "You can share: I study Topic A at School A."
        )

    if label == INTENT_LOCATION:
        return (
            "I don't have a reviewed memory for where you live yet. "
            "Tell me in your own words, for example: I live in City Z."
        )

    if label == INTENT_BIRTHPLACE:
        return (
            "I don't have a reviewed memory for where you were born yet. "
            "You can say: I was born in City Z."
        )

    if label == INTENT_CURRENT_LOCATION:
        return (
            "I don't have a reviewed memory for your current city this session yet. "
            'Tell me the city, for example: "I am in City Z".'
        )

    if label == INTENT_PLAN:
        return (
            "I don't have a reviewed memory for that plan yet. "
            "Future plans can be stored, for example: "
            "Remember this: I will meet Person C for lunch tomorrow."
        )

    if label == INTENT_PREFERENCE:
        return (
            "I don't have a reviewed memory for that preference yet. "
            "You can say: I prefer Topic A."
        )

    if label == INTENT_TRAVEL:
        return (
            "I don't have a reviewed memory for that travel detail yet. "
            f"{_PENDING_HINT}"
        )

    if label == INTENT_PROFILE_SUMMARY:
        return (
            "I don't have reviewed Brain v2 memories about you yet. "
            "Share a few facts in your own words and I can remember them in Brain v2."
        )

    if label == INTENT_GENERAL_MEMORY:
        return (
            "I don't have a reviewed memory for that yet. "
            f"{_PENDING_HINT}"
        )

    return "I do not have a reviewed memory for that yet."
