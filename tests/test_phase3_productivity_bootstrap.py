"""Production composition tests for the Phase 3 productivity runtime."""

from __future__ import annotations

import builtins
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import hikari

from core.productivity.bootstrap import (
    PRODUCTIVITY_DB_NAME,
    _production_approval_id,
    create_productivity_execution_coordinator,
    create_productivity_runtime,
    productivity_db_path,
)
from core.productivity.runtime import ProductivityRuntime
from core.productivity.service import ProductivityService
from core.productivity.contracts import ProductivityAction
from core.productivity.execution import AdapterResult, AdapterResultStatus
from core.productivity.adapters.calendar_read import CalendarReadMacAdapter
from core.productivity.adapters.macos_actions import (
    CalendarDraftMacAdapter,
    EmailDraftMacAdapter,
    ReminderCreateMacAdapter,
)
from core.productivity.adapters.research import BrowserResearchAdapter


REPO_ROOT = Path(__file__).resolve().parent.parent
IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")


class _Adapter:
    def __call__(self, input):
        return AdapterResult(AdapterResultStatus.SUCCESS)


def test_factory_uses_injected_path_clock_and_id_factory(tmp_path: Path):
    db_path = tmp_path / "private" / "approvals.db"
    clock = lambda: 1234.5
    approval_id_factory = lambda: "approval-test"

    runtime = create_productivity_runtime(
        db_path=db_path,
        clock=clock,
        approval_id_factory=approval_id_factory,
    )

    assert isinstance(runtime, ProductivityRuntime)
    assert isinstance(runtime._service, ProductivityService)
    assert runtime._clock is clock
    assert runtime._approval_id_factory is approval_id_factory
    assert db_path.is_file()


def test_production_approval_ids_are_random_and_canonical():
    identifiers = {_production_approval_id() for _ in range(32)}

    assert len(identifiers) == 32
    assert all(IDENTIFIER_PATTERN.fullmatch(value) for value in identifiers)


def test_execution_factory_accepts_deterministic_injected_adapters(tmp_path: Path):
    runtime = create_productivity_runtime(db_path=tmp_path / "approvals.db")
    adapter = _Adapter()

    coordinator = create_productivity_execution_coordinator(
        runtime,
        adapters={ProductivityAction.EMAIL_DRAFT: adapter},
    )

    assert coordinator._runtime is runtime
    assert coordinator._adapters == {ProductivityAction.EMAIL_DRAFT: adapter}


def test_execution_factory_registers_only_implemented_bounded_adapters(tmp_path: Path):
    runtime = create_productivity_runtime(db_path=tmp_path / "approvals.db")

    coordinator = create_productivity_execution_coordinator(runtime)

    assert set(coordinator._adapters) == {
        ProductivityAction.BROWSER_RESEARCH,
        ProductivityAction.EMAIL_DRAFT,
        ProductivityAction.CALENDAR_READ,
        ProductivityAction.CALENDAR_DRAFT,
        ProductivityAction.REMINDER_CREATE,
    }
    assert isinstance(
        coordinator._adapters[ProductivityAction.BROWSER_RESEARCH],
        BrowserResearchAdapter,
    )
    assert isinstance(
        coordinator._adapters[ProductivityAction.EMAIL_DRAFT],
        EmailDraftMacAdapter,
    )
    assert isinstance(
        coordinator._adapters[ProductivityAction.CALENDAR_READ],
        CalendarReadMacAdapter,
    )
    assert isinstance(
        coordinator._adapters[ProductivityAction.CALENDAR_DRAFT],
        CalendarDraftMacAdapter,
    )
    assert isinstance(
        coordinator._adapters[ProductivityAction.REMINDER_CREATE],
        ReminderCreateMacAdapter,
    )


def test_default_database_is_private_and_not_under_checkout(
    tmp_path: Path,
    monkeypatch,
):
    state_home = tmp_path / "private-home"
    working_directory = tmp_path / "working-directory"
    working_directory.mkdir()
    monkeypatch.setenv("HIKARI_HOME", str(state_home))
    monkeypatch.chdir(working_directory)

    runtime = create_productivity_runtime()
    db_path = productivity_db_path()

    assert isinstance(runtime, ProductivityRuntime)
    assert db_path == state_home / "policy" / PRODUCTIVITY_DB_NAME
    assert db_path.is_file()
    assert REPO_ROOT not in db_path.parents
    assert working_directory not in db_path.parents
    assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600


def test_imports_do_not_create_productivity_state(tmp_path: Path):
    state_home = tmp_path / "private-home"
    env = {**os.environ, "HIKARI_HOME": str(state_home)}

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import hikari; import core.productivity.bootstrap",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert not (state_home / "policy" / PRODUCTIVITY_DB_NAME).exists()


def test_non_server_status_commands_do_not_create_productivity_state(tmp_path: Path):
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
        assert not (state_home / "policy" / PRODUCTIVITY_DB_NAME).exists()


def test_interactive_mode_does_not_bootstrap_productivity(
    tmp_path: Path,
    monkeypatch,
):
    state_home = tmp_path / "private-home"
    monkeypatch.setenv("HIKARI_HOME", str(state_home))
    fake_orchestrator = SimpleNamespace(finalize_session=MagicMock())
    monkeypatch.setitem(
        sys.modules,
        "core.orchestrator",
        SimpleNamespace(get_orchestrator=lambda: fake_orchestrator),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.cli_status",
        SimpleNamespace(get_startup_panel=lambda: "ready"),
    )
    monkeypatch.setattr(builtins, "input", MagicMock(side_effect=EOFError))

    hikari.run_interactive()

    assert not (state_home / "policy" / PRODUCTIVITY_DB_NAME).exists()


def test_server_constructs_and_injects_productivity_runtime(monkeypatch):
    runtime = object()
    execution_coordinator = object()
    email_draft_factory = object()
    email_draft_registry = object()
    scheduled_job_runtime = object()
    orchestrator = object()
    server = MagicMock()
    server_class = MagicMock(return_value=server)
    factory = MagicMock(return_value=runtime)
    execution_factory = MagicMock(return_value=execution_coordinator)
    scheduled_job_factory = MagicMock(return_value=scheduled_job_runtime)
    monkeypatch.setitem(
        sys.modules,
        "core.orchestrator",
        SimpleNamespace(get_orchestrator=lambda: orchestrator),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.productivity.bootstrap",
        SimpleNamespace(
            create_productivity_runtime=factory,
            create_productivity_execution_coordinator=execution_factory,
            create_email_draft_preparation=lambda: (
                email_draft_factory,
                email_draft_registry,
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.jobs.bootstrap",
        SimpleNamespace(create_scheduled_job_runtime=scheduled_job_factory),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.server",
        SimpleNamespace(WebSocketServer=server_class),
    )

    hikari.run_server("127.0.0.1", 9876)

    factory.assert_called_once_with()
    execution_factory.assert_called_once_with(runtime)
    scheduled_job_factory.assert_called_once_with()
    server_class.assert_called_once_with(
        orchestrator,
        host="127.0.0.1",
        port=9876,
        productivity_runtime=runtime,
        productivity_execution_coordinator=execution_coordinator,
        scheduled_job_runtime=scheduled_job_runtime,
        email_draft_factory=email_draft_factory,
        email_draft_registry=email_draft_registry,
    )
    server.start.assert_called_once_with()


def test_server_bootstrap_failure_is_safe_and_fail_closed(monkeypatch, capsys):
    orchestrator = object()
    server = MagicMock()
    server_class = MagicMock(return_value=server)
    scheduled_job_runtime = object()
    email_draft_factory = object()
    email_draft_registry = object()

    def fail_bootstrap():
        raise RuntimeError("private path and provider detail")

    monkeypatch.setitem(
        sys.modules,
        "core.orchestrator",
        SimpleNamespace(get_orchestrator=lambda: orchestrator),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.productivity.bootstrap",
        SimpleNamespace(
            create_productivity_runtime=fail_bootstrap,
            create_productivity_execution_coordinator=MagicMock(),
            create_email_draft_preparation=lambda: (
                email_draft_factory,
                email_draft_registry,
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.jobs.bootstrap",
        SimpleNamespace(
            create_scheduled_job_runtime=lambda: scheduled_job_runtime
        ),
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
        productivity_runtime=None,
        scheduled_job_runtime=scheduled_job_runtime,
        email_draft_factory=None,
        email_draft_registry=None,
    )
    output = capsys.readouterr()
    assert "temporarily unavailable" in output.err
    assert "private path" not in output.err
    assert "provider" not in output.err
    server.start.assert_called_once_with()
