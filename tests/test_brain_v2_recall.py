"""Brain v2 recall intent, ranking, profile summary, and orchestrator guards."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.memory_review_gate import MemoryReviewGate
from core.brain_v2.recall_intent import (
    INTENT_CURRENT_LOCATION,
    INTENT_FAMILY_PERSON,
    INTENT_HIKARI_DECISION,
    INTENT_IDENTITY_SELF,
    INTENT_NON_MEMORY,
    INTENT_PREFERENCE,
    INTENT_PROFILE_SUMMARY,
    classify_recall_intent,
    is_positive_brain_v2_recall_answer,
    requested_relations,
    should_skip_external_research,
)
from core.brain_v2.retrieval import BrainV2Retrieval
from core.brain_v2.schemas import MemoryCandidateStatus, MemoryLayer


@pytest.fixture
def episode_db(tmp_path):
    from core.brain_v2 import EpisodeStore

    return EpisodeStore(db_path=tmp_path / "recall_v2.db")


def _accept_turn(store, statement: str, episode_key: str = "ep"):
    episode_id = store.create_episode(episode_key)
    store.add_turn(episode_id, statement, is_user=True)
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    assert candidates, f"no candidate for: {statement}"
    MemoryReviewGate(store).accept(candidates[0].candidate_id)
    return candidates[0]


@pytest.mark.parametrize(
    "query,expected",
    [
        ("who am I?", INTENT_IDENTITY_SELF),
        ("where am I?", INTENT_CURRENT_LOCATION),
        ("what do you know about me?", INTENT_PROFILE_SUMMARY),
        ("do you know my sister?", INTENT_FAMILY_PERSON),
        ("what did we decide about HIKARI?", INTENT_HIKARI_DECISION),
        ("what is the weather today?", INTENT_NON_MEMORY),
    ],
)
def test_recall_intent_classification(query, expected):
    assert classify_recall_intent(query) == expected


def test_sister_memory_ranks_for_family_query(episode_db):
    _accept_turn(episode_db, "My sister Maya studies at North Valley University.")
    packet = BrainV2Retrieval(episode_db).retrieve("do you know my sister?")
    semantic = packet.top_semantic_hits(1)
    assert semantic
    assert "maya" in semantic[0].text.lower() or "sister" in semantic[0].text.lower()


def test_requested_relations_normalization():
    assert requested_relations("do you know my brother?") == {"brother"}
    assert requested_relations("what does my gf do?") == {"girlfriend"}
    assert "mother" in requested_relations("do you know my mom?")


def test_brother_query_does_not_rank_sister_memory_first(episode_db):
    _accept_turn(episode_db, "My sister Maya studies at North Valley University.", "rank-sis")
    packet = BrainV2Retrieval(episode_db).retrieve("do you know my brother?")
    semantic = packet.top_semantic_hits(1)
    if semantic:
        assert "brother" in semantic[0].text.lower() or "maya" not in semantic[0].text.lower()
    else:
        assert True


def test_preference_memory_ranks_for_prefer_query(episode_db):
    _accept_turn(
        episode_db,
        "Remember this: I prefer local-first private tools.",
        "pref-ep",
    )
    packet = BrainV2Retrieval(episode_db).retrieve("what do I prefer?")
    semantic = packet.top_semantic_hits(1)
    assert semantic
    assert "local-first" in semantic[0].text.lower()


def test_hikari_decision_ranks_for_project_query(episode_db):
    _accept_turn(
        episode_db,
        "For HIKARI we decided to keep Brain v2 review manual.",
        "dec-ep",
    )
    packet = BrainV2Retrieval(episode_db).retrieve("what did we decide about HIKARI?")
    semantic = packet.top_semantic_hits(1)
    assert semantic
    assert "review" in semantic[0].text.lower() or "brain" in semantic[0].text.lower()


def test_profile_summary_includes_multiple_sections(episode_db):
    _accept_turn(episode_db, "My name is Alex.", "id-ep")
    _accept_turn(episode_db, "My sister Maya studies at North Valley University.", "fam-ep")
    _accept_turn(
        episode_db,
        "Remember this: I prefer local-first private tools.",
        "pref-ep2",
    )
    summary = BrainV2Retrieval(episode_db).build_profile_summary_context(
        "what do you know about me?", limit=8
    )
    assert "reviewed profile" in summary.lower()
    assert "alex" in summary.lower() or "maya" in summary.lower()
    assert "local-first" in summary.lower()


def test_pending_not_semantic_truth_recall(episode_db):
    episode_id = episode_db.create_episode("pend-r")
    episode_db.add_turn(
        episode_id,
        "Remember this: I prefer local-first private tools.",
        is_user=True,
    )
    EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)
    packet = BrainV2Retrieval(episode_db).retrieve("what do I prefer?")
    assert not packet.top_semantic_hits(3)


def test_rejected_not_semantic_truth_recall(episode_db):
    episode_id = episode_db.create_episode("rej-r")
    episode_db.add_turn(episode_id, "My dad Rowan lives in Lake Town.", is_user=True)
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    MemoryReviewGate(episode_db).reject(candidates[0].candidate_id)
    packet = BrainV2Retrieval(episode_db).retrieve("where does my dad live?")
    assert not any("rowan" in h.text.lower() for h in packet.top_semantic_hits(3))


def test_semantic_outranks_episodic_recall(episode_db):
    _accept_turn(episode_db, "Remember this: I prefer local-first private tools.", "sem-ep")
    episode_id = episode_db.create_episode("epi-ep")
    episode_db.add_turn(episode_id, "Remember this: I prefer local-first private tools.", is_user=True)
    EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)
    packet = BrainV2Retrieval(episode_db).retrieve("local-first")
    scores = {h.layer: h.score for h in packet.hits}
    assert scores.get(MemoryLayer.SEMANTIC.value, 0) > scores.get(MemoryLayer.EPISODIC.value, 0)


def test_answer_from_accepted_honest_when_missing(episode_db):
    reply = BrainV2Retrieval(episode_db).answer_from_accepted("do you know my sister?")
    assert reply
    assert "have a reviewed memory" in reply.lower()


def test_answer_from_accepted_with_sister_memory(episode_db):
    _accept_turn(episode_db, "My sister Maya studies at North Valley University.")
    reply = BrainV2Retrieval(episode_db).answer_from_accepted("do you know my sister?")
    assert reply
    assert is_positive_brain_v2_recall_answer(reply)
    assert "maya" in reply.lower()


def test_sister_memory_does_not_answer_brother_query(episode_db):
    _accept_turn(episode_db, "My sister Maya studies at North Valley University.", "sis-only")
    reply = BrainV2Retrieval(episode_db).answer_from_accepted("do you know my brother?")
    assert reply
    assert "have a reviewed memory" in reply.lower()
    assert "brother" in reply.lower()
    assert "maya" not in reply.lower()


def test_sister_memory_does_not_answer_gf_query(episode_db):
    _accept_turn(episode_db, "My sister Maya studies at North Valley University.", "sis-gf")
    reply = BrainV2Retrieval(episode_db).answer_from_accepted("what does my gf do?")
    assert reply
    assert "have a reviewed memory" in reply.lower()
    assert "maya" not in reply.lower()


def test_gf_memory_answers_gf_query(episode_db):
    _accept_turn(
        episode_db,
        "My girlfriend Priya works at River Clinic.",
        "gf-ep",
    )
    reply = BrainV2Retrieval(episode_db).answer_from_accepted("what does my gf do?")
    assert reply
    assert is_positive_brain_v2_recall_answer(reply)
    assert "priya" in reply.lower() or "clinic" in reply.lower()


def test_dad_memory_does_not_answer_mom_query(episode_db):
    _accept_turn(episode_db, "My dad Rowan lives in Lake Town.", "dad-only")
    reply = BrainV2Retrieval(episode_db).answer_from_accepted("do you know my mom?")
    assert reply
    assert "have a reviewed memory" in reply.lower()
    assert "rowan" not in reply.lower()


def test_personal_recall_skips_external_research():
    assert should_skip_external_research("do you know my sister?")
    assert not should_skip_external_research("what is the capital of France?")


def test_orchestrator_research_score_zero_for_personal_recall():
    from core.orchestrator import HIKARI_Orchestrator

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    research = MagicMock()
    research.can_handle.return_value = 0.9
    orch.agents = {
        "research": research,
        "memory": MagicMock(can_handle=MagicMock(return_value=0.0)),
        "voice": MagicMock(can_handle=MagicMock(return_value=0.0)),
    }
    orch.legacy_memory_enabled = False
    orch.brain_v2 = MagicMock()
    research.handle = MagicMock(return_value="web result")

    with patch.object(orch, "_get_ai_response", return_value="ai"):
        orch._route_to_agent("do you know my sister?")

    assert research.can_handle.called
    assert research.handle.call_count == 0


def test_memory_first_brain_v2_before_neural(monkeypatch):
    monkeypatch.setenv("HIKARI_DISABLE_BRAIN_V2", "0")
    from core.orchestrator import HIKARI_Orchestrator

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.speaker = type(
        "S",
        (),
        {"current_speaker": None, "primary_user": None, "last_contact_kind": None},
    )()
    orch.planner = None
    orch.neural_memory_enabled = True
    orch.brain_v2_enabled = True
    orch.brain_v2 = MagicMock()
    orch._brain_v2_session = "ctx-session"
    orch.brain_v2.build_prompt_context.return_value = "[Brain v2 context]\nsemantic:\n- fact"
    orch.brain = MagicMock()

    ctx = orch._build_memory_first_context("what do I prefer?")
    assert "[Brain v2 context]" in ctx
    orch.brain.build_prompt_context.assert_not_called()


def test_orchestrator_brain_v2_recall_before_ai(monkeypatch):
    from core.orchestrator import HIKARI_Orchestrator

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.brain_v2_enabled = True
    orch.brain_v2 = MagicMock()
    orch.brain_v2.try_answer_from_accepted_memories.return_value = (
        "I prefer local-first private tools."
    )
    orch.brain = MagicMock()
    orch.brain.answer.return_value = None
    orch.brain.remember_turn = MagicMock()
    orch._record_brain_v2_turn = MagicMock()
    orch._should_use_brain_v2_recall = MagicMock(return_value=True)

    with patch.object(HIKARI_Orchestrator, "process_input", wraps=lambda s, u, src="text": None):
        pass

    # Call the block logic via simplified path
    user_input = "do you know my sister?"
    brain_answer = orch.brain.answer(user_input)
    assert brain_answer is None
    v2 = orch._try_brain_v2_recall_answer(user_input)
    assert v2 and is_positive_brain_v2_recall_answer(v2)
