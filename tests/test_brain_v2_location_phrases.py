"""Location phrase guards and session-aware weather."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.brain_v2 import EpisodeStore
from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.location_phrases import (
    is_meta_or_deferred_location_phrase,
    is_valid_place_name,
)
from core.brain_v2.memory_type import infer_memory_type
from core.brain_v2.session_context import register_session_place_provider
from core.brain_statements import is_declarative_memory_statement
from tests.test_brain_memory import FakeNeural
from tests.test_brain_v2_write_authority import _minimal_orchestrator
from core.brain import HikariBrain


@pytest.fixture
def episode_db(tmp_path):
    return EpisodeStore(db_path=tmp_path / "location_phrases.db")


@pytest.fixture(autouse=True)
def reset_session_place_provider():
    register_session_place_provider(None)
    yield
    register_session_place_provider(None)


def test_meta_phrase_not_declarative_memory():
    assert is_meta_or_deferred_location_phrase("the city im in now")
    assert not is_declarative_memory_statement("the city im in now")


def test_infer_does_not_treat_meta_phrase_as_place():
    inferred = infer_memory_type("the city im in now")
    assert inferred.candidate_type != "current_location"


def test_valid_place_name():
    assert is_valid_place_name("City B")
    assert not is_valid_place_name("the city im in now")


def test_session_not_overwritten_by_meta_phrase(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    register_session_place_provider(
        lambda: coord.working.get_current_location()[0]
        if coord.working.get_current_location()
        else None
    )

    orch.process_input("I am in City B.")
    orch.process_input("the city im in now")

    loc = coord.working.get_current_location()
    assert loc
    assert loc[0].lower() == "city b"

    answer = orch.process_input("where am I now?")
    assert "city b" in answer.lower()
    assert "the city" not in answer.lower()


def test_call_me_identity_auto_trusted(episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))

    reply = orch.process_input("you can call me Person C")
    assert "brain v2" in reply.lower() or "call you" in reply.lower()
    assert "okay" not in reply.lower() or "remember" in reply.lower()


def test_weather_resolves_session_city(episode_db):
    register_session_place_provider(lambda: "City B")
    from agents.research import ResearchAgent

    agent = ResearchAgent(eager_legacy_brain=False)
    with patch.object(agent, "get_weather", return_value="Weather in City B: clear") as mock_w:
        result = agent.handle("whats the weather in the city im in now")
    assert result == "Weather in City B: clear"
    mock_w.assert_called_once_with("City B")


def test_weather_error_does_not_echo_provider_url_or_key(monkeypatch):
    from agents.research import ResearchAgent

    agent = ResearchAgent(eager_legacy_brain=False)

    def _boom(*_args, **_kwargs):
        raise RuntimeError(
            "HTTPConnectionPool(url=/data/2.5/weather?q=CityA&appid=secret-key)"
        )

    monkeypatch.setenv("WEATHER_API_KEY", "secret-key")
    monkeypatch.setattr("agents.research.requests.get", _boom)
    result = agent.get_weather("City A")
    assert "secret-key" not in result
    assert "appid" not in result
    assert "weather service is unavailable" in result.lower()
