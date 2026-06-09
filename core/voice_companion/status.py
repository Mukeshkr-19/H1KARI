"""Feature gate for the voice companion presentation layer."""

from __future__ import annotations

import os

_ENV_VOICE_COMPANION = "HIKARI_VOICE_COMPANION"


def is_voice_companion_enabled() -> bool:
    """Return True when the voice companion layer is explicitly enabled."""
    return os.environ.get(_ENV_VOICE_COMPANION, "").strip() in {"1", "true", "yes", "on"}
