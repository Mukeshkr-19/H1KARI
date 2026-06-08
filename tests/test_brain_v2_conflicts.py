"""Brain v2 read-only conflict scanner between reviewed memories and neural profile."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
import pytest

from core.brain_v2.conflicts import (
    ACTION_CLEAN_MANUALLY,
    ACTION_IGNORE,
    ACTION_REVIEW,
    CONFLICT_PARTNER_EDUCATION_ON_USER,
    CONFLICT_REDACTED_PLACEHOLDER,
    CONFLICT_STALE_CURRENTLY_IN,
    CONFLICT_STALE_HOME,
    CONFLICT_UNREVIEWED_LEGACY_HOME,
    NEURAL_UNAVAILABLE_NOTICE,
    fetch_neural_summary_quiet,
    format_conflict_report_lines,
    run_brain_v2_conflicts,
    scan_conflicts,
)
from core.brain_v2.schemas import SourceLinkedMemory
from tests.privacy_scan import REPO_ROOT
from core.path_literals import DOT_HIKARI, EPISODES_DB, HIKARI_MEMORY_DB


def _memory(
    statement: str,
    *,
    candidate_type: str,
    **metadata,
) -> SourceLinkedMemory:
    meta = {"candidate_type": candidate_type, **metadata}
    return SourceLinkedMemory(
        memory_id="mem-test",
        candidate_id="cand-test",
        episode_id="ep-test",
        statement=statement,
        metadata=meta,
    )


def test_default_conflict_output_is_redacted():
    memories = [
        _memory("I live in City A.", candidate_type="location", location="City A"),
    ]
    neural = "What I know about you:\n- Home: City B\n"
    reports = scan_conflicts(memories, neural)
    lines = format_conflict_report_lines(reports, redact=True)
    blob = "\n".join(lines)
    assert CONFLICT_STALE_HOME in blob
    assert CONFLICT_REDACTED_PLACEHOLDER in blob
    assert "city a" not in blob.lower()
    assert "city b" not in blob.lower()


def test_private_conflict_output_may_include_statements():
    memories = [
        _memory("I live in City A.", candidate_type="location", location="City A"),
    ]
    neural = "What I know about you:\n- Home: City B\n"
    reports = scan_conflicts(memories, neural)
    lines = format_conflict_report_lines(reports, redact=False)
    blob = "\n".join(lines)
    assert "city a" in blob.lower()
    assert "city b" in blob.lower()


def test_unreviewed_legacy_home_when_no_reviewed_location():
    neural = "What I know about you:\n- Home: City B\n"
    reports = scan_conflicts([], neural)
    assert len(reports) == 1
    assert reports[0].conflict_type == CONFLICT_UNREVIEWED_LEGACY_HOME
    assert reports[0].recommended_action == ACTION_REVIEW


def test_finds_stale_home_conflict():
    memories = [
        _memory("I live in City A.", candidate_type="location", location="City A"),
    ]
    neural = "What I know about you:\n- Home: City B\n"
    reports = scan_conflicts(memories, neural)
    assert len(reports) == 1
    report = reports[0]
    assert report.conflict_type == CONFLICT_STALE_HOME
    assert "city a" in report.reviewed_statement.lower()
    assert report.conflicting_line.lower().startswith("- home:")
    assert "city b" in report.conflicting_line.lower()
    assert report.recommended_action == ACTION_CLEAN_MANUALLY


def test_partner_described_neural_rows_do_not_trigger_owner_education_conflict():
    from core.brain_v2.legacy_reconciliation import (
        NeuralFactRow,
        build_neural_summary_lines,
    )

    rows = [
        NeuralFactRow(
            1,
            "FACT",
            "School A",
            "Partner Person B student education at School A",
        ),
    ]
    summary = build_neural_summary_lines(rows)
    memories = [
        _memory(
            "my partner Person B is a student at School A.",
            candidate_type="education",
            relation="partner",
            organization="School A",
        ),
    ]
    assert scan_conflicts(memories, summary) == []


def test_finds_partner_education_attributed_to_user_incorrectly():
    memories = [
        _memory(
            "my girlfriend Jamie is a medical student at School A.",
            candidate_type="education",
            relation="girlfriend",
            organization="School A",
        ),
    ]
    neural = "What I know about you:\n- Education: School A\n"
    reports = scan_conflicts(memories, neural)
    assert len(reports) == 1
    report = reports[0]
    assert report.conflict_type == CONFLICT_PARTNER_EDUCATION_ON_USER
    assert "school a" in report.reviewed_statement.lower()
    assert report.conflicting_line.lower().startswith("- education:")
    assert report.recommended_action == ACTION_CLEAN_MANUALLY


def test_no_conflict_when_lines_agree():
    memories = [
        _memory("I live in City A.", candidate_type="location", location="City A"),
        _memory(
            "Right now I'm in City B for summer holidays.",
            candidate_type="current_location",
            current_location="City B",
        ),
    ]
    neural = (
        "What I know about you:\n"
        "- Home: City A\n"
        "- Currently in: City B\n"
    )
    assert scan_conflicts(memories, neural) == []


def test_finds_stale_currently_in_when_reviewed_current_context():
    memories = [
        _memory(
            "Right now I'm in City B for summer holidays.",
            candidate_type="current_location",
            current_location="City B",
        ),
    ]
    neural = "What I know about you:\n- Currently in: City C\n"
    reports = scan_conflicts(memories, neural)
    assert len(reports) == 1
    assert reports[0].conflict_type == CONFLICT_STALE_CURRENTLY_IN
    assert "city b" in reports[0].reviewed_statement.lower()


def test_stale_currently_in_with_only_home_reviewed_is_ignore():
    memories = [
        _memory("I live in City A.", candidate_type="location", location="City A"),
    ]
    neural = "What I know about you:\n- Currently in: City C\n"
    reports = scan_conflicts(memories, neural)
    assert len(reports) == 1
    assert reports[0].conflict_type == CONFLICT_STALE_CURRENTLY_IN
    assert reports[0].recommended_action == ACTION_IGNORE


def test_empty_neural_summary_returns_no_conflicts():
    memories = [
        _memory("I live in City A.", candidate_type="location", location="City A"),
    ]
    assert scan_conflicts(memories, None) == []
    assert scan_conflicts(memories, "") == []
    assert scan_conflicts(memories, "   ") == []


def test_fetch_neural_summary_default_skips_neural(monkeypatch):
    monkeypatch.delenv("HIKARI_BRAIN_V2_CONFLICTS_PRIVATE", raising=False)
    summary, available = fetch_neural_summary_quiet()
    assert summary is None
    assert available is False


def test_fetch_neural_summary_private_reads_configured_db_only(
    tmp_path, monkeypatch
):
    import sqlite3

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
            ("LOCATION", "City B", "legacy home"),
        )
        conn.commit()
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(neural_db))
    monkeypatch.setenv("HIKARI_BRAIN_V2_CONFLICTS_PRIVATE", "1")
    summary, available = fetch_neural_summary_quiet()
    assert available is True
    assert summary
    assert "city b" in summary.lower()


def test_run_brain_v2_conflicts_prints_unavailable_notice(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    class _FakeStore:
        def get_accepted_memories(self, limit=200):
            return []

    def _fake_store(*, db_path=None, create_dirs=True):
        return _FakeStore()

    monkeypatch.setattr(
        "core.brain_v2.episode_store.EpisodeStore",
        _fake_store,
    )
    monkeypatch.setattr(
        "core.brain_v2.conflicts._default_episodes_db_path",
        lambda: tmp_path / "missing.db",
    )
    code = run_brain_v2_conflicts()
    captured = capsys.readouterr()
    assert code == 0
    assert NEURAL_UNAVAILABLE_NOTICE in captured.out
    assert "city a" not in captured.out.lower()
    assert "city b" not in captured.out.lower()
    assert not (tmp_path / DOT_HIKARI).exists()


def test_conflicts_cli_subprocess_no_hikari_dir(tmp_path):
    """CLI --brain-v2-conflicts with temp HOME must not touch live brain directory."""
    home_brain = tmp_path / DOT_HIKARI / "brain"
    home_brain.mkdir(parents=True)
    marker = home_brain / EPISODES_DB
    marker.write_text("must-not-touch", encoding="utf-8")
    before = marker.read_text(encoding="utf-8")

    env = {**dict(**__import__("os").environ), "HOME": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "hikari.py"), "--brain-v2-conflicts"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    combined = f"{proc.stdout}\n{proc.stderr}".lower()
    assert proc.returncode == 0, proc.stderr
    assert NEURAL_UNAVAILABLE_NOTICE.lower() in combined
    assert "failed to initialize hikari memory" not in combined
    assert marker.read_text(encoding="utf-8") == before
    assert not (tmp_path / DOT_HIKARI / "brain" / HIKARI_MEMORY_DB).exists()
