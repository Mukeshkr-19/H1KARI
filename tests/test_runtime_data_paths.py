"""Runtime data must not be written under the public repo data/ directory."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVICES = REPO_ROOT / "services"
SECURITY = REPO_ROOT / "security"
SKILLS = REPO_ROOT / "skills"


def _assert_no_public_data_path_in_source(text: str) -> None:
    assert 'Path(__file__).parent.parent / "data"' not in text
    assert 'os.path.join(_REPO_ROOT, "data"' not in text
    assert "_REPO_ROOT" not in text or '"data", "memory"' not in text


@pytest.fixture
def episode_db(tmp_path):
    from core.brain_v2 import EpisodeStore

    return EpisodeStore(db_path=tmp_path / "runtime_safety_v2.db")


def test_hikari_daemon_does_not_hardcode_public_data_dir():
    text = (SERVICES / "hikari_daemon.py").read_text(encoding="utf-8")
    assert 'os.path.join(_REPO_ROOT, "data"' not in text
    assert "legacy_data_dir" in text
    assert "LEGACY_DATA_DIR / \"learning.json\"" in text


def test_security_auth_does_not_hardcode_public_data_dir():
    text = (SECURITY / "auth.py").read_text(encoding="utf-8")
    _assert_no_public_data_path_in_source(text)
    assert "legacy_data_dir" in text


def test_security_enhanced_auth_does_not_hardcode_public_data_dir():
    text = (SECURITY / "enhanced_auth.py").read_text(encoding="utf-8")
    _assert_no_public_data_path_in_source(text)
    assert "legacy_data_dir" in text


def test_memory_skills_does_not_hardcode_public_data_dir():
    text = (SKILLS / "memory_skills.py").read_text(encoding="utf-8")
    _assert_no_public_data_path_in_source(text)
    assert "legacy_data_dir" in text
    assert 'legacy_data_dir() / "memory"' in text


def test_runtime_modules_resolve_under_legacy_data_env(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy-data"
    monkeypatch.setenv("HIKARI_LEGACY_DATA_DIR", str(legacy))

    from core.runtime_paths import legacy_data_dir

    assert legacy_data_dir() == legacy

    # Re-import path constants (module-level) after env is set.
    import importlib
    import security.auth as auth_mod
    import security.enhanced_auth as enhanced_mod
    import skills.memory_skills as mem_skills_mod

    importlib.reload(auth_mod)
    importlib.reload(enhanced_mod)
    importlib.reload(mem_skills_mod)

    assert auth_mod.DATA_DIR == legacy
    assert enhanced_mod.DATA_DIR == legacy
    assert mem_skills_mod.MEMORY_DIR == legacy / "memory"
    assert not str(auth_mod.DATA_DIR).startswith(str(REPO_ROOT / "data"))


def test_legacy_data_dir_default_is_under_private_brain(monkeypatch, tmp_path):
    brain = tmp_path / "brain"
    monkeypatch.setenv("HIKARI_BRAIN_DIR", str(brain))
    monkeypatch.delenv("HIKARI_LEGACY_DATA_DIR", raising=False)

    from core.runtime_paths import legacy_data_dir

    assert legacy_data_dir() == brain / "legacy-data"
    assert "data" not in legacy_data_dir().parts[-2:]


def test_brain_v2_accept_no_promote_skips_neural_promotion(episode_db):
    from core.brain_v2 import cli as brain_cli
    from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
    from core.brain_v2.coordinator import BrainV2Coordinator

    coord = BrainV2Coordinator(store=episode_db)
    episode_id = episode_db.create_episode("cli-np")
    episode_db.add_turn(episode_id, "Remember this: I prefer local-first tools.")
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    cand_id = candidates[0].candidate_id

    coord.promoter.promote = MagicMock(return_value="should-not-run")

    with patch.object(brain_cli, "_coordinator_readonly", return_value=coord):
        rc = brain_cli.run_brain_v2_cli("accept_no_promote", cand_id)

    assert rc == 0
    coord.promoter.promote.assert_not_called()
    assert episode_db.get_accepted_memories()


def test_brain_v2_accept_with_promote_calls_promoter(episode_db, capsys):
    from core.brain_v2 import cli as brain_cli
    from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
    from core.brain_v2.coordinator import BrainV2Coordinator

    coord = BrainV2Coordinator(store=episode_db)
    episode_id = episode_db.create_episode("cli-p")
    episode_db.add_turn(episode_id, "My name is Alex and I live in City B.")
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    cand_id = candidates[0].candidate_id

    coord.promoter.promote = MagicMock(return_value="brain_v2:test")

    with patch.object(brain_cli, "_coordinator_promote", return_value=coord):
        rc = brain_cli.run_brain_v2_cli(
            "accept",
            cand_id,
            confirm_promote=brain_cli.PROMOTE_CONFIRM_TOKEN,
        )

    assert rc == 0
    coord.promoter.promote.assert_called_once()
    out = capsys.readouterr().out
    assert "Promoted to neural memory: brain_v2:test" in out


def test_brain_v2_cli_status_and_pending(episode_db, capsys):
    from core.brain_v2 import cli as brain_cli
    from core.brain_v2.coordinator import BrainV2Coordinator

    coord = BrainV2Coordinator(store=episode_db)
    session = coord.start_session()
    coord.record_turn(session, "Remember this: flights on July 3.", "OK.")
    coord.close_and_consolidate(session)

    with patch.object(brain_cli, "_coordinator_readonly", return_value=coord):
        assert brain_cli.run_brain_v2_cli("status") == 0
        status_out = capsys.readouterr().out
        assert "Brain v2" in status_out

        assert brain_cli.run_brain_v2_cli("pending") == 0
        pending_out = capsys.readouterr().out
        assert "Pending" in pending_out or "No pending" in pending_out
