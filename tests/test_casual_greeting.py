"""Casual greetings must not call the LLM or invent stored identity."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.brain import HikariBrain
from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.recall_intent import is_casual_greeting
from tests.test_brain_memory import FakeNeural
from tests.test_brain_v2_write_authority import _minimal_orchestrator


@pytest.fixture
def episode_db(tmp_path):
    from core.brain_v2 import EpisodeStore

    return EpisodeStore(db_path=tmp_path / "greeting.db")


@pytest.mark.parametrize(
    "phrase",
    ["hi", "hello", "hey", "hello hikari", "good morning"],
)
def test_casual_greeting_detector(phrase: str):
    assert is_casual_greeting(phrase)


def test_hi_uses_deterministic_greeting_without_llm(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    brain = HikariBrain(FakeNeural([]))
    orch = _minimal_orchestrator(coord, brain)
    orch._get_ai_response = MagicMock(
        return_value="Hello Owner A, I remember you from City A."
    )

    reply = orch.process_input("hi")

    assert "how can i help" in reply.lower()
    orch._get_ai_response.assert_not_called()
