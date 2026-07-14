"""Brain v2 episode DB path resolution without importing neural_memory.config."""

from __future__ import annotations

import os
from pathlib import Path

from core.path_literals import EPISODES_DB
from core.runtime_paths import brain_dir

ENV_BRAIN_V2_EPISODES_DB = "HIKARI_BRAIN_V2_EPISODES_DB"


def resolve_episodes_db_path() -> Path:
    """Resolve episodes DB path from env or default under HOME (no neural config import)."""
    explicit = os.environ.get(ENV_BRAIN_V2_EPISODES_DB)
    if explicit:
        return Path(explicit).expanduser().resolve()
    return brain_dir() / "brain_v2" / EPISODES_DB


def episodes_db_explicitly_configured() -> bool:
    return bool(os.environ.get(ENV_BRAIN_V2_EPISODES_DB))


def open_readonly_episode_store() -> "EpisodeStore":
    """Open Brain v2 SQLite in URI read-only mode (no schema init or writes)."""
    from core.brain_v2.episode_store import EpisodeStore

    db_path = resolve_episodes_db_path()
    if not db_path.is_file():
        raise SystemExit(
            f"Brain v2 database not found at {db_path}. "
            f"Set {ENV_BRAIN_V2_EPISODES_DB} to an existing episodes database."
        )
    return EpisodeStore(db_path=db_path, create_dirs=False, readonly=True)


def open_episode_store(*, write: bool = False) -> "EpisodeStore":
    """Open Brain v2 store without creating HOME/.hikari unless policy allows.

    - read=False: true SQLite read-only (``mode=ro``); never calls ``_init_db()``.
    - Existing file + write: open read/write with create_dirs=False.
    - Explicit env path + write: may create parent dirs under that path only.
    - Default path + write: only if live brain directory already exists.
    - Otherwise: raise SystemExit (never mkdir a fresh HOME/.hikari tree).
    """
    if not write:
        return open_readonly_episode_store()

    from core.brain_v2.episode_store import EpisodeStore

    db_path = resolve_episodes_db_path()
    if db_path.is_file():
        return EpisodeStore(db_path=db_path, create_dirs=False)

    if episodes_db_explicitly_configured():
        return EpisodeStore(db_path=db_path, create_dirs=True)

    brain_dir = db_path.parent.parent
    if brain_dir.is_dir():
        return EpisodeStore(db_path=db_path, create_dirs=True)

    raise SystemExit(
        f"Brain v2 database not found at {db_path} and live brain directory is absent. "
        f"Set {ENV_BRAIN_V2_EPISODES_DB} for an explicit episodes database path, "
        "or initialize the live brain directory before accepting candidates."
    )
