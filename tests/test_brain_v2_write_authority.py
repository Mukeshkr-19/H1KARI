"""Brain v2 write authority: no unreviewed neural personal writes when Brain v2 is enabled."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from core.brain import BrainAnswer, HikariBrain
from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.memory_review_gate import MemoryReviewGate
from core.brain_v2.neural_conflict_state import CONFLICT_REVIEW_NEEDED_MESSAGE
from core.brain_v2.recall_intent import (
    BRAIN_V2_UNAVAILABLE_MESSAGE,
    is_brain_v2_no_reviewed_memory_answer,
)
from core.brain_v2.schemas import MemoryCandidateStatus
from core.path_literals import EPISODES_DB, HIKARI_MEMORY_DB
from tests.test_brain_memory import FakeNeural


def _minimal_orchestrator(brain_v2: BrainV2Coordinator, brain: HikariBrain):
    from core.orchestrator import HIKARI_Orchestrator
    from core.speaker_context import SpeakerContext

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.legacy_memory_enabled = False
    orch.brain_v2_enabled = True
    orch.brain_v2 = brain_v2
    orch._brain_v2_session = "write-auth-session"
    orch.brain = brain
    orch.speaker = SpeakerContext(primary_user="Owner A")
    orch.emotional_iq = MagicMock()
    orch.emotional_iq.detect_emotion.return_value = {}
    orch.emotional_iq.get_dominant_emotion.return_value = ("neutral", 0.0)
    orch.emotional_iq.log_emotion = MagicMock()
    orch.emotional_iq.adapt_response = MagicMock(side_effect=lambda r, *a, **k: r)
    orch.personality = MagicMock()
    orch.personality.learn_from_interaction = MagicMock()
    orch.personality.format_response = MagicMock(side_effect=lambda r: r)
    orch._check_health = MagicMock()
    orch._mentions_partner_context = MagicMock(return_value=False)
    orch._handle_special_commands = MagicMock(return_value=None)
    orch._normalize_user_input_text = lambda t: (t or "").strip()
    orch._normalize_brain_memory_statement = lambda t: (t or "").strip()
    orch._route_to_agent = MagicMock(return_value=None)
    orch._get_ai_response = MagicMock(return_value="Generic AI reply")
    orch.agents = {"research": MagicMock()}
    orch.brain.remember_turn = MagicMock()
    orch.brain.is_memory_statement = MagicMock(return_value=True)
    orch.brain.remember_fact = MagicMock(return_value=True)

    def _record_turn(u, r, s="chat", *, metadata=None):
        return brain_v2.record_turn(
            "write-auth-session",
            u,
            r or "",
            speaker_label=orch.speaker.current_speaker or "user",
            metadata=metadata,
        )

    orch._record_brain_v2_turn = MagicMock(
        side_effect=_record_turn
    )
    return orch


def _degraded_orchestrator(brain: HikariBrain):
    """Brain v2 policy on but coordinator init failed (fail-closed)."""
    from core.orchestrator import HIKARI_Orchestrator
    from core.speaker_context import SpeakerContext

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.legacy_memory_enabled = False
    orch.brain_v2_enabled = True
    orch.brain_v2 = None
    orch._brain_v2_session = None
    orch.brain = brain
    orch.speaker = SpeakerContext(primary_user="Owner A")
    orch.emotional_iq = MagicMock()
    orch.emotional_iq.detect_emotion.return_value = {}
    orch.emotional_iq.get_dominant_emotion.return_value = ("neutral", 0.0)
    orch.emotional_iq.log_emotion = MagicMock()
    orch.emotional_iq.adapt_response = MagicMock(side_effect=lambda r, *a, **k: r)
    orch.personality = MagicMock()
    orch.personality.learn_from_interaction = MagicMock()
    orch.personality.format_response = MagicMock(side_effect=lambda r: r)
    orch._check_health = MagicMock()
    orch._mentions_partner_context = MagicMock(return_value=False)
    orch._handle_special_commands = MagicMock(return_value=None)
    orch._normalize_user_input_text = lambda t: (t or "").strip()
    orch._normalize_brain_memory_statement = lambda t: (t or "").strip()
    orch._route_to_agent = MagicMock(return_value=None)
    orch._get_ai_response = MagicMock(return_value="Generic AI reply")
    orch.agents = {"research": MagicMock()}
    orch.brain.remember_turn = MagicMock()
    orch.brain.is_memory_statement = MagicMock(return_value=True)
    orch.brain.remember_fact = MagicMock(return_value=True)
    orch.neural_memory_enabled = True
    orch.neural_memory = MagicMock()
    orch.neural_memory.remember = MagicMock()
    orch._record_brain_v2_turn = MagicMock()
    return orch


@pytest.fixture
def episode_db(tmp_path):
    return EpisodeStore(db_path=tmp_path / "write_auth.db")


@pytest.fixture
def neural_db(tmp_path, monkeypatch):
    path = tmp_path / HIKARI_MEMORY_DB
    path.parent.mkdir(parents=True, exist_ok=True)
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
        conn.commit()
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(path))
    return path


def test_safe_owner_location_auto_trusts_brain_v2_without_neural_fact_write(episode_db, neural_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    brain = HikariBrain(FakeNeural([]))
    brain.neural.learn_from_text = MagicMock(return_value={"nodes_created": 1})
    orch = _minimal_orchestrator(coord, brain)

    reply = orch.process_input("Remember this: I live in City A.")

    brain.remember_fact.assert_not_called()
    brain.neural.learn_from_text.assert_not_called()
    assert "remember that in brain v2" in reply.lower()
    assert not episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)
    accepted = episode_db.get_active_accepted_memories(limit=10)
    assert any("city a" in memory.statement.lower() for memory in accepted)


def test_identity_declaration_is_auto_trusted_before_recall(episode_db, neural_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    brain = HikariBrain(FakeNeural([]))
    brain.answer = MagicMock(
        return_value=BrainAnswer(text="Wrong legacy identity.", confidence=0.9)
    )
    orch = _minimal_orchestrator(coord, brain)

    reply = orch.process_input("My name is Owner A.")

    assert "brain v2" in reply.lower()
    assert "remember" in reply.lower()
    assert "do not have a reviewed memory" not in reply.lower()
    brain.answer.assert_not_called()
    brain.remember_fact.assert_not_called()
    answer = orch.process_input("What is my name?")
    assert "owner a" in answer.lower()
    accepted = episode_db.get_active_accepted_memories(limit=10)
    assert any(
        (memory.metadata or {}).get("auto_trusted_owner_assertion")
        and "owner a" in memory.statement.lower()
        for memory in accepted
    )


def test_education_recall_routes_to_brain_v2_without_llm(episode_db, neural_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    brain = HikariBrain(FakeNeural([]))
    brain.answer = MagicMock(
        return_value=BrainAnswer(text="Wrong legacy education.", confidence=0.9)
    )
    orch = _minimal_orchestrator(coord, brain)
    orch._get_ai_response = MagicMock(
        return_value="I'm having trouble thinking right now."
    )

    orch.process_input("I am doing my bachelors in Topic A at School A.")
    answer = orch.process_input("what do I study?")

    assert "topic a" in answer.lower()
    assert "trouble thinking" not in answer.lower()
    brain.answer.assert_not_called()
    orch._get_ai_response.assert_not_called()


def test_current_location_is_available_immediately_in_session(episode_db, neural_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    brain = HikariBrain(FakeNeural([]))
    orch = _minimal_orchestrator(coord, brain)

    reply = orch.process_input("Right now I'm in City B.")
    assert "current location for this session" in reply.lower()
    answer = orch.process_input("where am I now?")
    assert "recent session context" in answer.lower()
    assert "city b" in answer.lower()


def test_conflicting_owner_location_stays_pending(episode_db, neural_db):
    episode_id = episode_db.create_episode("existing-location")
    episode_db.add_turn(episode_id, "I live in City A.", is_user=True)
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    MemoryReviewGate(episode_db).accept(candidates[0].candidate_id)
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    reply = orch.process_input("I live in City B.")
    assert "different reviewed memory" in reply.lower()
    where = orch.process_input("where do I live?")
    assert "city a" in where.lower()
    pending = episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)
    assert any("city b" in candidate.statement.lower() for candidate in pending)


def test_other_person_fact_remains_review_gated(episode_db, neural_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    reply = orch.process_input("My partner Person B studies at School A.")
    assert "extra care" in reply.lower()
    assert not episode_db.get_active_accepted_memories(limit=10)
    pending = episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)
    assert any("person b" in candidate.statement.lower() for candidate in pending)


def test_accepted_personal_recall_writes_no_neural_turn(episode_db, neural_db):
    episode_id = episode_db.create_episode("loc-accept")
    episode_db.add_turn(episode_id, "Remember this: I live in City A.", is_user=True)
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    MemoryReviewGate(episode_db).accept(candidates[0].candidate_id)

    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    brain = HikariBrain(FakeNeural([]))
    brain.answer = MagicMock(
        return_value=BrainAnswer(text="You live in City B.", confidence=0.9)
    )
    orch = _minimal_orchestrator(coord, brain)

    reply = orch.process_input("where do I live?")
    assert "city a" in reply.lower()
    brain.answer.assert_not_called()
    brain.remember_turn.assert_not_called()


def test_missing_reviewed_personal_recall_writes_no_neural(episode_db, neural_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    brain = HikariBrain(FakeNeural([]))
    brain.answer = MagicMock(
        return_value=BrainAnswer(text="You live in City B.", confidence=0.9)
    )
    orch = _minimal_orchestrator(coord, brain)

    reply = orch.process_input("where do I live?")
    assert is_brain_v2_no_reviewed_memory_answer(reply)
    brain.answer.assert_not_called()
    brain.remember_turn.assert_not_called()


@pytest.mark.parametrize(
    "user_input",
    [
        "I feel lonely today",
        "My birthday is May 25.",
        "My partner is sick today.",
    ],
)
def test_ordinary_personal_disclosure_writes_no_legacy_neural(
    episode_db, user_input: str
):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    brain = HikariBrain(FakeNeural([]))
    brain.answer = MagicMock(
        return_value=BrainAnswer(text="Stale neural.", confidence=0.9)
    )
    orch = _minimal_orchestrator(coord, brain)

    orch.process_input(user_input)
    brain.remember_turn.assert_not_called()
    brain.remember_fact.assert_not_called()


def test_conflict_or_no_reviewed_personal_recall_writes_no_neural_turn(
    episode_db, neural_db, monkeypatch
):
    """Current-location query must not write to legacy neural regardless of conflict path."""
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(episode_db.db_path))
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(neural_db))
    with sqlite3.connect(neural_db) as conn:
        conn.execute(
            "INSERT INTO nodes (node_type, name, content) VALUES (?, ?, ?)",
            ("LOCATION", "City C", "currently in City C"),
        )
        conn.commit()

    episode_id = episode_db.create_episode("conflict-loc")
    episode_db.add_turn(episode_id, "Remember this: I live in City A.", is_user=True)
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    MemoryReviewGate(episode_db).accept(candidates[0].candidate_id)

    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    brain = HikariBrain(FakeNeural([]))
    brain.answer = MagicMock(
        return_value=BrainAnswer(text="You are in City C.", confidence=0.9)
    )
    orch = _minimal_orchestrator(coord, brain)

    reply = orch.process_input("where am I right now?")
    assert reply == CONFLICT_REVIEW_NEEDED_MESSAGE or is_brain_v2_no_reviewed_memory_answer(
        reply
    )
    brain.answer.assert_not_called()
    brain.remember_turn.assert_not_called()


def test_coordinator_init_failure_personal_recall_fail_closed():
    brain = HikariBrain(FakeNeural([]))
    brain.answer = MagicMock(
        return_value=BrainAnswer(text="Legacy neural location.", confidence=0.9)
    )
    orch = _degraded_orchestrator(brain)

    reply = orch.process_input("where do I live?")

    assert BRAIN_V2_UNAVAILABLE_MESSAGE in reply
    brain.answer.assert_not_called()
    brain.remember_turn.assert_not_called()
    brain.remember_fact.assert_not_called()
    orch.neural_memory.remember.assert_not_called()


@pytest.mark.parametrize(
    "query",
    [
        "whats my name?",
        "what is my official name?",
        "does my brother love me?",
        "are my parents okay?",
    ],
)
def test_policy_on_never_calls_brain_answer_even_for_non_matched_general(
    episode_db, query: str
):
    """Categorical quarantine: brain.answer is not a fallback when Brain v2 policy is on."""
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    brain = HikariBrain(FakeNeural([]))
    brain.answer = MagicMock(
        return_value=BrainAnswer(text="Legacy neural should never run.", confidence=0.9)
    )
    orch = _minimal_orchestrator(coord, brain)
    orch.process_input(query)
    brain.answer.assert_not_called()


@pytest.mark.parametrize(
    "query",
    [
        "what is my birthday?",
        "where do I work?",
        "which university do I attend?",
        "what is my legal name?",
        "where was I born?",
        "do I have siblings?",
    ],
)
def test_missing_factual_personal_never_calls_brain_or_ai(episode_db, query: str):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    brain = HikariBrain(FakeNeural([]))
    brain.answer = MagicMock(
        return_value=BrainAnswer(text="Legacy invented fact.", confidence=0.9)
    )
    orch = _minimal_orchestrator(coord, brain)
    orch._get_ai_response = MagicMock(return_value="General AI invented fact.")
    orch._route_to_agent = MagicMock(return_value=None)

    reply = orch.process_input(query)

    brain.answer.assert_not_called()
    orch._get_ai_response.assert_not_called()
    orch._route_to_agent.assert_not_called()
    assert "invented" not in (reply or "").lower()


def test_stale_personality_json_file_unchanged_under_brain_v2(tmp_path, monkeypatch):
    import json
    import time

    from core.personality import PersonalityEngine

    legacy_dir = tmp_path / "legacy-data"
    legacy_dir.mkdir(parents=True)
    monkeypatch.setenv("HIKARI_LEGACY_DATA_DIR", str(legacy_dir))
    profile_path = legacy_dir / "personality_profile.json"
    original = {
        "traits": {"formality": 0.5, "verbosity": 0.5, "humor": 0.5},
        "user_prefs": {
            "name": "Stale Legacy Name",
            "favorite_topics": ["stale preference topic"],
            "health_concerns": ["stale health note"],
            "language": "en",
            "timezone": None,
            "communication_style": "friendly",
            "stress_triggers": [],
            "mood_boosters": [],
        },
        "interaction_count": 3,
    }
    profile_path.write_text(json.dumps(original), encoding="utf-8")
    before_mtime = profile_path.stat().st_mtime
    before_bytes = profile_path.read_bytes()

    pers = PersonalityEngine()
    pers.quarantine_loaded_personal_prefs()
    pers.learn_from_interaction("My name is Owner A", "", store_personal_facts=False)

    after_bytes = profile_path.read_bytes()
    assert after_bytes == before_bytes
    assert profile_path.stat().st_mtime == before_mtime


def test_stale_personality_json_not_rewritten_under_brain_v2(tmp_path, monkeypatch):
    import json

    from core.personality import PersonalityEngine

    legacy_dir = tmp_path / "legacy-data"
    legacy_dir.mkdir(parents=True)
    monkeypatch.setenv("HIKARI_LEGACY_DATA_DIR", str(legacy_dir))
    profile_path = legacy_dir / "personality_profile.json"
    stale_prefs = {
        "name": "Stale Legacy Name",
        "language": "en",
        "timezone": None,
        "favorite_topics": ["stale preference topic"],
        "communication_style": "friendly",
        "health_concerns": ["stale health note"],
        "stress_triggers": [],
        "mood_boosters": [],
    }
    profile_path.write_text(
        json.dumps(
            {
                "traits": {"formality": 0.5, "verbosity": 0.5, "humor": 0.5},
                "user_prefs": stale_prefs,
                "interaction_count": 3,
            }
        ),
        encoding="utf-8",
    )

    pers = PersonalityEngine()
    pers.quarantine_loaded_personal_prefs()
    assert pers.user_prefs.get("name") is None
    pers.learn_from_interaction(
        "My name is Owner A",
        "",
        store_personal_facts=False,
    )

    saved = json.loads(profile_path.read_text())
    assert saved["user_prefs"]["name"] == "Stale Legacy Name"
    assert saved["user_prefs"]["favorite_topics"] == ["stale preference topic"]
    assert saved["user_prefs"]["health_concerns"] == ["stale health note"]


def test_coordinator_init_failure_remember_this_blocks_legacy_writes():
    brain = HikariBrain(FakeNeural([]))
    brain.neural.learn_from_text = MagicMock(return_value={"nodes_created": 1})
    orch = _degraded_orchestrator(brain)

    reply = orch.process_input("Remember this: I live in City A.")

    assert BRAIN_V2_UNAVAILABLE_MESSAGE in reply
    brain.remember_fact.assert_not_called()
    brain.neural.learn_from_text.assert_not_called()
    orch.neural_memory.remember.assert_not_called()
