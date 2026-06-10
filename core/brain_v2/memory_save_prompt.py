"""Owner memory replies and optional session-only confirmations.

Local-first personal assistant: owner facts save to Brain v2 by default.
Trip/current city uses session context only. No save-vs-session prompt.
"""

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
    return f'Got it - "{snippet}".'


def format_saved_to_memory_reply() -> str:
    from core.brain_v2.natural_replies import format_fact_saved

    return format_fact_saved()


def format_saved_to_session_reply() -> str:
    return "Okay - I'll keep that for this session."


def format_save_needs_review_reply() -> str:
    from core.brain_v2.natural_replies import format_review_queued_quiet

    return format_review_queued_quiet()


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
    """Deprecated: policy engine routes silently; never ask save-vs-session."""
    return False
