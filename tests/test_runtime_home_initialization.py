"""Runtime initialization is explicit, private, idempotent, and download-free."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_init_plan_is_read_only(tmp_path):
    from core.runtime_setup import initialization_plan

    root = tmp_path / "state"
    plan = initialization_plan("text", root=root)

    assert plan["create"]
    assert not root.exists()
    assert plan["selection"]["voice"]["audio_egress"] is False


def test_init_creates_private_layout_and_config(tmp_path):
    from core.runtime_setup import initialize_runtime_home

    root = tmp_path / "state"
    result = initialize_runtime_home("voice", "openai-whisper", root=root)
    config_path = root / "runtime.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert result["already_initialized"] is False
    assert config["startup_mode"] == "voice"
    assert config["voice"]["backend"] == "openai-whisper"
    assert "download" in config["voice"]["download"].lower()
    assert config["voice"]["audio_egress"] is True
    assert (root / "brain" / "brain_v2").is_dir()
    assert (root / "brain" / "tasks").is_dir()
    assert (root / "brain" / "legacy-data").is_dir()
    assert (root / "backups").is_dir()
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert all(path.stat().st_mode & 0o777 == 0o700 for path in result["created"])


def test_matching_init_is_idempotent_and_conflict_fails(tmp_path):
    from core.runtime_setup import initialize_runtime_home

    root = tmp_path / "state"
    initialize_runtime_home("text", root=root)

    repeated = initialize_runtime_home("text", root=root)
    assert repeated["already_initialized"] is True
    assert repeated["created"] == []

    with pytest.raises(RuntimeError, match="different startup settings"):
        initialize_runtime_home("voice", "faster-whisper", root=root)


def test_init_refuses_existing_brain_symlink(tmp_path):
    from core.runtime_setup import initialize_runtime_home

    root = tmp_path / "state"
    target = tmp_path / "legacy-brain"
    root.mkdir()
    target.mkdir()
    (root / "brain").symlink_to(target, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink"):
        initialize_runtime_home("text", root=root)

    assert not (root / "runtime.json").exists()


def test_init_refuses_config_symlink(tmp_path):
    from core.runtime_setup import initialize_runtime_home

    root = tmp_path / "state"
    target = tmp_path / "outside.json"
    root.mkdir()
    target.write_text("{}", encoding="utf-8")
    (root / "runtime.json").symlink_to(target)

    with pytest.raises(RuntimeError, match="regular file"):
        initialize_runtime_home("text", root=root)

    assert target.read_text(encoding="utf-8") == "{}"


def test_init_refuses_code_checkout_root(tmp_path):
    from core.runtime_setup import initialize_runtime_home

    checkout = tmp_path / "checkout"
    (checkout / "core").mkdir(parents=True)
    (checkout / "hikari.py").touch()

    with pytest.raises(RuntimeError, match="code checkout"):
        initialize_runtime_home("text", root=checkout)


def test_voice_selection_validation():
    from core.runtime_setup import initialization_plan

    with pytest.raises(ValueError, match="explicit supported backend"):
        initialization_plan("voice")
    with pytest.raises(ValueError, match="requires startup mode voice"):
        initialization_plan("text", "google-speech")


def test_init_cli_loads_no_voice_model(tmp_path):
    marker = tmp_path / "model-imported"
    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    module_source = (
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['HIKARI_TEST_MODEL_MARKER']).write_text('imported')\n"
    )
    for name in ("whisper", "faster_whisper", "speechbrain"):
        (module_dir / f"{name}.py").write_text(module_source, encoding="utf-8")
    state_home = tmp_path / "state"
    env = {
        **os.environ,
        "HIKARI_HOME": str(state_home),
        "HIKARI_TEST_MODEL_MARKER": str(marker),
        "PYTHONPATH": os.pathsep.join((str(module_dir), str(REPO_ROOT))),
    }
    for name in (
        "HIKARI_BRAIN_DIR",
        "HIKARI_BRAIN_V2_EPISODES_DB",
        "HIKARI_NEURAL_MEMORY_DB",
        "HIKARI_LEGACY_DATA_DIR",
    ):
        env.pop(name, None)

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "hikari.py"),
            "--init",
            "--startup-mode",
            "voice",
            "--voice-backend",
            "faster-whisper",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "No model is imported or downloaded" in result.stdout
    assert not marker.exists()
