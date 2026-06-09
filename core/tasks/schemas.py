"""Task model types (not Brain v2 semantic memory)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4


class TaskStatus(str, Enum):
    """Lifecycle for task intents — scheduling is not implied."""

    RECORDED = "recorded"
    NOT_SCHEDULED = "not_scheduled"


@dataclass(frozen=True)
class TaskIntent:
    """Parsed task bucket from user phrasing."""

    kind: str
    raw_text: str


@dataclass
class TaskRecord:
    """Stored task intent; does not mean a reminder was scheduled."""

    task_id: str
    kind: str
    raw_text: str
    status: TaskStatus
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    note: Optional[str] = None

    @classmethod
    def from_intent(cls, intent: TaskIntent, *, note: Optional[str] = None) -> TaskRecord:
        return cls(
            task_id=uuid4().hex[:12],
            kind=intent.kind,
            raw_text=intent.raw_text,
            status=TaskStatus.NOT_SCHEDULED,
            note=note,
        )
