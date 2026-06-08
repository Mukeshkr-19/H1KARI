"""Brain v2 candidate scoring and duplicate-safe accept."""

from __future__ import annotations

import pytest

from core.brain_v2.candidate_scoring import annotate_and_rank_candidates, normalize_statement
from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.memory_review_gate import MemoryReviewGate
from core.brain_v2.retrieval import BrainV2Retrieval
from core.brain_v2.schemas import MemoryCandidate, MemoryCandidateStatus


def test_high_signal_candidate_ranks_above_low_signal(episode_db):
    from core.brain_v2.candidate_quality import QUALITY_KEEP, QUALITY_WEAK

    low = MemoryCandidate(
        candidate_id="low-1",
        episode_id="ep-1",
        statement="I think something happened yesterday.",
        confidence=0.4,
        salience=0.35,
        source_segment_ids=["s1"],
        metadata={"quality_label": QUALITY_WEAK},
    )
    high = MemoryCandidate(
        candidate_id="high-1",
        episode_id="ep-1",
        statement="Remember this: I prefer local-first private tools.",
        confidence=0.88,
        salience=0.88,
        source_segment_ids=["s2"],
        metadata={"quality_label": QUALITY_KEEP, "explicit_remember": True},
    )
    ranked = annotate_and_rank_candidates([low, high])
    assert ranked[0].candidate_id == "high-1"
    assert float((ranked[0].metadata or {})["rank_score"]) > float(
        (ranked[1].metadata or {})["rank_score"]
    )


def test_duplicate_candidates_marked_not_deleted():
    import uuid

    episode_id = "ep-dup"
    base = "I live in City B."
    raw = [
        MemoryCandidate(
            candidate_id=str(uuid.uuid4()),
            episode_id=episode_id,
            statement=base,
            confidence=0.8,
            salience=0.75,
            source_segment_ids=["a"],
        ),
        MemoryCandidate(
            candidate_id=str(uuid.uuid4()),
            episode_id=episode_id,
            statement="I live in city b.",
            confidence=0.7,
            salience=0.7,
            source_segment_ids=["b"],
        ),
    ]
    candidates = annotate_and_rank_candidates(raw)
    assert normalize_statement(candidates[0].statement) == normalize_statement(
        candidates[1].statement
    )
    assert (candidates[0].metadata or {}).get("duplicate_primary") is True
    assert (candidates[1].metadata or {}).get("duplicate_of") == candidates[0].candidate_id


def test_accept_duplicate_does_not_create_many_source_linked_memories(episode_db):
    episode_id = episode_db.create_episode("accept-dup")
    episode_db.add_turn(episode_id, "I live in City B.")
    episode_db.add_turn(episode_id, "I live in City B.")
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    gate = MemoryReviewGate(episode_db)
    for c in candidates:
        gate.accept(c.candidate_id)
    accepted = episode_db.get_accepted_memories()
    assert len(accepted) == 1


def test_rejected_and_pending_not_in_retrieval(episode_db):
    episode_id = episode_db.create_episode("rej-sess")
    episode_db.add_turn(episode_id, "My dad Rowan lives in Lake Town.")
    episode_db.add_turn(episode_id, "My mom Lina lives in Lake Town.")
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    gate = MemoryReviewGate(episode_db)

    reject_stmt = None
    accept_stmt = None
    for c in candidates:
        if "dad" in c.statement.lower() or "rowan" in c.statement.lower():
            gate.reject(c.candidate_id)
            reject_stmt = c.statement
        elif "mom" in c.statement.lower() or "lina" in c.statement.lower():
            gate.accept(c.candidate_id)
            accept_stmt = c.statement

    retrieval = BrainV2Retrieval(episode_db)
    packet = retrieval.retrieve("where does my mom live?")
    blob = " ".join(h.text.lower() for h in packet.hits)
    if accept_stmt:
        assert "lina" in blob or "lake town" in blob
    semantic = " ".join(h.text for h in packet.hits if h.layer == "semantic").lower()
    if reject_stmt:
        assert "rowan" not in semantic
        assert "dad" not in semantic or "lina" in semantic

    pending = episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)
    for p in pending:
        assert normalize_statement(p.statement) not in normalize_statement(semantic)


@pytest.fixture
def episode_db(tmp_path):
    from core.brain_v2 import EpisodeStore

    return EpisodeStore(db_path=tmp_path / "scoring_v2.db")


@pytest.fixture
def coordinator(episode_db):
    from core.brain_v2 import BrainV2Coordinator

    return BrainV2Coordinator(store=episode_db)
