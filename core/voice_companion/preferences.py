"""Local UI preferences for voice companion (not conversation storage)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.runtime_paths import hikari_home
from core.voice_companion.contract import (
    CompanionType,
    PresentationOption,
    validate_companion_type,
    validate_presentation,
)

DEFAULT_COMPANION_TYPE: CompanionType = "cat"
DEFAULT_PRESENTATION: PresentationOption = "non-binary"


def _prefs_path() -> Path:
    explicit = os.environ.get("HIKARI_COMPANION_PREFS_PATH")
    if explicit:
        return Path(explicit).expanduser()
    return hikari_home() / "companion_ui.json"


@dataclass(frozen=True)
class CompanionPreferences:
    companion_type: CompanionType
    presentation: PresentationOption

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "companion_type", validate_companion_type(str(self.companion_type))
        )
        object.__setattr__(
            self, "presentation", validate_presentation(str(self.presentation))
        )

    def to_dict(self) -> dict:
        return {
            "companion_type": self.companion_type,
            "presentation": self.presentation,
        }


def load_preferences() -> CompanionPreferences:
    path = _prefs_path()
    if not path.is_file():
        return CompanionPreferences(
            companion_type=DEFAULT_COMPANION_TYPE,
            presentation=DEFAULT_PRESENTATION,
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return CompanionPreferences(
            companion_type=validate_companion_type(str(data.get("companion_type", DEFAULT_COMPANION_TYPE))),
            presentation=validate_presentation(str(data.get("presentation", DEFAULT_PRESENTATION))),
        )
    except (OSError, json.JSONDecodeError, ValueError):
        return CompanionPreferences(
            companion_type=DEFAULT_COMPANION_TYPE,
            presentation=DEFAULT_PRESENTATION,
        )


def save_preferences(prefs: CompanionPreferences) -> None:
    """Validate and persist UI preferences; raises ValueError before any write."""
    companion_type = validate_companion_type(str(prefs.companion_type))
    presentation = validate_presentation(str(prefs.presentation))
    payload = {
        "companion_type": companion_type,
        "presentation": presentation,
    }
    path = _prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
