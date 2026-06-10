"""Orchestrator save-vs-session prompt integration."""

from __future__ import annotations

import pytest

from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.schemas import MemoryCandidateStatus
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


def test_core_preference_auto_saves_without_ask(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    reply = orch.process_input("I prefer Topic A.")
    assert not _asks_save_scope(reply)
    assert episode_db.get_active_accepted_memories(limit=5)


def test_third_party_education_queues_without_save_scope_ask(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    reply = orch.process_input("My partner Person B studies at School A.")
    assert not _asks_save_scope(reply)
    assert "review" in reply.lower() or "noted" in reply.lower()
    assert not episode_db.get_active_accepted_memories(limit=5)


def test_trip_city_stays_session_not_brain_v2(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    reply = orch.process_input("I am in City B.")
    assert not _asks_save_scope(reply)
    assert "session" in reply.lower()
    assert not episode_db.get_active_accepted_memories(limit=5)


def test_uncertain_declaration_stays_episode_only(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    reply = orch.process_input("I might move to City C next year.")
    assert not _asks_save_scope(reply)
    assert not episode_db.get_active_accepted_memories(limit=5)
    assert not episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)


def test_casual_filler_is_deterministic_episode_only(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    reply = orch.process_input("haha okay")
    assert reply == "Got it."
    assert not orch._get_ai_response.called
    assert not episode_db.get_active_accepted_memories(limit=5)
    assert not episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)


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
