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
from core.tasks.schemas import TaskIntent, TaskRecord, TaskStatus
from core.tasks.store import TaskStore

_DEFAULT_NOTE = "Task intent recorded only; scheduling is not wired up yet."


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
                "Task scheduling is not enabled yet. "
                "Set HIKARI_ENABLE_TASK_SCHEDULER=1 to allow macOS Reminders."
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
