"""Prompt/profile context must not inject legacy neural personal lines by default."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from core.brain import HikariBrain
from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.recall_intent import BRAIN_V2_UNAVAILABLE_MESSAGE
from core.path_literals import HIKARI_MEMORY_DB
from tests.test_brain_memory import FakeNeural


@pytest.fixture
def episode_db(tmp_path):
    return EpisodeStore(db_path=tmp_path / "prompt_episodes.db")


@pytest.fixture
def seeded_neural(tmp_path, monkeypatch):
    neural_db = tmp_path / HIKARI_MEMORY_DB
    neural_db.parent.mkdir(parents=True, exist_ok=True)
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
            ("LOCATION", "City B", "legacy home City B"),
        )
        conn.commit()
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(neural_db))
    return neural_db


def test_build_prompt_context_excludes_neural_personal_by_default(
    tmp_path, seeded_neural, monkeypatch
):
    store = EpisodeStore(db_path=tmp_path / "prompt.db")
    coord = BrainV2Coordinator(store=store, allow_neural_procedural=False)
    monkeypatch.delenv("HIKARI_BRAIN_V2_UNSAFE_NEURAL_PROFILE_SUPPLEMENT", raising=False)

    prompt = coord.build_prompt_context("what do you know about me?")
    low = (prompt or "").lower()
    assert "city b" not in low
    assert "legacy home" not in low
    assert "unsafe debug" not in low


def test_orchestrator_memory_first_context_excludes_neural_personal(
    tmp_path, seeded_neural, monkeypatch
):
    from core.orchestrator import HIKARI_Orchestrator

    isolated = tmp_path / "test_ep_store.sqlite"
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(isolated))
    monkeypatch.delenv("HIKARI_BRAIN_V2_UNSAFE_NEURAL_PROFILE_SUPPLEMENT", raising=False)

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.brain_v2 = BrainV2Coordinator(
        store=EpisodeStore(db_path=isolated),
        allow_neural_procedural=False,
    )
    orch.speaker = type(
        "S",
        (),
        {
            "current_speaker": None,
            "primary_user": "Owner A",
            "last_contact_kind": None,
        },
    )()
    orch.planner = None

    ctx = orch._build_memory_first_context("what do you know about me?")
    low = (ctx or "").lower()
    assert "city b" not in low
    assert "legacy home" not in low


def _orch_with_memory(episode_db, *, runtime_ready: bool = True):
    from core.orchestrator import HIKARI_Orchestrator
    from core.speaker_context import SpeakerContext

    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    brain = HikariBrain(FakeNeural([]))
    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.legacy_memory_enabled = False
    orch.brain_v2_enabled = True
    orch.brain_v2 = coord if runtime_ready else None
    orch._brain_v2_session = "summary-session" if runtime_ready else None
    orch.brain = brain
    orch.speaker = SpeakerContext(primary_user="Owner A")
    orch.emotional_iq = MagicMock()
    orch.emotional_iq.detect_emotion.return_value = {}
    orch.emotional_iq.get_dominant_emotion.return_value = ("neutral", 0.0)
    orch.emotional_iq.log_emotion = MagicMock()
    orch.emotional_iq.adapt_response = MagicMock(side_effect=lambda r, *a, **k: r)
    orch.personality = MagicMock()
    orch.personality.format_response = MagicMock(side_effect=lambda r: r)
    orch._check_health = MagicMock()
    orch._mentions_partner_context = MagicMock(return_value=False)
    orch._normalize_user_input_text = lambda t: (t or "").strip()
    orch.memory = MagicMock()
    orch.memory.get_recent_conversations = MagicMock(
        return_value=[{"user": "Legacy private conversation snippet"}]
    )
    return orch


@pytest.mark.parametrize(
    "command",
    ["what do you remember?", "what have we talked about?"],
)
def test_memory_summary_commands_skip_legacy_json_log(tmp_path, command: str):
    store = EpisodeStore(db_path=tmp_path / "summary_cmd.db")
    orch = _orch_with_memory(store)
    reply = orch.process_input(command)
    orch.memory.get_recent_conversations.assert_not_called()
    assert "legacy private conversation" not in (reply or "").lower()
    assert "recent conversations" not in (reply or "").lower()


def _legacy_json_orch(tmp_path, episode_db, monkeypatch):
    from core.memory import MemorySystem
    from core.orchestrator import HIKARI_Orchestrator
    from core.personality import PersonalityEngine
    from core.speaker_context import SpeakerContext

    legacy_dir = tmp_path / "legacy-data"
    monkeypatch.setenv("HIKARI_LEGACY_DATA_DIR", str(legacy_dir))
    monkeypatch.setenv("HIKARI_ENABLE_LEGACY_MEMORY", "1")

    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    brain = HikariBrain(FakeNeural([]))
    brain.answer = MagicMock(return_value=None)
    brain.is_memory_statement = MagicMock(return_value=False)

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.legacy_memory_enabled = True
    orch.brain_v2_enabled = True
    orch.brain_v2 = coord
    orch._brain_v2_session = "json-guard-session"
    orch.brain = brain
    orch.memory = MemorySystem()
    orch.personality = PersonalityEngine()
    orch.speaker = SpeakerContext(primary_user="Owner A")
    orch.emotional_iq = MagicMock()
    orch.emotional_iq.detect_emotion.return_value = {}
    orch.emotional_iq.get_dominant_emotion.return_value = ("neutral", 0.0)
    orch.emotional_iq.log_emotion = MagicMock()
    orch.emotional_iq.adapt_response = MagicMock(side_effect=lambda r, *a, **k: r)
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
    orch.neural_memory_enabled = False
    orch.neural_memory = None
    orch._record_brain_v2_turn = MagicMock()
    return orch, legacy_dir


@pytest.mark.parametrize(
    "user_input",
    [
        "My name is Owner A",
        "I prefer local tools",
        "I feel sick today",
        "My birthday is May 25",
        "Remember this: I live in City A",
    ],
)
def test_brain_v2_on_writes_no_legacy_json_personal(
    tmp_path, episode_db, monkeypatch, user_input: str
):
    import json

    orch, legacy_dir = _legacy_json_orch(tmp_path, episode_db, monkeypatch)
    orch.process_input(user_input)

    profile_path = legacy_dir / "personality_profile.json"
    if profile_path.is_file():
        prefs = json.loads(profile_path.read_text()).get("user_prefs", {})
        assert prefs.get("name") != "Owner A"
        assert not any("owner a" in str(v).lower() for v in prefs.get("favorite_topics", []))
        assert not any("sick" in str(v).lower() for v in prefs.get("health_concerns", []))

    memory_path = legacy_dir / "memory.json"
    if memory_path.is_file():
        for conv in json.loads(memory_path.read_text()).get("conversations", []):
            user_line = (conv.get("user") or "").lower()
            assert "owner a" not in user_line
            assert "city a" not in user_line
            assert "may 25" not in user_line


def test_stale_legacy_profile_not_in_prompt_or_summary(tmp_path, monkeypatch, episode_db):
    import importlib
    import json

    legacy_dir = tmp_path / "legacy-data"
    legacy_dir.mkdir(parents=True)
    monkeypatch.setenv("HIKARI_LEGACY_DATA_DIR", str(legacy_dir))
    (legacy_dir / "user_profile.json").write_text(
        json.dumps({"name": "Stale Profile Name", "preferences": {}}),
        encoding="utf-8",
    )
    (legacy_dir / "personality_profile.json").write_text(
        json.dumps(
            {
                "traits": {"formality": 0.5, "verbosity": 0.5, "humor": 0.5},
                "user_prefs": {
                    "name": "Stale Personality Name",
                    "favorite_topics": ["stale topic"],
                    "health_concerns": [],
                    "stress_triggers": [],
                    "mood_boosters": [],
                    "language": "en",
                    "timezone": None,
                    "communication_style": "friendly",
                },
                "interaction_count": 0,
            }
        ),
        encoding="utf-8",
    )

    import core.personality as personality_mod
    import core.user_profile as user_profile_mod

    user_profile_mod = importlib.reload(user_profile_mod)
    personality_mod = importlib.reload(personality_mod)
    from core.orchestrator import HIKARI_Orchestrator
    from core.speaker_context import SpeakerContext

    profile = user_profile_mod.UserProfile()
    assert profile.get_name() == "Stale Profile Name"

    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.brain_v2_enabled = True
    orch.brain_v2 = coord
    orch._brain_v2_session = "stale-prompt-session"
    orch.brain = HikariBrain(FakeNeural([]))
    orch.speaker = SpeakerContext(primary_user="Owner A")
    orch.personality = personality_mod.PersonalityEngine()
    orch.personality.quarantine_loaded_personal_prefs()
    orch.user_profile = profile
    orch.planner = None
    orch.neural_memory_enabled = False

    ctx = orch._build_memory_first_context("what do you know about me?")
    prompt_blob = (ctx or "").lower()
    assert "stale profile name" not in prompt_blob
    assert "stale personality name" not in prompt_blob
    assert "stale topic" not in prompt_blob

    persona = orch.personality.get_prompt_context(
        orch.speaker, include_personal_facts=False, brain_v2_authority=True
    )
    assert "stale personality name" not in (persona or "").lower()

    summary = orch._get_user_summary()
    assert "stale profile name" not in (summary or "").lower()


def test_build_prompt_context_never_calls_legacy_brain_when_authority_on(
    tmp_path, seeded_neural, monkeypatch
):
    from core.brain import HikariBrain
    from unittest.mock import patch

    store = EpisodeStore(db_path=tmp_path / "no_legacy_prompt.db")
    coord = BrainV2Coordinator(store=store, allow_neural_procedural=False)
    with patch.object(HikariBrain, "build_prompt_context", return_value="legacy neural") as legacy:
        coord.build_prompt_context("what do you know about me?")
    legacy.assert_not_called()


def test_memory_summary_unavailable_when_runtime_degraded(tmp_path):
    store = EpisodeStore(db_path=tmp_path / "summary_degraded.db")
    orch = _orch_with_memory(store, runtime_ready=False)
    reply = orch.process_input("what do you remember?")
    orch.memory.get_recent_conversations.assert_not_called()
    assert BRAIN_V2_UNAVAILABLE_MESSAGE in (reply or "")
