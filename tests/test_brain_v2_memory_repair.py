"""Brain v2 accepted-memory repair lifecycle (generic fixtures only)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.memory_lifecycle import CORRECTION_SOURCE_OPERATOR
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.memory_lifecycle import LIFECYCLE_RETIRED, LIFECYCLE_SUPERSEDED, lifecycle_status
from core.brain_v2.memory_repair import MemoryRepairGate
from core.brain_v2.memory_review_gate import MemoryReviewGate
from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.retrieval import BrainV2Retrieval
from core.brain_v2.schemas import MemoryCandidate, MemoryCandidateStatus, SourceLinkedMemory
from core.brain_v2.working_memory import WorkingMemory
from core.path_literals import EPISODES_DB


def _accept_statement(store: EpisodeStore, statement: str, episode_key: str = "ep") -> str:
    episode_id = store.create_episode(episode_key)
    store.add_turn(episode_id, statement, is_user=True)
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    assert candidates
    linked = MemoryReviewGate(store).accept(candidates[0].candidate_id)
    return linked.memory_id


def test_retire_removes_from_active_recall_preserves_row(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    memory_id = _accept_statement(store, "Owner A lives in City A.")
    repair = MemoryRepairGate(store)
    retired = repair.retire(memory_id, reason="test_retire")
    assert lifecycle_status(retired.metadata) == LIFECYCLE_RETIRED
    assert store.get_source_linked_memory(memory_id) is not None
    assert not store.get_active_accepted_memories(limit=20)
    retrieval = BrainV2Retrieval(store, WorkingMemory(), neural_bridge=None, allow_neural_procedural=False)
    reply = retrieval.answer_from_accepted("where do I live?")
    assert reply is None or "city a" not in (reply or "").lower()


def test_supersede_returns_only_corrected_active_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    old_id = _accept_statement(store, "Owner A lives in City A.")
    repair = MemoryRepairGate(store)
    old, new = repair.supersede(
        old_id,
        statement="Owner A lives in City B.",
        candidate_type="location",
    )
    assert lifecycle_status(old.metadata) == LIFECYCLE_SUPERSEDED
    assert (old.metadata or {}).get("superseded_by") == new.memory_id
    assert (new.metadata or {}).get("supersedes") == old_id
    assert not new.source_segment_ids
    assert (new.metadata or {}).get("correction_source") == CORRECTION_SOURCE_OPERATOR
    assert (new.metadata or {}).get("predecessor_evidence_segment_ids") is not None
    active = store.get_active_accepted_memories(limit=20)
    assert len(active) == 1
    assert active[0].memory_id == new.memory_id
    retrieval = BrainV2Retrieval(store, WorkingMemory(), neural_bridge=None, allow_neural_procedural=False)
    reply = retrieval.answer_from_accepted("where do I live?")
    assert reply
    assert "city b" in reply.lower()
    assert "city a" not in reply.lower()


def test_prefix_memory_id_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    memory_id = _accept_statement(store, "Guest B is Person C's sibling.")
    prefix = memory_id[:8]
    repair = MemoryRepairGate(store)
    retired = repair.retire(prefix)
    assert retired.memory_id == memory_id


def test_memory_history_traces_supersession(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    old_id = _accept_statement(store, "Remember this: Owner A lives in City A.")
    repair = MemoryRepairGate(store)
    _old, new = repair.supersede(old_id, statement="Remember this: Owner A lives in City B.")
    chain = repair.memory_history(new.memory_id)
    assert len(chain) >= 2
    assert chain[0].memory_id == new.memory_id
    assert any(m.memory_id == old_id for m in chain)


def test_memory_history_from_original_id_shows_active_successor(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    old_id = _accept_statement(store, "Owner A lives in City A.")
    repair = MemoryRepairGate(store)
    _old, new = repair.supersede(old_id, statement="Owner A lives in City B.")
    chain = repair.memory_history(old_id)
    assert chain[0].memory_id == new.memory_id
    assert any(m.memory_id == old_id for m in chain)


def test_failed_supersede_rolls_back_fully(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    old_id = _accept_statement(store, "Owner A lives in City A.")
    before = store.get_source_linked_memory(old_id)
    assert lifecycle_status(before.metadata) == "active"
    with patch.object(
        store,
        "_persist_source_linked_conn",
        side_effect=RuntimeError("simulated failure"),
    ):
        with pytest.raises(RuntimeError, match="simulated"):
            MemoryRepairGate(store).supersede(
                old_id, statement="Owner A lives in City B."
            )
    after = store.get_source_linked_memory(old_id)
    assert lifecycle_status(after.metadata) == "active"
    assert store.get_active_accepted_memories(limit=5)


def test_active_memory_limit_survives_many_newer_inactive_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    older_id = _accept_statement(
        store, "Remember this: Owner A prefers Restaurant A.", episode_key="keep"
    )
    repair = MemoryRepairGate(store)
    for idx in range(105):
        mid = _accept_statement(
            store,
            f"Remember this: Owner A prefers Restaurant B variant {idx}.",
            episode_key=f"retire-{idx}",
        )
        repair.retire(mid)
    active = store.get_active_accepted_memories(limit=1)
    assert len(active) == 1
    assert active[0].memory_id == older_id


def test_multi_hop_history_from_each_chain_member(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    repair = MemoryRepairGate(store)
    a_id = _accept_statement(store, "Owner A lives in City A.")
    _a, b = repair.supersede(a_id, statement="Owner A lives in City B.")
    _b, c = repair.supersede(b.memory_id, statement="Owner A lives in City C.")
    for start_id in (a_id, b.memory_id, c.memory_id):
        chain = repair.memory_history(start_id)
        ids = {m.memory_id for m in chain}
        assert ids == {a_id, b.memory_id, c.memory_id}
        assert chain[0].memory_id == c.memory_id
        assert chain[-1].memory_id == a_id


def test_unique_prefix_supersede_succeeds(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    memory_id = _accept_statement(store, "Owner A lives in City A.")
    prefix = memory_id[:8]
    assert store.resolve_source_linked_memory_id(prefix) == memory_id
    _old, new = MemoryRepairGate(store).supersede(
        prefix, statement="Owner A lives in City B."
    )
    assert new.memory_id != memory_id
    assert lifecycle_status(store.get_source_linked_memory(memory_id).metadata) == LIFECYCLE_SUPERSEDED


def test_ambiguous_prefix_supersede_fails_without_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("ambig")
    shared = "aaaa1111"
    for suffix, statement in (
        ("-1111-1111-1111-111111111111", "Owner A lives in City A."),
        ("-2222-2222-2222-222222222222", "Owner A lives in City B."),
    ):
        mid = f"{shared}{suffix}"
        cand = MemoryCandidate(
            candidate_id=f"cand-{suffix}",
            episode_id=episode_id,
            statement=statement,
            review_status=MemoryCandidateStatus.ACCEPTED.value,
        )
        store.save_candidates([cand])
        store.save_source_linked_memory(
            SourceLinkedMemory(
                memory_id=mid,
                candidate_id=cand.candidate_id,
                episode_id=episode_id,
                statement=statement,
            )
        )
    with pytest.raises(ValueError, match="Ambiguous"):
        MemoryRepairGate(store).supersede(
            shared, statement="Owner A lives in City C."
        )
    assert lifecycle_status(
        store.get_source_linked_memory(f"{shared}-1111-1111-1111-111111111111").metadata
    ) == "active"
    assert lifecycle_status(
        store.get_source_linked_memory(f"{shared}-2222-2222-2222-222222222222").metadata
    ) == "active"


def test_second_supersede_cannot_branch_from_inactive_predecessor(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    old_id = _accept_statement(store, "Owner A lives in City A.")
    repair = MemoryRepairGate(store)
    repair.supersede(old_id, statement="Owner A lives in City B.")
    with pytest.raises(ValueError, match="not active|successor"):
        repair.supersede(old_id, statement="Owner A lives in City C.")
    active = store.get_active_accepted_memories(limit=10)
    assert len(active) == 1
    assert "city b" in active[0].statement.lower()


def test_accept_no_promote_does_not_require_neural(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    coord = BrainV2Coordinator(store=store, neural_bridge=None, allow_neural_procedural=False)
    episode_id = store.create_episode("no-neural")
    store.add_turn(episode_id, "Remember this: Owner A prefers Restaurant A.", is_user=True)
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    linked, promo = coord.accept_candidate(candidates[0].candidate_id, promote=False)
    assert linked.memory_id
    assert promo is None
    assert linked.neural_node_key is None


def test_pending_never_active_truth(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("pending-only")
    store.add_turn(episode_id, "Remember this: Owner A will visit City B next week.", is_user=True)
    EpisodeConsolidationPipeline(store).process_episode(episode_id)
    pending = store.get_candidates(status=MemoryCandidateStatus.PENDING)
    assert pending
    retrieval = BrainV2Retrieval(store, WorkingMemory(), neural_bridge=None, allow_neural_procedural=False)
    packet = retrieval.retrieve("where will Owner A travel?")
    blob = " ".join(h.text.lower() for h in packet.hits)
    assert "city b" not in blob or "reviewed memory" not in blob
