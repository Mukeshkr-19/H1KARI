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
    QUEUED = "queued"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TASK_TRANSITIONS = {
    TaskStatus.RECORDED: frozenset(
        {TaskStatus.NOT_SCHEDULED, TaskStatus.QUEUED, TaskStatus.CANCELLED}
    ),
    TaskStatus.NOT_SCHEDULED: frozenset(
        {TaskStatus.SCHEDULED, TaskStatus.SCHEDULE_FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.SCHEDULE_FAILED: frozenset(
        {TaskStatus.SCHEDULED, TaskStatus.CANCELLED}
    ),
    TaskStatus.QUEUED: frozenset(
        {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.INTERRUPTED,
            TaskStatus.VERIFYING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.INTERRUPTED: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELLED}),
    TaskStatus.VERIFYING: frozenset(
        {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.INTERRUPTED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.FAILED: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELLED}),
    TaskStatus.SCHEDULED: frozenset(),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}

TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.SCHEDULED, TaskStatus.COMPLETED, TaskStatus.CANCELLED}
)
ALLOWED_TASK_ACTORS = frozenset({"owner", "guest", "unknown", "system"})
MAX_TASK_KIND_CHARS = 64
MAX_TASK_RAW_TEXT_CHARS = 20_000
MAX_SELECTED_PATH_CHARS = 4_096


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    """Return whether a lifecycle transition is valid; repeats are idempotent."""
    return current is target or target in TASK_TRANSITIONS.get(current, frozenset())


def sanitize_task_text(value: Optional[str], *, limit: int) -> Optional[str]:
    """Remove control characters and bound persisted task output."""
    if value is None:
        return None
    cleaned = "".join(
        char
        for char in str(value)
        if char in ("\n", "\t") or (ord(char) >= 32 and ord(char) != 127)
    ).strip()
    return cleaned[:limit]


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
    actor: str = "owner"
    progress: int = 0
    checkpoint: Optional[str] = None
    attempt_count: int = 0
    last_error: Optional[str] = None
    result_summary: Optional[str] = None
    verified_at: Optional[str] = None
    completed_at: Optional[str] = None
    parent_task_id: Optional[str] = None
    selected_path: Optional[str] = None

    @classmethod
    def from_intent(
        cls,
        intent: TaskIntent,
        *,
        note: Optional[str] = None,
        context: Optional[TaskRecordContext] = None,
        due_text: Optional[str] = None,
        status: TaskStatus = TaskStatus.NOT_SCHEDULED,
    ) -> TaskRecord:
        ctx = context or TaskRecordContext()
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            task_id=uuid4().hex,
            kind=intent.kind,
            raw_text=intent.raw_text,
            status=status,
            created_at=now,
            updated_at=now,
            note=note,
            speaker_label=(ctx.speaker_label or "").strip() or "owner",
            session_id=ctx.session_id,
            source=ctx.source,
            due_text=due_text,
            actor=ctx.actor or ("guest" if ctx.is_guest else "owner"),
        )
