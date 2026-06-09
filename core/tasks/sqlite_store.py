"""SQLite-backed task intent store (not Brain v2 semantic memory)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from core.tasks.schemas import TaskRecord, TaskStatus
from core.tasks.store import TaskStore, _filter_scope

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
    updated_at TEXT
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
)


class SqliteTaskStore(TaskStore):
    """Persistent task intents scoped by speaker/session."""

    def __init__(self, db_path: Path, *, create_dirs: bool = True) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        if create_dirs:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)
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

    def add(self, record: TaskRecord) -> TaskRecord:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_intents (
                    task_id, kind, raw_text, status, created_at, note,
                    speaker_label, session_id, source, scheduled_at, due_text,
                    scheduler_backend, scheduler_result, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _record_values(record),
            )
            conn.commit()
        return record

    def update(self, record: TaskRecord) -> TaskRecord:
        record.updated_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE task_intents SET
                    kind = ?, raw_text = ?, status = ?, note = ?,
                    speaker_label = ?, session_id = ?, source = ?, scheduled_at = ?,
                    due_text = ?, scheduler_backend = ?, scheduler_result = ?,
                    updated_at = ?
                WHERE task_id = ?
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
                    record.task_id,
                ),
            )
            conn.commit()
        return record

    def list_recent(
        self,
        *,
        limit: int = 20,
        speaker_label: Optional[str] = None,
        session_id: Optional[str] = None,
        include_all_scopes: bool = False,
    ) -> List[TaskRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT task_id, kind, raw_text, status, created_at, note,
                       speaker_label, session_id, source, scheduled_at, due_text,
                       scheduler_backend, scheduler_result, updated_at
                FROM task_intents
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, int(limit * 4 if not include_all_scopes else limit)),),
            ).fetchall()
        records = [_row_to_record(row) for row in rows]
        if not include_all_scopes:
            records = _filter_scope(
                records, speaker_label=speaker_label, session_id=session_id
            )
        return records[: max(1, int(limit))]

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
        speaker_label=str(row["speaker_label"]) if "speaker_label" in keys and row["speaker_label"] else "owner",
        session_id=row["session_id"] if "session_id" in keys else None,
        source=str(row["source"]) if "source" in keys and row["source"] else "text",
        scheduled_at=row["scheduled_at"] if "scheduled_at" in keys else None,
        due_text=row["due_text"] if "due_text" in keys else None,
        scheduler_backend=row["scheduler_backend"] if "scheduler_backend" in keys else None,
        scheduler_result=row["scheduler_result"] if "scheduler_result" in keys else None,
        updated_at=row["updated_at"] if "updated_at" in keys else None,
    )
