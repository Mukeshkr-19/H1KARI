"""Record task intents without storing them as Brain v2 personal facts."""

from __future__ import annotations

from core.brain_statements import classify_task_action_kind
from core.tasks.schemas import TaskIntent, TaskRecord
from core.tasks.store import InMemoryTaskStore, TaskStore

_DEFAULT_NOTE = "Task intent recorded only; scheduling is not wired up yet."


class TaskIntentService:
    def __init__(self, store: TaskStore | None = None) -> None:
        self.store = store or InMemoryTaskStore()

    def parse_intent(self, text: str) -> TaskIntent:
        raw = (text or "").strip()
        return TaskIntent(kind=classify_task_action_kind(raw), raw_text=raw)

    def record_intent(self, text: str) -> TaskRecord:
        intent = self.parse_intent(text)
        return self.store.add(TaskRecord.from_intent(intent, note=_DEFAULT_NOTE))
