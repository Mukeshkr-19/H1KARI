"""SQLite-backed task intent store (not Brain v2 semantic memory)."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from core.tasks.schemas import (
    TERMINAL_TASK_STATUSES,
    TaskRecord,
    TaskStatus,
    can_transition,
    sanitize_task_text,
)
from core.tasks.store import (
    TaskStore,
    _validate_legacy_update,
    _validate_progress,
    _validate_record,
    _require_scope,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_intents (
    task_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    note TEXT,
    speaker_label TEXT,
    session_id TEXT,
    source TEXT,
    scheduled_at TEXT,
    due_text TEXT,
    scheduler_backend TEXT,
    scheduler_result TEXT,
    updated_at TEXT,
    actor TEXT NOT NULL DEFAULT 'owner',
    progress INTEGER NOT NULL DEFAULT 0,
    checkpoint TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    result_summary TEXT,
    verified_at TEXT,
    completed_at TEXT,
    parent_task_id TEXT,
    selected_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_task_intents_created
    ON task_intents(created_at DESC);
"""

_MIGRATION_COLUMNS = (
    ("speaker_label", "TEXT"),
    ("session_id", "TEXT"),
    ("source", "TEXT"),
    ("scheduled_at", "TEXT"),
    ("due_text", "TEXT"),
    ("scheduler_backend", "TEXT"),
    ("scheduler_result", "TEXT"),
    ("updated_at", "TEXT"),
    ("actor", "TEXT NOT NULL DEFAULT 'owner'"),
    ("progress", "INTEGER NOT NULL DEFAULT 0"),
    ("checkpoint", "TEXT"),
    ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
    ("last_error", "TEXT"),
    ("result_summary", "TEXT"),
    ("verified_at", "TEXT"),
    ("completed_at", "TEXT"),
    ("parent_task_id", "TEXT"),
    ("selected_path", "TEXT"),
)

_POST_MIGRATION_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_task_intents_scope_updated
    ON task_intents(actor, speaker_label, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_intents_status_updated
    ON task_intents(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_intents_parent_created
    ON task_intents(parent_task_id, created_at);
"""


class SqliteTaskStore(TaskStore):
    """Persistent task intents scoped by speaker/session."""

    def __init__(self, db_path: Path, *, create_dirs: bool = True) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        if create_dirs:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self.db_path.parent, 0o700)
        if self.db_path.exists():
            os.chmod(self.db_path, 0o600)
        self._init_db()
        os.chmod(self.db_path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)
            conn.executescript(_POST_MIGRATION_SCHEMA)
            conn.commit()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        existing = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(task_intents)")
        }
        for column, col_type in _MIGRATION_COLUMNS:
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE task_intents ADD COLUMN {column} {col_type}"
                )
        conn.execute(
            """
            UPDATE task_intents
            SET actor = 'guest'
            WHERE source = 'guest' AND (actor IS NULL OR actor = 'owner')
            """
        )

    def recover_incomplete(self, *, actor: str, speaker_label: str) -> int:
        _require_scope(actor, speaker_label)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE task_intents
                SET status = ?, checkpoint = COALESCE(checkpoint, ?), updated_at = ?
                WHERE actor = ? AND speaker_label = ? AND status IN (?, ?)
                """,
                (
                    TaskStatus.INTERRUPTED.value,
                    "recovered_after_restart",
                    now,
                    actor,
                    speaker_label,
                    TaskStatus.RUNNING.value,
                    TaskStatus.VERIFYING.value,
                ),
            )
            conn.commit()
        return cursor.rowcount

    def add(self, record: TaskRecord) -> TaskRecord:
        _validate_record(record)
        with self._connect() as conn:
            if record.parent_task_id is not None:
                parent = conn.execute(
                    "SELECT parent_task_id FROM task_intents "
                    "WHERE task_id = ? AND actor = ? AND speaker_label = ?",
                    (record.parent_task_id, record.actor, record.speaker_label),
                ).fetchone()
                if parent is None or parent["parent_task_id"] is not None:
                    raise ValueError("task parent is invalid")
            conn.execute(
                """
                INSERT INTO task_intents (
                    task_id, kind, raw_text, status, created_at, note,
                    speaker_label, session_id, source, scheduled_at, due_text,
                    scheduler_backend, scheduler_result, updated_at, actor,
                    progress, checkpoint, attempt_count, last_error,
                    result_summary, verified_at, completed_at, parent_task_id,
                    selected_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _record_values(record),
            )
            conn.commit()
        return record

    def update(self, record: TaskRecord) -> TaskRecord:
        _validate_record(record)
        with self._connect() as conn:
            current_row = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM task_intents WHERE task_id = ?",
                (record.task_id,),
            ).fetchone()
            if current_row is None:
                raise KeyError(f"unknown task: {record.task_id}")
            current = _row_to_record(current_row)
            _validate_legacy_update(current, record)
            record.updated_at = datetime.now(timezone.utc).isoformat()
            cursor = conn.execute(
                """
                UPDATE task_intents SET
                    kind = ?, raw_text = ?, status = ?, note = ?,
                    speaker_label = ?, session_id = ?, source = ?, scheduled_at = ?,
                    due_text = ?, scheduler_backend = ?, scheduler_result = ?,
                    updated_at = ?, actor = ?, progress = ?, checkpoint = ?,
                    attempt_count = ?, last_error = ?, result_summary = ?,
                    verified_at = ?, completed_at = ?, parent_task_id = ?,
                    selected_path = ?
                WHERE task_id = ? AND actor = ? AND speaker_label = ? AND status = ?
                """,
                (
                    record.kind,
                    record.raw_text,
                    record.status.value,
                    record.note,
                    record.speaker_label,
                    record.session_id,
                    record.source,
                    record.scheduled_at,
                    record.due_text,
                    record.scheduler_backend,
                    record.scheduler_result,
                    record.updated_at,
                    record.actor,
                    record.progress,
                    record.checkpoint,
                    record.attempt_count,
                    record.last_error,
                    record.result_summary,
                    record.verified_at,
                    record.completed_at,
                    record.parent_task_id,
                    record.selected_path,
                    record.task_id,
                    current.actor,
                    current.speaker_label,
                    current.status.value,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown task: {record.task_id}")
            conn.commit()
        return record

    def get(
        self,
        task_id: str,
        *,
        actor: str,
        speaker_label: str,
    ) -> Optional[TaskRecord]:
        _require_scope(actor, speaker_label)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM task_intents "
                "WHERE task_id = ? AND actor = ? AND speaker_label = ?",
                (task_id, actor, speaker_label),
            ).fetchone()
        return _row_to_record(row) if row else None

    def get_legacy_unscoped(self, task_id: str) -> Optional[TaskRecord]:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM task_intents "
                "WHERE task_id = ? AND kind = 'reminder' "
                "AND status IN ('recorded', 'not_scheduled', 'scheduled', "
                "'schedule_failed')",
                (task_id,),
            ).fetchone()
        return _row_to_record(row) if row else None

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
        if progress is not None:
            _validate_progress(progress)
        if expected_status is new_status and expected_status in TERMINAL_TASK_STATUSES:
            current = self.get(
                task_id,
                actor=actor,
                speaker_label=speaker_label,
            )
            return current if current and current.status is expected_status else None
        now = datetime.now(timezone.utc).isoformat()
        assignments = ["status = ?", "updated_at = ?"]
        values: list[object] = [new_status.value, now]
        if reset_lifecycle:
            assignments.extend(
                (
                    "progress = 0",
                    "last_error = NULL",
                    "result_summary = NULL",
                    "verified_at = NULL",
                    "completed_at = NULL",
                )
            )
        for column, value in (
            ("progress", progress),
            ("checkpoint", checkpoint),
            ("last_error", sanitize_task_text(last_error, limit=320)),
            ("result_summary", sanitize_task_text(result_summary, limit=4000)),
            ("verified_at", verified_at),
            ("completed_at", completed_at),
        ):
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(value)
        if increment_attempt:
            assignments.append("attempt_count = attempt_count + 1")
        where = ["task_id = ?", "status = ?"]
        values.extend((task_id, expected_status.value))
        where.extend(("actor = ?", "speaker_label = ?"))
        values.extend((actor, speaker_label))
        if expected_updated_at is not None:
            where.append("updated_at IS ?")
            values.append(expected_updated_at)
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE task_intents SET {', '.join(assignments)} "
                f"WHERE {' AND '.join(where)}",
                tuple(values),
            )
            conn.commit()
        if cursor.rowcount != 1:
            return None
        return self.get(task_id, actor=actor, speaker_label=speaker_label)

    def list_recent(
        self,
        *,
        limit: int = 20,
        speaker_label: Optional[str] = None,
        session_id: Optional[str] = None,
        actor: Optional[str] = None,
        include_all_scopes: bool = False,
    ) -> List[TaskRecord]:
        where: list[str] = []
        values: list[object] = []
        if not include_all_scopes and actor and speaker_label:
            for column, value in (
                ("speaker_label", speaker_label),
                ("session_id", session_id),
                ("actor", actor),
            ):
                if value:
                    where.append(f"{column} = ?")
                    values.append(value)
        elif not include_all_scopes:
            # The unscoped API predates Phase 1. Keep only its reminder view;
            # private document roots and follow-ups require an exact actor scope.
            where.extend(
                (
                    "kind = 'reminder'",
                    "status IN ('recorded', 'not_scheduled', 'scheduled', "
                    "'schedule_failed')",
                )
            )
            for column, value in (
                ("speaker_label", speaker_label),
                ("session_id", session_id),
            ):
                if value:
                    where.append(f"{column} = ?")
                    values.append(value)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        values.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM task_intents {clause} "
                "ORDER BY created_at DESC LIMIT ?",
                tuple(values),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def list_children(
        self,
        parent_task_id: str,
        *,
        actor: str,
        speaker_label: str,
    ) -> List[TaskRecord]:
        _require_scope(actor, speaker_label)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM task_intents "
                "WHERE parent_task_id = ? AND actor = ? AND speaker_label = ? "
                "ORDER BY created_at",
                (parent_task_id, actor, speaker_label),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def find_latest_unscheduled_reminder(
        self,
        *,
        speaker_label: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        for row in self.list_recent(
            limit=50,
            speaker_label=speaker_label,
            session_id=session_id,
        ):
            if row.kind == "reminder" and row.status in (
                TaskStatus.NOT_SCHEDULED,
                TaskStatus.RECORDED,
            ):
                return row
        return None


def _record_values(record: TaskRecord) -> tuple:
    return (
        record.task_id,
        record.kind,
        record.raw_text,
        record.status.value,
        record.created_at,
        record.note,
        record.speaker_label,
        record.session_id,
        record.source,
        record.scheduled_at,
        record.due_text,
        record.scheduler_backend,
        record.scheduler_result,
        record.updated_at,
        record.actor,
        record.progress,
        record.checkpoint,
        record.attempt_count,
        record.last_error,
        record.result_summary,
        record.verified_at,
        record.completed_at,
        record.parent_task_id,
        record.selected_path,
    )


def _row_to_record(row: sqlite3.Row) -> TaskRecord:
    keys = set(row.keys())
    return TaskRecord(
        task_id=str(row["task_id"]),
        kind=str(row["kind"]),
        raw_text=str(row["raw_text"]),
        status=TaskStatus(str(row["status"])),
        created_at=str(row["created_at"]),
        note=row["note"],
        speaker_label=(
            str(row["speaker_label"])
            if "speaker_label" in keys and row["speaker_label"]
            else "owner"
        ),
        session_id=row["session_id"] if "session_id" in keys else None,
        source=str(row["source"]) if "source" in keys and row["source"] else "text",
        scheduled_at=row["scheduled_at"] if "scheduled_at" in keys else None,
        due_text=row["due_text"] if "due_text" in keys else None,
        scheduler_backend=row["scheduler_backend"] if "scheduler_backend" in keys else None,
        scheduler_result=row["scheduler_result"] if "scheduler_result" in keys else None,
        updated_at=row["updated_at"] if "updated_at" in keys else None,
        actor=str(row["actor"]) if "actor" in keys and row["actor"] else "owner",
        progress=int(row["progress"] or 0) if "progress" in keys else 0,
        checkpoint=row["checkpoint"] if "checkpoint" in keys else None,
        attempt_count=(
            int(row["attempt_count"] or 0) if "attempt_count" in keys else 0
        ),
        last_error=row["last_error"] if "last_error" in keys else None,
        result_summary=row["result_summary"] if "result_summary" in keys else None,
        verified_at=row["verified_at"] if "verified_at" in keys else None,
        completed_at=row["completed_at"] if "completed_at" in keys else None,
        parent_task_id=(
            row["parent_task_id"] if "parent_task_id" in keys else None
        ),
        selected_path=row["selected_path"] if "selected_path" in keys else None,
    )


_SELECT_COLUMNS = """
task_id, kind, raw_text, status, created_at, note, speaker_label, session_id,
source, scheduled_at, due_text, scheduler_backend, scheduler_result, updated_at,
actor, progress, checkpoint, attempt_count, last_error, result_summary,
verified_at, completed_at, parent_task_id, selected_path
"""
