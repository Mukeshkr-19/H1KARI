"""Runtime backup, migration planning, and rollback must be conservative."""

from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def _initialized(tmp_path: Path) -> Path:
    from core.runtime_setup import initialize_runtime_home

    root = tmp_path / "state"
    initialize_runtime_home("text", root=root)
    return root


def test_backup_copies_state_without_recursing_or_following_symlinks(tmp_path):
    from core.runtime_setup import backup_runtime_home

    root = _initialized(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("private", encoding="utf-8")
    (root / "brain" / "linked").symlink_to(outside, target_is_directory=True)
    destination = tmp_path / "backup"

    result = backup_runtime_home(root=root, destination=destination)

    assert result == destination
    assert (destination / "runtime.json").is_file()
    assert (destination / "brain" / "linked").is_symlink()
    assert not (destination / "backups").exists()


def test_backup_rejects_destination_inside_state_outside_backups(tmp_path):
    from core.runtime_setup import backup_runtime_home

    root = _initialized(tmp_path)
    with pytest.raises(RuntimeError, match="must be under backups"):
        backup_runtime_home(root=root, destination=root / "not-backups" / "copy")


def test_migration_plan_is_read_only_for_legacy_symlink(tmp_path):
    from core.runtime_setup import runtime_migration_plan

    root = tmp_path / "state"
    source = tmp_path / "legacy"
    root.mkdir()
    source.mkdir()
    (root / "brain").symlink_to(source, target_is_directory=True)

    plan = runtime_migration_plan(root=root, repo_root=tmp_path / "repo")

    assert plan["state"] == "legacy brain symlink detected"
    assert plan["source"] == source
    assert (root / "brain").is_symlink()


def test_migration_plan_detects_sibling_private_brain(tmp_path):
    from core.path_literals import HIKARI_PRIVATE
    from core.runtime_setup import runtime_migration_plan

    repo = tmp_path / "repo"
    repo.mkdir()
    source = tmp_path / HIKARI_PRIVATE / "live-brain"
    source.mkdir(parents=True)

    plan = runtime_migration_plan(root=tmp_path / "state", repo_root=repo)

    assert plan["state"] == "legacy sibling brain available"
    assert plan["source"] == source


def test_rollback_removes_only_initialization_paths(tmp_path):
    from core.runtime_setup import rollback_initialization

    root = _initialized(tmp_path)
    removed = rollback_initialization("ROLLBACK", root=root)

    assert removed
    assert not root.exists()


def test_rollback_preserves_preexisting_root(tmp_path):
    from core.runtime_setup import initialize_runtime_home, rollback_initialization

    root = tmp_path / "state"
    root.mkdir()
    initialize_runtime_home("text", root=root)

    rollback_initialization("ROLLBACK", root=root)

    assert root.is_dir()
    assert list(root.iterdir()) == []


def test_rollback_refuses_created_directory_with_new_data(tmp_path):
    from core.runtime_setup import rollback_initialization

    root = _initialized(tmp_path)
    marker = root / "brain" / "tasks" / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(RuntimeError, match="contains data"):
        rollback_initialization("ROLLBACK", root=root)

    assert marker.is_file()
    assert (root / "runtime.json").is_file()


def test_rollback_requires_exact_token(tmp_path):
    from core.runtime_setup import rollback_initialization

    root = _initialized(tmp_path)
    with pytest.raises(ValueError, match="exactly ROLLBACK"):
        rollback_initialization("rollback", root=root)

    assert root.is_dir()


def test_rollback_rejects_tampered_manifest_paths(tmp_path):
    from core.runtime_setup import rollback_initialization

    root = _initialized(tmp_path)
    config_path = root / "runtime.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["created_paths"] = ["../outside", 7]
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(RuntimeError, match="unsafe rollback paths"):
        rollback_initialization("ROLLBACK", root=root)

    assert root.is_dir()


def test_recovery_cli_sequence_uses_isolated_runtime_home(tmp_path):
    state_home = tmp_path / "state"
    backup = tmp_path / "backup"
    env = {**os.environ, "HIKARI_HOME": str(state_home)}
    for name in (
        "HIKARI_BRAIN_DIR",
        "HIKARI_BRAIN_V2_EPISODES_DB",
        "HIKARI_NEURAL_MEMORY_DB",
        "HIKARI_LEGACY_DATA_DIR",
    ):
        env.pop(name, None)

    commands = (
        ("--init", "--startup-mode", "text"),
        ("--runtime-backup", "--backup-destination", str(backup)),
        ("--migration-plan",),
        ("--rollback-init", "ROLLBACK"),
    )
    outputs = []
    for arguments in commands:
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "hikari.py"), *arguments],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout)

    assert "Runtime backup complete" in outputs[1]
    assert "No files were read, copied, moved, or removed" in outputs[2]
    assert "rolled back" in outputs[3]
    assert backup.is_dir()
    assert not state_home.exists()
