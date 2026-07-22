"""Integration tests for the lazy Phase 4 production composition boundary."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import hikari
import pytest

from core.action_policy import Actor, ActorContext
from core.handoff import FrozenHandoffPreview
from core.phase4 import Phase4BootstrapError, create_phase4_subsystem


REPO_ROOT = Path(__file__).resolve().parent.parent


def _lookup(_actor: ActorContext, task_id: str):
    if task_id != "task-1":
        return None
    return FrozenHandoffPreview(task_id="task-1", summary="Review result")


def _policy(actor: ActorContext, _preview: FrozenHandoffPreview) -> bool:
    return actor.actor is Actor.OWNER


def test_subsystem_uses_injected_factories_and_private_permissions(tmp_path) -> None:
    codes: list[str] = []
    subsystem = create_phase4_subsystem(
        task_lookup=_lookup,
        acceptance_policy=_policy,
        clock=lambda: 1000.0,
        handoff_db_path=tmp_path / "handoff" / "handoffs.db",
        pairing_db_path=tmp_path / "pairing" / "devices.db",
        handoff_id_factory=lambda: "handoff-1",
        transfer_id_factory=lambda: "transfer-1",
        challenge_id_factory=lambda: "challenge-1",
        device_id_factory=lambda: "device-1",
        secret_code_factory=lambda: "ABC123",
        digest_key=b"phase4-test-digest-key",
        display_sink=codes.append,
    )

    challenge = subsystem.pairing_runtime.prepare("request-1")
    assert challenge["challenge_id"] == "challenge-1"
    assert codes == ["ABC123"]
    assert subsystem.handoff_runtime is not None
    assert subsystem.handoff_transport is not None
    assert subsystem.visual_transfer_runtime is not None

    for path in (
        tmp_path / "handoff" / "handoffs.db",
        tmp_path / "pairing" / "devices.db",
    ):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_import_does_not_create_phase4_state(tmp_path) -> None:
    private_home = tmp_path / "isolated-home"
    env = dict(os.environ)
    env["HOME"] = str(private_home)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import core.phase4.bootstrap; import core.pairing.bootstrap",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert not private_home.exists()


def test_bootstrap_failure_is_content_free(tmp_path) -> None:
    unusable = tmp_path / "not-a-database"
    unusable.mkdir()
    with pytest.raises(Phase4BootstrapError) as captured:
        create_phase4_subsystem(
            task_lookup=_lookup,
            acceptance_policy=_policy,
            handoff_db_path=unusable,
            pairing_db_path=tmp_path / "pairing" / "devices.db",
        )
    assert str(captured.value) == "phase 4 bootstrap failed"
    assert str(tmp_path) not in str(captured.value)
    assert str(tmp_path) not in repr(captured.value)


def test_invalid_dependencies_fail_before_runtime_use() -> None:
    with pytest.raises(Phase4BootstrapError, match="^phase 4 bootstrap failed$"):
        create_phase4_subsystem(
            task_lookup=None,  # type: ignore[arg-type]
            acceptance_policy=_policy,
        )


def test_run_server_lazily_injects_complete_phase4_subsystem(monkeypatch) -> None:
    orchestrator = object()
    phase1_runtime = object()
    phase4_subsystem = SimpleNamespace(
        pairing_runtime=object(),
        handoff_transport=object(),
        visual_transfer_runtime=object(),
    )
    productivity_runtime = object()
    scheduled_runtime = object()
    server = MagicMock()
    server_class = MagicMock(return_value=server)
    phase4_factory = MagicMock(return_value=phase4_subsystem)

    monkeypatch.setitem(
        sys.modules,
        "core.orchestrator",
        SimpleNamespace(get_orchestrator=lambda: orchestrator),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.phase1_runtime",
        SimpleNamespace(create_phase1_runtime=lambda: phase1_runtime),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.phase4",
        SimpleNamespace(create_phase4_subsystem=phase4_factory),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.productivity.bootstrap",
        SimpleNamespace(
            create_productivity_runtime=lambda: productivity_runtime,
            create_email_draft_preparation=lambda: (object(), object()),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.jobs.bootstrap",
        SimpleNamespace(create_scheduled_job_runtime=lambda: scheduled_runtime),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.server",
        SimpleNamespace(WebSocketServer=server_class),
    )

    hikari.run_server("127.0.0.1", 9876)

    phase4_factory.assert_called_once()
    kwargs = server_class.call_args.kwargs
    assert kwargs["phase1_runtime"] is phase1_runtime
    assert kwargs["pairing_runtime"] is phase4_subsystem.pairing_runtime
    assert kwargs["handoff_transport"] is phase4_subsystem.handoff_transport
    assert kwargs["visual_transfer_runtime"] is phase4_subsystem.visual_transfer_runtime
    server.start.assert_called_once_with()
