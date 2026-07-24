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
from tests.test_brain_v2_write_authority import _minimal_orchestrator, _teach_long_term
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
    assert "for this session" in answer.lower()
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


def test_favorite_slot_correction_retires_prior_value(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    first = coord.ingest_trusted_owner_declaration(
        "sess-favorite-1",
        "My favorite artist is Lorde.",
    )
    second = coord.ingest_trusted_owner_declaration(
        "sess-favorite-2",
        "My favorite artist is Lana Del Rey, not Lorde.",
    )

    assert first["status"] == "accepted"
    assert second["status"] == "accepted"
    active = episode_db.get_active_accepted_memories(limit=20)
    artist_memories = [
        memory
        for memory in active
        if (memory.metadata or {}).get("preference_kind") == "artist"
    ]
    assert len(artist_memories) == 1
    assert artist_memories[0].statement == "My favorite artist is Lana Del Rey."
    assert coord.retrieval.answer_from_accepted("Who's my favorite artist?") == (
        "My favorite artist is Lana Del Rey."
    )


def test_distinct_favorite_categories_coexist(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    coord.ingest_trusted_owner_declaration(
        "sess-favorite-artist",
        "My favorite artist is Lana Del Rey.",
    )
    coord.ingest_trusted_owner_declaration(
        "sess-favorite-color",
        "My favorite color is blue.",
    )

    active = episode_db.get_active_accepted_memories(limit=20)
    assert {
        (memory.metadata or {}).get("preference_kind")
        for memory in active
        if (memory.metadata or {}).get("candidate_type") == "preference"
    } == {"artist", "color"}


def test_identity_parser_drops_trailing_conversation_filler(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    outcome = coord.ingest_trusted_owner_declaration(
        "sess-identity-filler",
        "My name is Owner A but u can call me Person B okay?",
    )

    assert outcome["status"] == "accepted"
    active = episode_db.get_active_accepted_memories(limit=10)
    assert len(active) == 1
    assert (active[0].metadata or {}).get("legal_name") == "Owner A"
    assert (active[0].metadata or {}).get("preferred_name") == "Person B"


def test_owner_legal_and_preferred_name_stored_separately(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    reply = _teach_long_term(
        orch,
        "My real name is Owner A but I told you to call me Person B.",
    )
    assert "got it" in reply.lower()

    legal = coord.retrieval.answer_from_accepted("what is my real name?")
    casual = coord.retrieval.answer_from_accepted("what is my name?")
    assert legal
    assert "owner a" in legal.lower()
    assert casual
    assert "person b" in casual.lower()
    preferred = coord.retrieval.answer_from_accepted("What should you call me?")
    assert preferred == "I call you Person B."


def test_real_name_query_does_not_return_preferred_only(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    coord.ingest_trusted_owner_declaration(
        "sess-pref-only",
        "You can call me Person B.",
    )
    answer = coord.retrieval.answer_from_accepted("what is my real name?")
    assert answer
    assert "person b" not in answer.lower() or "do not have your full legal name" in answer.lower()


def test_owner_degree_statement_auto_accepted(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    outcome = coord.ingest_trusted_owner_declaration(
        "sess-edu",
        "I am doing my bachelors in computer science in university at City A.",
    )
    assert outcome["status"] == "accepted"
    accepted = episode_db.get_active_accepted_memories(limit=10)
    assert any("computer science" in m.statement.lower() for m in accepted)
    retrieval = BrainV2Retrieval(episode_db)
    answer = retrieval.answer_from_accepted("what do I study?")
    assert answer
    assert "computer science" in answer.lower()


def test_owner_graduation_statement_auto_accepted(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    outcome = coord.ingest_trusted_owner_declaration(
        "sess-grad",
        "I am a rising senior and I will be graduating in May 2027.",
    )
    assert outcome["status"] == "accepted"
    accepted = episode_db.get_active_accepted_memories(limit=10)
    assert any("graduat" in m.statement.lower() for m in accepted)


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
    assert "for this session" in answer.lower()
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
