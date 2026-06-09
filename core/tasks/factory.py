"""Open the configured task store backend."""

from __future__ import annotations

from core.tasks.db_paths import resolve_tasks_db_path
from core.tasks.sqlite_store import SqliteTaskStore
from core.tasks.store import InMemoryTaskStore, TaskStore


def open_task_store(*, prefer_memory: bool = False, create_dirs: bool = True) -> TaskStore:
    """Return persistent SQLite store unless tests request in-memory only."""
    if prefer_memory:
        return InMemoryTaskStore()
    return SqliteTaskStore(resolve_tasks_db_path(), create_dirs=create_dirs)
