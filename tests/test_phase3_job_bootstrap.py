"""Production composition tests for the Phase 3 scheduled-job runtime."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import hikari

from core.jobs.bootstrap import (
    SCHEDULED_JOBS_AUDIT_DB_NAME,
    SCHEDULED_JOBS_DB_NAME,
    SCHEDULED_ACTIONS_DB_NAME,
    SCHEDULED_OWNER_SCOPE_NAME,
    create_scheduled_job_subsystem,
    create_scheduled_job_runtime,
    scheduled_jobs_audit_db_path,
    scheduled_jobs_db_path,
    scheduled_owner_scope_path,
)
from core.productivity.contracts import ProductivityAction
from core.jobs.runtime import ScheduledJobRuntime
from core.jobs.service import ScheduledJobService


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_factory_uses_injected_path_and_clock(tmp_path: Path):
    db_path = tmp_path / "private" / "jobs.db"
    audit_db_path = tmp_path / "private" / "audit.db"
    now = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
    clock = lambda: now

    runtime = create_scheduled_job_runtime(
        db_path=db_path,
        audit_db_path=audit_db_path,
        clock=clock,
        event_id_factory=lambda: "event-1",
    )

    assert isinstance(runtime, ScheduledJobRuntime)
    assert isinstance(runtime._service, ScheduledJobService)
    assert runtime._service._clock is clock
    assert runtime._service._lifecycle is not None
    assert db_path.is_file()
    assert audit_db_path.is_file()
    assert (db_path.parent / SCHEDULED_OWNER_SCOPE_NAME).is_file()


def test_default_database_is_private_and_restrictive(tmp_path: Path, monkeypatch):
    state_home = tmp_path / "private-home"
    working_directory = tmp_path / "working-directory"
    working_directory.mkdir()
    monkeypatch.setenv("HIKARI_HOME", str(state_home))
    monkeypatch.chdir(working_directory)

    runtime = create_scheduled_job_runtime()
    db_path = scheduled_jobs_db_path()
    audit_db_path = scheduled_jobs_audit_db_path()
    scope_path = scheduled_owner_scope_path()

    assert isinstance(runtime, ScheduledJobRuntime)
    assert db_path == state_home / "policy" / SCHEDULED_JOBS_DB_NAME
    assert audit_db_path == state_home / "policy" / SCHEDULED_JOBS_AUDIT_DB_NAME
    assert db_path.is_file()
    assert audit_db_path.is_file()
    assert scope_path.is_file()
    assert REPO_ROOT not in db_path.parents
    assert working_directory not in db_path.parents
    assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(audit_db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(scope_path.stat().st_mode) == 0o600
    assert scope_path.read_text(encoding="ascii").startswith("installation-")


def test_owner_scope_is_stable_across_runtime_restarts(tmp_path: Path):
    db_path = tmp_path / "private" / "jobs.db"
    first = create_scheduled_job_runtime(db_path=db_path)
    second = create_scheduled_job_runtime(db_path=db_path)

    assert first._owner_scope_id == second._owner_scope_id
    assert first._owner_scope_id.startswith("installation-")
    assert (db_path.parent / SCHEDULED_OWNER_SCOPE_NAME).is_file()


def test_complete_subsystem_is_lazy_and_uses_injected_dependencies(tmp_path: Path):
    db_path = tmp_path / "private" / "jobs.db"
    calls: list[object] = []

    def unused(value):
        calls.append(value)
        raise AssertionError("adapter must not run during bootstrap")

    subsystem = create_scheduled_job_subsystem(
        db_path=db_path,
        clock=lambda: datetime(2026, 7, 20, 12, tzinfo=timezone.utc),
        job_id_factory=lambda: "job-1",
        event_id_factory=lambda: "event-1",
        owner_scope_id="installation-1",
        adapters={
            ProductivityAction.BROWSER_RESEARCH: unused,
            ProductivityAction.CALENDAR_READ: lambda value: unused(value),
        },
    )

    assert subsystem.runtime._owner_scope_id == "installation-1"
    assert subsystem.store.db_path == db_path.resolve()
    assert subsystem.action_store.db_path == (
        db_path.parent / SCHEDULED_ACTIONS_DB_NAME
    ).resolve()
    assert calls == []


def test_imports_do_not_create_scheduled_job_state(tmp_path: Path):
    state_home = tmp_path / "private-home"
    env = {**os.environ, "HIKARI_HOME": str(state_home)}

    result = subprocess.run(
        [sys.executable, "-c", "import hikari; import core.jobs.bootstrap"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert not (state_home / "policy" / SCHEDULED_JOBS_DB_NAME).exists()
    assert not (state_home / "policy" / SCHEDULED_JOBS_AUDIT_DB_NAME).exists()
    assert not (state_home / "policy" / SCHEDULED_OWNER_SCOPE_NAME).exists()
    assert not (state_home / "policy" / SCHEDULED_ACTIONS_DB_NAME).exists()


def test_non_server_commands_do_not_create_scheduled_job_state(tmp_path: Path):
    for command in ("--doctor", "--voice-status"):
        state_home = tmp_path / command.removeprefix("--")
        env = {**os.environ, "HIKARI_HOME": str(state_home)}
        subprocess.run(
            [sys.executable, str(REPO_ROOT / "hikari.py"), command],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert not (state_home / "policy" / SCHEDULED_JOBS_DB_NAME).exists()
        assert not (
            state_home / "policy" / SCHEDULED_JOBS_AUDIT_DB_NAME
        ).exists()
        assert not (state_home / "policy" / SCHEDULED_OWNER_SCOPE_NAME).exists()
        assert not (state_home / "policy" / SCHEDULED_ACTIONS_DB_NAME).exists()


def test_server_injects_scheduled_job_runtime(monkeypatch):
    orchestrator = object()
    productivity_runtime = object()
    email_draft_factory = object()
    email_draft_registry = object()
    scheduled_job_runtime = object()
    scheduled_job_subsystem = SimpleNamespace(runtime=scheduled_job_runtime)
    server = MagicMock()
    server_class = MagicMock(return_value=server)
    scheduled_job_factory = MagicMock(return_value=scheduled_job_subsystem)
    monkeypatch.setitem(
        sys.modules,
        "core.orchestrator",
        SimpleNamespace(get_orchestrator=lambda: orchestrator),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.productivity.bootstrap",
        SimpleNamespace(
            create_productivity_runtime=lambda: productivity_runtime,
            create_email_draft_preparation=lambda: (
                email_draft_factory,
                email_draft_registry,
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.jobs.bootstrap",
        SimpleNamespace(create_scheduled_job_subsystem=scheduled_job_factory),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.server",
        SimpleNamespace(WebSocketServer=server_class),
    )

    hikari.run_server("127.0.0.1", 9876)

    scheduled_job_factory.assert_called_once_with()
    server_class.assert_called_once_with(
        orchestrator,
        host="127.0.0.1",
        port=9876,
        productivity_runtime=productivity_runtime,
        scheduled_job_runtime=scheduled_job_runtime,
        scheduled_job_subsystem=scheduled_job_subsystem,
        email_draft_factory=email_draft_factory,
        email_draft_registry=email_draft_registry,
    )
    server.start.assert_called_once_with()


def test_scheduled_job_bootstrap_failure_is_safe(monkeypatch, capsys):
    orchestrator = object()
    productivity_runtime = object()
    email_draft_factory = object()
    email_draft_registry = object()
    server = MagicMock()
    server_class = MagicMock(return_value=server)

    def fail_bootstrap():
        raise RuntimeError("private database path and provider detail")

    monkeypatch.setitem(
        sys.modules,
        "core.orchestrator",
        SimpleNamespace(get_orchestrator=lambda: orchestrator),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.productivity.bootstrap",
        SimpleNamespace(
            create_productivity_runtime=lambda: productivity_runtime,
            create_email_draft_preparation=lambda: (
                email_draft_factory,
                email_draft_registry,
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.jobs.bootstrap",
        SimpleNamespace(create_scheduled_job_subsystem=fail_bootstrap),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.server",
        SimpleNamespace(WebSocketServer=server_class),
    )

    hikari.run_server("127.0.0.1", 9876)

    server_class.assert_called_once_with(
        orchestrator,
        host="127.0.0.1",
        port=9876,
        productivity_runtime=productivity_runtime,
        scheduled_job_runtime=None,
        email_draft_factory=email_draft_factory,
        email_draft_registry=email_draft_registry,
    )
    output = capsys.readouterr()
    assert "Scheduled jobs are temporarily unavailable" in output.err
    assert "private database" not in output.err
    assert "provider" not in output.err
    server.start.assert_called_once_with()
