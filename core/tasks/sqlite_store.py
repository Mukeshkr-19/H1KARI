"""SQLite-backed task intent store (not Brain v2 semantic memory)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List

from core.tasks.schemas import TaskRecord, TaskStatus
from core.tasks.store import TaskStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_intents (
    task_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    note TEXT
);

CREATE INDEX IF NOT EXISTS idx_task_intents_created
    ON task_intents(created_at DESC);
"""


class SqliteTaskStore(TaskStore):
    """Persistent task intents; status remains NOT_SCHEDULED until a scheduler exists."""

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
            conn.commit()

    def add(self, record: TaskRecord) -> TaskRecord:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_intents (
                    task_id, kind, raw_text, status, created_at, note
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.task_id,
                    record.kind,
                    record.raw_text,
                    record.status.value,
                    record.created_at,
                    record.note,
                ),
            )
            conn.commit()
        return record

    def list_recent(self, *, limit: int = 20) -> List[TaskRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT task_id, kind, raw_text, status, created_at, note
                FROM task_intents
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [_row_to_record(row) for row in rows]


def _row_to_record(row: sqlite3.Row) -> TaskRecord:
    return TaskRecord(
        task_id=str(row["task_id"]),
        kind=str(row["kind"]),
        raw_text=str(row["raw_text"]),
        status=TaskStatus(str(row["status"])),
        created_at=str(row["created_at"]),
        note=row["note"],
    )
