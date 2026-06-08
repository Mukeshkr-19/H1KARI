"""Detect whether user text is a real system/mac command vs casual chat."""

from __future__ import annotations

import re
from typing import Optional

# Nickname / affection — not FaceTime contacts
CASUAL_CALL_ME_RE = re.compile(
    r"^call\s+me\s+(?:baby|babe|bro|dude|man|sweetie|honey|dear|boss|king|queen)\b",
    re.IGNORECASE,
)

PHONE_CALL_RE = re.compile(
    r"^(?:please\s+)?call\s+(?!me\b)(?P<target>.+)$",
    re.IGNORECASE,
)

OPEN_APP_RE = re.compile(
    r"^(?:please\s+)?(?:open|launch)\s+(?:the\s+)?(?P<app>[a-z0-9][\w\s\-]{0,40})$",
    re.IGNORECASE,
)

VOLUME_RE = re.compile(
    r"^(?:please\s+)?(?:set\s+)?volume\s+to\s+\d{1,3}\s*%?$",
    re.IGNORECASE,
)

LOCK_SCREEN_RE = re.compile(
    r"^(?:please\s+)?(?:lock\s+screen|lock\s+mac|lock\s+my\s+mac)\s*\.?$",
    re.IGNORECASE,
)

SYSTEM_STATUS_RE = re.compile(
    r"^(?:please\s+)?(?:show\s+)?system\s+status\s*\.?$",
    re.IGNORECASE,
)

_CASUAL_CALL_TARGETS = frozenset(
    {
        "baby",
        "babe",
        "bro",
        "dude",
        "man",
        "sweetie",
        "honey",
        "dear",
        "boss",
    }
)


def is_casual_call_me(text: str) -> bool:
    return bool(CASUAL_CALL_ME_RE.match((text or "").strip()))


def is_phone_call_command(text: str) -> bool:
    """True for 'call mom', not for 'call me baby'."""
    raw = (text or "").strip()
    if not raw or is_casual_call_me(raw):
        return False
    match = PHONE_CALL_RE.match(raw)
    if not match:
        return False
    target = match.group("target").strip().lower()
    if not target or target in _CASUAL_CALL_TARGETS:
        return False
    return True


def phone_call_target(text: str) -> Optional[str]:
    match = PHONE_CALL_RE.match((text or "").strip())
    if not match or not is_phone_call_command(text):
        return None
    return match.group("target").strip()


def is_explicit_system_command(text: str) -> bool:
    """High-confidence mac/system intents only."""
    raw = (text or "").strip()
    if not raw:
        return False
    if is_phone_call_command(raw):
        return True
    if OPEN_APP_RE.match(raw):
        return True
    if VOLUME_RE.match(raw):
        return True
    if LOCK_SCREEN_RE.match(raw):
        return True
    if SYSTEM_STATUS_RE.match(raw):
        return True
    lowered = raw.lower()
    if lowered.startswith(("open ", "launch ")) and len(lowered.split()) <= 6:
        return True
    if lowered in ("lock screen", "lock mac", "show system status", "system status"):
        return True
    return False


def system_agent_confidence(text: str) -> float:
    """Score for routing to SystemAgent — low for casual chat."""
    raw = (text or "").strip()
    if not raw:
        return 0.0
    if is_casual_call_me(raw):
        return 0.05
    if is_explicit_system_command(raw):
        return 0.92
    lowered = raw.lower()
    # Weak hints only — do not auto-run mac actions
    if lowered.startswith(("open ", "launch ")):
        return 0.35
    if "call " in lowered and not lowered.startswith("call me"):
        return 0.25
    if any(
        w in lowered
        for w in ("lock screen", "lock mac", "volume to", "system status", "empty trash")
    ):
        return 0.4
    return 0.1
