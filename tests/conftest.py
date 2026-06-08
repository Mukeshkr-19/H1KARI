"""Test isolation for runtime files (Brain v2 / legacy / neural data only)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Set isolation env before any core.neural_memory import side effects.
_LEGACY_DATA_DIR = Path(tempfile.mkdtemp(prefix="hikari-test-legacy-data-"))
os.environ["HIKARI_LEGACY_DATA_DIR"] = str(_LEGACY_DATA_DIR)

_BRAIN_V2_TEST_ROOT = Path(tempfile.mkdtemp(prefix="hikari-test-brain-v2-"))
from core.path_literals import EPISODES_DB, HIKARI_MEMORY_DB

_BRAIN_V2_TEST_DB = _BRAIN_V2_TEST_ROOT / "brain_v2" / EPISODES_DB
_BRAIN_V2_TEST_DB.parent.mkdir(parents=True, exist_ok=True)
os.environ["HIKARI_BRAIN_V2_EPISODES_DB"] = str(_BRAIN_V2_TEST_DB)

_NEURAL_TEST_DB = _BRAIN_V2_TEST_ROOT / "neural" / HIKARI_MEMORY_DB
_NEURAL_TEST_DB.parent.mkdir(parents=True, exist_ok=True)
os.environ["HIKARI_NEURAL_MEMORY_DB"] = str(_NEURAL_TEST_DB)

import pytest


@pytest.fixture(scope="session", autouse=True)
def _session_brain_v2_episodes_db() -> Path:
    """Route default EpisodeStore / BrainV2Coordinator away from live brain for the suite."""
    from core.neural_memory.config import reset_memory_config_singleton

    os.environ["HIKARI_BRAIN_V2_EPISODES_DB"] = str(_BRAIN_V2_TEST_DB)
    os.environ["HIKARI_NEURAL_MEMORY_DB"] = str(_NEURAL_TEST_DB)
    os.environ["HIKARI_LEGACY_DATA_DIR"] = str(_LEGACY_DATA_DIR)
    _BRAIN_V2_TEST_DB.parent.mkdir(parents=True, exist_ok=True)
    _NEURAL_TEST_DB.parent.mkdir(parents=True, exist_ok=True)
    reset_memory_config_singleton()
    return _BRAIN_V2_TEST_DB
