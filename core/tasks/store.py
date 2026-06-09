"""Task persistence interface (separate from Brain v2 semantic memory)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from core.tasks.schemas import TaskRecord, TaskStatus


class TaskStore(ABC):
    @abstractmethod
    def add(self, record: TaskRecord) -> TaskRecord:
        raise NotImplementedError

    @abstractmethod
    def update(self, record: TaskRecord) -> TaskRecord:
        raise NotImplementedError

    @abstractmethod
    def list_recent(
        self,
        *,
        limit: int = 20,
        speaker_label: Optional[str] = None,
        session_id: Optional[str] = None,
        include_all_scopes: bool = False,
    ) -> List[TaskRecord]:
        raise NotImplementedError

    @abstractmethod
    def find_latest_unscheduled_reminder(
        self,
        *,
        speaker_label: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        raise NotImplementedError


class InMemoryTaskStore(TaskStore):
    """Ephemeral task store for tests."""

    def __init__(self) -> None:
        self._rows: List[TaskRecord] = []

    def add(self, record: TaskRecord) -> TaskRecord:
        self._rows.append(record)
        return record

    def update(self, record: TaskRecord) -> TaskRecord:
        for index, row in enumerate(self._rows):
            if row.task_id == record.task_id:
                self._rows[index] = record
                return record
        self._rows.append(record)
        return record

    def list_recent(
        self,
        *,
        limit: int = 20,
        speaker_label: Optional[str] = None,
        session_id: Optional[str] = None,
        include_all_scopes: bool = False,
    ) -> List[TaskRecord]:
        rows = list(self._rows)
        if not include_all_scopes:
            rows = _filter_scope(rows, speaker_label=speaker_label, session_id=session_id)
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows[: max(1, int(limit))]

    def find_latest_unscheduled_reminder(
        self,
        *,
        speaker_label: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        for row in self.list_recent(
            limit=200,
            speaker_label=speaker_label,
            session_id=session_id,
        ):
            if row.kind == "reminder" and row.status in (
                TaskStatus.NOT_SCHEDULED,
                TaskStatus.RECORDED,
            ):
                return row
        return None


def _filter_scope(
    rows: List[TaskRecord],
    *,
    speaker_label: Optional[str],
    session_id: Optional[str],
) -> List[TaskRecord]:
    out: List[TaskRecord] = []
    for row in rows:
        if speaker_label and row.speaker_label != speaker_label:
            continue
        if session_id and row.session_id != session_id:
            continue
        out.append(row)
    return out
