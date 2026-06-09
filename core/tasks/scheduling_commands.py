"""Explicit user confirmation to schedule a recorded task intent."""

from __future__ import annotations

import re

_SCHEDULE_CONFIRM_PATTERNS = (
    re.compile(r"^schedule\s+that\s+reminder\b", re.I),
    re.compile(r"^yes\s*,?\s*schedule\s+it\b", re.I),
    re.compile(r"^create\s+reminder\b", re.I),
    re.compile(r"^please\s+schedule\s+that\s+reminder\b", re.I),
)


def is_task_schedule_confirmation(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    return any(pat.search(raw) for pat in _SCHEDULE_CONFIRM_PATTERNS)
