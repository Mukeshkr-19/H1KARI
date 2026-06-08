"""Owner auto-trust policy and session-location recall."""

from __future__ import annotations

import pytest

from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.memory_review_gate import MemoryReviewGate
from core.brain_v2.recall_intent import (
    INTENT_CURRENT_LOCATION,
    classify_recall_intent,
    query_seeks_session_location,
)
from core.brain_v2.retrieval import BrainV2Retrieval
from core.brain_v2.schemas import MemoryCandidateStatus
from core.brain_v2.working_memory import WorkingMemory
from tests.test_brain_v2_write_authority import _minimal_orchestrator
from tests.test_brain_memory import FakeNeural
from core.brain import HikariBrain


@pytest.fixture
def episode_db(tmp_path):
    from core.brain_v2 import EpisodeStore

    return EpisodeStore(db_path=tmp_path / "owner_trust.db")


def test_where_am_i_classifies_as_current_location():
    assert classify_recall_intent("where am I?") == INTENT_CURRENT_LOCATION
    assert query_seeks_session_location("where am I?")


def test_session_location_before_general_memory_firewall(episode_db):
    working = WorkingMemory()
    working.note_current_location("City B", "I am in City B.")
    retrieval = BrainV2Retrieval(episode_db, working)
    answer = retrieval.answer_from_accepted("where am I?")
    assert answer
    assert "recent session context" in answer.lower()
    assert "city b" in answer.lower()


def test_remember_this_plan_auto_accepted(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    outcome = coord.ingest_trusted_owner_declaration(
        "sess-plan",
        "Remember this: I am meeting Person A for lunch on Sunday May 25.",
    )
    assert outcome["status"] == "accepted"
    assert not episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)
    accepted = episode_db.get_active_accepted_memories(limit=10)
    assert any("meeting" in m.statement.lower() for m in accepted)


def test_owner_relation_auto_accepted(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    outcome = coord.ingest_trusted_owner_declaration(
        "sess-rel",
        "My sister Person A lives in City B.",
    )
    assert outcome["status"] == "accepted"
    accepted = episode_db.get_active_accepted_memories(limit=10)
    assert any("person a" in m.statement.lower() for m in accepted)


def test_partner_education_stays_review_gated(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    outcome = coord.ingest_trusted_owner_declaration(
        "sess-partner-edu",
        "My partner Person B studies at School A.",
    )
    assert outcome["status"] == "pending_review"
    assert not episode_db.get_active_accepted_memories(limit=10)


def test_where_am_i_after_i_am_in_city(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    orch.process_input("I am in City B.")
    answer = orch.process_input("where am I?")
    assert "recent session context" in answer.lower()
    assert "city b" in answer.lower()


def test_auto_trusted_session_copy_does_not_create_duplicate_pending(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    session_id = coord.start_session()
    user_text = "My name is Owner A."
    outcome = coord.ingest_trusted_owner_declaration(session_id, user_text)
    assert outcome["status"] == "accepted"

    coord.record_turn(
        session_id,
        user_text,
        "Got it. I will remember that in Brain v2.",
        metadata={"skip_candidate_extraction": True},
    )
    for idx in range(coord.consolidate_every_n_turns):
        coord.record_turn(session_id, f"ordinary chat turn {idx}", "ok")

    pending = episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)
    assert not any("owner a" in candidate.statement.lower() for candidate in pending)
