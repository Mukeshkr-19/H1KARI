"""Stale neural conflict suppression during recall (generic fixtures only)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.memory_review_gate import MemoryReviewGate
from core.brain_v2.neural_conflict_state import CONFLICT_REVIEW_NEEDED_MESSAGE
from core.brain_v2.profile_summary import build_merged_user_profile_answer
from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.retrieval import BrainV2Retrieval
from core.brain_v2.working_memory import WorkingMemory
from core.path_literals import EPISODES_DB, HIKARI_MEMORY_DB


def test_production_retrieval_disables_neural_conflict_reads(tmp_path):
    store = EpisodeStore(db_path=tmp_path / "flag-test.db")
    coord = BrainV2Coordinator(
        store=store,
        neural_bridge=None,
        allow_neural_procedural=False,
        allow_neural_conflict_reads=False,
    )
    assert coord.retrieval.allow_neural_conflict_reads is False


def _seed_neural_stale_home(path: Path, *, home_name: str = "City C") -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY,
                node_type TEXT NOT NULL,
                name TEXT NOT NULL,
                content TEXT,
                is_archived INTEGER DEFAULT 0
            );
            """
        )
        conn.execute(
            "INSERT INTO nodes (node_type, name, content) VALUES (?, ?, ?)",
            ("LOCATION", home_name, "legacy home"),
        )
        conn.commit()


def test_reviewed_location_overrides_stale_neural(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_neural_stale_home(neural_db)
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(neural_db))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("loc-ep")
    store.add_turn(episode_id, "Remember this: I live in City A.", is_user=True)
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    MemoryReviewGate(store).accept(candidates[0].candidate_id)
    retrieval = BrainV2Retrieval(
        store, WorkingMemory(), neural_bridge=None, allow_neural_procedural=False
    )
    reply = retrieval.answer_from_accepted("where do I live?")
    assert reply
    assert "city a" in reply.lower()
    assert "city c" not in reply.lower()


def test_unreviewed_legacy_home_does_not_trigger_review_needed(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_neural_stale_home(neural_db)
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(neural_db))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    retrieval = BrainV2Retrieval(
        store,
        WorkingMemory(),
        neural_bridge=None,
        allow_neural_procedural=False,
        allow_neural_conflict_reads=False,
    )
    reply = retrieval.answer_from_accepted("where do I live?")
    assert reply != CONFLICT_REVIEW_NEEDED_MESSAGE
    assert "have a reviewed memory" in (reply or "").lower()


def test_explicit_conflict_reads_may_surface_review_needed(tmp_path, monkeypatch):
    """Optional conflict-read path (not used in production chat) can block recall."""
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("current-cli")
    store.add_turn(episode_id, "Remember this: I live in City A.", is_user=True)
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    MemoryReviewGate(store).accept(candidates[0].candidate_id)
    neural_summary = "What I know about you:\n- Home: City C\n- Currently in: City C\n"
    retrieval = BrainV2Retrieval(
        store,
        WorkingMemory(),
        neural_bridge=None,
        allow_neural_procedural=False,
        allow_neural_conflict_reads=True,
    )
    monkeypatch.setattr(
        "core.brain_v2.legacy_reconciliation.fetch_neural_summary_readonly",
        lambda: (neural_summary, True),
    )
    reply = retrieval.answer_from_accepted("where am I right now?")
    assert reply == CONFLICT_REVIEW_NEEDED_MESSAGE


def test_contradictory_reviewed_location_does_not_return_stale_neural(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_neural_stale_home(neural_db)
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(neural_db))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("loc-ep")
    store.add_turn(episode_id, "Remember this: I live in City A.", is_user=True)
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    MemoryReviewGate(store).accept(candidates[0].candidate_id)
    retrieval = BrainV2Retrieval(
        store, WorkingMemory(), neural_bridge=None, allow_neural_procedural=False
    )
    reply = retrieval.answer_from_accepted("where do I live?")
    assert reply
    assert "city a" in reply.lower()
    assert reply != CONFLICT_REVIEW_NEEDED_MESSAGE


def test_profile_merge_suppresses_conflicting_neural_line(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_neural_stale_home(neural_db)
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(neural_db))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("profile-ep")
    store.add_turn(episode_id, "Remember this: I live in City A.", is_user=True)
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    linked = MemoryReviewGate(store).accept(candidates[0].candidate_id)
    memories = store.get_active_accepted_memories(limit=10)
    neural_summary = "What I know about you:\n- Home: City C\n"
    merged = build_merged_user_profile_answer(memories, neural_summary)
    assert merged
    assert "city a" in merged.lower()
    assert "city c" not in merged.lower()
    assert linked.memory_id
