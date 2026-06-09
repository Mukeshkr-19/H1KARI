"""Companion state and websocket event contracts (UI layer only)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Literal, Optional, Tuple

# WebSocket event type names (stable API surface).
WS_EVENT_COMPANION_UPDATE = "companion_update"
WS_EVENT_COMPANION_PREFERENCES = "companion_preferences"

MAX_COMPANION_CAPTION_CHARS = 500

CompanionType = Literal["cat", "dog", "bird"]
PresentationOption = Literal["male", "female", "non-binary"]

ALLOWED_COMPANION_TYPES: Tuple[CompanionType, ...] = ("cat", "dog", "bird")
ALLOWED_PRESENTATIONS: Tuple[PresentationOption, ...] = (
    "male",
    "female",
    "non-binary",
)


class CompanionState(str, Enum):
    HIDDEN = "hidden"
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


CaptionRole = Literal["user", "assistant", "system"]


@dataclass(frozen=True)
class CompanionCaption:
    role: CaptionRole
    text: str
    is_final: bool
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "text": self.text,
            "is_final": self.is_final,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class CompanionPreferencesPayload:
    """UI-only preferences (companion look); never conversation text."""

    companion_type: CompanionType
    presentation: PresentationOption

    def to_dict(self) -> Dict[str, str]:
        return {
            "companion_type": self.companion_type,
            "presentation": self.presentation,
        }


def sanitize_caption_text(text: str) -> str:
    """Strip control characters and bound length for WebSocket caption payloads."""
    if not text:
        return ""
    cleaned = "".join(
        ch for ch in text if ch in ("\n", "\t") or (ord(ch) >= 32 and ord(ch) != 127)
    )
    cleaned = cleaned.strip()
    if len(cleaned) > MAX_COMPANION_CAPTION_CHARS:
        return cleaned[:MAX_COMPANION_CAPTION_CHARS]
    return cleaned


def validate_companion_type(value: str) -> CompanionType:
    if value not in ALLOWED_COMPANION_TYPES:
        raise ValueError(
            f"Invalid companion_type {value!r}; allowed: {', '.join(ALLOWED_COMPANION_TYPES)}"
        )
    return value  # type: ignore[return-value]


def validate_presentation(value: str) -> PresentationOption:
    if value not in ALLOWED_PRESENTATIONS:
        raise ValueError(
            f"Invalid presentation {value!r}; allowed: {', '.join(ALLOWED_PRESENTATIONS)}"
        )
    return value  # type: ignore[return-value]


def companion_update_payload(
    state: CompanionState,
    *,
    caption: Optional[CompanionCaption] = None,
    preferences: Optional[CompanionPreferencesPayload] = None,
    error_message: Optional[str] = None,
) -> Dict[str, Any]:
    """Build outbound ``companion_update`` websocket payload."""
    body: Dict[str, Any] = {"state": state.value}
    if caption is not None:
        safe = CompanionCaption(
            role=caption.role,
            text=sanitize_caption_text(caption.text),
            is_final=caption.is_final,
            timestamp=caption.timestamp,
        )
        body["caption"] = safe.to_dict()
    if preferences is not None:
        body["preferences"] = preferences.to_dict()
    if error_message:
        body["error"] = error_message
    return {"type": WS_EVENT_COMPANION_UPDATE, "companion": body}
