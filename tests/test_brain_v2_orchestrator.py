"""Orchestrator Brain v2 integration guards."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from core.brain import BrainAnswer, HikariBrain
from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.memory_review_gate import MemoryReviewGate
from core.brain_v2.neural_conflict_state import CONFLICT_REVIEW_NEEDED_MESSAGE
from core.brain_v2.recall_intent import is_positive_brain_v2_recall_answer


def test_brain_v2_coordinator_init_failure_leaves_runtime_unavailable(monkeypatch):
    monkeypatch.delenv("HIKARI_DISABLE_BRAIN_V2", raising=False)
    monkeypatch.setattr(
        "core.brain_service.BrainV2Coordinator",
        MagicMock(side_effect=RuntimeError("coordinator init failed")),
    )
    from core.orchestrator import HIKARI_Orchestrator

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.brain_v2_enabled = True
    orch.neural_memory = None
    orch.neural_memory_enabled = False
    orch._init_brain_v2()
    assert orch.brain_v2 is None
    assert orch._brain_v2_session is None


def test_brain_v2_disabled_by_env(monkeypatch):
    monkeypatch.setenv("HIKARI_DISABLE_BRAIN_V2", "1")
    from core.brain_v2.status import is_brain_v2_enabled

    assert not is_brain_v2_enabled()

    from core.orchestrator import HIKARI_Orchestrator

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.brain_v2_enabled = os.getenv("HIKARI_DISABLE_BRAIN_V2", "0") != "1"
    orch.brain_v2 = None
    orch._brain_v2_session = None
    orch._init_brain_v2()
    assert orch.brain_v2 is None


def test_build_memory_first_context_without_brain_v2(monkeypatch):
    monkeypatch.setenv("HIKARI_DISABLE_BRAIN_V2", "1")
    from core.orchestrator import HIKARI_Orchestrator

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.brain_v2 = None
    orch.brain = None
    orch.speaker = type("S", (), {"current_speaker": None, "primary_user": None, "last_contact_kind": None})()
    orch.planner = None
    orch.neural_memory_enabled = True

    class FakeBrain:
        def build_prompt_context(self, q):
            return "[Brain context]\nsemantic:\n- test"

    orch.brain = FakeBrain()
    ctx = orch._build_memory_first_context("hello")
    assert "semantic" in ctx or "Brain" in ctx


def test_is_positive_brain_v2_recall_answer():
    assert is_positive_brain_v2_recall_answer("From reviewed memory: I live in City A.")
    assert is_positive_brain_v2_recall_answer("I live in City A.")
    assert is_positive_brain_v2_recall_answer(
        "From recent session context: Right now I'm in City B for summer holidays."
    )
    assert is_positive_brain_v2_recall_answer("You're in City B for this session.")
    assert not is_positive_brain_v2_recall_answer(
        "I don't have a reviewed memory for that yet."
    )
    assert not is_positive_brain_v2_recall_answer(None)


@pytest.fixture
def episode_db(tmp_path):
    from core.brain_v2 import EpisodeStore

    return EpisodeStore(db_path=tmp_path / "orch_v2.db")


def _production_brain_v2(store) -> BrainV2Coordinator:
    """Match production orchestrator Brain v2 coordinator settings."""
    return BrainV2Coordinator(
        store=store,
        neural_bridge=None,
        allow_neural_procedural=False,
        allow_neural_conflict_reads=False,
    )


def _accept(episode_db, statement: str, key: str):
    episode_id = episode_db.create_episode(key)
    episode_db.add_turn(episode_id, statement, is_user=True)
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    MemoryReviewGate(episode_db).accept(candidates[0].candidate_id)


def _orch_with_real_brain_v2_recording(brain_v2: BrainV2Coordinator, brain: HikariBrain, *, speaker=None):
    """Minimal orchestrator that uses real _record_brain_v2_turn (not mocked)."""
    from core.orchestrator import HIKARI_Orchestrator
    from core.speaker_context import SpeakerContext

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.legacy_memory_enabled = False
    orch.brain_v2_enabled = True
    orch.brain_v2 = brain_v2
    orch._brain_v2_session = brain_v2.start_session()
    orch.brain = brain
    orch.speaker = speaker or SpeakerContext(primary_user="Owner A")
    orch.emotional_iq = MagicMock()
    orch.emotional_iq.detect_emotion.return_value = {}
    orch.emotional_iq.get_dominant_emotion.return_value = ("neutral", 0.0)
    orch.emotional_iq.log_emotion = MagicMock()
    orch.emotional_iq.adapt_response = MagicMock(side_effect=lambda r, *a, **k: r)
    orch.personality = MagicMock()
    orch.personality.traits = {
        "formality": 0.5,
        "verbosity": 0.5,
        "humor": 0.3,
        "helpfulness": 0.8,
    }
    orch.personality.learn_from_interaction = MagicMock()
    orch.personality.format_response = MagicMock(side_effect=lambda r: r)
    orch.personality.get_greeting = MagicMock(return_value="Hi")
    orch.personality.get_prompt_context = MagicMock(return_value="")
    orch._check_health = MagicMock()
    orch._mentions_partner_context = MagicMock(return_value=False)
    orch._handle_special_commands = MagicMock(return_value=None)
    orch._normalize_user_input_text = lambda t: (t or "").strip()
    orch._normalize_brain_memory_statement = lambda t: (t or "").strip()
    orch._route_to_agent = MagicMock(return_value=None)
    orch.router = MagicMock()
    orch.router.generate.return_value = "Generic AI reply"
    orch.agents = {"research": MagicMock()}
    orch.brain.remember_turn = MagicMock()
    orch.brain.is_memory_statement = MagicMock(return_value=False)
    orch.brain.remember_fact = MagicMock(return_value=False)
    orch.planner = None
    return orch


def _minimal_orchestrator(brain_v2: BrainV2Coordinator, brain: HikariBrain, *, speaker=None):
    from core.orchestrator import HIKARI_Orchestrator
    from core.speaker_context import SpeakerContext

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.legacy_memory_enabled = False
    orch.brain_v2_enabled = True
    orch.brain_v2 = brain_v2
    orch._brain_v2_session = "test-session"
    orch.brain = brain
    orch.speaker = speaker or SpeakerContext(primary_user="Owner A")
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
    orch.brain.is_memory_statement = MagicMock(return_value=False)
    orch.brain.remember_fact = MagicMock(return_value=False)

    def _record_turn(u, r, s="chat", *, metadata=None):
        if not brain_v2:
            return None
        return brain_v2.record_turn(
            "test-session",
            u,
            r or "",
            speaker_label=orch.speaker.current_speaker or "user",
            metadata=metadata,
        )

    orch._record_brain_v2_turn = MagicMock(
        side_effect=_record_turn
    )
    orch._pending_memory_choice = None
    return orch


def test_brain_v2_location_overrides_stale_neural(episode_db):
    from tests.test_brain_memory import FakeNeural

    _accept(episode_db, "Remember this: I live in City A.", "loc")
    coord = _production_brain_v2(episode_db)
    stale_brain = HikariBrain(FakeNeural([]))
    stale_brain.answer = MagicMock(
        return_value=BrainAnswer(text="You live in City B.", confidence=0.9)
    )
    orch = _minimal_orchestrator(coord, stale_brain)

    reply = orch.process_input("where do I live?")
    assert reply
    assert "city a" in reply.lower()
    assert "city b" not in reply.lower()
    stale_brain.answer.assert_not_called()


def test_brain_v2_plan_overrides_stale_meeting_location(episode_db):
    from tests.test_brain_memory import FakeNeural

    _accept(
        episode_db,
        "Remember this: on Sunday May 24 2026 I am meeting my girlfriend Jamie at Restaurant B for lunch.",
        "plan",
    )
    coord = _production_brain_v2(episode_db)
    stale_brain = HikariBrain(FakeNeural([]))
    stale_brain.answer = MagicMock(
        return_value=BrainAnswer(text="You're currently in City C.", confidence=0.9)
    )
    orch = _minimal_orchestrator(coord, stale_brain)

    reply = orch.process_input("where am I meeting Jamie?")
    assert reply
    assert "restaurant" in reply.lower()
    assert "city c" not in reply.lower()
    stale_brain.answer.assert_not_called()


def test_orchestrator_production_chat_returns_no_reviewed_not_conflict_review(
    tmp_path, monkeypatch
):
    from tests.test_brain_memory import FakeNeural
    import sqlite3
    from core.path_literals import EPISODES_DB, HIKARI_MEMORY_DB

    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    with sqlite3.connect(neural_db) as conn:
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
            ("FACT", "City C", "currently in City C"),
        )
        conn.commit()
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(neural_db))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("home-only")
    store.add_turn(episode_id, "Remember this: I live in City A.", is_user=True)
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    MemoryReviewGate(store).accept(candidates[0].candidate_id)
    coord = _production_brain_v2(store)
    stale_brain = HikariBrain(FakeNeural([]))
    stale_brain.answer = MagicMock(
        return_value=BrainAnswer(text="You are currently in City C.", confidence=0.9)
    )
    orch = _minimal_orchestrator(coord, stale_brain)
    reply = orch.process_input("where am I right now?")
    assert reply
    assert reply != CONFLICT_REVIEW_NEEDED_MESSAGE
    assert "have a reviewed memory" in reply.lower()
    stale_brain.answer.assert_not_called()


def test_personal_recall_no_reviewed_memory_quarantines_neural_home(episode_db):
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    stale_brain = HikariBrain(FakeNeural([]))
    stale_brain.answer = MagicMock(
        return_value=BrainAnswer(text="You live in City B.", confidence=0.9)
    )
    orch = _minimal_orchestrator(coord, stale_brain)

    reply = orch.process_input("where do I live?")
    assert "have a reviewed memory" in reply.lower()
    assert "city b" not in reply.lower()
    stale_brain.answer.assert_not_called()


def test_personal_recall_quarantines_stale_neural_family_and_education(episode_db):
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    stale_brain = HikariBrain(FakeNeural([]))
    stale_brain.answer = MagicMock(
        return_value=BrainAnswer(
            text="Your brother Person C studies at School B.",
            confidence=0.9,
        )
    )
    orch = _minimal_orchestrator(coord, stale_brain)

    family = orch.process_input("who is my brother?")
    assert "have a reviewed memory" in family.lower()
    assert "person c" not in family.lower()
    assert "school b" not in family.lower()

    education = orch.process_input("where did I go to school?")
    assert "have a reviewed memory" in education.lower()
    assert "school b" not in education.lower()
    assert stale_brain.answer.call_count == 0


def test_non_personal_query_uses_ai_not_neural_when_policy_on(episode_db):
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    stale_brain = HikariBrain(FakeNeural([]))
    stale_brain.answer = MagicMock(
        return_value=BrainAnswer(text="Topic A is a research field.", confidence=0.9)
    )
    orch = _minimal_orchestrator(coord, stale_brain)
    orch._get_ai_response = MagicMock(return_value="AI explains Topic A in physics.")

    reply = orch.process_input("explain Topic A in physics")
    assert reply == "AI explains Topic A in physics."
    stale_brain.answer.assert_not_called()
    orch._get_ai_response.assert_called()


def test_get_user_summary_prefers_brain_v2_over_neural(episode_db):
    from core.orchestrator import HIKARI_Orchestrator
    from tests.test_brain_memory import FakeNeural

    _accept(episode_db, "Remember this: I live in City A.", "prof-loc")
    _accept(
        episode_db,
        "Remember this: my girlfriend Jamie is a medical student at River Medical University.",
        "prof-edu",
    )
    _accept(
        episode_db,
        "Remember this: on Sunday May 24 2026 I am meeting my girlfriend Jamie at Restaurant B for lunch.",
        "prof-plan",
    )
    coord = _production_brain_v2(episode_db)
    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.brain_v2_enabled = True
    orch.brain_v2 = coord
    orch._brain_v2_session = "summary-session"
    orch.brain = HikariBrain(FakeNeural([]))
    orch.brain.summarize_user = MagicMock(
        return_value=(
            "What I know about you:\n"
            "- Education: River Medical University\n"
            "- Home: City B\n"
            "- Currently in: City C\n"
        )
    )
    orch.personality = MagicMock()
    orch.personality.user_prefs = {}
    orch.user_profile = MagicMock()
    orch.user_profile.name = "Alex"

    summary = orch._get_user_summary()
    assert "city a" in summary.lower()
    assert "restaurant" in summary.lower() or "jamie" in summary.lower()
    assert "reviewed memories" in summary.lower()
    assert "city b" not in summary.lower()
    assert "city c" not in summary.lower()
    assert "education: river medical university" not in summary.lower().replace("jamie", "")


def test_get_user_summary_honest_empty_without_reviewed_memories(episode_db):
    from core.orchestrator import HIKARI_Orchestrator
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.brain_v2_enabled = True
    orch.brain_v2 = coord
    orch._brain_v2_session = "summary-session"
    orch.brain = HikariBrain(FakeNeural([]))
    neural = "What I know about you:\n- Home: City B\n"
    orch.brain.summarize_user = MagicMock(return_value=neural)
    orch.personality = MagicMock()
    orch.personality.user_prefs = {}
    orch.user_profile = MagicMock()
    orch.user_profile.name = "user"

    summary = orch._get_user_summary()
    assert "reviewed memory" in summary.lower()
    assert "city b" not in summary.lower()
    orch.brain.summarize_user.assert_not_called()


def test_who_am_i_and_profile_use_same_summary_path(episode_db):
    from core.orchestrator import HIKARI_Orchestrator
    from tests.test_brain_memory import FakeNeural

    _accept(episode_db, "Remember this: I live in City A.", "whoami")
    coord = _production_brain_v2(episode_db)
    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.brain_v2_enabled = True
    orch.brain_v2 = coord
    orch._brain_v2_session = "summary-session"
    orch.brain = HikariBrain(FakeNeural([]))
    orch.brain.summarize_user = MagicMock(return_value="What I know about you:\n- Home: City B\n")
    orch.personality = MagicMock()
    orch.personality.user_prefs = {}
    orch.user_profile = MagicMock()
    orch.user_profile.name = "user"
    orch._handle_special_commands = HIKARI_Orchestrator._handle_special_commands.__get__(
        orch, HIKARI_Orchestrator
    )

    who = orch._handle_special_commands("who am I?")
    what = orch._handle_special_commands("what do you know about me?")
    profile = orch._handle_special_commands("profile")
    my_profile = orch._handle_special_commands("my profile")
    show_profile = orch._handle_special_commands("show profile")
    assert who == what == profile == my_profile == show_profile
    assert "city a" in who.lower()


def test_profile_command_routes_to_brain_v2_summary(episode_db):
    from core.orchestrator import HIKARI_Orchestrator
    from tests.test_brain_memory import FakeNeural

    _accept(episode_db, "Remember this: I live in City A.", "profile-cmd")
    coord = _production_brain_v2(episode_db)
    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.brain_v2_enabled = True
    orch.brain_v2 = coord
    orch._brain_v2_session = "summary-session"
    orch.brain = HikariBrain(FakeNeural([]))
    orch.brain.summarize_user = MagicMock(return_value="What I know about you:\n- Home: City B\n")
    orch.personality = MagicMock()
    orch.personality.user_prefs = {}
    orch.user_profile = MagicMock()
    orch.user_profile.name = "Owner A"
    orch._handle_special_commands = HIKARI_Orchestrator._handle_special_commands.__get__(
        orch, HIKARI_Orchestrator
    )

    reply = orch._handle_special_commands("profile")
    assert reply
    assert "reviewed memories" in reply.lower()
    assert "city a" in reply.lower()
    assert "city b" not in reply.lower()


def test_guest_speaker_intro_does_not_update_owner_name(episode_db):
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    stale_brain = HikariBrain(FakeNeural([]))
    stale_brain.answer = MagicMock(return_value=None)
    orch = _minimal_orchestrator(coord, stale_brain)
    orch.personality.user_prefs = {"name": "Owner A"}

    reply = orch.process_input("I am Guest B talking to you now")
    assert "guest mode" in reply.lower()
    orch.personality.learn_from_interaction.assert_not_called()
    assert orch.speaker.current_speaker == "Guest B"
    assert orch.personality.user_prefs["name"] == "Owner A"


def test_guest_speaker_intro_does_not_create_owner_identity_candidate(episode_db):
    from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline

    episode_id = episode_db.create_episode("guest-intro")
    episode_db.add_turn(
        episode_id,
        "I am Guest B talking to you now",
        is_user=True,
        speaker_label="Guest B",
    )
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    assert candidates == []


def test_owner_profile_stable_after_guest_speaker_intro(episode_db):
    from core.orchestrator import HIKARI_Orchestrator
    from core.speaker_context import SpeakerContext
    from tests.test_brain_memory import FakeNeural

    _accept(episode_db, "Remember this: I live in City A.", "owner-stable")
    coord = _production_brain_v2(episode_db)
    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.brain_v2_enabled = True
    orch.brain_v2 = coord
    orch._brain_v2_session = "summary-session"
    orch.brain = HikariBrain(FakeNeural([]))
    orch.brain.summarize_user = MagicMock(return_value="What I know about you:\n- Home: City B\n")
    orch.personality = MagicMock()
    orch.personality.user_prefs = {"name": "Owner A"}
    orch.user_profile = MagicMock()
    orch.user_profile.name = "Owner A"
    orch.speaker = SpeakerContext(primary_user="Owner A")

    stale_brain = orch.brain
    stale_brain.answer = MagicMock(return_value=None)
    orch._handle_special_commands = HIKARI_Orchestrator._handle_special_commands.__get__(
        orch, HIKARI_Orchestrator
    )

    orch.speaker.update_from_input("I am Guest B talking to you now")
    summary = orch._get_user_summary()
    assert "city a" not in summary.lower()
    assert "reviewed brain v2 memories for guest b" in summary.lower()
    assert orch.personality.user_prefs["name"] == "Owner A"


def test_guest_family_question_does_not_use_owner_memory(episode_db):
    from tests.test_brain_memory import FakeNeural

    _accept(episode_db, "Remember this: my brother Person C studies at School A.", "owner-bro")
    coord = _production_brain_v2(episode_db)
    stale_brain = HikariBrain(FakeNeural([]))
    stale_brain.answer = MagicMock(
        return_value=BrainAnswer(text="Yes, your brother Person C loves you.", confidence=0.9)
    )
    orch = _minimal_orchestrator(coord, stale_brain)
    orch.speaker.update_from_input("I am Guest B talking to you now")

    reply = orch.process_input("does my brother love me?")
    assert reply
    assert "reviewed brain v2 memories" in reply.lower()
    assert "guest" in reply.lower()
    assert "person c" not in reply.lower()
    stale_brain.answer.assert_not_called()


def test_speaker_reset_clears_guest_context(episode_db):
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    stale_brain = HikariBrain(FakeNeural([]))
    stale_brain.answer = MagicMock(return_value=None)
    orch = _minimal_orchestrator(coord, stale_brain)
    orch.process_input("I am Guest B talking to you now")
    assert orch.speaker.is_guest_speaker()

    orch.process_input("I'm just testing")
    assert orch.speaker.current_speaker is None
    assert not orch.speaker.is_guest_speaker()


def test_speaker_reset_clears_stale_contact_context(episode_db):
    from tests.test_brain_memory import FakeNeural
    from core.speaker_context import SpeakerContext

    coord = _production_brain_v2(episode_db)
    stale_brain = HikariBrain(FakeNeural([]))
    stale_brain.answer = MagicMock(return_value=None)
    orch = _minimal_orchestrator(coord, stale_brain, speaker=SpeakerContext(primary_user="Owner A"))
    orch.speaker.note_contact_discussed("partner")
    orch.speaker.note_family_relation("sister")
    orch.process_input("I am Guest B talking to you now")
    assert orch.speaker.last_contact_kind is None
    assert orch.speaker.last_family_relation is None

    orch.speaker.note_contact_discussed("family", "brother")
    orch.process_input("I'm just testing")
    assert orch.speaker.last_contact_kind is None
    assert orch.speaker.last_family_relation is None


def test_back_to_owner_clears_stale_contact_context(episode_db):
    from tests.test_brain_memory import FakeNeural
    from core.speaker_context import SpeakerContext

    coord = _production_brain_v2(episode_db)
    stale_brain = HikariBrain(FakeNeural([]))
    stale_brain.answer = MagicMock(return_value=None)
    orch = _minimal_orchestrator(coord, stale_brain, speaker=SpeakerContext(primary_user="Owner A"))
    orch.speaker.note_contact_discussed("partner")
    orch.process_input("I am Guest B talking to you now")
    orch.process_input("back to owner")
    assert orch.speaker.current_speaker == "Owner A"
    assert orch.speaker.last_contact_kind is None
    assert orch.speaker.last_family_relation is None


def test_phrase_reset_clears_working_memory(episode_db):
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    orch.brain_v2.working.push_turn("guest-only turn", "reply")
    orch.process_input("I am Guest B talking to you now")
    orch.process_input("I'm just testing")
    assert not any(
        "guest-only" in item.value for item in orch.brain_v2.working.recent_items()
    )


def test_back_to_owner_restores_primary_speaker(episode_db):
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    stale_brain = HikariBrain(FakeNeural([]))
    stale_brain.answer = MagicMock(return_value=None)
    orch = _minimal_orchestrator(coord, stale_brain)
    orch.process_input("I am Guest B talking to you now")
    orch.process_input("back to owner")
    assert orch.speaker.current_speaker == "Owner A"
    assert not orch.speaker.is_guest_speaker()


def test_guest_memory_status_hides_owner_identity(episode_db):
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    _accept(episode_db, "Remember this: I live in City A.", "guest-memstat")
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    orch.process_input("I am Guest B talking to you now")
    reply = orch.process_input("memory status")
    assert reply
    assert "Owner A" not in reply
    assert "city a" not in reply.lower()
    assert "guest" in reply.lower()
    assert "household owner" in reply.lower()


_OWNER_CONTEXT_MARKER = "owner-marker-topic-alpha"


def test_owner_current_location_then_where_am_i_now(episode_db):
    from tests.test_brain_memory import FakeNeural

    _accept(episode_db, "Remember this: I live in City A.", "stable-home")
    coord = _production_brain_v2(episode_db)
    orch = _orch_with_real_brain_v2_recording(coord, HikariBrain(FakeNeural([])))
    orch._handle_special_commands = MagicMock(return_value=None)
    orch._route_to_agent = MagicMock(return_value=None)
    orch.router = MagicMock()
    orch.router.generate.return_value = "Generic AI reply"
    orch.personality.traits = {
        "formality": 0.5,
        "verbosity": 0.5,
        "humor": 0.3,
        "helpfulness": 0.8,
    }
    orch.personality.get_prompt_context = MagicMock(return_value="")

    before = orch.process_input("where am I now?")
    assert before
    assert "have a reviewed memory" in before.lower()

    declare = orch.process_input("I am in City B.")
    assert declare
    assert "session" in declare.lower() and "city b" in declare.lower()

    after = orch.process_input("where am I now?")
    assert after
    assert "city b" in after.lower()
    assert "for this session" in after.lower()


def test_what_do_you_remember_returns_profile_not_random_semantic_hit(episode_db):
    from core.orchestrator import HIKARI_Orchestrator
    from tests.test_brain_memory import FakeNeural

    _accept(episode_db, "Remember this: I live in City A.", "summary-home")
    _accept(episode_db, "Remember this: I prefer Topic A.", "summary-pref")
    coord = _production_brain_v2(episode_db)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    orch._handle_special_commands = HIKARI_Orchestrator._handle_special_commands.__get__(
        orch, HIKARI_Orchestrator
    )

    reply = orch.process_input("what do you remember?")
    assert reply
    low = reply.lower()
    assert "what i know about you" in low
    assert "city a" in low
    assert "topic a" in low


def test_owner_current_location_restored_after_guest_reset(episode_db):
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    orch = _orch_with_real_brain_v2_recording(coord, HikariBrain(FakeNeural([])))
    orch._handle_special_commands = MagicMock(return_value=None)

    owner_set = orch.process_input("I am in City B.")
    assert "session" in owner_set.lower() and "city b" in owner_set.lower()

    orch.process_input("I am Guest B talking to you now")
    guest_reply = orch.process_input("where am I now?")
    assert "city b" not in (guest_reply or "").lower()
    assert "guest" in (guest_reply or "").lower()

    orch.process_input("back to owner")
    owner_reply = orch.process_input("where am I now?")
    assert "city b" in (owner_reply or "").lower()
    assert "for this session" in (owner_reply or "").lower()


def test_guest_general_chat_does_not_inject_owner_brain_v2_context(episode_db):
    from unittest.mock import patch

    from tests.test_brain_memory import FakeNeural

    _accept(episode_db, f"Remember this: I prefer {_OWNER_CONTEXT_MARKER}.", "owner-ctx")
    coord = _production_brain_v2(episode_db)
    orch = _orch_with_real_brain_v2_recording(coord, HikariBrain(FakeNeural([])))
    orch.process_input("I am Guest B talking to you now")
    with patch.object(
        coord, "build_prompt_context", wraps=coord.build_prompt_context
    ) as build_ctx:
        orch.process_input("tell me about astronomy in one sentence")
        build_ctx.assert_not_called()
    ctx = orch._build_memory_first_context("tell me about astronomy in one sentence")
    assert _OWNER_CONTEXT_MARKER not in (ctx or "").lower()


def test_guest_intro_gets_deterministic_no_owner_memory_reply(episode_db):
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    orch = _orch_with_real_brain_v2_recording(coord, HikariBrain(FakeNeural([])))
    orch.router = MagicMock()
    orch.router.generate.return_value = "Generic AI reply"

    reply = orch.process_input("I am Guest B talking to you now")
    assert reply
    low = reply.lower()
    assert "guest b" in low
    assert "guest mode" in low
    assert "owner" in low
    orch.router.generate.assert_not_called()


def test_guest_disclosure_not_recorded_in_owner_brain_v2_store(episode_db):
    from core.brain_v2.schemas import MemoryCandidateStatus
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    orch = _orch_with_real_brain_v2_recording(coord, HikariBrain(FakeNeural([])))
    session = orch._brain_v2_session
    orch.process_input("I am Guest B talking to you now")
    reply = orch.process_input("I live in City B.")
    assert reply
    assert "recorded" not in reply.lower()
    assert "remember" not in reply.lower()
    assert "will not store guest personal details" in reply.lower()
    episode_id = coord._session_episodes.get(session)
    if episode_id:
        segments = episode_db.get_raw_segments(episode_id)
        assert not any("city b" in (s.text or "").lower() for s in segments)
    assert not any("city b" in (item.value or "").lower() for item in coord.working.recent_items())
    orch.finalize_session()
    pending = episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)
    assert not any("city b" in (c.statement or "").lower() for c in pending)


def test_guest_remember_this_honest_no_store(episode_db):
    from core.brain_v2.schemas import MemoryCandidateStatus
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    orch = _orch_with_real_brain_v2_recording(coord, HikariBrain(FakeNeural([])))
    orch.process_input("I am Guest B talking to you now")
    reply = orch.process_input("Remember this: I prefer Topic B.")
    assert reply
    assert "recorded" not in reply.lower()
    assert "remember" not in reply.lower()
    assert "will not store guest personal details" in reply.lower()
    orch.finalize_session()
    pending = episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)
    assert not any("topic b" in (c.statement or "").lower() for c in pending)


def test_guest_identity_declaration_is_honest_no_store(episode_db):
    from core.brain_v2.schemas import MemoryCandidateStatus
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    orch = _orch_with_real_brain_v2_recording(coord, HikariBrain(FakeNeural([])))
    orch.process_input("I am Guest B talking to you now")
    reply = orch.process_input("My name is Guest B.")
    assert "will not store guest personal details" in reply.lower()
    orch.finalize_session()
    pending = episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)
    assert not any("guest b" in (c.statement or "").lower() for c in pending)


def test_owner_brain_v2_recording_resumes_after_guest_reset(episode_db):
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    orch = _orch_with_real_brain_v2_recording(coord, HikariBrain(FakeNeural([])))
    session = orch._brain_v2_session
    orch.process_input("I am Guest B talking to you now")
    orch.process_input("I live in City B.")
    orch.process_input("back to owner")
    owner_reply = orch.process_input(f"Remember this: I prefer {_OWNER_CONTEXT_MARKER}.")
    assert owner_reply
    assert "got it" in owner_reply.lower()
    accepted = episode_db.get_active_accepted_memories(limit=20)
    assert any(_OWNER_CONTEXT_MARKER in (m.statement or "").lower() for m in accepted)


def test_guest_system_status_hides_owner_memory_counts(episode_db):
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    orch.process_input("I am Guest B talking to you now")
    reply = orch.process_input("status")
    assert reply
    assert "Owner A" not in reply
    assert "guest" in reply.lower()
    assert "household owner" in reply.lower()
    assert "conversations" not in reply.lower()


def test_guest_brain_status_hides_owner_identity(episode_db):
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    orch.process_input("I am Guest B talking to you now")
    reply = orch.process_input("brain status")
    assert reply
    assert "Owner A" not in reply
    assert "guest" in reply.lower()
    assert "household owner" in reply.lower()


def test_owner_memory_status_omits_primary_user_line(episode_db):
    from core.memory_status import format_memory_status_report
    from core.speaker_context import SpeakerContext

    coord = _production_brain_v2(episode_db)
    orch = _minimal_orchestrator(
        coord,
        HikariBrain(MagicMock()),
        speaker=SpeakerContext(primary_user="Owner A"),
    )
    orch.speaker.current_speaker = "Owner A"
    report = format_memory_status_report(orch)
    assert "quarantined" in report.lower()
    assert "Primary user" not in report
    assert "Owner A" not in report or "Current speaker: Owner A" in report


def test_guest_profile_command_blocks_owner_reviewed_facts(episode_db):
    from tests.test_brain_memory import FakeNeural

    _accept(episode_db, "Remember this: I live in City A.", "guest-prof")
    coord = _production_brain_v2(episode_db)
    brain = HikariBrain(FakeNeural([]))
    brain.answer = MagicMock(
        return_value=BrainAnswer(text="Owner profile City A.", confidence=0.9)
    )
    orch = _minimal_orchestrator(coord, brain)
    orch.speaker.update_from_input("I am Guest B talking to you now")

    reply = orch.process_input("profile")
    assert "city a" not in (reply or "").lower()
    assert "guest b" in (reply or "").lower()
    brain.answer.assert_not_called()


def test_guest_where_do_i_live_blocks_owner_location(episode_db):
    from tests.test_brain_memory import FakeNeural

    _accept(episode_db, "Remember this: I live in City A.", "guest-loc")
    coord = _production_brain_v2(episode_db)
    brain = HikariBrain(FakeNeural([]))
    brain.answer = MagicMock(
        return_value=BrainAnswer(text="You live in City A.", confidence=0.9)
    )
    orch = _minimal_orchestrator(coord, brain)
    orch._get_ai_response = MagicMock(return_value="AI says City A.")
    orch.speaker.update_from_input("I am Guest B talking to you now")

    reply = orch.process_input("where do I live?")
    assert "city a" not in (reply or "").lower()
    assert "guest" in (reply or "").lower()
    brain.answer.assert_not_called()


def test_remote_guest_request_does_not_read_owner_memory(episode_db):
    from core.action_policy import Actor, ActorContext
    from tests.test_brain_memory import FakeNeural

    _accept(episode_db, "Remember this: I live in City A.", "remote-guest-loc")
    coord = _production_brain_v2(episode_db)
    brain = HikariBrain(FakeNeural([]))
    brain.answer = MagicMock(
        return_value=BrainAnswer(text="You live in City A.", confidence=0.9)
    )
    orch = _minimal_orchestrator(coord, brain)
    orch._get_ai_response = MagicMock(return_value="AI says City A.")
    context = ActorContext(
        actor_id="guest", actor=Actor.GUEST, session_id="test-session", source="device"
    )

    reply = orch.process_input("where do I live?", source="device", context=context)

    assert "city a" not in (reply or "").lower()
    assert "cannot" in (reply or "").lower()
    brain.answer.assert_not_called()
    orch._get_ai_response.assert_not_called()


def test_remote_guest_request_does_not_record_brain_turn(episode_db):
    from core.action_policy import Actor, ActorContext
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    context = ActorContext(
        actor_id="guest", actor=Actor.GUEST, session_id="test-session", source="device"
    )

    orch.process_input("I live in City B.", source="device", context=context)

    orch._record_brain_v2_turn.assert_not_called()


def test_remote_guest_request_does_not_mutate_speaker_context(episode_db):
    from core.action_policy import Actor, ActorContext
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    original_speaker = orch.speaker.current_speaker
    context = ActorContext(
        actor_id="guest", actor=Actor.GUEST, session_id="test-session", source="device"
    )

    orch.process_input("I am Guest B talking to you now", source="device", context=context)

    assert orch.speaker.current_speaker == original_speaker


def test_remote_guest_request_cannot_call_provider(episode_db):
    from core.action_policy import Actor, ActorContext
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    orch.router = MagicMock()
    orch.router.generate = MagicMock(return_value="AI reply")
    context = ActorContext(
        actor_id="guest", actor=Actor.GUEST, session_id="test-session", source="device"
    )

    reply = orch.process_input("explain quantum physics", source="device", context=context)

    orch.router.generate.assert_not_called()
    assert "cannot" in (reply or "").lower()


def test_invalid_actor_context_fails_closed(episode_db):
    from core.action_policy import Actor, ActorContext
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    context = ActorContext(
        actor_id="invalid!!!", actor=Actor.UNKNOWN, session_id="test-session", source="device"
    )

    reply = orch.process_input("hello", source="device", context=context)

    assert "cannot" in (reply or "").lower()


def test_owner_and_guest_concurrent_requests_remain_isolated(episode_db):
    from core.action_policy import Actor, ActorContext
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    owner_context = ActorContext(
        actor_id="local-owner", actor=Actor.OWNER, session_id="owner-session", source="text"
    )
    guest_context = ActorContext(
        actor_id="guest", actor=Actor.GUEST, session_id="guest-session", source="device"
    )

    orch.speaker.update_from_input("I am Owner A talking to you now")
    owner_reply = orch.process_input("who am I?", source="text", context=owner_context)
    guest_reply = orch.process_input("who am I?", source="device", context=guest_context)

    assert "owner" in (owner_reply or "").lower() or "owner a" in (owner_reply or "").lower()
    assert "owner" not in (guest_reply or "").lower()
    assert "cannot" in (guest_reply or "").lower()
    orch._get_ai_response.assert_not_called()


def test_guest_what_do_you_remember_blocks_owner_memory(episode_db):
    from tests.test_brain_memory import FakeNeural

    _accept(episode_db, "Remember this: I prefer Topic A.", "guest-rem")
    coord = _production_brain_v2(episode_db)
    brain = HikariBrain(FakeNeural([]))
    orch = _minimal_orchestrator(coord, brain)
    orch.memory = MagicMock()
    orch.memory.get_recent_conversations = MagicMock(
        return_value=[{"user": "Owner A private conversation"}]
    )
    orch.speaker.update_from_input("I am Guest B talking to you now")

    reply = orch.process_input("what do you remember?")
    orch.memory.get_recent_conversations.assert_not_called()
    assert "topic a" not in (reply or "").lower()
    assert "owner a private" not in (reply or "").lower()


def test_new_session_clears_guest_and_working_context(episode_db):
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    brain = HikariBrain(FakeNeural([]))
    orch = _minimal_orchestrator(coord, brain)
    orch.process_input("I am Guest B talking to you now")
    assert orch.speaker.is_guest_speaker()
    orch.brain_v2.working.note_speaker("Guest B")
    orch.brain_v2.working.push_turn("guest-only turn", "reply")

    orch.speaker.note_contact_discussed("family", "brother")
    orch._begin_fresh_brain_v2_session()
    assert not orch.speaker.is_guest_speaker()
    assert not orch.speaker.session_speaker_mode
    assert orch.speaker.current_speaker is None
    assert orch.speaker.last_contact_kind is None
    assert orch.speaker.last_family_relation is None
    assert "Guest B" not in str(orch.brain_v2.working.speaker_context)
    assert not any("guest-only" in item.value for item in orch.brain_v2.working.recent_items())


def _no_primary_speaker(monkeypatch):
    monkeypatch.delenv("HIKARI_PRIMARY_USER", raising=False)
    from core.speaker_context import SpeakerContext

    return SpeakerContext(primary_user=None)


def test_no_primary_guest_where_do_i_live_blocks_owner_location(episode_db, monkeypatch):
    from core.orchestrator import HIKARI_Orchestrator
    from tests.test_brain_memory import FakeNeural

    _accept(episode_db, "Remember this: I live in City A.", "no-primary-loc")
    coord = _production_brain_v2(episode_db)
    brain = HikariBrain(FakeNeural([]))
    brain.answer = MagicMock(
        return_value=BrainAnswer(text="You live in City A.", confidence=0.9)
    )
    orch = _minimal_orchestrator(
        coord, brain, speaker=_no_primary_speaker(monkeypatch)
    )
    orch._get_ai_response = MagicMock(return_value="AI says City A.")
    orch.process_input("I am Guest B talking to you now")

    reply = orch.process_input("where do I live?")
    assert "city a" not in (reply or "").lower()
    assert "guest" in (reply or "").lower()
    brain.answer.assert_not_called()


def test_no_primary_guest_profile_blocks_owner_reviewed_facts(episode_db, monkeypatch):
    from core.orchestrator import HIKARI_Orchestrator
    from tests.test_brain_memory import FakeNeural

    _accept(episode_db, "Remember this: I live in City A.", "no-primary-prof")
    coord = _production_brain_v2(episode_db)
    brain = HikariBrain(FakeNeural([]))
    brain.answer = MagicMock(
        return_value=BrainAnswer(text="Owner profile City A.", confidence=0.9)
    )
    orch = _minimal_orchestrator(
        coord, brain, speaker=_no_primary_speaker(monkeypatch)
    )
    orch._handle_special_commands = HIKARI_Orchestrator._handle_special_commands.__get__(
        orch, HIKARI_Orchestrator
    )
    orch.process_input("I am Guest B talking to you now")

    reply = orch.process_input("profile")
    assert "city a" not in (reply or "").lower()
    assert "guest b" in (reply or "").lower()
    brain.answer.assert_not_called()


def test_no_primary_guest_what_do_you_remember_blocks_owner_memory(episode_db, monkeypatch):
    from core.orchestrator import HIKARI_Orchestrator
    from tests.test_brain_memory import FakeNeural

    _accept(episode_db, "Remember this: I prefer Topic A.", "no-primary-rem")
    coord = _production_brain_v2(episode_db)
    brain = HikariBrain(FakeNeural([]))
    orch = _minimal_orchestrator(
        coord, brain, speaker=_no_primary_speaker(monkeypatch)
    )
    orch.memory = MagicMock()
    orch.memory.get_recent_conversations = MagicMock(
        return_value=[{"user": "Household private conversation"}]
    )
    orch._handle_special_commands = HIKARI_Orchestrator._handle_special_commands.__get__(
        orch, HIKARI_Orchestrator
    )
    orch.process_input("I am Guest B talking to you now")

    reply = orch.process_input("what do you remember?")
    orch.memory.get_recent_conversations.assert_not_called()
    assert "topic a" not in (reply or "").lower()
    assert "household private" not in (reply or "").lower()
    assert "guest" in (reply or "").lower()


def test_no_primary_fresh_session_clears_transient_guest_context(episode_db, monkeypatch):
    from tests.test_brain_memory import FakeNeural

    coord = _production_brain_v2(episode_db)
    orch = _minimal_orchestrator(
        coord, HikariBrain(FakeNeural([])), speaker=_no_primary_speaker(monkeypatch)
    )
    orch.process_input("I am Guest B talking to you now")
    orch.speaker.note_family_relation("sister")
    assert orch.speaker.session_speaker_mode
    assert orch.speaker.is_guest_speaker()

    orch._begin_fresh_brain_v2_session()
    assert not orch.speaker.session_speaker_mode
    assert not orch.speaker.is_guest_speaker()
    assert orch.speaker.last_family_relation is None


def test_personal_recall_still_blocks_research():
    from core.orchestrator import HIKARI_Orchestrator

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.legacy_memory_enabled = False
    research = MagicMock()
    research.can_handle.return_value = 0.95
    research.handle.return_value = "web hit"
    orch.agents = {
        "research": research,
        "memory": MagicMock(can_handle=MagicMock(return_value=0.0)),
    }

    orch._route_to_agent("where do I live?")
    research.handle.assert_not_called()
