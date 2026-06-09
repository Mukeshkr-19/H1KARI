"""Task persistence interface (stub — not wired to scheduler yet)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from core.tasks.schemas import TaskRecord


class TaskStore(ABC):
    @abstractmethod
    def add(self, record: TaskRecord) -> TaskRecord:
        raise NotImplementedError

    @abstractmethod
    def list_recent(self, *, limit: int = 20) -> List[TaskRecord]:
        raise NotImplementedError


class InMemoryTaskStore(TaskStore):
    """Ephemeral task store for tests and early orchestrator wiring."""

    def __init__(self) -> None:
        self._rows: List[TaskRecord] = []

    def add(self, record: TaskRecord) -> TaskRecord:
        self._rows.append(record)
        return record

    def list_recent(self, *, limit: int = 20) -> List[TaskRecord]:
        return list(reversed(self._rows[-limit:]))
