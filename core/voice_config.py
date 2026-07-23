"""Small, side-effect-free configuration helpers for spoken output."""

from __future__ import annotations

import os
import re


DEFAULT_TTS_RATE = 170
MIN_TTS_RATE = 120
MAX_TTS_RATE = 220
DEFAULT_TTS_VOICE = "alba"
_VOICE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def tts_rate() -> int:
    """Return a comfortable bounded words-per-minute rate for spoken output."""

    raw = (os.getenv("HIKARI_TTS_RATE") or "").strip()
    if not raw:
        return DEFAULT_TTS_RATE
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TTS_RATE
    return min(MAX_TTS_RATE, max(MIN_TTS_RATE, value))


def tts_voice_name() -> str:
    """Return a bounded preset voice name; never accept a path from the environment."""

    value = (os.getenv("HIKARI_TTS_VOICE") or DEFAULT_TTS_VOICE).strip()
    return value if _VOICE_NAME.fullmatch(value) else DEFAULT_TTS_VOICE
