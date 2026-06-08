"""Legacy memory flags must not bypass Brain v2 personal authority."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.brain import BrainAnswer, HikariBrain
from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.episode_store import EpisodeStore
from tests.test_brain_memory import FakeNeural


@pytest.fixture
def episode_db(tmp_path):
    return EpisodeStore(db_path=tmp_path / "legacy_bypass.db")


def test_legacy_memory_enabled_does_not_answer_personal_before_brain_v2(episode_db, monkeypatch):
    from core.orchestrator import HIKARI_Orchestrator
    from core.speaker_context import SpeakerContext

    monkeypatch.setenv("HIKARI_ENABLE_LEGACY_MEMORY", "1")
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    brain = HikariBrain(FakeNeural([]))
    brain.answer = MagicMock(
        return_value=BrainAnswer(text="Legacy neural household answer.", confidence=0.9)
    )

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.legacy_memory_enabled = True
    orch.brain_v2_enabled = True
    orch.brain_v2 = coord
    orch._brain_v2_session = "legacy-bypass"
    orch.brain = brain
    orch.memory = MagicMock()
    orch.memory.add_conversation = MagicMock()
    orch.neural_memory_enabled = False
    orch.neural_memory = None
    orch.speaker = SpeakerContext(primary_user="Owner A")
    orch.emotional_iq = MagicMock()
    orch.emotional_iq.detect_emotion.return_value = {}
    orch.emotional_iq.get_dominant_emotion.return_value = ("neutral", 0.0)
    orch.emotional_iq.log_emotion = MagicMock()
    orch.emotional_iq.adapt_response = MagicMock(side_effect=lambda r, *a, **k: r)
    orch.personality = MagicMock()
    orch.personality.learn_from_interaction = MagicMock()
    orch.personality.format_response = MagicMock(side_effect=lambda r: r)
    orch.user_profile = MagicMock()
    orch.user_profile.extract_info_from_conversation = MagicMock()
    orch.knowledge_graph = MagicMock()
    orch.knowledge_graph.extract_from_conversation = MagicMock()
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

    with patch("core.family_memory.answer_household_memory") as household:
        household.return_value = "Legacy JSON family answer."
        reply = orch.process_input("who is my sister?")

    household.assert_not_called()
    assert "legacy json" not in (reply or "").lower()
    assert "legacy neural household" not in (reply or "").lower()
    brain.answer.assert_not_called()
    brain.remember_turn.assert_not_called()
    orch.user_profile.extract_info_from_conversation.assert_not_called()
    orch.knowledge_graph.extract_from_conversation.assert_not_called()
