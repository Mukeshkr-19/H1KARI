"""Clean-room Brain v2 contract tests (fake fixtures only)."""

from __future__ import annotations

import pytest

from core.brain_statements import (
    classify_task_action_kind,
    is_declarative_memory_statement,
    is_task_or_action_statement,
)
from core.brain_v2.schemas import MemoryCandidateStatus
from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.location_phrases import has_owner_presence_anchor
from core.brain_v2.memory_type import infer_memory_type
from core.brain_v2.retrieval import BrainV2Retrieval
from core.brain_v2.session_context import register_session_place_provider
from core.brain_v2.working_memory import WorkingMemory
from core.brain import HikariBrain
from tests.test_brain_memory import FakeNeural
from tests.test_brain_v2_write_authority import _minimal_orchestrator, _teach_long_term


@pytest.fixture
def episode_db(tmp_path):
    from core.brain_v2 import EpisodeStore

    return EpisodeStore(db_path=tmp_path / "clean_room.db")


@pytest.fixture(autouse=True)
def reset_session_place_provider():
    register_session_place_provider(None)
    yield
    register_session_place_provider(None)


@pytest.mark.parametrize(
    "phrase",
    [
        "I am in City A",
        "I'm in City A",
        "im in City A",
    ],
)
def test_im_in_city_variants(phrase: str):
    assert has_owner_presence_anchor(phrase)
    assert is_declarative_memory_statement(phrase)
    inferred = infer_memory_type(phrase)
    assert inferred.candidate_type == "current_location"
    assert inferred.metadata.get("current_location", "").lower() == "city a"


@pytest.mark.parametrize(
    "phrase",
    [
        "remind me to call Person C tomorrow",
        "open the settings panel",
        "write code for Topic A",
        "schedule my meeting with Person C",
    ],
)
def test_task_action_not_declarative_memory(phrase: str):
    assert is_task_or_action_statement(phrase)
    assert not is_declarative_memory_statement(phrase)


def test_task_action_not_stored_as_candidate(episode_db):
    episode_id = episode_db.create_episode("task")
    episode_db.add_turn(episode_id, "remind me to call Person C tomorrow", is_user=True)
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    assert not candidates


@pytest.mark.parametrize(
    "phrase,kind,expected_fragment",
    [
        (
            "remind me to call Person C tomorrow",
            "reminder",
            "not scheduled yet",
        ),
        (
            "schedule my meeting with Person C",
            "schedule",
            "calendar scheduling is not wired up yet",
        ),
        (
            "write code for Topic A",
            "code",
            "coding task request",
        ),
    ],
)
def test_task_action_gets_deterministic_no_memory_reply(
    episode_db, phrase: str, kind: str, expected_fragment: str
):
    assert classify_task_action_kind(phrase) == kind
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    orch._route_to_agent.return_value = None
    orch._get_ai_response.return_value = "I'm having trouble thinking right now."

    reply = orch.process_input(phrase)

    low = reply.lower()
    assert "will not store that as a brain v2 memory" in low
    assert expected_fragment in low
    assert "trouble thinking" not in low
    assert not episode_db.get_active_accepted_memories(limit=10)
    assert not episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)


def test_task_phrases_create_no_brain_v2_candidates(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    orch._route_to_agent.return_value = None

    for phrase in (
        "remind me to call Person C tomorrow",
        "schedule my meeting with Person C",
        "write code for Topic A",
    ):
        orch.process_input(phrase)

    assert not episode_db.get_active_accepted_memories(limit=10)
    assert not episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)


def test_task_phrases_absent_from_what_do_you_remember(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    orch._route_to_agent.return_value = None

    orch.process_input("remind me to call Person C tomorrow")
    orch.process_input("schedule my meeting with Person C")
    orch.process_input("write code for Topic A")
    coord.ingest_trusted_owner_declaration(
        "sess-owner",
        "Remember this: My name is Owner A.",
    )

    summary = orch.process_input("what do you remember?")
    low = summary.lower()
    assert "owner a" in low
    assert "remind" not in low
    assert "schedule" not in low
    assert "topic a" not in low


def test_explicit_future_plan_is_not_blocked_as_task_action(episode_db):
    phrase = "Remember this: I will meet Person C for lunch tomorrow."
    assert not is_task_or_action_statement(phrase)

    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    orch._route_to_agent.return_value = None

    reply = orch.process_input(phrase)
    assert "got it" in reply.lower()

    accepted = episode_db.get_active_accepted_memories(limit=10)
    assert any("meet person c" in mem.statement.lower() for mem in accepted)

    plan_reply = orch.process_input("what are my plans tomorrow?")
    assert "person c" in plan_reply.lower()
    assert "lunch" in plan_reply.lower()


def test_open_task_routes_to_agent_without_brain_v2_memory(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    orch._route_to_agent.return_value = "Opening the settings panel..."
    orch._get_ai_response.return_value = "I'm having trouble thinking right now."

    reply = orch.process_input("open the settings panel")

    assert "opening" in reply.lower()
    assert "trouble thinking" not in reply.lower()
    assert "will not store that as a brain v2 memory" not in reply.lower()
    assert not episode_db.get_active_accepted_memories(limit=10)


def test_identity_merge_preferred_and_legal(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    coord.ingest_trusted_owner_declaration(
        "sess-id",
        "Remember this: My name is Owner A but official name is Person C.",
    )
    coord.ingest_trusted_owner_declaration("sess-id", "you can call me Person C")
    retrieval = BrainV2Retrieval(episode_db, coord.working)
    answer = retrieval.answer_from_accepted("what is my name?")
    assert answer == "Your name is Person C."

    official = retrieval.answer_from_accepted("what is my official name?")
    assert official == "Your official name is Person C."


def test_identity_prefers_casual_name_but_keeps_real_name(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    coord.ingest_trusted_owner_declaration(
        "sess-id",
        "My name is Owner A but you can call me Person C.",
    )
    retrieval = BrainV2Retrieval(episode_db, coord.working)

    assert retrieval.answer_from_accepted("whats my name?") == "Your name is Person C."
    assert retrieval.answer_from_accepted("whats my real name?") == "Your real name is Owner A."


def test_person_c_is_my_sister_auto_accept_and_recall(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    reply = _teach_long_term(orch, "Person C is my sister")
    assert "got it" in reply.lower()

    answer = orch.process_input("who is my sister?")
    assert "person c" in answer.lower()


def test_bare_my_name_is_auto_trusted(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    reply = _teach_long_term(orch, "My name is Owner A.")
    assert "got it" in reply.lower()
    answer = orch.process_input("what is my name?")
    assert "owner a" in answer.lower()


def test_bare_preference_auto_trusted(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    reply = _teach_long_term(orch, "I prefer Topic A.")
    assert "got it" in reply.lower()
    profile = orch.process_input("what do you know about me?")
    assert "topic a" in profile.lower()


def test_bare_dislike_auto_trusted(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    reply = _teach_long_term(orch, "I don't like Topic B.")
    assert "got it" in reply.lower()
    profile = orch.process_input("what do you know about me?")
    assert "topic b" in profile.lower()


def test_bare_education_auto_trusted(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    reply = _teach_long_term(orch, "I study at School A.")
    assert "got it" in reply.lower()
    profile = orch.process_input("what do you know about me?")
    assert "school a" in profile.lower()


def test_guest_restore_owner_session_location(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    orch.process_input("I am in City B.")
    orch.process_input("I am Guest B talking to you now")
    guest_where = orch.process_input("where am I now?")
    assert "city b" not in guest_where.lower()

    reset = orch.process_input("back to owner")
    assert "back to you" in reset.lower()
    owner_where = orch.process_input("where am I now?")
    assert "city b" in owner_where.lower()


def test_guest_declarative_not_stored_in_owner_brain(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    orch.process_input("I am Guest B talking to you now")
    reply = orch.process_input("My name is Guest B.")
    assert "will not" in reply.lower() or "not store" in reply.lower()
    assert not episode_db.get_active_accepted_memories(limit=20)


def test_what_do_you_remember_uses_brain_v2_only(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    orch.process_input("Remember this: I live in City A.")
    summary = orch.process_input("what do you remember?")
    low = summary.lower()
    assert "city a" in low
    assert "neural" not in low
    assert "recent conversations" not in low


def test_guest_weather_does_not_leak_owner_session_city(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    register_session_place_provider(
        lambda: coord.working.get_current_location()[0]
        if coord.working.get_current_location()
        else None,
        guest_is_active=orch.speaker.is_guest_speaker,
    )

    orch.process_input("I am in City B.")
    orch.process_input("I am Guest B talking to you now")

    from agents.research import ResearchAgent

    agent = ResearchAgent(eager_legacy_brain=False)
    result = agent.handle("whats the weather in the city im in now")
    low = (result or "").lower()
    assert "city b" not in low
    assert "which city" in low or "api key" in low or "weather" in low
