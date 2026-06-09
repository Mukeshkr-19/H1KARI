"""Brain v2 repair CLI: preview, confirmation, history, and rollback safety."""

from __future__ import annotations

import io
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import pytest

from core.brain_v2.cli import (
    cmd_edit_metadata,
    cmd_memory_history,
    cmd_repair_show,
    cmd_retire,
    cmd_supersede,
    run_brain_v2_cli_retire,
)
from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.memory_lifecycle import LIFECYCLE_RETIRED, lifecycle_status
from core.brain_v2.memory_repair import MemoryRepairGate
from core.brain_v2.memory_review_gate import MemoryReviewGate
from core.brain_v2.repair_safety import (
    REPAIR_CONFIRM_EDIT,
    REPAIR_CONFIRM_RETIRE,
    REPAIR_CONFIRM_SUPERSEDE,
)
from core.path_literals import EPISODES_DB


def _accept_statement(store: EpisodeStore, statement: str, episode_key: str = "ep") -> str:
    episode_id = store.create_episode(episode_key)
    store.add_turn(episode_id, statement, is_user=True)
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    assert candidates
    linked = MemoryReviewGate(store).accept(candidates[0].candidate_id)
    return linked.memory_id


@pytest.fixture
def episode_store(tmp_path, monkeypatch):
    db = tmp_path / EPISODES_DB
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(db))
    return EpisodeStore(db_path=db)


def test_repair_show_read_only(episode_store):
    memory_id = _accept_statement(episode_store, "Owner A lives in City A.")
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cmd_repair_show(memory_id) == 0
    out = buf.getvalue()
    assert "memory_id:" in out
    assert "city a" in out.lower()
    assert "source-linked transcript" in out.lower()


def test_retire_preview_does_not_mutate(episode_store):
    memory_id = _accept_statement(episode_store, "Owner A lives in City A.")
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cmd_retire(memory_id, preview=True) == 0
    assert "Repair preview: RETIRE" in buf.getvalue()
    assert lifecycle_status(episode_store.get_source_linked_memory(memory_id).metadata) == "active"


def test_supersede_preview_does_not_mutate(episode_store):
    memory_id = _accept_statement(episode_store, "Owner A lives in City A.")
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cmd_supersede(
            memory_id,
            "Owner A lives in City B.",
            preview=True,
        ) == 0
    out = buf.getvalue()
    assert "Repair preview: SUPERSEDE" in out
    assert "city b" in out.lower()
    assert lifecycle_status(episode_store.get_source_linked_memory(memory_id).metadata) == "active"


def test_edit_metadata_preview_does_not_mutate(episode_store):
    memory_id = _accept_statement(episode_store, "Owner A lives in City A.")
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cmd_edit_metadata(
            memory_id, candidate_type="location", preview=True
        ) == 0
    assert "Repair preview: EDIT_METADATA" in buf.getvalue()


@pytest.fixture
def require_live_repair_confirm(monkeypatch):
    monkeypatch.setattr(
        "core.brain_v2.repair_safety.repair_confirmation_required",
        lambda: True,
    )


def test_retire_requires_confirm_on_live_db(episode_store, require_live_repair_confirm):
    memory_id = _accept_statement(episode_store, "Owner A lives in City A.")
    err = io.StringIO()
    with redirect_stderr(err):
        rc = cmd_retire(memory_id)
    assert rc == 1
    assert "confirm-repair RETIRE" in err.getvalue()
    assert lifecycle_status(episode_store.get_source_linked_memory(memory_id).metadata) == "active"


def test_retire_wrong_confirm_token_refuses(episode_store, require_live_repair_confirm):
    memory_id = _accept_statement(episode_store, "Owner A lives in City A.")
    err = io.StringIO()
    with redirect_stderr(err):
        rc = cmd_retire(memory_id, confirm_repair="WRONG")
    assert rc == 1
    assert "expected exactly RETIRE" in err.getvalue()


def test_retire_with_confirm_on_live_db(episode_store, require_live_repair_confirm):
    memory_id = _accept_statement(episode_store, "Owner A lives in City A.")
    rc = run_brain_v2_cli_retire(memory_id, confirm_repair=REPAIR_CONFIRM_RETIRE)
    assert rc == 0
    assert lifecycle_status(episode_store.get_source_linked_memory(memory_id).metadata) == LIFECYCLE_RETIRED


def test_supersede_confirm_token(episode_store, require_live_repair_confirm):
    memory_id = _accept_statement(episode_store, "Owner A lives in City A.")
    err = io.StringIO()
    with redirect_stderr(err):
        rc = cmd_supersede(memory_id, "Owner A lives in City B.")
    assert rc == 1
    assert "SUPERSEDE" in err.getvalue()

    assert (
        cmd_supersede(
            memory_id,
            "Owner A lives in City B.",
            confirm_repair=REPAIR_CONFIRM_SUPERSEDE,
        )
        == 0
    )
    active = episode_store.get_active_accepted_memories(limit=5)
    assert len(active) == 1
    assert "city b" in active[0].statement.lower()


def test_edit_metadata_confirm_token(episode_store, require_live_repair_confirm):
    memory_id = _accept_statement(episode_store, "Remember this: Owner A studies at School A.")
    err = io.StringIO()
    with redirect_stderr(err):
        rc = cmd_edit_metadata(memory_id, candidate_type="education")
    assert rc == 1
    assert "EDIT" in err.getvalue()

    assert (
        cmd_edit_metadata(
            memory_id,
            candidate_type="education",
            confirm_repair=REPAIR_CONFIRM_EDIT,
        )
        == 0
    )
    meta = episode_store.get_source_linked_memory(memory_id).metadata or {}
    assert meta.get("candidate_type") == "education"
    assert meta.get("correction_audit")


def test_memory_history_shows_audit_after_retire(episode_store):
    memory_id = _accept_statement(episode_store, "Remember this: Owner A prefers Topic A.")
    MemoryRepairGate(episode_store).retire(memory_id, reason="test_audit")
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cmd_memory_history(memory_id) == 0
    assert "last_audit: retire" in buf.getvalue()


def test_failed_supersede_cli_rolls_back(episode_store):
    memory_id = _accept_statement(episode_store, "Owner A lives in City A.")
    with patch.object(
        episode_store,
        "_persist_source_linked_conn",
        side_effect=RuntimeError("simulated"),
    ):
        with pytest.raises(RuntimeError):
            MemoryRepairGate(episode_store).supersede(
                memory_id, statement="Owner A lives in City B."
            )
    assert lifecycle_status(episode_store.get_source_linked_memory(memory_id).metadata) == "active"


def test_hikari_cli_repair_preview_subprocess(episode_store, monkeypatch):
    memory_id = _accept_statement(episode_store, "Owner A lives in City A.")
    env = {"HIKARI_BRAIN_V2_EPISODES_DB": str(episode_store.db_path)}
    repo = __import__("os").path.dirname(
        __import__("os").path.dirname(__import__("os").path.abspath(__file__))
    )
    proc = subprocess.run(
        [
            sys.executable,
            "hikari.py",
            "--brain-v2-retire",
            memory_id,
            "--repair-preview",
        ],
        cwd=repo,
        env={**__import__("os").environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Repair preview: RETIRE" in proc.stdout
    assert lifecycle_status(episode_store.get_source_linked_memory(memory_id).metadata) == "active"
