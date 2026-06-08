"""Personal phrasing variants must not fall through to legacy neural brain.answer()."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.brain import BrainAnswer, HikariBrain
from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.recall_intent import (
    BRAIN_V2_UNAVAILABLE_MESSAGE,
    classify_recall_intent,
    has_personal_memory_authority_surface,
    is_personal_factual_question,
    is_personal_recall_intent,
    is_plausible_personal_memory_query,
    is_task_or_action_request,
    matches_personal_factual_firewall,
)
from tests.test_brain_memory import FakeNeural


PERSONAL_VARIANTS = [
    "tell me about my sister",
    "where did my sister study?",
    "tell me about my girlfriend",
    "what is my brother doing?",
    "where are my parents?",
    "where does my gf studies?",
    "who is my brother?",
    "what school did I attend?",
    "how is my sister?",
    "does my sister live in City A?",
    "is my girlfriend in City B?",
    "tell me about my partner",
    "is my sister in City A?",
    "is my brother in City B?",
    "are my parents in City A?",
    "is my mother in City B?",
    "is my father in City A?",
]

FACTUAL_PERSONAL_QUERIES = [
    "what is my birthday?",
    "where do I work?",
    "which university do I attend?",
    "what is my legal name?",
    "where was I born?",
    "do I have siblings?",
]

FIREWALL_PERSONAL_FACTUAL_QUERIES = [
    "how old am I?",
    "what is my age?",
    "what is my degree?",
    "what is my major?",
    "when do I graduate?",
    "what is my graduation date?",
    "where am I employed?",
    "what is my current job?",
    "which hospital was I born in?",
    "what is my anniversary?",
    "what is my favorite food?",
    "tell me my name",
    "do you know my email?",
]

REQUIRED_PROTECTED_PERSONAL_FACTUAL = [
    "where is my apartment?",
    "what is my height?",
    "what is my blood type?",
    "what is my doctor name?",
    "what is my favorite color?",
    "what did i tell you about my internship?",
    "what time is my exam?",
    "where is my class tomorrow?",
    "what is my address?",
    "who is my emergency contact?",
]

HELP_ME_REMEMBER_PERSONAL = [
    "help me remember my address",
    "help me recall my blood type",
    "help me remember what time my exam is",
    "can you help me remember my doctor name",
]

TASK_ACTION_QUERIES = [
    "help me write my resume",
    "how do I fix my code",
    "draft my email",
    "plan my study schedule",
    "help me prepare for my exam",
    "help me draft an email",
    "help me debug my code",
    "can you write my cover letter",
    "please fix my python script",
    "help me draft my email to my professor",
    "make me a study plan for finals",
]


BROAD_PERSONAL_AUTHORITY_QUERIES = [
    "whats my name?",
    "what is my official name?",
    "my sister's full name?",
    "does my brother love me?",
    "is my sister happy?",
    "did my dad call me?",
    "are my parents okay?",
]


@pytest.mark.parametrize("query", FIREWALL_PERSONAL_FACTUAL_QUERIES)
def test_firewall_personal_factual_queries_classified(query: str):
    assert is_personal_factual_question(query)
    assert has_personal_memory_authority_surface(query)


@pytest.mark.parametrize("query", REQUIRED_PROTECTED_PERSONAL_FACTUAL)
def test_required_protected_personal_factual_firewall(query: str):
    assert matches_personal_factual_firewall(query)
    assert is_personal_factual_question(query)
    assert is_personal_recall_intent(classify_recall_intent(query))


@pytest.mark.parametrize("query", HELP_ME_REMEMBER_PERSONAL)
def test_help_me_remember_personal_wins_over_task_exclusion(query: str):
    assert not is_task_or_action_request(query)
    assert matches_personal_factual_firewall(query)
    assert is_personal_factual_question(query)


@pytest.mark.parametrize("query", HELP_ME_REMEMBER_PERSONAL)
def test_help_me_remember_personal_never_uses_general_ai(episode_db, query: str):
    orch, brain = _orch(episode_db)
    orch._get_ai_response = MagicMock(return_value="AI invented personal fact.")
    orch._route_to_agent = MagicMock(return_value=None)
    reply = orch.process_input(query)
    orch._get_ai_response.assert_not_called()
    orch._route_to_agent.assert_not_called()
    brain.answer.assert_not_called()
    assert "ai invented" not in (reply or "").lower()


@pytest.mark.parametrize("query", TASK_ACTION_QUERIES)
def test_task_action_queries_not_personal_firewall(query: str):
    assert is_task_or_action_request(query)
    assert not matches_personal_factual_firewall(query)
    assert not is_personal_factual_question(query)


@pytest.mark.parametrize("query", FACTUAL_PERSONAL_QUERIES)
def test_factual_personal_queries_classified(query: str):
    assert has_personal_memory_authority_surface(query)


@pytest.mark.parametrize("query", PERSONAL_VARIANTS + BROAD_PERSONAL_AUTHORITY_QUERIES)
def test_variants_classified_as_personal(query: str):
    assert is_plausible_personal_memory_query(query)
    assert has_personal_memory_authority_surface(query)


@pytest.fixture
def episode_db(tmp_path):
    return EpisodeStore(db_path=tmp_path / "phrasing.db")


def _orch(episode_db):
    from core.orchestrator import HIKARI_Orchestrator
    from core.speaker_context import SpeakerContext

    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    brain = HikariBrain(FakeNeural([]))
    brain.answer = MagicMock(
        return_value=BrainAnswer(text="Stale neural personal truth.", confidence=0.9)
    )
    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.legacy_memory_enabled = False
    orch.brain_v2_enabled = True
    orch.brain_v2 = coord
    orch._brain_v2_session = "phrasing-session"
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
    orch.brain.is_memory_statement = MagicMock(return_value=False)
    orch._record_brain_v2_turn = MagicMock()
    return orch, brain


@pytest.mark.parametrize(
    "query",
    FIREWALL_PERSONAL_FACTUAL_QUERIES + REQUIRED_PROTECTED_PERSONAL_FACTUAL,
)
def test_firewall_personal_factual_never_uses_general_ai(episode_db, query: str):
    orch, brain = _orch(episode_db)
    orch._get_ai_response = MagicMock(return_value="AI invented personal fact.")
    orch._route_to_agent = MagicMock(return_value=None)

    reply = orch.process_input(query)

    orch._get_ai_response.assert_not_called()
    orch._route_to_agent.assert_not_called()
    brain.answer.assert_not_called()
    assert "ai invented" not in (reply or "").lower()


@pytest.mark.parametrize("query", FACTUAL_PERSONAL_QUERIES)
def test_factual_personal_never_uses_general_ai(episode_db, query: str):
    orch, brain = _orch(episode_db)
    orch._get_ai_response = MagicMock(return_value="AI invented personal fact.")
    orch._route_to_agent = MagicMock(return_value=None)

    reply = orch.process_input(query)

    orch._get_ai_response.assert_not_called()
    orch._route_to_agent.assert_not_called()
    brain.answer.assert_not_called()
    assert "ai invented" not in (reply or "").lower()


@pytest.mark.parametrize("query", PERSONAL_VARIANTS + BROAD_PERSONAL_AUTHORITY_QUERIES)
def test_variants_never_call_neural_answer(episode_db, query: str):
    orch, brain = _orch(episode_db)
    reply = orch.process_input(query)
    assert reply
    assert "stale neural" not in (reply or "").lower()
    brain.answer.assert_not_called()


def _degraded_orch():
    from core.orchestrator import HIKARI_Orchestrator
    from core.speaker_context import SpeakerContext

    brain = HikariBrain(FakeNeural([]))
    brain.answer = MagicMock(
        return_value=BrainAnswer(text="Stale neural personal truth.", confidence=0.9)
    )
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
    orch.brain.is_memory_statement = MagicMock(return_value=False)
    orch._record_brain_v2_turn = MagicMock()
    return orch, brain


@pytest.mark.parametrize("query", BROAD_PERSONAL_AUTHORITY_QUERIES)
def test_broad_personal_queries_fail_closed_without_coordinator(query: str):
    orch, brain = _degraded_orch()
    reply = orch.process_input(query)
    assert BRAIN_V2_UNAVAILABLE_MESSAGE in (reply or "")
    brain.answer.assert_not_called()
