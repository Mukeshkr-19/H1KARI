from __future__ import annotations

import stat

from core.action_policy import Actor
from core.phase1_runtime import create_phase1_runtime, owner_contexts


def test_owner_contexts_are_server_derived_and_aligned():
    actor, task = owner_contexts(session_id="session-1", source="web")

    assert actor.actor is Actor.OWNER
    assert actor.actor_id == "local-owner"
    assert task.speaker_label == actor.actor_id
    assert task.session_id == actor.session_id
    assert task.source == actor.source
    assert task.actor == actor.actor.value


def test_runtime_state_stays_private(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_HOME", str(tmp_path / "private-home"))
    monkeypatch.setenv("HIKARI_TASKS_DB", str(tmp_path / "private-home" / "tasks.db"))

    runtime = create_phase1_runtime(router=object())  # type: ignore[arg-type]

    assert runtime.policy.grants.db_path.parent == tmp_path / "private-home" / "policy"
    assert stat.S_IMODE((tmp_path / "private-home").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "private-home" / "brain").stat().st_mode) == 0o700
    assert stat.S_IMODE(runtime.policy.grants.db_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(runtime.policy.grants.db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(runtime.policy.audit.db_path.stat().st_mode) == 0o600
