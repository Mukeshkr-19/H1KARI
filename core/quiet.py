"""Log noise control: quiet by default; set HIKARI_VERBOSE=1 (or HIKARI_QUIET=0) for full logs."""

import os
from typing import Any


def is_quiet() -> bool:
    if os.environ.get("HIKARI_VERBOSE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return False
    v = os.environ.get("HIKARI_QUIET", "1").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    return True


def debug(*args: Any, **kwargs: Any) -> None:
    """Print debug/status logs only when verbose mode is enabled."""
    if not is_quiet():
        print(*args, **kwargs)
