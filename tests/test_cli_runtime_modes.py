"""The CLI must reject conflicting runtime modes before starting side effects."""

from __future__ import annotations

import itertools
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_MODES = ("--text", "--daemon", "--tray", "--server")


@pytest.mark.parametrize("first,second", itertools.combinations(RUNTIME_MODES, 2))
def test_conflicting_runtime_modes_fail_before_mac_ui_import(
    tmp_path: Path,
    first: str,
    second: str,
):
    marker = tmp_path / "appkit-imported"
    (tmp_path / "AppKit.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['HIKARI_TEST_APPKIT_MARKER']).write_text('imported')\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HIKARI_TEST_APPKIT_MARKER"] = str(marker)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(tmp_path), env.get("PYTHONPATH")) if part
    )

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "hikari.py"), first, second],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert "not allowed with argument" in result.stderr
    assert not marker.exists()


def test_daemon_alias_conflicts_with_server():
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "hikari.py"), "--bg", "--server"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert "not allowed with argument" in result.stderr
