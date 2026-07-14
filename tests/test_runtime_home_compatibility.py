"""Launcher compatibility while HIKARI_HOME changes from repo to runtime state."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _fake_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout"
    (checkout / "bin").mkdir(parents=True)
    (checkout / ".venv" / "bin").mkdir(parents=True)
    (checkout / "hikari.py").touch()
    shutil.copy2(REPO_ROOT / "bin" / "Hikari", checkout / "bin" / "Hikari")
    python = checkout / ".venv" / "bin" / "python"
    python.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"${HIKARI_REPO_ROOT-unset}\" \"${HIKARI_HOME-unset}\" \"$@\"\n",
        encoding="utf-8",
    )
    python.chmod(0o755)
    return checkout


def _run_launcher(checkout: Path, env: dict[str, str]) -> list[str]:
    result = subprocess.run(
        [str(checkout / "bin" / "Hikari"), "--voice-status"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.splitlines()


def test_legacy_hikari_home_repo_wrapper_remains_compatible(tmp_path):
    checkout = _fake_checkout(tmp_path)
    env = {**os.environ, "HIKARI_HOME": str(checkout)}
    env.pop("HIKARI_REPO_ROOT", None)

    lines = _run_launcher(checkout, env)

    assert lines[:2] == [str(checkout), "unset"]
    assert lines[2:] == ["-E", str(checkout / "hikari.py"), "--voice-status"]


def test_runtime_hikari_home_does_not_override_checkout(tmp_path):
    checkout = _fake_checkout(tmp_path)
    state_home = tmp_path / "state"
    state_home.mkdir()
    env = {**os.environ, "HIKARI_HOME": str(state_home)}
    env.pop("HIKARI_REPO_ROOT", None)

    lines = _run_launcher(checkout, env)

    assert lines[:2] == [str(checkout), str(state_home)]


def test_repo_root_override_and_runtime_home_coexist(tmp_path):
    checkout = _fake_checkout(tmp_path)
    state_home = tmp_path / "state"
    env = {
        **os.environ,
        "HIKARI_REPO_ROOT": str(checkout),
        "HIKARI_HOME": str(state_home),
    }

    lines = _run_launcher(checkout, env)

    assert lines[:2] == [str(checkout), str(state_home)]
