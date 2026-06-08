"""Retrieval must honor accepted-memory lifecycle (generic fixtures only)."""

from __future__ import annotations

import pytest

from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.memory_lifecycle import (
    CORRECTION_SOURCE_OPERATOR,
    LIFECYCLE_RETIRED,
    lifecycle_status,
)
from core.brain_v2.memory_repair import MemoryRepairGate
from core.brain_v2.memory_review_gate import MemoryReviewGate
from core.brain_v2.profile_summary import format_reviewed_profile_answer
from core.brain_v2.retrieval import BrainV2Retrieval
from core.brain_v2.schemas import StructuredEpisode
from core.brain_v2.working_memory import WorkingMemory
from core.path_literals import EPISODES_DB


def _accept(store: EpisodeStore, statement: str, episode_key: str = "ep") -> str:
    episode_id = store.create_episode(episode_key)
    store.add_turn(episode_id, statement, is_user=True)
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    linked = MemoryReviewGate(store).accept(candidates[0].candidate_id)
    return linked.memory_id


def _retrieval(store: EpisodeStore) -> BrainV2Retrieval:
    return BrainV2Retrieval(
        store, WorkingMemory(), neural_bridge=None, allow_neural_procedural=False
    )


def test_retired_fact_absent_from_semantic_and_episodic_layers(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    mid = _accept(store, "Owner A lives in City A.")
    MemoryRepairGate(store).retire(mid)
    packet = _retrieval(store).retrieve("where does Owner A live?")
    blob = " ".join(h.text.lower() for h in packet.hits)
    assert "city a" not in blob


def test_superseded_old_absent_active_correction_returned(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    old_id = _accept(store, "Owner A lives in City A.")
    _, new = MemoryRepairGate(store).supersede(
        old_id, statement="Owner A lives in City B.", candidate_type="location"
    )
    packet = _retrieval(store).retrieve("where does Owner A live?")
    blob = " ".join(h.text.lower() for h in packet.hits)
    assert "city b" in blob
    assert "city a" not in blob
    assert (new.metadata or {}).get("correction_source") == CORRECTION_SOURCE_OPERATOR


def test_newer_retired_row_does_not_hide_older_active_at_limit_one(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    older_id = _accept(
        store,
        "Remember this: Owner A prefers Restaurant A.",
        episode_key="older",
    )
    newer_id = _accept(
        store,
        "Remember this: Owner A prefers Restaurant B.",
        episode_key="newer",
    )
    MemoryRepairGate(store).retire(newer_id)
    active = store.get_active_accepted_memories(limit=1)
    assert len(active) == 1
    assert active[0].memory_id == older_id


def test_retired_long_statement_episode_excluded_despite_truncated_summary(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    long_stmt = (
        "Remember this: Owner A has a detailed preference for Restaurant A "
        "in City A downtown district near School A."
    )
    episode_id = store.create_episode("trunc-ep")
    store.add_turn(episode_id, long_stmt, is_user=True)
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    linked = MemoryReviewGate(store).accept(candidates[0].candidate_id)
    structured = store.get_structured_episode(episode_id)
    assert structured is not None
    truncated_summary = (structured.summary or long_stmt)[:55]
    store.save_structured_episode(
        StructuredEpisode(
            episode_id=structured.episode_id,
            session_id=structured.session_id,
            lifecycle_state=structured.lifecycle_state,
            title=structured.title or "Episode",
            summary=truncated_summary,
            action_items=structured.action_items,
            events=structured.events,
            segment_count=structured.segment_count,
            started_at=structured.started_at,
            ended_at=structured.ended_at,
            metadata=structured.metadata,
        )
    )
    MemoryRepairGate(store).retire(linked.memory_id)
    packet = _retrieval(store).retrieve(
        "Owner A Restaurant A downtown City A preference detailed"
    )
    episodic = [h for h in packet.hits if h.source == "structured_episode"]
    assert not episodic
    blob = " ".join(h.text.lower() for h in packet.hits)
    assert "restaurant a" not in blob
    assert "city a" not in blob


def test_profile_summary_uses_active_truth_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    mid = _accept(store, "Remember this: I live in City A.")
    MemoryRepairGate(store).retire(mid)
    assert not store.get_active_accepted_memories(limit=20)
    summary = format_reviewed_profile_answer(
        store.get_active_accepted_memories(limit=20)
    )
    assert "city a" not in (summary or "").lower()
