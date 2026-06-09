"""Task intent DB path resolution (separate from Brain v2 episodes)."""

from __future__ import annotations

import os
from pathlib import Path

from core.path_literals import TASKS_DB
from core.runtime_paths import brain_dir

ENV_HIKARI_TASKS_DB = "HIKARI_TASKS_DB"


def resolve_tasks_db_path() -> Path:
    """Resolve task SQLite path from env or private brain runtime area."""
    explicit = os.environ.get(ENV_HIKARI_TASKS_DB)
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (brain_dir() / "tasks" / TASKS_DB).expanduser().resolve()
