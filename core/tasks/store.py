"""Task persistence interface (separate from Brain v2 semantic memory)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from typing import List, Optional

from core.tasks.schemas import (
    ALLOWED_TASK_ACTORS,
    MAX_TASK_KIND_CHARS,
    MAX_TASK_RAW_TEXT_CHARS,
    MAX_SELECTED_PATH_CHARS,
    TERMINAL_TASK_STATUSES,
    TaskRecord,
    TaskStatus,
    can_transition,
    sanitize_task_text,
)

_LEGACY_UPDATE_TRANSITIONS = frozenset(
    {
        (TaskStatus.NOT_SCHEDULED, TaskStatus.SCHEDULED),
        (TaskStatus.NOT_SCHEDULED, TaskStatus.SCHEDULE_FAILED),
        (TaskStatus.SCHEDULE_FAILED, TaskStatus.SCHEDULED),
    }
)


def _validate_legacy_update(current: TaskRecord, replacement: TaskRecord) -> None:
    """Keep raw updates limited to the pre-Phase-1 reminder compatibility path."""
    if current.actor != replacement.actor or current.speaker_label != replacement.speaker_label:
        raise ValueError("task scope is immutable")
    if (
        current.parent_task_id != replacement.parent_task_id
        or current.selected_path != replacement.selected_path
    ):
        raise ValueError("task context is immutable")
    if current.status in TERMINAL_TASK_STATUSES:
        raise ValueError("terminal task is immutable")
    if current.status is not replacement.status and (
        current.status,
        replacement.status,
    ) not in _LEGACY_UPDATE_TRANSITIONS:
        raise ValueError("task lifecycle updates require transition")


class TaskStore(ABC):
    @abstractmethod
    def add(self, record: TaskRecord) -> TaskRecord:
        raise NotImplementedError

    @abstractmethod
    def update(self, record: TaskRecord) -> TaskRecord:
        raise NotImplementedError

    @abstractmethod
    def get(
        self,
        task_id: str,
        *,
        actor: str,
        speaker_label: str,
    ) -> Optional[TaskRecord]:
        raise NotImplementedError

    @abstractmethod
    def get_legacy_unscoped(self, task_id: str) -> Optional[TaskRecord]:
        """Compatibility read for pre-Phase-1 reminder records only."""
        raise NotImplementedError

    @abstractmethod
    def recover_incomplete(
        self,
        *,
        actor: str,
        speaker_label: str,
    ) -> int:
        raise NotImplementedError

    @abstractmethod
    def transition(
        self,
        task_id: str,
        *,
        expected_status: TaskStatus,
        new_status: TaskStatus,
        progress: Optional[int] = None,
        checkpoint: Optional[str] = None,
        last_error: Optional[str] = None,
        result_summary: Optional[str] = None,
        verified_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        increment_attempt: bool = False,
        reset_lifecycle: bool = False,
        expected_updated_at: Optional[str] = None,
        actor: str,
        speaker_label: str,
    ) -> Optional[TaskRecord]:
        raise NotImplementedError

    @abstractmethod
    def list_recent(
        self,
        *,
        limit: int = 20,
        speaker_label: Optional[str] = None,
        session_id: Optional[str] = None,
        actor: Optional[str] = None,
        include_all_scopes: bool = False,
    ) -> List[TaskRecord]:
        raise NotImplementedError

    @abstractmethod
    def list_children(
        self,
        parent_task_id: str,
        *,
        actor: str,
        speaker_label: str,
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
        _validate_record(record)
        if record.parent_task_id is not None:
            parent = self.get(
                record.parent_task_id,
                actor=record.actor,
                speaker_label=record.speaker_label,
            )
            if parent is None or parent.parent_task_id is not None:
                raise ValueError("task parent is invalid")
        self._rows.append(deepcopy(record))
        return record

    def update(self, record: TaskRecord) -> TaskRecord:
        for index, row in enumerate(self._rows):
            if row.task_id == record.task_id:
                _validate_record(record)
                _validate_legacy_update(row, record)
                self._rows[index] = deepcopy(record)
                return record
        raise KeyError(f"unknown task: {record.task_id}")

    def get(
        self,
        task_id: str,
        *,
        actor: str,
        speaker_label: str,
    ) -> Optional[TaskRecord]:
        _require_scope(actor, speaker_label)
        record = next(
            (
                row
                for row in self._rows
                if row.task_id == task_id
                and row.actor == actor
                and row.speaker_label == speaker_label
            ),
            None,
        )
        return deepcopy(record) if record is not None else None

    def get_legacy_unscoped(self, task_id: str) -> Optional[TaskRecord]:
        record = next(
            (
                row
                for row in self._rows
                if row.task_id == task_id and row.kind == "reminder"
                and row.status in {
                    TaskStatus.RECORDED,
                    TaskStatus.NOT_SCHEDULED,
                    TaskStatus.SCHEDULED,
                    TaskStatus.SCHEDULE_FAILED,
                }
            ),
            None,
        )
        return deepcopy(record) if record is not None else None

    def recover_incomplete(self, *, actor: str, speaker_label: str) -> int:
        _require_scope(actor, speaker_label)
        recovered = 0
        for record in self._rows:
            if (
                record.actor == actor
                and record.speaker_label == speaker_label
                and record.status in {TaskStatus.RUNNING, TaskStatus.VERIFYING}
            ):
                record.status = TaskStatus.INTERRUPTED
                record.checkpoint = record.checkpoint or "recovered_after_restart"
                recovered += 1
        return recovered

    def transition(
        self,
        task_id: str,
        *,
        expected_status: TaskStatus,
        new_status: TaskStatus,
        progress: Optional[int] = None,
        checkpoint: Optional[str] = None,
        last_error: Optional[str] = None,
        result_summary: Optional[str] = None,
        verified_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        increment_attempt: bool = False,
        reset_lifecycle: bool = False,
        expected_updated_at: Optional[str] = None,
        actor: str,
        speaker_label: str,
    ) -> Optional[TaskRecord]:
        _require_scope(actor, speaker_label)
        if not can_transition(expected_status, new_status):
            raise ValueError(
                f"invalid task transition: {expected_status.value} -> {new_status.value}"
            )
        record = self.get(
            task_id,
            actor=actor,
            speaker_label=speaker_label,
        )
        if record is None or record.status is not expected_status:
            return None
        if expected_updated_at is not None and record.updated_at != expected_updated_at:
            return None
        if expected_status is new_status and expected_status in TERMINAL_TASK_STATUSES:
            return record
        if reset_lifecycle:
            record.progress = 0
            record.last_error = None
            record.result_summary = None
            record.verified_at = None
            record.completed_at = None
        if progress is not None:
            _validate_progress(progress)
            record.progress = progress
        record.status = new_status
        if checkpoint is not None:
            record.checkpoint = checkpoint
        if last_error is not None:
            record.last_error = sanitize_task_text(last_error, limit=320)
        if result_summary is not None:
            record.result_summary = sanitize_task_text(result_summary, limit=4000)
        if verified_at is not None:
            record.verified_at = verified_at
        if completed_at is not None:
            record.completed_at = completed_at
        if increment_attempt:
            record.attempt_count += 1
        for index, stored in enumerate(self._rows):
            if (
                stored.task_id == task_id
                and stored.status is expected_status
                and stored.actor == actor
                and stored.speaker_label == speaker_label
            ):
                self._rows[index] = deepcopy(record)
                return record
        return None

    def list_recent(
        self,
        *,
        limit: int = 20,
        speaker_label: Optional[str] = None,
        session_id: Optional[str] = None,
        actor: Optional[str] = None,
        include_all_scopes: bool = False,
    ) -> List[TaskRecord]:
        rows = deepcopy(self._rows)
        if not include_all_scopes:
            rows = _filter_scope(
                rows,
                speaker_label=speaker_label,
                session_id=session_id,
                actor=actor,
            )
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows[: max(1, int(limit))]

    def list_children(
        self,
        parent_task_id: str,
        *,
        actor: str,
        speaker_label: str,
    ) -> List[TaskRecord]:
        _require_scope(actor, speaker_label)
        rows = [
            deepcopy(row)
            for row in self._rows
            if row.parent_task_id == parent_task_id
            and row.actor == actor
            and row.speaker_label == speaker_label
        ]
        rows.sort(key=lambda row: row.created_at)
        return rows

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
    actor: Optional[str] = None,
) -> List[TaskRecord]:
    out: List[TaskRecord] = []
    for row in rows:
        if speaker_label and row.speaker_label != speaker_label:
            continue
        if session_id and row.session_id != session_id:
            continue
        if actor and row.actor != actor:
            continue
        out.append(row)
    return out


def _validate_progress(progress: int) -> None:
    if (
        isinstance(progress, bool)
        or not isinstance(progress, int)
        or not 0 <= progress <= 100
    ):
        raise ValueError("task progress must be an integer from 0 to 100")


def _require_scope(actor: str, speaker_label: str) -> None:
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("task actor scope is required")
    if not isinstance(speaker_label, str) or not speaker_label.strip():
        raise ValueError("task speaker scope is required")


def _validate_record(record: TaskRecord) -> None:
    _validate_progress(record.progress)
    kind = (record.kind or "").strip()
    raw_text = (record.raw_text or "").strip()
    if not kind or len(kind) > MAX_TASK_KIND_CHARS:
        raise ValueError(
            f"task kind must contain 1 to {MAX_TASK_KIND_CHARS} characters"
        )
    if not raw_text or len(raw_text) > MAX_TASK_RAW_TEXT_CHARS:
        raise ValueError(
            f"task text must contain 1 to {MAX_TASK_RAW_TEXT_CHARS} characters"
        )
    if record.actor not in ALLOWED_TASK_ACTORS:
        raise ValueError("task actor is invalid")
    if record.parent_task_id is not None and not record.parent_task_id.strip():
        raise ValueError("task parent is invalid")
    if record.selected_path is not None:
        if (
            not record.selected_path
            or len(record.selected_path) > MAX_SELECTED_PATH_CHARS
            or any(ord(char) < 32 or ord(char) == 127 for char in record.selected_path)
        ):
            raise ValueError("selected path is invalid")
    record.kind = kind
    record.raw_text = raw_text
    record.last_error = sanitize_task_text(record.last_error, limit=320)
    record.result_summary = sanitize_task_text(record.result_summary, limit=4000)
