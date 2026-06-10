"""Brain v2 live recording: memory statements, finalize, consolidate."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.schemas import MemoryCandidateStatus


@pytest.fixture
def episode_db(tmp_path):
    from core.brain_v2 import EpisodeStore

    return EpisodeStore(db_path=tmp_path / "recording_v2.db")


def _minimal_orch(episode_db):
    from core.orchestrator import HIKARI_Orchestrator

    coord = BrainV2Coordinator(store=episode_db)
    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.legacy_memory_enabled = False
    orch.brain_v2_enabled = True
    orch.brain_v2 = coord
    orch._brain_v2_session = coord.start_session()
    orch.speaker = MagicMock(
        current_speaker="user",
        primary_user=None,
        last_contact_kind=None,
        last_was_session_intro=False,
    )
    orch.speaker.is_guest_speaker.return_value = False
    orch.speaker.should_skip_owner_identity_learning.return_value = False
    orch.speaker.update_from_input.return_value = None
    orch.emotional_iq = MagicMock()
    orch.emotional_iq.detect_emotion.return_value = {}
    orch.emotional_iq.get_dominant_emotion.return_value = ("neutral", 0.0)
    orch.personality = MagicMock()
    orch.personality.learn_from_interaction = MagicMock()
    orch.personality.format_response = lambda r: r
    orch.planner = None
    orch.brain = MagicMock()
    orch.brain.answer.return_value = None
    orch.brain.is_memory_statement.return_value = False
    orch.brain.remember_fact.return_value = False
    orch.brain.remember_turn = MagicMock()
    orch._should_use_brain_v2_recall = MagicMock(return_value=False)
    orch._normalize_brain_memory_statement = lambda t: t
    orch._check_health = MagicMock()
    return orch


def test_memory_statement_got_it_records_brain_v2_turn(episode_db):
    orch = _minimal_orch(episode_db)
    orch.brain.is_memory_statement.return_value = True
    orch.brain.remember_fact.return_value = True

    with patch.object(orch, "_handle_special_commands", return_value=None):
        response = orch.process_input(
            "Remember this: I prefer local-first private tools.",
            source="text",
        )

    assert "got it" in response.lower()
    orch.brain.remember_fact.assert_not_called()
    orch.brain.remember_turn.assert_not_called()
    accepted = episode_db.get_active_accepted_memories(limit=10)
    assert any("local-first" in memory.statement.lower() for memory in accepted)
    assert any(
        (memory.metadata or {}).get("auto_trusted_owner_assertion")
        for memory in accepted
    )


def test_finalize_session_creates_pending_candidates(episode_db):
    orch = _minimal_orch(episode_db)
    orch._record_brain_v2_turn(
        "Remember this: I prefer local-first private tools.",
        "Got it. I'll remember that.",
        "text",
    )
    orch.finalize_session()

    pending = episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)
    assert pending
    assert any("local-first" in c.statement.lower() for c in pending)
    assert episode_db.get_structured_episode(
        pending[0].episode_id
    ) is not None


def test_finalize_session_no_crash_when_brain_v2_disabled():
    from core.orchestrator import HIKARI_Orchestrator

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch.brain_v2 = None
    orch._brain_v2_session = None
    orch.finalize_session()


def test_text_mode_exit_calls_finalize_session(monkeypatch):
    orch = MagicMock()
    inputs = iter(["hello", "exit"])

    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr("hikari.print_banner", lambda: None)
    monkeypatch.setattr(
        "core.cli_status.get_startup_panel",
        lambda: "panel",
    )
    monkeypatch.setattr("core.orchestrator.get_orchestrator", lambda: orch)
    orch.process_input.return_value = "Hi"

    from hikari import run_interactive

    run_interactive()
    orch.finalize_session.assert_called_once()


def test_consolidate_pending_episodes_recovery(episode_db):
    coord = BrainV2Coordinator(store=episode_db)
    episode_id = episode_db.create_episode("recovery-session")
    episode_db.add_turn(
        episode_id,
        "Remember this: I prefer local-first private tools.",
        is_user=True,
    )
    episode_db.add_turn(
        episode_id,
        "Got it. I'll remember that.",
        is_user=False,
        speaker_label="assistant",
    )

    summary = coord.consolidate_pending_episodes()
    assert summary["episodes"] == 1
    assert summary["candidates"] >= 1
    pending = episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)
    assert pending


def _pending_candidate(episode_db):
    from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline

    episode_id = episode_db.create_episode("prefix-test")
    episode_db.add_turn(
        episode_id,
        "Remember this: I prefer local-first private tools.",
        is_user=True,
    )
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    assert candidates
    return candidates[0]


def test_reject_full_candidate_id(episode_db):
    from core.brain_v2.memory_review_gate import MemoryReviewGate

    cand = _pending_candidate(episode_db)
    rejected = MemoryReviewGate(episode_db).reject(cand.candidate_id)
    assert rejected.review_status == MemoryCandidateStatus.REJECTED.value
    stored = episode_db.get_candidate(cand.candidate_id)
    assert stored.review_status == MemoryCandidateStatus.REJECTED.value


def test_reject_unique_prefix_candidate_id(episode_db):
    from core.brain_v2.memory_review_gate import MemoryReviewGate

    cand = _pending_candidate(episode_db)
    prefix = cand.candidate_id[:8]
    assert len(episode_db.get_candidate(prefix).candidate_id) == len(cand.candidate_id)

    rejected = MemoryReviewGate(episode_db).reject(prefix)
    assert rejected.candidate_id == cand.candidate_id
    assert rejected.review_status == MemoryCandidateStatus.REJECTED.value
    assert not episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)


def test_cli_reject_prefix_removes_from_pending(episode_db, capsys):
    from core.brain_v2 import cli as brain_cli
    from core.brain_v2.coordinator import BrainV2Coordinator

    cand = _pending_candidate(episode_db)
    prefix = cand.candidate_id[:8]
    coord = BrainV2Coordinator(store=episode_db)

    with patch.object(brain_cli, "_coordinator_readonly", return_value=coord):
        assert brain_cli.run_brain_v2_cli("reject", prefix) == 0

    assert not episode_db.get_candidates(status=MemoryCandidateStatus.PENDING)
    out = capsys.readouterr().out
    assert cand.candidate_id in out


def test_cli_accept_no_promote_with_prefix(episode_db):
    from core.brain_v2 import cli as brain_cli
    from core.brain_v2.coordinator import BrainV2Coordinator

    cand = _pending_candidate(episode_db)
    prefix = cand.candidate_id[:8]
    coord = BrainV2Coordinator(store=episode_db)
    coord.promoter.promote = MagicMock(return_value="should-not-run")

    with patch.object(brain_cli, "_coordinator_readonly", return_value=coord):
        assert brain_cli.run_brain_v2_cli("accept_no_promote", prefix) == 0

    coord.promoter.promote.assert_not_called()
    assert episode_db.get_accepted_memories()
    stored = episode_db.get_candidate(cand.candidate_id)
    assert stored.review_status == MemoryCandidateStatus.ACCEPTED.value
