"""Regression checks for deterministic SQLite connection cleanup."""

from __future__ import annotations

import sqlite3

import pytest

from core.action_audit import ActionAuditStore
from core.brain_v2.episode_store import EpisodeStore
from core.grants import GrantStore
from core.path_literals import EPISODES_DB
from core.tasks.sqlite_store import SqliteTaskStore


@pytest.fixture(
    params=(GrantStore, ActionAuditStore, SqliteTaskStore, EpisodeStore),
    ids=("grants", "action-audit", "tasks", "episodes"),
)
def writable_store(request, tmp_path):
    return request.param(tmp_path / f"{request.node.name}.db")


def test_connection_closes_after_context_exit(writable_store):
    with writable_store._connect() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        conn.execute("SELECT 1")


def test_connection_context_preserves_commit_and_rollback(writable_store):
    with writable_store._connect() as conn:
        conn.execute("CREATE TABLE lifetime_probe (value TEXT)")
        conn.execute("INSERT INTO lifetime_probe VALUES ('committed')")

    with pytest.raises(RuntimeError, match="force rollback"):
        with writable_store._connect() as conn:
            conn.execute("INSERT INTO lifetime_probe VALUES ('rolled-back')")
            raise RuntimeError("force rollback")

    with writable_store._connect() as conn:
        values = [row[0] for row in conn.execute("SELECT value FROM lifetime_probe")]
    assert values == ["committed"]


def test_readonly_episode_connection_closes_after_context_exit(tmp_path):
    db_path = tmp_path / EPISODES_DB
    EpisodeStore(db_path)
    store = EpisodeStore(db_path, readonly=True)

    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_episodes").fetchone()[0] == 0

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        conn.execute("SELECT 1")
