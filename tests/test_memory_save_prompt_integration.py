"""Orchestrator save-vs-session prompt integration."""

from __future__ import annotations

import pytest

from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain import HikariBrain
from tests.test_brain_memory import FakeNeural
from tests.test_brain_v2_write_authority import (
    _asks_save_scope,
    _minimal_orchestrator,
    _teach_long_term,
)


@pytest.fixture
def episode_db(tmp_path):
    from core.brain_v2 import EpisodeStore

    return EpisodeStore(db_path=tmp_path / "save_prompt.db")


def test_declarative_fact_asks_before_save(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    ask = orch.process_input("I prefer Topic A.")
    assert _asks_save_scope(ask)
    assert not episode_db.get_active_accepted_memories(limit=5)


def test_session_only_keeps_fact_out_of_brain_v2(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    orch.process_input("I prefer Topic A.")
    reply = orch.process_input("session only")
    assert "session only" in reply.lower()
    assert not episode_db.get_active_accepted_memories(limit=5)
    recall = orch.process_input("what do I prefer?")
    assert "topic a" in recall.lower()


def test_remember_this_skips_ask(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    reply = orch.process_input("Remember this: I live in City A.")
    assert not _asks_save_scope(reply)
    assert episode_db.get_active_accepted_memories(limit=5)


def test_teach_long_term_helper(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    _teach_long_term(orch, "My name is Owner A.")
    answer = orch.process_input("what is my name?")
    assert "owner a" in answer.lower()
