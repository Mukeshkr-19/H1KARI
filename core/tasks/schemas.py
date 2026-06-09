"""Task model types (not Brain v2 semantic memory)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from core.tasks.context import TaskRecordContext


class TaskStatus(str, Enum):
    """Lifecycle for task intents — scheduling is not implied until SCHEDULED."""

    RECORDED = "recorded"
    NOT_SCHEDULED = "not_scheduled"
    SCHEDULED = "scheduled"
    SCHEDULE_FAILED = "schedule_failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskIntent:
    """Parsed task bucket from user phrasing."""

    kind: str
    raw_text: str


@dataclass
class TaskRecord:
    """Stored task intent; separate from Brain v2 semantic memory."""

    task_id: str
    kind: str
    raw_text: str
    status: TaskStatus
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: Optional[str] = None
    note: Optional[str] = None
    speaker_label: str = "owner"
    session_id: Optional[str] = None
    source: str = "text"
    scheduled_at: Optional[str] = None
    due_text: Optional[str] = None
    scheduler_backend: Optional[str] = None
    scheduler_result: Optional[str] = None

    @classmethod
    def from_intent(
        cls,
        intent: TaskIntent,
        *,
        note: Optional[str] = None,
        context: Optional[TaskRecordContext] = None,
        due_text: Optional[str] = None,
    ) -> TaskRecord:
        ctx = context or TaskRecordContext()
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            task_id=uuid4().hex[:12],
            kind=intent.kind,
            raw_text=intent.raw_text,
            status=TaskStatus.NOT_SCHEDULED,
            created_at=now,
            updated_at=now,
            note=note,
            speaker_label=ctx.speaker_label,
            session_id=ctx.session_id,
            source=ctx.source,
            due_text=due_text,
        )
