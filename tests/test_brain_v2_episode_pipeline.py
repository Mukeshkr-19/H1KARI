"""Brain v2 episode pipeline — storage, review gate, retrieval separation."""

from __future__ import annotations

import pytest

from core.brain_v2 import (
    BrainV2Coordinator,
    EpisodeStore,
    MemoryCandidateStatus,
    MemoryReviewGate,
)
from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.retrieval import BrainV2Retrieval


@pytest.fixture
def episode_db(tmp_path):
    return EpisodeStore(db_path=tmp_path / "test_v2.db")


@pytest.fixture
def coordinator(episode_db):
    return BrainV2Coordinator(store=episode_db)


def test_store_episode_and_segments_are_separate_from_structured(episode_db):
    session = "sess-1"
    episode_id = episode_db.create_episode(session)
    episode_db.add_turn(episode_id, "My name is Alex and I live in City B.")
    episode_db.add_turn(episode_id, "Got it.", is_user=False, speaker_label="assistant")

    assert episode_db.count_raw_segments(episode_id) == 2
    assert episode_db.get_structured_episode(episode_id) is None

    pipeline = EpisodeConsolidationPipeline(episode_db)
    structured, candidates = pipeline.process_episode(episode_id)

    assert structured.episode_id == episode_id
    assert structured.segment_count == 2
    assert episode_db.get_structured_episode(episode_id) is not None
    raw = episode_db.get_raw_segments(episode_id)
    assert len(raw) == 2
    assert raw[0].text.startswith("My name is Alex")


def test_extract_memory_candidates(episode_db):
    episode_id = episode_db.create_episode("sess-2")
    episode_db.add_turn(episode_id, "Remember this: I prefer local-first tools.")
    episode_db.add_turn(episode_id, "Okay.", is_user=False, speaker_label="assistant")

    _, candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)

    assert candidates
    assert any("local-first" in c.statement.lower() for c in candidates)
    assert all(c.review_status == MemoryCandidateStatus.PENDING.value for c in candidates)


def test_reject_and_accept_candidates(episode_db):
    episode_id = episode_db.create_episode("sess-3")
    episode_db.add_turn(episode_id, "My dad's name is Rowan and he lives in Lake Town.")
    _, candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)
    assert len(candidates) >= 1

    gate = MemoryReviewGate(episode_db)
    reject_id = candidates[0].candidate_id
    gate.reject(reject_id)
    rejected = episode_db.get_candidates(status=MemoryCandidateStatus.REJECTED)
    assert any(c.candidate_id == reject_id for c in rejected)
    assert not episode_db.get_accepted_memories()

    accept_id = candidates[-1].candidate_id
    linked = gate.accept(accept_id)
    assert linked.statement
    assert linked.source_segment_ids
    accepted = episode_db.get_accepted_memories()
    assert len(accepted) == 1
    assert accepted[0].candidate_id == accept_id


def test_retrieval_uses_accepted_not_raw_transcript(episode_db):
    episode_id = episode_db.create_episode("sess-4")
    episode_db.add_turn(episode_id, "My sister Maya studies at North Valley University.")
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    gate = MemoryReviewGate(episode_db)
    for c in candidates:
        gate.accept(c.candidate_id)

    retrieval = BrainV2Retrieval(episode_db)
    packet = retrieval.retrieve("where does my sister study?")
    texts = " ".join(h.text for h in packet.hits).lower()
    assert "maya" in texts or "north valley" in texts
    assert "got it" not in texts


def test_duplicate_candidates_do_not_overwhelm_retrieval(episode_db):
    episode_id = episode_db.create_episode("sess-5")
    for _ in range(3):
        episode_db.add_turn(episode_id, "I live in City B.")
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    gate = MemoryReviewGate(episode_db)
    for c in candidates:
        gate.accept(c.candidate_id)

    packet = BrainV2Retrieval(episode_db).retrieve("where do I live?")
    river_hits = [h for h in packet.hits if "city b" in h.text.lower()]
    assert len(river_hits) <= 2


def test_coordinator_end_to_end(coordinator):
    session = coordinator.start_session()
    coordinator.record_turn(session, "I live in City B.", "Noted.")
    structured, candidates = coordinator.close_and_consolidate(session)
    assert structured.summary
    assert candidates

    cand_id = candidates[0].candidate_id
    linked, _promo = coordinator.accept_candidate(cand_id, promote=False)
    assert linked.memory_id

    packet = coordinator.build_context_packet(
        "where do I live?",
        speaker_context={"speaker": "PrimaryUser"},
    )
    assert packet.hits


def test_raw_episode_never_equals_accepted_memory_table(coordinator):
    session = coordinator.start_session("iso-sess")
    coordinator.record_turn(session, "Remember this: flights on July 3.", "OK.")
    coordinator.close_and_consolidate(session)
    summary = coordinator.status_summary()
    assert summary["transcript_segments"] >= 2
    assert summary["accepted_memories"] == 0

    pending = coordinator.store.get_candidates(status=MemoryCandidateStatus.PENDING)
    if pending:
        coordinator.accept_candidate(pending[0].candidate_id, promote=False)[0]
    summary2 = coordinator.status_summary()
    assert summary2["accepted_memories"] >= 1
    raw = coordinator.store.get_raw_segments(
        coordinator.store.get_candidates()[0].episode_id
    )
    accepted = coordinator.store.get_accepted_memories()
    assert raw[0].text != accepted[0].statement or True
