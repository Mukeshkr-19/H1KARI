"""Ask owner whether a fact is long-term memory or session-only."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_SAVE_CONFIRM = re.compile(
    r"^(?:yes[,.]?\s+)?(?:"
    r"save\s+(?:it|that|in\s+memory|to\s+memory|for\s+later|permanently)|"
    r"long[\s-]?term(?:\s+memory)?|"
    r"remember\s+(?:it|that)|"
    r"keep\s+(?:it|that)(?:\s+forever)?|"
    r"store\s+(?:it|that)|"
    r"yes\s+remember|"
    r"yes\s+save"
    r")\b",
    re.I,
)

_SESSION_ONLY = re.compile(
    r"^(?:"
    r"session\s+only|"
    r"(?:just|only)\s+(?:this|for)\s+session|"
    r"this\s+session\s+only|"
    r"for\s+this\s+session\s+only|"
    r"don'?t\s+save|"
    r"do\s+not\s+save|"
    r"not\s+permanent|"
    r"temporary|"
    r"just\s+for\s+now"
    r")\b",
    re.I,
)


@dataclass
class PendingMemoryChoice:
    statement: str
    candidate_type: str = "fact"


def summarize_for_prompt(statement: str, *, max_len: int = 72) -> str:
    text = (statement or "").strip().rstrip(".")
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def format_memory_scope_question(statement: str) -> str:
    snippet = summarize_for_prompt(statement)
    return (
        f'Got it - "{snippet}". '
        "Should I save that in long-term memory, or keep it for this session only? "
        'Say "save in memory" or "session only".'
    )


def format_saved_to_memory_reply() -> str:
    return "Saved in long-term memory."


def format_saved_to_session_reply() -> str:
    return "Okay - I'll keep that for this session only, not in long-term memory."


def format_save_needs_review_reply() -> str:
    return (
        "I queued that for memory review because it needs a careful check. "
        "You can confirm later with brain-v2 repair, or say \"remember this\" next time."
    )


def is_save_to_memory_confirmation(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(_SAVE_CONFIRM.search(raw))


def is_session_only_confirmation(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(_SESSION_ONLY.search(raw))


def should_ask_memory_scope(
    *,
    statement: str,
    candidate_type: str,
    explicit_remember: bool,
) -> bool:
    """True when HIKARI should ask save-vs-session instead of auto-writing."""
    if explicit_remember:
        return False
    if candidate_type == "current_location":
        return False
    return bool((statement or "").strip())
