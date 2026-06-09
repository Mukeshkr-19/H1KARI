"""Gate macOS side effects (osascript) for tests and live QA."""

from __future__ import annotations

import os

ENV_DISABLE_OSASCRIPT = "HIKARI_DISABLE_OSASCRIPT"


def osascript_disabled() -> bool:
    return os.getenv(ENV_DISABLE_OSASCRIPT, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
