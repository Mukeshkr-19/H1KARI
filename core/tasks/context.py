"""Speaker/session metadata for scoped task intents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TaskRecordContext:
    speaker_label: str = "owner"
    session_id: Optional[str] = None
    source: str = "text"
    is_guest: bool = False
    actor: Optional[str] = None
