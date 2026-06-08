"""Tests for Brain v2 offline eval (isolated temp DB, synthetic fixtures)."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import pytest

from core.brain_v2.eval import EVAL_FORBIDDEN_OUTPUT_MARKERS, run_brain_v2_eval
from core.path_literals import DOT_HIKARI, EPISODES_DB, HIKARI_MEMORY_DB

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_eval_returns_success_when_all_pass():
    result = run_brain_v2_eval()
    assert result.exit_code == 0, result.report
    assert "PASS" in result.report
    assert "0/" not in result.report.split("\n")[-1]


def test_eval_output_has_no_forbidden_neural_or_path_leaks():
    result = run_brain_v2_eval()
    low = result.report.lower()
    for marker in EVAL_FORBIDDEN_OUTPUT_MARKERS:
        assert marker not in low, f"forbidden marker {marker!r} in eval report"


def test_eval_uses_temp_path_not_home_hikari(monkeypatch, tmp_path):
    home_brain = tmp_path / DOT_HIKARI / "brain"
    home_brain.mkdir(parents=True)
    marker = home_brain / EPISODES_DB
    marker.write_text("must-not-touch", encoding="utf-8")
    before = marker.read_text(encoding="utf-8")

    monkeypatch.setenv("HOME", str(tmp_path))
    result = run_brain_v2_eval()

    assert marker.read_text(encoding="utf-8") == before
    assert result.db_path.is_absolute()
    assert DOT_HIKARI not in str(result.db_path)
    assert str(result.db_path).startswith(tempfile_prefix())


def test_eval_report_is_concise_table():
    result = run_brain_v2_eval()
    lines = result.report.splitlines()
    assert lines[0] == "Brain v2 eval"
    assert any("stable_location_vs_current" in line for line in lines)
    assert lines[-1].endswith("passed")


def tempfile_prefix() -> str:
    import tempfile

    return tempfile.gettempdir()


@pytest.mark.parametrize(
    "field",
    ["exit_code", "report", "db_path"],
)
def test_eval_result_fields(field):
    result = run_brain_v2_eval()
    assert hasattr(result, field)
    if field == "db_path":
        assert isinstance(result.db_path, Path)


def test_eval_no_neural_init_noise(capsys):
    """Eval must not touch or log live Hikari Memory / live brain paths."""
    logging.basicConfig(level=logging.DEBUG, force=True)
    result = run_brain_v2_eval()
    captured = capsys.readouterr()
    combined = f"{captured.out}\n{captured.err}\n{result.report}".lower()
    for marker in EVAL_FORBIDDEN_OUTPUT_MARKERS:
        assert marker not in combined, f"forbidden marker {marker!r} in eval output"


def test_eval_cli_subprocess_poisoned_neural_path_still_passes(tmp_path, monkeypatch):
    """Eval must ignore a configured live-like neural DB and never read it."""
    import os
    import sqlite3

    fake_home = tmp_path / "fake_home_eval"
    fake_home.mkdir()
    poisoned = fake_home / "brain" / HIKARI_MEMORY_DB
    poisoned.parent.mkdir(parents=True)
    with sqlite3.connect(poisoned) as conn:
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
            ("FACT", "Poison", "must not be read by eval"),
        )
        conn.commit()

    env = {
        **dict(os.environ),
        "HOME": str(fake_home),
        "HIKARI_NEURAL_MEMORY_DB": str(poisoned),
    }
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "hikari.py"), "--brain-v2-eval"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "8/8 passed" in proc.stdout
    assert "poison" not in proc.stdout.lower()
    assert "must not be read" not in proc.stdout.lower()
    with sqlite3.connect(poisoned) as conn:
        row = conn.execute("SELECT content FROM nodes WHERE id = 1").fetchone()
    assert row[0] == "must not be read by eval"


def test_eval_cli_subprocess_no_neural_noise(tmp_path):
    """CLI --brain-v2-eval must not create live brain dirs or print neural init failures."""
    home_brain = tmp_path / DOT_HIKARI / "brain"
    home_brain.mkdir(parents=True)
    episodes_marker = home_brain / EPISODES_DB
    episodes_marker.write_text("must-not-touch", encoding="utf-8")
    before = episodes_marker.read_text(encoding="utf-8")

    env = {**dict(**__import__("os").environ), "HOME": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "hikari.py"), "--brain-v2-eval"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    combined = f"{proc.stdout}\n{proc.stderr}".lower()
    for forbidden in EVAL_FORBIDDEN_OUTPUT_MARKERS:
        assert forbidden not in combined, (
            f"forbidden marker {forbidden!r} in CLI eval output "
            f"(exit {proc.returncode})"
        )
    assert proc.returncode == 0, proc.stderr
    assert "brain v2 eval" in proc.stdout.lower()
    assert "passed" in proc.stdout.lower()
    assert episodes_marker.read_text(encoding="utf-8") == before
    assert not (tmp_path / DOT_HIKARI / "brain" / HIKARI_MEMORY_DB).exists()
