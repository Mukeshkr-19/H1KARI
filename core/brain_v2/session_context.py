"""Process-wide session hooks so tools (weather, etc.) read Brain v2 working memory."""

from __future__ import annotations

from typing import Callable, Optional

_session_place_provider: Optional[Callable[[], Optional[str]]] = None
_session_guest_guard: Optional[Callable[[], bool]] = None


def register_session_place_provider(
    provider: Optional[Callable[[], Optional[str]]],
    *,
    guest_is_active: Optional[Callable[[], bool]] = None,
) -> None:
    global _session_place_provider, _session_guest_guard
    _session_place_provider = provider
    _session_guest_guard = guest_is_active


def get_session_current_place() -> Optional[str]:
    if _session_guest_guard and _session_guest_guard():
        return None
    if _session_place_provider is None:
        return None
    try:
        place = _session_place_provider()
    except Exception:
        return None
    if not place or not str(place).strip():
        return None
    return str(place).strip()
