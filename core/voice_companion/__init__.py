"""Voice companion UI contract — presentation layer only, not Brain v2 memory."""

from core.voice_companion.bridge import VoiceCompanionBridge
from core.voice_companion.contract import (
    ALLOWED_COMPANION_TYPES,
    ALLOWED_PRESENTATIONS,
    MAX_COMPANION_CAPTION_CHARS,
    CompanionCaption,
    CompanionState,
    CompanionType,
    PresentationOption,
    WS_EVENT_COMPANION_UPDATE,
    WS_EVENT_COMPANION_PREFERENCES,
    sanitize_caption_text,
)
from core.voice_companion.preferences import CompanionPreferences, load_preferences, save_preferences
from core.voice_companion.session import VoiceCompanionSession
from core.voice_companion.status import is_voice_companion_enabled

__all__ = [
    "ALLOWED_COMPANION_TYPES",
    "ALLOWED_PRESENTATIONS",
    "MAX_COMPANION_CAPTION_CHARS",
    "sanitize_caption_text",
    "CompanionCaption",
    "CompanionPreferences",
    "CompanionState",
    "CompanionType",
    "PresentationOption",
    "VoiceCompanionBridge",
    "VoiceCompanionSession",
    "WS_EVENT_COMPANION_UPDATE",
    "WS_EVENT_COMPANION_PREFERENCES",
    "is_voice_companion_enabled",
    "load_preferences",
    "save_preferences",
]
