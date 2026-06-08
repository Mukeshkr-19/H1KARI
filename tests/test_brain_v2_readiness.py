"""Brain v2 redacted readiness / sign-off command (generic fixtures only)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.readiness import (
    BRAIN_V2_RUNTIME_DEGRADED,
    LEGACY_PERSONAL_RECALL_DEGRADED,
    READINESS_NOT_READY,
    READINESS_READY,
    REPAIR_MODE_READ_ONLY_PLAN,
    REPAIR_APPLY_NOT_IMPLEMENTED,
    assess_brain_v2_readiness,
    format_readiness_lines,
)
from core.brain_v2.legacy_neural_repair import apply_neural_repair_item
from core.brain_v2.legacy_reconciliation import RepairPlanItem
from core.brain_v2.recall_intent import is_plausible_personal_memory_query
from core.brain_v2.memory_review_gate import MemoryReviewGate
from core.path_literals import EPISODES_DB, HIKARI_MEMORY_DB


def _seed_fake_neural(path: Path) -> None:
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
        conn.execute(
            "INSERT INTO nodes (node_type, name, content) VALUES (?, ?, ?)",
            ("LOCATION", "City C", "legacy home"),
        )
        conn.commit()


def test_readiness_ready_on_clean_fake_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    report = assess_brain_v2_readiness(episodes_store=store)
    assert report.state == READINESS_READY
    assert report.pending_count == 0
    assert report.legacy_personal_recall_authority == "quarantined"
    assert report.legacy_personal_answer_path == "quarantined"
    assert report.legacy_personal_prompt_path == "quarantined"
    assert report.legacy_personal_write_path == "quarantined"
    assert report.unsafe_override_active is False
    assert report.personal_factual_general_ai_fallback == "blocked"
    assert report.repair_capability == REPAIR_MODE_READ_ONLY_PLAN
    assert report.repair_apply_status == REPAIR_APPLY_NOT_IMPLEMENTED
    lines = format_readiness_lines(report)
    blob = "\n".join(lines)
    assert "READY" in blob
    assert "City C" not in blob


def test_readiness_not_ready_with_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("pending-ep")
    store.add_turn(
        episode_id,
        "Remember this: Owner A prefers Restaurant A.",
        is_user=True,
    )
    EpisodeConsolidationPipeline(store).process_episode(episode_id)
    report = assess_brain_v2_readiness(episodes_store=store)
    assert report.state == READINESS_NOT_READY
    assert report.pending_count >= 1
    assert any("pending_review" in b for b in report.blockers)


def test_readiness_ready_with_quarantined_legacy_only_neural(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_fake_neural(neural_db)
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(neural_db))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    report = assess_brain_v2_readiness(episodes_store=store)
    assert report.state == READINESS_READY
    assert report.legacy_personal_recall_authority == "quarantined"
    assert report.legacy_rows_preserved_for_private_migration >= 1
    lines = format_readiness_lines(report)
    assert "quarantined" in "\n".join(lines)
    assert "legacy_rows_preserved_for_private_migration=1" in "\n".join(lines)


def test_readiness_not_ready_when_brain_v2_disabled_and_legacy_present(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_DISABLE_BRAIN_V2", "1")
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_fake_neural(neural_db)
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(neural_db))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    report = assess_brain_v2_readiness(episodes_store=store)
    assert report.state == READINESS_NOT_READY
    assert report.legacy_personal_recall_authority == "not_quarantined"
    assert any("not_quarantined" in b for b in report.blockers)


def test_readiness_ready_with_quarantined_stale_home_beyond_reporting_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    from tests.test_brain_v2_legacy_reconciliation import _seed_many_rows_plus_stale_home

    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_many_rows_plus_stale_home(neural_db, filler_count=301)
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(neural_db))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("stale-old-row")
    store.add_turn(episode_id, "Remember this: I live in City A.", is_user=True)
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    MemoryReviewGate(store).accept(candidates[0].candidate_id)
    report = assess_brain_v2_readiness(episodes_store=store)
    assert report.state == READINESS_READY
    lines = format_readiness_lines(report)
    assert "archival_legacy_findings" in "\n".join(lines)
    assert "stale_stable_location" in "\n".join(lines)
    assert "City A" not in "\n".join(lines)
    assert "City B" not in "\n".join(lines)


def test_readiness_ready_with_many_unrelated_neural_rows(tmp_path, monkeypatch):
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
        for idx in range(1, 302):
            conn.execute(
                "INSERT INTO nodes (node_type, name, content) VALUES (?, ?, ?)",
                ("FACT", f"Place {idx}", f"neutral filler {idx}"),
            )
        conn.commit()
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(neural_db))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("neutral-accepted")
    store.add_turn(
        episode_id,
        "Remember this: Owner A prefers Topic A for discussions.",
        is_user=True,
    )
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    MemoryReviewGate(store).accept(candidates[0].candidate_id)
    report = assess_brain_v2_readiness(episodes_store=store)
    assert report.state == READINESS_READY
    assert report.legacy_personal_recall_authority == "quarantined"
    assert report.legacy_rows_preserved_for_private_migration >= 300


def test_readiness_ready_reviewed_truth_plus_preserved_legacy_rows(tmp_path, monkeypatch):
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
            ("FACT", "Topic Z", "unrelated legacy filler"),
        )
        conn.commit()
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(neural_db))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("reviewed-with-legacy")
    store.add_turn(episode_id, "Remember this: I live in City A.", is_user=True)
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    MemoryReviewGate(store).accept(candidates[0].candidate_id)
    report = assess_brain_v2_readiness(episodes_store=store)
    assert report.state == READINESS_READY
    assert report.legacy_personal_recall_authority == "quarantined"
    assert report.legacy_rows_preserved_for_private_migration >= 1
    assert not report.unresolved_conflict_categories


def test_readiness_ready_with_quarantined_legacy_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_fake_neural(neural_db)
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(neural_db))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("conflict-ep")
    store.add_turn(episode_id, "Remember this: I live in City A.", is_user=True)
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    MemoryReviewGate(store).accept(candidates[0].candidate_id)
    report = assess_brain_v2_readiness(episodes_store=store)
    assert report.state == READINESS_READY
    assert report.unresolved_conflict_categories
    lines = format_readiness_lines(report)
    assert "archival_legacy_findings" in "\n".join(lines)
    assert "stale_stable_location" in "\n".join(lines)
    assert "City A" not in "\n".join(lines)
    assert "City C" not in "\n".join(lines)


@pytest.mark.parametrize("unsafe_value", ["1", "true", "yes"])
def test_readiness_not_ready_when_unsafe_override_env_set(
    tmp_path, monkeypatch, unsafe_value: str
):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    monkeypatch.setenv("HIKARI_BRAIN_V2_UNSAFE_NEURAL_PROFILE_SUPPLEMENT", unsafe_value)
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    report = assess_brain_v2_readiness(episodes_store=store)
    assert report.state == READINESS_NOT_READY
    assert report.unsafe_override_active is True
    assert any("unsafe_neural_profile_supplement" in b for b in report.blockers)
    assert report.legacy_personal_prompt_path == "quarantined"


def test_readiness_degraded_when_runtime_probe_fails(monkeypatch, tmp_path):
    missing = tmp_path / "missing_ep_store.sqlite"
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(missing))
    report = assess_brain_v2_readiness()
    assert report.brain_v2_policy == "enabled"
    assert report.brain_v2_runtime == BRAIN_V2_RUNTIME_DEGRADED
    assert report.legacy_personal_recall_authority == LEGACY_PERSONAL_RECALL_DEGRADED
    assert report.state == READINESS_NOT_READY
    assert any("degraded_unavailable" in b for b in report.blockers)


@pytest.mark.parametrize(
    "query",
    [
        "is my sister in City A?",
        "is my brother in City B?",
        "are my parents in City A?",
        "is my mother in City B?",
        "is my father in City A?",
    ],
)
def test_redteam_sibling_parent_in_place_is_personal(query: str):
    assert is_plausible_personal_memory_query(query)


@pytest.mark.parametrize(
    "command",
    ["what do you remember?", "what have we talked about?"],
)
def test_redteam_memory_summary_commands_classified_general(command: str):
    from core.brain_v2.recall_intent import classify_recall_intent, INTENT_GENERAL_MEMORY

    assert classify_recall_intent(command) == INTENT_GENERAL_MEMORY


def test_redteam_production_repair_apply_unavailable():
    with pytest.raises(NotImplementedError, match="not implemented"):
        apply_neural_repair_item(
            neural_db_path=Path("/tmp/unused.db"),
            backup_path=Path("/tmp/unused_backup.db"),
            item=RepairPlanItem(
                plan_id="rt1",
                category="test",
                action="archive_neural",
                neural_target_id="n1",
                status="not_applied",
            ),
            confirm_token="REPAIR",
        )
