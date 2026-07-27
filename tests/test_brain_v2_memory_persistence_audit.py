"""Regression tests for Brain v2 explicit remember persistence audit (synthetic only)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.brain import HikariBrain
from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.memory_review_gate import MemoryReviewGate
from core.brain_v2.schemas import MemoryCandidate, MemoryCandidateStatus, SourceLinkedMemory, TranscriptSegment
from core.brain_service import ReviewedMemoryHit
from tests.test_brain_memory import FakeNeural
from tests.test_brain_v2_write_authority import _minimal_orchestrator


SENTINEL = "SECRET_FACT_SENTINEL_XYZZY"


@pytest.fixture
def episode_db(tmp_path):
    return EpisodeStore(db_path=tmp_path / "audit.db")


def test_explicit_remember_plan_body_not_discarded_as_task(episode_db):
  coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
  orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
  reply = orch.process_input(
      "Remember this: I will call TestPersonAlpha tomorrow."
  )
  assert "will not store" not in reply.lower()
  assert episode_db.get_active_accepted_memories(limit=5)


def test_sister_name_conflict_second_fact_pending(episode_db):
  coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
  r1 = coord.ingest_trusted_owner_declaration("sess", "Remember this: My sister's name is TestPersonAlpha.")
  assert r1.get("status") == "accepted"
  r2 = coord.ingest_trusted_owner_declaration("sess", "Remember this: My sister's name is TestPersonBeta.")
  assert r2.get("status") == "pending_conflict"
  active = episode_db.get_active_accepted_memories(limit=20)
  persons = {
      str((m.metadata or {}).get("person") or "") for m in active
      if (m.metadata or {}).get("relation") == "sister"
  }
  assert "TestPersonBeta" not in persons


def test_friend_relation_inference_and_recall_filter(episode_db):
  from core.brain_v2.memory_type import infer_memory_type
  from core.brain_v2.recall_intent import requested_relations

  inf = infer_memory_type("My friend TestPersonAlpha works at TestClinic.")
  assert inf.candidate_type == "relation"
  assert inf.metadata.get("relation") == "friend"
  assert requested_relations("What does my friend TestPersonAlpha do?")


def test_persistence_survives_new_coordinator_instance(episode_db):
  coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
  orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
  orch.process_input("Remember this: My favorite color is TestColorAmber.")
  coord2 = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
  orch2 = _minimal_orchestrator(coord2, HikariBrain(FakeNeural([])))
  reply = orch2.process_input("What is my favorite color?")
  assert "TestColorAmber" in reply


def test_content_free_reprs_do_not_leak_statement_text():
  seg = TranscriptSegment(segment_id="s1", episode_id="e1", sequence=0, text=SENTINEL)
  cand = MemoryCandidate(candidate_id="c1", episode_id="e1", statement=SENTINEL)
  mem = SourceLinkedMemory(
      memory_id="m1",
      candidate_id="c1",
      episode_id="e1",
      statement=SENTINEL,
      source_segment_ids=["s1"],
  )
  hit = ReviewedMemoryHit(
      text=SENTINEL,
      score=1.0,
      memory_id="m1",
      candidate_id="c1",
      episode_id="e1",
      source_segment_ids=("s1",),
      predecessor_memory_ids=(),
      predecessor_evidence_segment_ids=(),
      correction_actions=(),
  )
  for obj in (seg, cand, mem, hit):
      assert SENTINEL not in repr(obj)


def test_atomic_accept_rolls_back_on_linked_persist_failure(episode_db, monkeypatch):
  episode_id = episode_db.create_episode("atomic-test")
  cand_id = "cand-atomic-1"
  episode_db.save_candidates(
      [
          MemoryCandidate(
              candidate_id=cand_id,
              episode_id=episode_id,
              statement="TestCityOrchid is my birthplace.",
              candidate_type="birthplace",
          )
      ]
  )

  real_persist = episode_db._persist_source_linked_conn

  def boom(conn, memory):
      raise RuntimeError("persist_failed")

  monkeypatch.setattr(episode_db, "_persist_source_linked_conn", boom)
  linked = SourceLinkedMemory(
      memory_id="m-atomic",
      candidate_id=cand_id,
      episode_id=episode_id,
      statement="Test birthplace fact",
      source_segment_ids=[],
  )
  with pytest.raises(RuntimeError):
      episode_db.atomic_accept_source_linked(cand_id, linked)
  cand = episode_db.get_candidate(cand_id)
  assert cand is not None
  assert cand.review_status != MemoryCandidateStatus.ACCEPTED.value
  assert not episode_db.get_active_accepted_memories(limit=5)
