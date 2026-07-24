"""Candidate queue hygiene — no durable truth from session noise (generic fixtures)."""

from __future__ import annotations

import pytest

from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.memory_review_gate import MemoryReviewGate
from core.brain_v2.schemas import MemoryCandidateStatus
from core.path_literals import EPISODES_DB


def test_temporary_speaker_intro_not_in_pending_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("guest-intro")
    store.add_turn(
        episode_id,
        "I am Guest B talking to you now.",
        is_user=True,
        speaker_label="Guest B",
    )
    EpisodeConsolidationPipeline(store).process_episode(episode_id)
    pending = store.get_candidates(status=MemoryCandidateStatus.PENDING)
    assert not pending


def test_general_explanation_request_not_in_pending_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("ordinary-command")
    store.add_turn(
        episode_id,
        "Explain why the sky is blue in one short sentence.",
        is_user=True,
    )
    EpisodeConsolidationPipeline(store).process_episode(episode_id)
    assert not store.get_candidates(status=MemoryCandidateStatus.PENDING)


def test_negated_remember_request_not_in_pending_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("negated-memory")
    store.add_turn(
        episode_id,
        "No, don't remember that; there is nothing to remember.",
        is_user=True,
    )
    EpisodeConsolidationPipeline(store).process_episode(episode_id)
    assert not store.get_candidates(status=MemoryCandidateStatus.PENDING)


def test_durable_remember_fact_enters_pending_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("durable")
    store.add_turn(
        episode_id,
        "Remember this: Owner A studies at School A.",
        is_user=True,
    )
    EpisodeConsolidationPipeline(store).process_episode(episode_id)
    pending = store.get_candidates(status=MemoryCandidateStatus.PENDING)
    assert pending
    assert any("school a" in (c.statement or "").lower() for c in pending)


def test_prefix_accept_no_promote_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("accept-prefix")
    store.add_turn(
        episode_id,
        "Remember this: Owner A prefers Restaurant A.",
        is_user=True,
    )
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    assert candidates
    prefix = candidates[0].candidate_id[:8]
    linked = MemoryReviewGate(store).accept(prefix)
    assert linked.candidate_id == candidates[0].candidate_id
    assert linked.neural_node_key is None
