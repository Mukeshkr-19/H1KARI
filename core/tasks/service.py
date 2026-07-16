"""Record and optionally schedule task intents (not Brain v2 memory)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Tuple

from core.brain_statements import classify_task_action_kind
from core.tasks.context import TaskRecordContext
from core.tasks.factory import open_task_store
from core.tasks.scheduler import (
    MacOSReminderScheduler,
    SchedulerResult,
    _sanitize_error,
    extract_due_text,
    task_scheduler_enabled,
)
from core.tasks.schemas import ALLOWED_TASK_ACTORS, TaskIntent, TaskRecord, TaskStatus
from core.tasks.store import TaskStore

_DEFAULT_NOTE = "Task intent recorded only; scheduling is not wired up yet."


def _trusted_scope(context: TaskRecordContext) -> tuple[str, str]:
    if not isinstance(context, TaskRecordContext):
        raise TypeError("trusted task context is required")
    speaker_label = (context.speaker_label or "").strip()
    if not speaker_label:
        raise ValueError("task speaker identity is required")
    actor = context.actor or ("guest" if context.is_guest else "owner")
    if actor not in ALLOWED_TASK_ACTORS:
        raise ValueError("task actor is invalid")
    return actor, speaker_label


class TaskIntentService:
    def __init__(
        self,
        store: TaskStore | None = None,
        scheduler: MacOSReminderScheduler | None = None,
    ) -> None:
        self.store = store if store is not None else open_task_store()
        self.scheduler = scheduler or MacOSReminderScheduler()

    def parse_intent(self, text: str) -> TaskIntent:
        raw = (text or "").strip()
        return TaskIntent(kind=classify_task_action_kind(raw), raw_text=raw)

    def record_intent(
        self,
        text: str,
        *,
        context: Optional[TaskRecordContext] = None,
    ) -> TaskRecord:
        intent = self.parse_intent(text)
        due = extract_due_text(text) if intent.kind == "reminder" else None
        return self.store.add(
            TaskRecord.from_intent(
                intent,
                note=_DEFAULT_NOTE,
                context=context,
                due_text=due,
            )
        )

    def queue_task(
        self,
        text: str,
        *,
        kind: Optional[str] = None,
        context: TaskRecordContext,
    ) -> TaskRecord:
        """Create a durable Phase 1 task without changing reminder semantics."""
        _trusted_scope(context)
        intent = self.parse_intent(text)
        if kind:
            intent = TaskIntent(kind=kind.strip(), raw_text=intent.raw_text)
        return self.store.add(
            TaskRecord.from_intent(
                intent,
                context=context,
                status=TaskStatus.QUEUED,
            )
        )

    def queue_document_root(
        self,
        selected_path: str,
        *,
        context: TaskRecordContext,
    ) -> TaskRecord:
        """Queue a document root while retaining only the selected path."""
        _trusted_scope(context)
        record = TaskRecord.from_intent(
            TaskIntent(kind="document_read", raw_text="Read selected document"),
            context=context,
            status=TaskStatus.QUEUED,
        )
        record.selected_path = selected_path
        return self.store.add(record)

    def queue_follow_up(
        self,
        root_task_id: str,
        question: str,
        *,
        context: TaskRecordContext,
    ) -> Optional[TaskRecord]:
        """Queue a scoped child without copying the root path or document bytes."""
        actor, speaker_label = _trusted_scope(context)
        root = self.store.get(
            root_task_id,
            actor=actor,
            speaker_label=speaker_label,
        )
        if (
            root is None
            or root.parent_task_id is not None
            or root.kind != "document_read"
            or root.status is not TaskStatus.COMPLETED
        ):
            return None
        record = TaskRecord.from_intent(
            TaskIntent(kind="document_follow_up", raw_text=question),
            context=context,
            status=TaskStatus.QUEUED,
        )
        record.parent_task_id = root.task_id
        return self.store.add(record)

    def list_children(
        self,
        root_task_id: str,
        *,
        context: TaskRecordContext,
    ) -> list[TaskRecord]:
        actor, speaker_label = _trusted_scope(context)
        root = self.store.get(
            root_task_id,
            actor=actor,
            speaker_label=speaker_label,
        )
        if root is None or root.parent_task_id is not None:
            return []
        return self.store.list_children(
            root_task_id,
            actor=actor,
            speaker_label=speaker_label,
        )

    def get_task(
        self,
        task_id: str,
        *,
        context: TaskRecordContext,
    ) -> Optional[TaskRecord]:
        actor, speaker_label = _trusted_scope(context)
        return self.store.get(
            task_id,
            actor=actor,
            speaker_label=speaker_label,
        )

    def recover_incomplete(self, *, context: TaskRecordContext) -> int:
        """Deliberately recover this actor's unfinished tasks at app startup."""
        actor, speaker_label = _trusted_scope(context)
        return self.store.recover_incomplete(
            actor=actor,
            speaker_label=speaker_label,
        )

    def start_task(
        self,
        task_id: str,
        *,
        context: TaskRecordContext,
        checkpoint: str = "running",
    ) -> Optional[TaskRecord]:
        actor, speaker_label = _trusted_scope(context)
        return self.store.transition(
            task_id,
            expected_status=TaskStatus.QUEUED,
            new_status=TaskStatus.RUNNING,
            checkpoint=checkpoint,
            increment_attempt=True,
            actor=actor,
            speaker_label=speaker_label,
        )

    def update_progress(
        self,
        task_id: str,
        progress: int,
        *,
        context: TaskRecordContext,
        checkpoint: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        actor, speaker_label = _trusted_scope(context)
        record = self.store.get(
            task_id,
            actor=actor,
            speaker_label=speaker_label,
        )
        if record is None or record.status not in {
            TaskStatus.RUNNING,
            TaskStatus.VERIFYING,
        }:
            return None
        if progress < record.progress:
            return None
        return self.store.transition(
            task_id,
            expected_status=record.status,
            new_status=record.status,
            progress=progress,
            checkpoint=checkpoint,
            expected_updated_at=record.updated_at,
            actor=actor,
            speaker_label=speaker_label,
        )

    def interrupt_task(
        self,
        task_id: str,
        *,
        context: TaskRecordContext,
        checkpoint: str = "interrupted",
    ) -> Optional[TaskRecord]:
        actor, speaker_label = _trusted_scope(context)
        record = self.store.get(
            task_id,
            actor=actor,
            speaker_label=speaker_label,
        )
        if record is None or record.status not in {
            TaskStatus.RUNNING,
            TaskStatus.VERIFYING,
        }:
            return None
        return self.store.transition(
            task_id,
            expected_status=record.status,
            new_status=TaskStatus.INTERRUPTED,
            checkpoint=checkpoint,
            actor=actor,
            speaker_label=speaker_label,
        )

    def begin_verification(
        self,
        task_id: str,
        *,
        context: TaskRecordContext,
        checkpoint: str = "verifying",
    ) -> Optional[TaskRecord]:
        actor, speaker_label = _trusted_scope(context)
        return self.store.transition(
            task_id,
            expected_status=TaskStatus.RUNNING,
            new_status=TaskStatus.VERIFYING,
            checkpoint=checkpoint,
            actor=actor,
            speaker_label=speaker_label,
        )

    def complete_task(
        self,
        task_id: str,
        *,
        context: TaskRecordContext,
        result_summary: str = "",
        checkpoint: str = "completed",
    ) -> Optional[TaskRecord]:
        actor, speaker_label = _trusted_scope(context)
        now = datetime.now(timezone.utc).isoformat()
        return self.store.transition(
            task_id,
            expected_status=TaskStatus.VERIFYING,
            new_status=TaskStatus.COMPLETED,
            progress=100,
            checkpoint=checkpoint,
            result_summary=result_summary,
            verified_at=now,
            completed_at=now,
            actor=actor,
            speaker_label=speaker_label,
        )

    def fail_task(
        self,
        task_id: str,
        error: str,
        *,
        context: TaskRecordContext,
        checkpoint: str = "failed",
    ) -> Optional[TaskRecord]:
        actor, speaker_label = _trusted_scope(context)
        record = self.store.get(
            task_id,
            actor=actor,
            speaker_label=speaker_label,
        )
        if record is None or record.status not in {
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.VERIFYING,
        }:
            return None
        return self.store.transition(
            task_id,
            expected_status=record.status,
            new_status=TaskStatus.FAILED,
            checkpoint=checkpoint,
            last_error=error,
            actor=actor,
            speaker_label=speaker_label,
        )

    def retry_task(
        self,
        task_id: str,
        *,
        context: TaskRecordContext,
    ) -> Optional[TaskRecord]:
        actor, speaker_label = _trusted_scope(context)
        record = self.store.get(
            task_id,
            actor=actor,
            speaker_label=speaker_label,
        )
        if record is None or record.status not in {
            TaskStatus.FAILED,
            TaskStatus.INTERRUPTED,
        }:
            return None
        return self.store.transition(
            task_id,
            expected_status=record.status,
            new_status=TaskStatus.QUEUED,
            checkpoint="queued_for_retry",
            reset_lifecycle=True,
            actor=actor,
            speaker_label=speaker_label,
        )

    def cancel_task(
        self,
        task_id: str,
        *,
        context: TaskRecordContext,
    ) -> Optional[TaskRecord]:
        actor, speaker_label = _trusted_scope(context)
        record = self.store.get(
            task_id,
            actor=actor,
            speaker_label=speaker_label,
        )
        if record is None:
            return None
        if record.status is TaskStatus.CANCELLED:
            return record
        if record.status in {TaskStatus.COMPLETED, TaskStatus.SCHEDULED}:
            return None
        return self.store.transition(
            task_id,
            expected_status=record.status,
            new_status=TaskStatus.CANCELLED,
            checkpoint="cancelled",
            actor=actor,
            speaker_label=speaker_label,
        )

    def schedule_latest_reminder(
        self,
        *,
        context: Optional[TaskRecordContext] = None,
    ) -> Tuple[Optional[TaskRecord], str]:
        ctx = context or TaskRecordContext()
        task = self.store.find_latest_unscheduled_reminder(
            speaker_label=ctx.speaker_label,
            session_id=ctx.session_id,
        )
        if not task:
            return None, (
                "I do not have a recent unscheduled reminder task in this session. "
                "Say something like: remind me to call Person C tomorrow."
            )
        if not task_scheduler_enabled():
            return task, (
                "Task scheduling is not enabled yet; it is disabled until its policy adapter is approved. "
                "Create the reminder directly in macOS Reminders for now."
            )

        title = task.raw_text
        body = f"HIKARI task intent ({task.task_id})"
        if task.due_text:
            body = f"{body}; due hint: {task.due_text}"

        result = self.scheduler.schedule_reminder(title=title, body=body)
        now = datetime.now(timezone.utc).isoformat()
        task.scheduler_backend = result.backend
        if result.ok:
            task.status = TaskStatus.SCHEDULED
            task.scheduled_at = now
            task.scheduler_result = "scheduled"
            task.note = "Scheduled in macOS Reminders; still not Brain v2 memory."
            self.store.update(task)
            return task, (
                "I created a macOS Reminders item for your task. "
                "It is still separate from Brain v2 memory."
            )

        safe_error = _sanitize_error(
            result.error or "",
            redactions=(task.raw_text, body),
        )
        task.status = TaskStatus.SCHEDULE_FAILED
        task.scheduler_result = safe_error
        task.note = "Scheduler failed; task remains outside Brain v2 memory."
        self.store.update(task)
        return task, (
            "I could not create the macOS reminder. "
            f"Scheduler error: {safe_error}."
        )
