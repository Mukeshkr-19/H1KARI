"""Read-only CLI helpers for task intents."""

from __future__ import annotations

from typing import Optional

from core.tasks.db_paths import resolve_tasks_db_path
from core.tasks.factory import open_task_store


def format_tasks_list(
    *,
    limit: int = 20,
    include_all_scopes: bool = False,
    speaker_label: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    db_path = resolve_tasks_db_path()
    lines = [
        "Task intents (separate from Brain v2 memory)",
        f"Database: {db_path}",
        "",
    ]
    if not db_path.exists():
        lines.append("No task intents recorded yet.")
        return "\n".join(lines)

    store = open_task_store(create_dirs=False)
    rows = store.list_recent(
        limit=limit,
        speaker_label=None if include_all_scopes else speaker_label,
        session_id=None if include_all_scopes else session_id,
        include_all_scopes=include_all_scopes,
    )
    if not rows:
        lines.append("No task intents recorded yet.")
        return "\n".join(lines)

    for index, row in enumerate(rows, start=1):
        status = row.status.value.upper().replace("_", " ")
        lines.append(f"{index}. [{status}] {row.kind} — {row.raw_text}")
        lines.append(
            f"   id={row.task_id}  speaker={row.speaker_label or 'owner'}  "
            f"source={row.source or 'text'}  recorded={row.created_at}"
        )
        if row.session_id:
            lines.append(f"   session={row.session_id}")
        if row.due_text:
            lines.append(f"   due_hint={row.due_text}")
        if row.scheduled_at:
            lines.append(f"   scheduled_at={row.scheduled_at}")
        if row.scheduler_backend:
            lines.append(f"   scheduler={row.scheduler_backend}")
        if row.scheduler_result:
            lines.append(f"   scheduler_result={row.scheduler_result}")
        if row.note:
            lines.append(f"   note={row.note}")
    return "\n".join(lines)


def run_tasks_list_cli(*, limit: int = 20, include_all_scopes: bool = False) -> int:
    print(format_tasks_list(limit=limit, include_all_scopes=include_all_scopes))
    return 0
