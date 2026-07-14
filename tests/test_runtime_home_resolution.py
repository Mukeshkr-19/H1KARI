"""Private runtime defaults must follow HIKARI_HOME without touching the repo."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_hikari_home_defaults_to_private_directory(tmp_path):
    from core.runtime_paths import hikari_home

    assert hikari_home(environ={}, home=tmp_path) == tmp_path / ".hikari"


def test_hikari_home_accepts_explicit_state_root(tmp_path):
    from core.runtime_paths import hikari_home

    state_home = tmp_path / "state"
    assert hikari_home(environ={"HIKARI_HOME": str(state_home)}) == state_home


def test_hikari_home_rejects_code_checkout(tmp_path):
    from core.runtime_paths import hikari_home

    checkout = tmp_path / "checkout"
    (checkout / "core").mkdir(parents=True)
    (checkout / "hikari.py").touch()

    with pytest.raises(RuntimeError, match="HIKARI_REPO_ROOT"):
        hikari_home(environ={"HIKARI_HOME": str(checkout)})


def test_brain_and_episode_defaults_follow_hikari_home(monkeypatch, tmp_path):
    from core.brain_v2.db_paths import resolve_episodes_db_path
    from core.path_literals import EPISODES_DB
    from core.runtime_paths import brain_dir

    state_home = tmp_path / "state"
    monkeypatch.setenv("HIKARI_HOME", str(state_home))
    monkeypatch.delenv("HIKARI_BRAIN_DIR", raising=False)
    monkeypatch.delenv("HIKARI_BRAIN_V2_EPISODES_DB", raising=False)

    assert brain_dir() == state_home / "brain"
    assert resolve_episodes_db_path() == state_home / "brain" / "brain_v2" / EPISODES_DB


def test_specific_brain_overrides_keep_precedence(monkeypatch, tmp_path):
    from core.brain_v2.db_paths import resolve_episodes_db_path
    from core.runtime_paths import brain_dir

    state_home = tmp_path / "state"
    brain = tmp_path / "brain-override"
    episodes = tmp_path / "episodes-override.db"
    monkeypatch.setenv("HIKARI_HOME", str(state_home))
    monkeypatch.setenv("HIKARI_BRAIN_DIR", str(brain))
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(episodes))

    assert brain_dir() == brain
    assert resolve_episodes_db_path() == episodes.resolve()


def test_companion_preferences_follow_hikari_home(monkeypatch, tmp_path):
    from core.voice_companion.preferences import (
        CompanionPreferences,
        save_preferences,
    )

    state_home = tmp_path / "state"
    monkeypatch.setenv("HIKARI_HOME", str(state_home))
    monkeypatch.delenv("HIKARI_COMPANION_PREFS_PATH", raising=False)

    save_preferences(CompanionPreferences("cat", "female"))

    assert (state_home / "companion_ui.json").is_file()


def test_neural_default_follows_hikari_home(monkeypatch, tmp_path):
    from core.neural_memory.config import resolve_neural_brain_dir

    state_home = tmp_path / "state"
    monkeypatch.setenv("HIKARI_HOME", str(state_home))
    monkeypatch.delenv("HIKARI_BRAIN_DIR", raising=False)
    monkeypatch.delenv("HIKARI_NEURAL_MEMORY_DB", raising=False)

    assert resolve_neural_brain_dir() == state_home / "brain"


def test_legacy_neural_maintenance_follows_hikari_home(monkeypatch, tmp_path):
    from core.brain_v2.legacy_neural_repair import canonical_live_neural_db_path
    from core.brain_v2.legacy_reconciliation import resolve_neural_db_path
    from core.path_literals import HIKARI_MEMORY_DB

    state_home = tmp_path / "state"
    neural_db = state_home / "brain" / HIKARI_MEMORY_DB
    neural_db.parent.mkdir(parents=True)
    neural_db.touch()
    monkeypatch.setenv("HIKARI_HOME", str(state_home))
    monkeypatch.delenv("HIKARI_NEURAL_MEMORY_DB", raising=False)

    assert canonical_live_neural_db_path() == neural_db
    assert resolve_neural_db_path() == neural_db
