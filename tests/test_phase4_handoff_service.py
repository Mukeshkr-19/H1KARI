"""Phase 4 handoff service controller tests."""

from __future__ import annotations

from typing import Optional

import pytest

from core.action_policy import Actor, ActorContext
from core.handoff.contracts import (
    FrozenHandoffPreview,
    HandoffErrorCode,
    HandoffState,
)
from core.handoff.service import HandoffService
from core.handoff.store import HandoffStore


@pytest.fixture
def owner_actor() -> ActorContext:
    return ActorContext(
        actor_id="local-owner",
        actor=Actor.OWNER,
        session_id="session-1",
        source="local",
    )


@pytest.fixture
def guest_actor() -> ActorContext:
    return ActorContext(
        actor_id="guest",
        actor=Actor.GUEST,
        session_id="guest-session",
        source="websocket",
    )


@pytest.fixture
def other_session_actor() -> ActorContext:
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


@pytest.fixture
def preview_lookup():
    previews = {
        "task-123": FrozenHandoffPreview(task_id="task-123", summary="Review report"),
        "task-456": FrozenHandoffPreview(task_id="task-456", summary="Another task"),
    }

    def lookup(actor: ActorContext, task_id: str) -> Optional[FrozenHandoffPreview]:
        return previews.get(task_id)

    return lookup, previews


def _service(tmp_path, clock, factory, lookup, policy=None) -> HandoffService:
    store = HandoffStore(
        tmp_path / "handoffs.db",
        clock=clock,
        handoff_id_factory=factory,
    )
    return HandoffService(
        store,
        task_lookup=lookup,
        acceptance_policy=policy or (lambda actor, preview: actor.actor is Actor.OWNER),
    )


def test_guest_offer_can_be_accepted_by_desktop_owner(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    guest_actor,
    owner_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    offered = service.prepare(guest_actor, "task-123", "Review report", "req-1")
    assert offered.success is True
    assert service.status(owner_actor, "h001").state is HandoffState.OFFERED

    accepted = service.accept(owner_actor, "h001", acknowledged=True)
    assert accepted.success is True
    assert accepted.state is HandoffState.ACCEPTED


def test_fresh_policy_receives_frozen_preview_and_can_deny(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    guest_actor,
    owner_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    seen = []

    def deny(actor, preview):
        seen.append((actor, preview))
        return False

    service = _service(tmp_path, clock, id_sequence, lookup, policy=deny)
    service.prepare(guest_actor, "task-123", "Review report", "req-1")

    denied = service.accept(owner_actor, "h001", acknowledged=True)
    assert denied.success is False
    assert denied.error_code is HandoffErrorCode.POLICY_DENIED
    assert seen == [
        (
            owner_actor,
            FrozenHandoffPreview(task_id="task-123", summary="Review report"),
        )
    ]
    assert service.status(guest_actor, "h001").state is HandoffState.OFFERED


def test_policy_failure_is_safe_and_preserves_offer(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    guest_actor,
    owner_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup

    def fail(actor, preview):
        raise RuntimeError("sensitive policy detail")

    service = _service(tmp_path, clock, id_sequence, lookup, policy=fail)
    service.prepare(guest_actor, "task-123", "Review report", "req-1")
    result = service.accept(owner_actor, "h001", acknowledged=True)
    assert result.success is False
    assert result.error_code is HandoffErrorCode.UNAVAILABLE
    assert "sensitive" not in repr(result)
    assert service.status(guest_actor, "h001").state is HandoffState.OFFERED


def test_other_guest_session_cannot_read_or_cancel_offer(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    guest_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)
    service.prepare(guest_actor, "task-123", "Review report", "req-1")
    other_guest = ActorContext(
        actor_id="guest-other",
        actor=Actor.GUEST,
        session_id="guest-session-other",
        source="websocket",
    )
    assert service.status(other_guest, "h001").error_code is HandoffErrorCode.HANDOFF_NOT_FOUND
    assert service.cancel(other_guest, "h001").error_code is HandoffErrorCode.HANDOFF_NOT_FOUND
    assert service.status(guest_actor, "h001").state is HandoffState.OFFERED


def test_successful_offer_and_accept(tmp_path, fake_clock, id_sequence, preview_lookup, owner_actor):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    prepared = service.prepare(owner_actor, "task-123", "Review report", "req-1")
    assert prepared.success is True
    assert prepared.handoff_id == "h001"
    assert prepared.state is HandoffState.OFFERED
    assert prepared.request_id == "req-1"

    accepted = service.accept(owner_actor, "h001", acknowledged=True)
    assert accepted.success is True
    assert accepted.state is HandoffState.ACCEPTED
    assert accepted.handoff_id == "h001"


def test_server_generated_handoff_id(tmp_path, fake_clock, id_sequence, preview_lookup, owner_actor):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    first = service.prepare(owner_actor, "task-123", "Review report", "req-1")
    second = service.prepare(owner_actor, "task-456", "Another task", "req-2")
    assert first.handoff_id == "h001"
    assert second.handoff_id == "h002"
    assert first.handoff_id != second.handoff_id


def test_invalid_id_factory_output(tmp_path, fake_clock, owner_actor):
    clock, _ = fake_clock

    def bad_factory() -> str:
        return "BAD-ID"

    def lookup(actor: ActorContext, task_id: str) -> Optional[FrozenHandoffPreview]:
        return FrozenHandoffPreview(task_id=task_id, summary="summary")

    service = _service(tmp_path, clock, bad_factory, lookup)
    result = service.prepare(owner_actor, "task-123", "summary", "req-1")
    assert result.success is False
    assert result.error_code is HandoffErrorCode.UNAVAILABLE


def test_missing_task_returns_task_not_found(tmp_path, fake_clock, id_sequence, owner_actor):
    clock, _ = fake_clock

    def lookup(actor: ActorContext, task_id: str) -> Optional[FrozenHandoffPreview]:
        return None

    service = _service(tmp_path, clock, id_sequence, lookup)
    result = service.prepare(owner_actor, "task-999", "summary", "req-1")
    assert result.success is False
    assert result.error_code is HandoffErrorCode.TASK_NOT_FOUND
    assert result.request_id == "req-1"


def test_task_correlation_rejects_mismatched_preview(tmp_path, fake_clock, id_sequence, owner_actor):
    clock, _ = fake_clock

    def lookup(actor: ActorContext, task_id: str) -> Optional[FrozenHandoffPreview]:
        return FrozenHandoffPreview(task_id="other-task", summary="summary")

    service = _service(tmp_path, clock, id_sequence, lookup)
    result = service.prepare(owner_actor, "task-123", "summary", "req-1")
    assert result.success is False
    assert result.error_code is HandoffErrorCode.TASK_NOT_FOUND


def test_guest_cannot_accept(tmp_path, fake_clock, id_sequence, preview_lookup, owner_actor, guest_actor):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    service.prepare(owner_actor, "task-123", "Review report", "req-1")
    result = service.accept(guest_actor, "h001", acknowledged=True)
    assert result.success is False
    assert result.error_code is HandoffErrorCode.UNAUTHORIZED


def test_cross_session_status_is_not_found(tmp_path, fake_clock, id_sequence, preview_lookup, owner_actor, other_session_actor):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    service.prepare(owner_actor, "task-123", "Review report", "req-1")
    result = service.status(other_session_actor, "h001")
    assert result.success is False
    assert result.error_code is HandoffErrorCode.HANDOFF_NOT_FOUND


def test_cross_session_accept_is_not_found(tmp_path, fake_clock, id_sequence, preview_lookup, owner_actor, other_session_actor):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    service.prepare(owner_actor, "task-123", "Review report", "req-1")
    result = service.accept(other_session_actor, "h001", acknowledged=True)
    assert result.success is False
    assert result.error_code is HandoffErrorCode.HANDOFF_NOT_FOUND


def test_duplicate_offer_for_same_task_is_conflict(tmp_path, fake_clock, id_sequence, preview_lookup, owner_actor):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    first = service.prepare(owner_actor, "task-123", "Review report", "req-1")
    assert first.success is True
    second = service.prepare(owner_actor, "task-123", "Review report", "req-2")
    assert second.success is False
    assert second.error_code is HandoffErrorCode.HANDOFF_CONFLICT


def test_duplicate_accept_is_conflict(tmp_path, fake_clock, id_sequence, preview_lookup, owner_actor):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    service.prepare(owner_actor, "task-123", "Review report", "req-1")
    first = service.accept(owner_actor, "h001", acknowledged=True)
    assert first.success is True
    second = service.accept(owner_actor, "h001", acknowledged=True)
    assert second.success is False
    assert second.error_code is HandoffErrorCode.HANDOFF_CONFLICT


def test_duplicate_reject_and_cancel_are_idempotent(tmp_path, fake_clock, id_sequence, preview_lookup, owner_actor):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    service.prepare(owner_actor, "task-123", "Review report", "req-1")
    first = service.reject(owner_actor, "h001")
    assert first.success is True
    assert first.state is HandoffState.REJECTED

    second = service.reject(owner_actor, "h001")
    assert second.success is True
    assert second.state is HandoffState.REJECTED

    third = service.cancel(owner_actor, "h001")
    assert third.success is False
    assert third.error_code is HandoffErrorCode.HANDOFF_CONFLICT


def test_accept_requires_acknowledged_exactly_true(tmp_path, fake_clock, id_sequence, preview_lookup, owner_actor):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    service.prepare(owner_actor, "task-123", "Review report", "req-1")
    for bad in [False, 1, "true", None]:
        result = service.accept(owner_actor, "h001", acknowledged=bad)
        assert result.success is False
        assert result.error_code is HandoffErrorCode.INVALID_REQUEST


def test_expiry_boundary(tmp_path, fake_clock, id_sequence, preview_lookup, owner_actor):
    clock, value = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    service.prepare(owner_actor, "task-123", "Review report", "req-1")
    value["now"] = 1000.0 + 15 * 60 + 1.0

    result = service.accept(owner_actor, "h001", acknowledged=True)
    assert result.success is False
    assert result.error_code is HandoffErrorCode.HANDOFF_EXPIRED

    status = service.status(owner_actor, "h001")
    assert status.success is True
    assert status.state is HandoffState.EXPIRED

    record = service.store.status(
        "h001",
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
    )
    assert record is not None
    assert record.state is HandoffState.EXPIRED


def test_terminal_state_past_ttl_is_not_reported_as_expired(tmp_path, fake_clock, id_sequence, preview_lookup, owner_actor):
    clock, value = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    service.prepare(owner_actor, "task-123", "Review report", "req-1")
    accepted = service.accept(owner_actor, "h001", acknowledged=True)
    assert accepted.success is True

    value["now"] = 1000.0 + 15 * 60 + 1.0
    status = service.status(owner_actor, "h001")
    assert status.success is True
    assert status.state is HandoffState.ACCEPTED


def test_expire_due_service_method(tmp_path, fake_clock, id_sequence, preview_lookup, owner_actor):
    clock, value = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    service.prepare(owner_actor, "task-123", "Review report", "req-1")
    value["now"] = 1000.0 + 15 * 60 + 1.0
    assert service.expire_due() == 1


def test_concurrent_accept_has_exactly_one_winner(tmp_path, fake_clock, id_sequence, preview_lookup, owner_actor):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    service.prepare(owner_actor, "task-123", "Review report", "req-1")
    results = [
        service.accept(owner_actor, "h001", acknowledged=True)
        for _ in range(10)
    ]
    winners = [r for r in results if r.success]
    assert len(winners) == 1
    assert all(r.error_code is HandoffErrorCode.HANDOFF_CONFLICT for r in results if not r.success)


def test_task_unchanged_after_failure_and_terminal(tmp_path, fake_clock, id_sequence, preview_lookup, owner_actor):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    service.prepare(owner_actor, "task-123", "Review report", "req-1")
    service.reject(owner_actor, "h001")
    result = service.accept(owner_actor, "h001", acknowledged=True)
    assert result.success is False
    assert result.error_code is HandoffErrorCode.HANDOFF_CONFLICT


def test_frozen_preview_immutability(tmp_path, fake_clock, id_sequence, preview_lookup, owner_actor):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    service.prepare(owner_actor, "task-123", "Review report", "req-1")
    stored = service.status(owner_actor, "h001")
    assert stored.success is True


def test_later_input_cannot_change_stored_preview(tmp_path, fake_clock, id_sequence, owner_actor):
    clock, _ = fake_clock
    captured: dict[str, FrozenHandoffPreview] = {}

    def lookup(actor: ActorContext, task_id: str) -> Optional[FrozenHandoffPreview]:
        if task_id not in captured:
            captured[task_id] = FrozenHandoffPreview(task_id=task_id, summary="Original summary")
        return captured[task_id]

    service = _service(tmp_path, clock, id_sequence, lookup)

    service.prepare(owner_actor, "task-123", "Original summary", "req-1")
    captured["task-123"] = FrozenHandoffPreview(task_id="task-123", summary="Changed summary")
    status = service.status(owner_actor, "h001")
    assert status.success is True

    record = service.store.status(
        "h001",
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
    )
    assert record is not None
    assert record.summary == "Original summary"


def test_content_free_repr_in_service_results(tmp_path, fake_clock, id_sequence, preview_lookup, owner_actor):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    result = service.prepare(owner_actor, "task-123", "Review report", "req-1")
    rep = repr(result)
    assert "req-1" not in rep
    assert "h001" not in rep


def test_no_authority_fields_in_results(tmp_path, fake_clock, id_sequence, preview_lookup, owner_actor):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    result = service.prepare(owner_actor, "task-123", "Review report", "req-1")
    assert "approval_id" not in repr(result).lower()
    assert "grant" not in repr(result).lower()
    assert "ticket" not in repr(result).lower()


def test_service_rejects_invalid_actor_context(tmp_path, fake_clock, id_sequence, preview_lookup):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    result = service.prepare("not-an-actor-context", "task-123", "summary", "req-1")  # type: ignore[arg-type]
    assert result.success is False
    assert result.error_code is HandoffErrorCode.UNAUTHORIZED


def test_invalid_actor_context_for_accept_reject_cancel_status(tmp_path, fake_clock, id_sequence, preview_lookup):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    cases = (
        (service.accept, ("not-an-actor-context", "h001", True)),
        (service.reject, ("not-an-actor-context", "h001")),
        (service.cancel, ("not-an-actor-context", "h001")),
        (service.status, ("not-an-actor-context", "h001")),
    )
    for method, args in cases:  # type: ignore[unreachable]
        result = method(*args)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code is HandoffErrorCode.UNAUTHORIZED


def test_summary_mismatch_returns_invalid_request(tmp_path, fake_clock, id_sequence, preview_lookup, owner_actor):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    service = _service(tmp_path, clock, id_sequence, lookup)

    result = service.prepare(owner_actor, "task-123", "Wrong summary", "req-1")
    assert result.success is False
    assert result.error_code is HandoffErrorCode.INVALID_REQUEST


def test_no_forbidden_imports():
    import ast
    from pathlib import Path
    import core.handoff as handoff_pkg
    from core.handoff import service as service_module
    from core.handoff import store as store_module
    from core.handoff import contracts as contracts_module

    forbidden = {"subprocess", "requests", "httpx", "urllib", "socket", "asyncio"}
    modules = {
        "init": Path(handoff_pkg.__file__),
        "service": service_module,
        "store": store_module,
        "contracts": contracts_module,
    }
    for name, module in modules.items():
        path = str(module) if isinstance(module, Path) else module.__file__
        assert path is not None
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        assert forbidden.isdisjoint(imported), f"forbidden import in {module.__name__}"
