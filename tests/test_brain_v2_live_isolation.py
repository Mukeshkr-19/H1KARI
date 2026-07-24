"""Prove normal pytest cannot touch the live Brain v2 episodes database."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from core.brain_v2.db_paths import ENV_BRAIN_V2_EPISODES_DB, resolve_episodes_db_path
from core.path_literals import DOT_HIKARI, EPISODES_DB, HIKARI_MEMORY_DB

REPO_ROOT = Path(__file__).resolve().parent.parent


def _sentinel_live_episodes_db(fake_home: Path) -> Path:
    return (fake_home / DOT_HIKARI.lstrip("~/") / "brain" / "brain_v2" / EPISODES_DB).resolve()


def test_suite_uses_isolated_brain_v2_episodes_db():
    explicit = os.environ.get(ENV_BRAIN_V2_EPISODES_DB)
    assert explicit
    assert "hikari-test-brain-v2" in explicit
    resolved = resolve_episodes_db_path().resolve()
    assert resolved == Path(explicit).resolve()


def test_suite_uses_isolated_neural_db_path():
    import tests.conftest as isolation

    neural = os.environ.get("HIKARI_NEURAL_MEMORY_DB", "")
    assert "hikari-test-brain-v2" in neural
    assert Path(neural).resolve() == Path(isolation._NEURAL_TEST_DB).resolve()


def test_neural_config_db_path_honors_env_override(tmp_path, monkeypatch):
    from core.neural_memory.config import (
        reset_memory_config_singleton,
        resolve_neural_db_path,
    )

    isolated = tmp_path / "isolated_neural.db"
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(isolated))
    reset_memory_config_singleton()
    from core.neural_memory.config import config

    assert config.DB_PATH.resolve() == isolated.resolve()
    assert resolve_neural_db_path() == isolated.resolve()


def test_explicit_neural_db_does_not_create_home_hikari_tree(tmp_path, monkeypatch):
    from core.neural_memory.config import reset_memory_config_singleton

    fake_home = tmp_path / "fake_home_neural"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    isolated = tmp_path / "only_neural.db"
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(isolated))
    reset_memory_config_singleton()
    from core.neural_memory.config import config

    _ = config.DB_PATH  # force singleton load
    assert isolated.is_file() or isolated.parent.exists()
    assert not (fake_home / ".hikari").exists()


def test_research_agent_time_query_without_legacy_brain(tmp_path, monkeypatch):
    """Brain-v2-on research path must not construct HikariBrain for time or personal screening."""
    from agents.research import ResearchAgent

    monkeypatch.delenv("HIKARI_DISABLE_BRAIN_V2", raising=False)
    agent = ResearchAgent(eager_legacy_brain=False)
    reply = agent.handle("what is the time")
    assert reply
    assert "time" in reply.lower()
    assert agent._brain is None
    assert not agent._legacy_brain_allowed


def test_brain_v2_on_orchestrator_skips_neural_init(monkeypatch, tmp_path):
    fake_home = tmp_path / "fake_home_orch_neural"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    isolated_ep = tmp_path / EPISODES_DB
    monkeypatch.setenv(ENV_BRAIN_V2_EPISODES_DB, str(isolated_ep))
    monkeypatch.delenv("HIKARI_DISABLE_BRAIN_V2", raising=False)
    monkeypatch.setattr(
        "core.neural_memory_bridge.init_neural_memory",
        lambda: (_ for _ in ()).throw(
            AssertionError("init_neural_memory must not run when Brain v2 is on")
        ),
    )
    from core.orchestrator import HIKARI_Orchestrator

    orch = HIKARI_Orchestrator()
    assert orch.brain_v2_enabled
    assert orch.brain is None
    assert not orch.neural_memory_enabled
    assert orch.neural_memory is None
    assert not (fake_home / ".hikari").exists()


_ORCH_BRAIN_V2_INIT_SCRIPT = """
import os
import sys
from pathlib import Path

home = Path(sys.argv[1])
episodes_db = Path(sys.argv[2])
legacy_dir = Path(sys.argv[3])
repo_root = Path(sys.argv[4])
os.environ["HOME"] = str(home)
os.environ["HIKARI_BRAIN_V2_EPISODES_DB"] = str(episodes_db)
os.environ["HIKARI_LEGACY_DATA_DIR"] = str(legacy_dir)
os.environ.pop("HIKARI_NEURAL_MEMORY_DB", None)
os.environ.pop("HIKARI_DISABLE_BRAIN_V2", None)
os.environ["HIKARI_DISABLE_PROACTIVE_SCHEDULER"] = "1"
sys.path.insert(0, str(repo_root))

from core.orchestrator import HIKARI_Orchestrator

orch = HIKARI_Orchestrator()
assert orch.brain_v2_enabled
assert orch.brain is None
assert not orch.neural_memory_enabled
assert orch.neural_memory is None
assert not (home / ".hikari").exists()
assert "core.neural_memory_bridge" not in sys.modules
from core.path_literals import HIKARI_MEMORY_DB

neural_db = home / ".hikari" / "brain" / HIKARI_MEMORY_DB
assert not neural_db.exists()
print("ok")
"""

_ORCH_BRAIN_V2_TIME_QUERY_SCRIPT = """
import os
import sys
from pathlib import Path

home = Path(sys.argv[1])
episodes_db = Path(sys.argv[2])
legacy_dir = Path(sys.argv[3])
repo_root = Path(sys.argv[4])
os.environ["HOME"] = str(home)
os.environ["HIKARI_BRAIN_V2_EPISODES_DB"] = str(episodes_db)
os.environ["HIKARI_LEGACY_DATA_DIR"] = str(legacy_dir)
os.environ.pop("HIKARI_NEURAL_MEMORY_DB", None)
os.environ.pop("HIKARI_DISABLE_BRAIN_V2", None)
os.environ["HIKARI_DISABLE_PROACTIVE_SCHEDULER"] = "1"
sys.path.insert(0, str(repo_root))

from core.orchestrator import HIKARI_Orchestrator

orch = HIKARI_Orchestrator()
assert orch.brain_v2_enabled
reply = orch.process_input("what is the time")
assert reply
assert "time" in reply.lower()
assert "core.neural_memory_bridge" not in sys.modules
assert not (home / ".hikari").exists()
print("ok")
"""


def test_orchestrator_subprocess_brain_v2_no_neural_without_env_db(tmp_path):
    """Fresh process: Brain v2 on, no HIKARI_NEURAL_MEMORY_DB, no legacy neural side effects."""
    home = tmp_path / "subprocess_home_brain_v2"
    home.mkdir()
    episodes_db = tmp_path / "subprocess_episodes.db"
    legacy_dir = tmp_path / "subprocess_legacy_data"
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "HIKARI_NEURAL_MEMORY_DB",
            "HIKARI_DISABLE_BRAIN_V2",
            "HIKARI_LEGACY_DATA_DIR",
            "HIKARI_DISABLE_PROACTIVE_SCHEDULER",
        )
    }
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["HIKARI_DISABLE_PROACTIVE_SCHEDULER"] = "1"
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            _ORCH_BRAIN_V2_INIT_SCRIPT,
            str(home),
            str(episodes_db),
            str(legacy_dir),
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert proc.stdout.strip() == "ok"
    assert not (home / DOT_HIKARI).exists()


def test_subprocess_brain_v2_time_query_no_neural_bridge(tmp_path):
    """Authority-on: non-personal time query via research must not import neural bridge."""
    home = tmp_path / "subprocess_home_time"
    home.mkdir()
    episodes_db = tmp_path / "subprocess_time_episodes.db"
    legacy_dir = tmp_path / "subprocess_time_legacy"
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "HIKARI_NEURAL_MEMORY_DB",
            "HIKARI_DISABLE_BRAIN_V2",
            "HIKARI_LEGACY_DATA_DIR",
            "HIKARI_DISABLE_PROACTIVE_SCHEDULER",
        )
    }
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["HIKARI_DISABLE_PROACTIVE_SCHEDULER"] = "1"
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            _ORCH_BRAIN_V2_TIME_QUERY_SCRIPT,
            str(home),
            str(episodes_db),
            str(legacy_dir),
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert proc.stdout.strip() == "ok"
    assert not (home / DOT_HIKARI).exists()


_ORCH_BRAIN_V2_DATE_QUERY_SCRIPT = """
import os
import sys
from pathlib import Path

home = Path(sys.argv[1])
episodes_db = Path(sys.argv[2])
legacy_dir = Path(sys.argv[3])
repo_root = Path(sys.argv[4])
os.environ["HOME"] = str(home)
os.environ["HIKARI_BRAIN_V2_EPISODES_DB"] = str(episodes_db)
os.environ["HIKARI_LEGACY_DATA_DIR"] = str(legacy_dir)
os.environ.pop("HIKARI_NEURAL_MEMORY_DB", None)
os.environ.pop("HIKARI_DISABLE_BRAIN_V2", None)
os.environ["HIKARI_DISABLE_PROACTIVE_SCHEDULER"] = "1"
sys.path.insert(0, str(repo_root))

from core.orchestrator import HIKARI_Orchestrator

orch = HIKARI_Orchestrator()
reply = orch.process_input("what is today's date")
assert reply
assert "today" in reply.lower() or any(m in reply.lower() for m in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))
assert "core.neural_memory_bridge" not in sys.modules
assert not (home / ".hikari").exists()
print("ok")
"""

_ORCH_BRAIN_V2_ROUTING_MOCKED_SCRIPT = """
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

home = Path(sys.argv[1])
episodes_db = Path(sys.argv[2])
legacy_dir = Path(sys.argv[3])
repo_root = Path(sys.argv[4])
os.environ["HOME"] = str(home)
os.environ["HIKARI_BRAIN_V2_EPISODES_DB"] = str(episodes_db)
os.environ["HIKARI_LEGACY_DATA_DIR"] = str(legacy_dir)
os.environ.pop("HIKARI_NEURAL_MEMORY_DB", None)
os.environ.pop("HIKARI_DISABLE_BRAIN_V2", None)
os.environ["HIKARI_DISABLE_PROACTIVE_SCHEDULER"] = "1"
sys.path.insert(0, str(repo_root))

from core.orchestrator import HIKARI_Orchestrator
from core.current_facts import CurrentFactHeadline
from core.location_service import CurrentWeather

orch = HIKARI_Orchestrator()

class FakeLocationService:
    def current_weather(self, query):
        return CurrentWeather(
            "Sample City",
            "clear",
            22.0,
            22.0,
            40,
            0.0,
            5.0,
        )

orch._public_location_service = FakeLocationService()
class FakeCurrentFactsService:
    def search(self, query):
        return (CurrentFactHeadline("Mock current headline", "Mock source"),)

orch._public_current_facts_service = FakeCurrentFactsService()
search_resp = MagicMock()
search_resp.json.return_value = {
    "Abstract": "Mock search result for isolation test.",
    "AbstractURL": "https://example.test",
    "RelatedTopics": [],
}
feed = MagicMock()
feed.entries = [MagicMock(title="Mock headline A"), MagicMock(title="Mock headline B")]

w = orch.process_input("what is the weather in Sample City")
assert w and "weather" in w.lower()
with patch("agents.research.requests.get", return_value=search_resp):
    s = orch.process_input("search for sample isolation topic")
    assert s
n = orch.process_input("give me the news")
assert n

assert "core.neural_memory_bridge" not in sys.modules
assert not (home / ".hikari").exists()
print("ok")
"""

_ORCH_BRAIN_V2_SCHEDULER_CALLBACK_SCRIPT = """
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

home = Path(sys.argv[1])
episodes_db = Path(sys.argv[2])
legacy_dir = Path(sys.argv[3])
repo_root = Path(sys.argv[4])
os.environ["HOME"] = str(home)
os.environ["HIKARI_BRAIN_V2_EPISODES_DB"] = str(episodes_db)
os.environ["HIKARI_LEGACY_DATA_DIR"] = str(legacy_dir)
os.environ.pop("HIKARI_NEURAL_MEMORY_DB", None)
os.environ.pop("HIKARI_DISABLE_BRAIN_V2", None)
os.environ.pop("HIKARI_DISABLE_PROACTIVE_SCHEDULER", None)
sys.path.insert(0, str(repo_root))

from core.orchestrator import HIKARI_Orchestrator

orch = HIKARI_Orchestrator()
assert orch.scheduler is None
assert "agents.research" not in sys.modules
assert "core.neural_memory_bridge" not in sys.modules
assert not (home / ".hikari").exists()
print("ok")
"""

_ORCH_LEGACY_INIT_SCRIPT = """
import os
import sys
from pathlib import Path

home = Path(sys.argv[1])
repo_root = Path(sys.argv[2])
os.environ["HOME"] = str(home)
os.environ["HIKARI_DISABLE_BRAIN_V2"] = "1"
os.environ.pop("HIKARI_NEURAL_MEMORY_DB", None)
os.environ["HIKARI_DISABLE_PROACTIVE_SCHEDULER"] = "1"
sys.path.insert(0, str(repo_root))

from core.orchestrator import HIKARI_Orchestrator

orch = HIKARI_Orchestrator()
assert not orch.brain_v2_enabled
assert orch.brain is not None
assert orch.brain_v2 is None
# Legacy mode may initialize neural bridge when the default DB path is available.
from core.brain import HikariBrain

assert isinstance(orch.brain, HikariBrain)
print("ok")
"""


_QUARANTINE_SUBPROCESS_ENV_KEYS = (
    "HIKARI_NEURAL_MEMORY_DB",
    "HIKARI_DISABLE_BRAIN_V2",
    "HIKARI_LEGACY_DATA_DIR",
    "HIKARI_DISABLE_PROACTIVE_SCHEDULER",
)


def _run_brain_v2_quarantine_subprocess(
    script: str,
    tmp_path: Path,
    *,
    disable_scheduler: bool = True,
) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "quarantine_home"
    home.mkdir()
    episodes_db = tmp_path / "quarantine_episodes.db"
    legacy_dir = tmp_path / "quarantine_legacy_data"
    legacy_dir.mkdir(exist_ok=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in _QUARANTINE_SUBPROCESS_ENV_KEYS
    }
    env["PYTHONPATH"] = str(REPO_ROOT)
    if disable_scheduler:
        env["HIKARI_DISABLE_PROACTIVE_SCHEDULER"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(home),
            str(episodes_db),
            str(legacy_dir),
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_subprocess_brain_v2_date_query_no_neural_bridge(tmp_path):
    proc = _run_brain_v2_quarantine_subprocess(_ORCH_BRAIN_V2_DATE_QUERY_SCRIPT, tmp_path)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert proc.stdout.strip() == "ok"
    assert not (tmp_path / "quarantine_home" / DOT_HIKARI).exists()


def test_subprocess_brain_v2_routing_mocked_no_neural_bridge(tmp_path):
    proc = _run_brain_v2_quarantine_subprocess(
        _ORCH_BRAIN_V2_ROUTING_MOCKED_SCRIPT, tmp_path
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert proc.stdout.strip() == "ok"
    assert not (tmp_path / "quarantine_home" / DOT_HIKARI).exists()


def test_subprocess_brain_v2_scheduler_callback_no_neural_bridge(tmp_path):
    proc = _run_brain_v2_quarantine_subprocess(
        _ORCH_BRAIN_V2_SCHEDULER_CALLBACK_SCRIPT,
        tmp_path,
        disable_scheduler=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert proc.stdout.strip() == "ok"
    assert not (tmp_path / "quarantine_home" / DOT_HIKARI).exists()


def test_memory_status_brain_v2_quarantined_report(monkeypatch, tmp_path):
    from core.memory_status import format_memory_status_report
    from core.orchestrator import HIKARI_Orchestrator

    fake_home = tmp_path / "fake_home_mem_status"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv(ENV_BRAIN_V2_EPISODES_DB, str(tmp_path / EPISODES_DB))
    monkeypatch.delenv("HIKARI_DISABLE_BRAIN_V2", raising=False)
    monkeypatch.setenv("HIKARI_DISABLE_PROACTIVE_SCHEDULER", "1")
    orch = HIKARI_Orchestrator()
    report = format_memory_status_report(orch)
    assert "quarantined" in report.lower()
    assert "core.neural_memory" not in report


def test_orchestrator_subprocess_legacy_brain_when_brain_v2_disabled(tmp_path):
    home = tmp_path / "subprocess_home_legacy"
    home.mkdir()
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("HIKARI_NEURAL_MEMORY_DB", "HIKARI_DISABLE_BRAIN_V2")
    }
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            _ORCH_LEGACY_INIT_SCRIPT,
            str(home),
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert proc.stdout.strip() == "ok"


def test_resolved_episodes_db_not_under_fake_home_sentinel(tmp_path, monkeypatch):
    fake_home = tmp_path / "fake_home_no_live_brain"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    isolated_db = tmp_path / "isolated_episodes.db"
    monkeypatch.setenv(ENV_BRAIN_V2_EPISODES_DB, str(isolated_db))

    resolved = resolve_episodes_db_path().resolve()
    sentinel = _sentinel_live_episodes_db(fake_home)
    assert resolved == isolated_db.resolve()
    assert resolved != sentinel
    assert not sentinel.exists()


def test_orchestrator_default_store_uses_env_not_home_default(tmp_path, monkeypatch):
    from core.orchestrator import HIKARI_Orchestrator

    fake_home = tmp_path / "fake_home_orch"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    isolated_db = tmp_path / EPISODES_DB
    monkeypatch.setenv(ENV_BRAIN_V2_EPISODES_DB, str(isolated_db))

    orch = HIKARI_Orchestrator()
    assert orch.brain_v2 is not None
    assert orch.brain_v2.store.db_path.resolve() == isolated_db.resolve()
    assert orch.brain_v2.store.db_path.resolve() != _sentinel_live_episodes_db(fake_home)


def test_isolated_writes_stay_on_temp_db_only(tmp_path, monkeypatch):
    fake_home = tmp_path / "fake_home_write"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    isolated_db = tmp_path / EPISODES_DB
    monkeypatch.setenv(ENV_BRAIN_V2_EPISODES_DB, str(isolated_db))

    from core.brain_v2.episode_store import EpisodeStore

    store = EpisodeStore(db_path=isolated_db)
    episode_id = store.create_episode("isolation-proof")
    store.add_turn(episode_id, "Owner A lives in City A for testing.", is_user=True)

    assert isolated_db.is_file()
    assert resolve_episodes_db_path().resolve() == isolated_db.resolve()
    assert not _sentinel_live_episodes_db(fake_home).exists()

    with sqlite3.connect(isolated_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM raw_episodes").fetchone()[0]
    assert count >= 1
