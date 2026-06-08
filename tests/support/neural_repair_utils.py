"""Test-only helpers for isolated legacy neural repair databases."""

from __future__ import annotations

import shutil
from pathlib import Path

from core.brain_v2.legacy_neural_repair import assert_repair_target_neural_path


def copy_neural_db_for_repair(source: Path, dest: Path) -> Path:
    """Create an isolated neural DB copy for repair tests (never the live source path)."""
    assert_repair_target_neural_path(source)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return dest
