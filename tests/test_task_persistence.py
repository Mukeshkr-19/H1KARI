"""Persistent task intents — separate from Brain v2 semantic memory."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from core.brain import HikariBrain
from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.path_literals import EPISODES_DB
from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.schemas import MemoryCandidateStatus
from core.tasks.db_paths import ENV_HIKARI_TASKS_DB
from core.tasks.schemas import TaskStatus
from core.tasks.service import TaskIntentService
from core.tasks.sqlite_store import SqliteTaskStore
from tests.test_brain_memory import FakeNeural
from tests.test_brain_v2_write_authority import _minimal_orchestrator


@pytest.fixture
def episode_db(tmp_path):
    from core.brain_v2 import EpisodeStore

    return EpisodeStore(db_path=tmp_path / EPISODES_DB)


@pytest.fixture
def tasks_db_path(tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    monkeypatch.setenv(ENV_HIKARI_TASKS_DB, str(path))
    return path


def test_task_persists_across_service_instances(tasks_db_path):
    first = TaskIntentService()
    record = first.record_intent("schedule my meeting with Person C")
    assert isinstance(first.store, SqliteTaskStore)

    second = TaskIntentService()
    rows = second.store.list_recent(limit=5)
    assert len(rows) == 1
    assert rows[0].task_id == record.task_id
    assert rows[0].status == TaskStatus.NOT_SCHEDULED


def test_orchestrator_restart_reads_previous_task(tasks_db_path, episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch1 = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    orch1.process_input("remind me to call Person C tomorrow")

    orch2 = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    rows = orch2._task_intent_service().store.list_recent(limit=1)
    assert rows
    assert rows[0].kind == "reminder"
    assert "person c" in rows[0].raw_text.lower()


def test_reminder_not_stored_as_brain_v2_memory(tasks_db_path, episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    orch.process_input("remind me to call Person C tomorrow")

    assert not episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)
    assert not episode_db.get_active_accepted_memories(limit=5)

    episode_id = episode_db.create_episode("manual-check")
    episode_db.add_turn(episode_id, "remind me to call Person C tomorrow", is_user=True)
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    assert not candidates


def test_what_you_remember_excludes_task_text(tasks_db_path, episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    orch.process_input("Remember this: My name is Owner A.")
    orch.process_input("remind me to call Person C tomorrow")
    summary = orch.process_input("what do you remember?")

    low = (summary or "").lower()
    assert "owner a" in low
    assert "remind me" not in low
    assert "call person c" not in low


def test_tasks_list_cli_against_isolated_db(tasks_db_path):
    TaskIntentService().record_intent("write code for Topic A")

    env = os.environ.copy()
    env[ENV_HIKARI_TASKS_DB] = str(tasks_db_path)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run(
        [sys.executable, "hikari.py", "--tasks-list"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.lower()
    assert "not scheduled" in out
    assert "topic a" in out
    assert "separate from brain v2 memory" in out


def test_tasks_list_cli_does_not_create_missing_db(tasks_db_path):
    assert not tasks_db_path.exists()

    env = os.environ.copy()
    env[ENV_HIKARI_TASKS_DB] = str(tasks_db_path)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run(
        [sys.executable, "hikari.py", "--tasks-list"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "No task intents recorded yet." in proc.stdout
    assert not tasks_db_path.exists()
