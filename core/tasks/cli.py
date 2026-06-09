"""Read-only CLI helpers for task intents."""

from __future__ import annotations

from core.tasks.db_paths import resolve_tasks_db_path
from core.tasks.factory import open_task_store


def format_tasks_list(*, limit: int = 20) -> str:
    db_path = resolve_tasks_db_path()
    lines = [
        "Task intents (scheduling is not wired up yet)",
        f"Database: {db_path}",
        "",
    ]
    if not db_path.exists():
        lines.append("No task intents recorded yet.")
        return "\n".join(lines)

    store = open_task_store()
    rows = store.list_recent(limit=limit)
    if not rows:
        lines.append("No task intents recorded yet.")
        return "\n".join(lines)

    for index, row in enumerate(rows, start=1):
        status = row.status.value.upper().replace("_", " ")
        lines.append(f"{index}. [{status}] {row.kind} — {row.raw_text}")
        lines.append(f"   id={row.task_id}  recorded={row.created_at}")
        if row.note:
            lines.append(f"   note={row.note}")
    return "\n".join(lines)


def run_tasks_list_cli(*, limit: int = 20) -> int:
    print(format_tasks_list(limit=limit))
    return 0
