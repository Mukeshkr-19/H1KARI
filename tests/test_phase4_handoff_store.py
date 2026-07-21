"""Phase 4 handoff SQLite store tests."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from core.action_policy import Actor, ActorContext
from core.handoff.contracts import FrozenHandoffPreview, HandoffState
from core.handoff.store import (
    DuplicateHandoffError,
    HandoffStore,
    InvalidHandoffIdError,
)


@pytest.fixture
def owner_actor() -> ActorContext:
    return ActorContext(
        actor_id="local-owner",
        actor=Actor.OWNER,
        session_id="session-1",
        source="local",
    )


@pytest.fixture
def other_actor() -> ActorContext:
    return ActorContext(
        actor_id="local-owner",
        actor=Actor.OWNER,
        session_id="session-2",
        source="local",
    )


@pytest.fixture
def fake_clock():
    value = {"now": 1000.0}

    def clock() -> float:
        return value["now"]

    return clock, value


@pytest.fixture
def id_sequence():
    counter = {"n": 0}

    def factory() -> str:
        counter["n"] += 1
        return f"h{counter['n']:03d}"

    return factory


def _store(tmp_path: Path, clock, factory) -> HandoffStore:
    return HandoffStore(
        tmp_path / "handoffs.db",
        clock=clock,
        handoff_id_factory=factory,
    )


def test_create_offer_and_get_scoped(tmp_path, fake_clock, id_sequence, owner_actor):
    clock, _ = fake_clock
    store = _store(tmp_path, clock, id_sequence)

    record = store.create_offer(
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
        task_id="task-123",
        summary="Review report",
        snapshot_digest="a" * 64,
        request_id="req-1",
    )
    assert record.handoff_id == "h001"
    assert record.state is HandoffState.OFFERED
    assert record.actor_id == owner_actor.actor_id
    assert record.session_id == owner_actor.session_id
    assert record.task_id == "task-123"

    loaded = store.get_scoped(
        record.handoff_id,
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
    )
    assert loaded == record


def test_invalid_handoff_id_factory_is_rejected(tmp_path, fake_clock):
    clock, _ = fake_clock

    def bad_factory() -> str:
        return "BAD-ID"

    store = _store(tmp_path, clock, bad_factory)
    with pytest.raises(InvalidHandoffIdError):
        store.create_offer(
            actor_id="local-owner",
            session_id="session-1",
            task_id="task-123",
            summary="Review report",
            snapshot_digest="a" * 64,
            request_id="req-1",
        )


def test_duplicate_active_offer_for_same_tuple_is_rejected(tmp_path, fake_clock, id_sequence):
    clock, _ = fake_clock
    store = _store(tmp_path, clock, id_sequence)
    store.create_offer(
        actor_id="local-owner",
        session_id="session-1",
        task_id="task-123",
        summary="First offer",
        snapshot_digest="a" * 64,
        request_id="req-1",
    )
    with pytest.raises(DuplicateHandoffError):
        store.create_offer(
            actor_id="local-owner",
            session_id="session-1",
            task_id="task-123",
            summary="Second offer",
            snapshot_digest="b" * 64,
            request_id="req-2",
        )


def test_duplicate_offer_after_terminal_is_allowed(tmp_path, fake_clock, id_sequence):
    clock, _ = fake_clock
    store = _store(tmp_path, clock, id_sequence)
    first = store.create_offer(
        actor_id="local-owner",
        session_id="session-1",
        task_id="task-123",
        summary="First offer",
        snapshot_digest="a" * 64,
        request_id="req-1",
    )
    accepted = store.accept(
        first.handoff_id,
        actor_id="local-owner",
        session_id="session-1",
    )
    assert accepted is not None

    second = store.create_offer(
        actor_id="local-owner",
        session_id="session-1",
        task_id="task-123",
        summary="Second offer",
        snapshot_digest="b" * 64,
        request_id="req-2",
    )
    assert second.handoff_id == "h002"
    assert second.state is HandoffState.OFFERED


def test_cross_session_get_scoped_returns_none(tmp_path, fake_clock, id_sequence, owner_actor, other_actor):
    clock, _ = fake_clock
    store = _store(tmp_path, clock, id_sequence)
    record = store.create_offer(
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
        task_id="task-123",
        summary="Review report",
        snapshot_digest="a" * 64,
        request_id="req-1",
    )
    loaded = store.get_scoped(
        record.handoff_id,
        actor_id=other_actor.actor_id,
        session_id=other_actor.session_id,
    )
    assert loaded is None


def test_accept_transitions_state_and_increments_revision(tmp_path, fake_clock, id_sequence, owner_actor):
    clock, _ = fake_clock
    store = _store(tmp_path, clock, id_sequence)
    record = store.create_offer(
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
        task_id="task-123",
        summary="Review report",
        snapshot_digest="a" * 64,
        request_id="req-1",
    )
    accepted = store.accept(
        record.handoff_id,
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
    )
    assert accepted is not None
    assert accepted.state is HandoffState.ACCEPTED
    assert accepted.revision == 2

    second = store.accept(
        record.handoff_id,
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
    )
    assert second is None


def test_reject_and_cancel_are_idempotent(tmp_path, fake_clock, id_sequence, owner_actor):
    clock, _ = fake_clock
    store = _store(tmp_path, clock, id_sequence)
    record = store.create_offer(
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
        task_id="task-123",
        summary="Review report",
        snapshot_digest="a" * 64,
        request_id="req-1",
    )
    rejected = store.reject(
        record.handoff_id,
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
    )
    assert rejected is not None
    assert rejected.state is HandoffState.REJECTED

    rejected_again = store.reject(
        record.handoff_id,
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
    )
    assert rejected_again is not None
    assert rejected_again.state is HandoffState.REJECTED


def test_cancel_after_accept_is_conflict(tmp_path, fake_clock, id_sequence, owner_actor):
    clock, _ = fake_clock
    store = _store(tmp_path, clock, id_sequence)
    record = store.create_offer(
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
        task_id="task-123",
        summary="Review report",
        snapshot_digest="a" * 64,
        request_id="req-1",
    )
    accepted = store.accept(
        record.handoff_id,
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
    )
    assert accepted is not None

    cancelled = store.cancel(
        record.handoff_id,
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
    )
    assert cancelled is None


def test_stale_revision_cannot_mutate(tmp_path, fake_clock, id_sequence, owner_actor):
    clock, _ = fake_clock
    store = _store(tmp_path, clock, id_sequence)
    record = store.create_offer(
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
        task_id="task-123",
        summary="Review report",
        snapshot_digest="a" * 64,
        request_id="req-1",
    )
    accepted = store.accept(
        record.handoff_id,
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
    )
    assert accepted is not None

    forged = store.accept(
        record.handoff_id,
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
    )
    assert forged is None


def test_expire_due_transitions_past_ttl(tmp_path, fake_clock, id_sequence, owner_actor):
    clock, value = fake_clock
    store = _store(tmp_path, clock, id_sequence)
    record = store.create_offer(
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
        task_id="task-123",
        summary="Review report",
        snapshot_digest="a" * 64,
        request_id="req-1",
    )
    value["now"] = record.expires_at + 1.0
    expired_count = store.expire_due(now=value["now"])
    assert expired_count == 1

    loaded = store.get_scoped(
        record.handoff_id,
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
    )
    assert loaded is not None
    assert loaded.state is HandoffState.EXPIRED


def test_expire_due_does_not_affect_terminal_or_fresh(tmp_path, fake_clock, id_sequence, owner_actor):
    clock, value = fake_clock
    store = _store(tmp_path, clock, id_sequence)
    offered = store.create_offer(
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
        task_id="task-123",
        summary="Review report",
        snapshot_digest="a" * 64,
        request_id="req-1",
    )
    accepted = store.create_offer(
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
        task_id="task-456",
        summary="Accepted task",
        snapshot_digest="b" * 64,
        request_id="req-2",
    )
    accepted = store.accept(
        accepted.handoff_id,
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
    )
    assert accepted is not None

    value["now"] = offered.expires_at + 1.0
    expired_count = store.expire_due(now=value["now"])
    assert expired_count == 1


def test_expire_single_persists_expired_state(tmp_path, fake_clock, id_sequence, owner_actor):
    clock, value = fake_clock
    store = _store(tmp_path, clock, id_sequence)
    record = store.create_offer(
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
        task_id="task-123",
        summary="Review report",
        snapshot_digest="a" * 64,
        request_id="req-1",
    )
    value["now"] = record.expires_at + 1.0
    expired = store.expire(
        record.handoff_id,
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
        now=value["now"],
    )
    assert expired is not None
    assert expired.state is HandoffState.EXPIRED



def test_database_and_directory_permissions(tmp_path):
    def clock() -> float:
        return 1000.0

    def factory() -> str:
        return "h001"

    path = tmp_path / "state" / "handoffs.db"
    store = HandoffStore(path, clock=clock, handoff_id_factory=factory)
    store.create_offer(
        actor_id="local-owner",
        session_id="session-1",
        task_id="task-123",
        summary="Review report",
        snapshot_digest="a" * 64,
        request_id="req-1",
    )

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_store_repr_is_content_free(tmp_path, fake_clock, id_sequence, owner_actor):
    clock, _ = fake_clock
    store = _store(tmp_path, clock, id_sequence)
    rep = repr(store)
    assert "handoffs.db" not in rep
    assert "db_path" not in rep
    assert "HandoffStore(<redacted>)" in rep
