"""Tasks must not be stored as Brain v2 personal semantic memory."""

from __future__ import annotations

import pytest

from core.brain import HikariBrain
from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.schemas import MemoryCandidateStatus
from core.tasks.db_paths import ENV_HIKARI_TASKS_DB
from core.tasks.schemas import TaskStatus
from core.tasks.service import TaskIntentService
from core.tasks.store import InMemoryTaskStore
from tests.test_brain_memory import FakeNeural
from tests.test_brain_v2_write_authority import _minimal_orchestrator


@pytest.fixture
def episode_db(tmp_path):
    from core.brain_v2 import EpisodeStore

    return EpisodeStore(db_path=tmp_path / "task_boundary.db")


@pytest.fixture(autouse=True)
def isolated_tasks_db(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_HIKARI_TASKS_DB, str(tmp_path / "tasks.db"))


def test_task_intent_service_records_not_scheduled():
    svc = TaskIntentService(store=InMemoryTaskStore())
    record = svc.record_intent("remind me to call Person C tomorrow")
    assert record.kind == "reminder"
    assert record.status == TaskStatus.NOT_SCHEDULED
    assert svc.store.list_recent(limit=1)[0].task_id == record.task_id


def test_reminder_not_stored_as_brain_v2_candidate(episode_db):
    episode_id = episode_db.create_episode("task-boundary")
    episode_db.add_turn(episode_id, "remind me to call Person C tomorrow", is_user=True)
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    assert not candidates


def test_orchestrator_records_task_intent_without_brain_memory(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    reply = orch.process_input("remind me to call Person C tomorrow")
    assert "will not store that as a brain v2 memory" in reply.lower()
    assert not episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)
    assert not episode_db.get_active_accepted_memories(limit=5)
    records = orch._task_intent_service().store.list_recent(limit=1)
    assert records
    assert records[0].kind == "reminder"
