"""Phase 4 handoff runtime tests."""

from __future__ import annotations

from typing import Optional

import pytest

from core.action_policy import Actor, ActorContext
from core.handoff.contracts import FrozenHandoffPreview, HandoffErrorCode, HandoffState
from core.handoff.runtime import HandoffRuntime
from core.handoff.service import HandoffService
from core.handoff.store import HandoffStore
from core.protocol import validate_server_message


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
        "task-789": FrozenHandoffPreview(task_id="task-789", summary="Third task"),
    }

    def lookup(actor: ActorContext, task_id: str) -> Optional[FrozenHandoffPreview]:
        return previews.get(task_id)

    return lookup, previews


def _runtime(tmp_path, clock, factory, lookup, policy=None) -> HandoffRuntime:
    store = HandoffStore(
        tmp_path / "handoffs.db",
        clock=clock,
        handoff_id_factory=factory,
    )
    service = HandoffService(
        store,
        task_lookup=lookup,
        acceptance_policy=policy
        or (lambda actor, preview: actor.actor is Actor.OWNER),
    )
    return HandoffRuntime(service)


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
    runtime = _runtime(tmp_path, clock, id_sequence, lookup)

    offer = runtime.prepare(guest_actor, "req-1", "task-123", "Review report")
    assert offer["type"] == "handoff_offer"
    assert offer["request_id"] == "req-1"
    assert offer["task_id"] == "task-123"
    assert offer["summary"] == "Review report"
    assert "expires_at" in offer
    handoff_id = offer["handoff_id"]

    update = runtime.accept(owner_actor, "req-2", handoff_id, True)
    assert update == {
        "type": "handoff_update",
        "request_id": "req-2",
        "handoff_id": handoff_id,
        "status": "accepted",
    }


def test_fresh_policy_is_invoked_once_with_exact_frozen_preview(
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

    def policy(actor, preview):
        seen.append((actor, preview))
        return actor.actor is Actor.OWNER

    runtime = _runtime(tmp_path, clock, id_sequence, lookup, policy=policy)
    runtime.prepare(guest_actor, "req-1", "task-123", "Review report")
    runtime.accept(owner_actor, "req-2", "h001", True)

    assert len(seen) == 1
    assert seen[0][0] is owner_actor
    assert seen[0][1] == FrozenHandoffPreview(
        task_id="task-123", summary="Review report"
    )


def test_caller_variables_cannot_change_stored_preview(
    tmp_path,
    fake_clock,
    id_sequence,
    owner_actor,
):
    clock, _ = fake_clock
    captured: dict[str, FrozenHandoffPreview] = {}

    def lookup(actor: ActorContext, task_id: str) -> Optional[FrozenHandoffPreview]:
        if task_id not in captured:
            captured[task_id] = FrozenHandoffPreview(
                task_id=task_id, summary="Original summary"
            )
        return captured[task_id]

    runtime = _runtime(tmp_path, clock, id_sequence, lookup)
    offer = runtime.prepare(owner_actor, "req-1", "task-123", "Original summary")
    assert offer["summary"] == "Original summary"

    captured["task-123"] = FrozenHandoffPreview(
        task_id="task-123", summary="Changed summary"
    )
    status = runtime.status(owner_actor, "req-2", offer["handoff_id"])
    assert status["status"] == "offered"

    runtime.accept(owner_actor, "req-3", offer["handoff_id"], True)
    record = runtime._service.store.status(
        offer["handoff_id"],
        actor_id=owner_actor.actor_id,
        session_id=owner_actor.session_id,
    )
    assert record is not None
    assert record.summary == "Original summary"


def test_policy_false_leaves_offer_unaccepted(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    guest_actor,
    owner_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    runtime = _runtime(
        tmp_path, clock, id_sequence, lookup, policy=lambda actor, preview: False
    )
    runtime.prepare(guest_actor, "req-1", "task-123", "Review report")
    result = runtime.accept(owner_actor, "req-2", "h001", True)
    assert result == {
        "type": "handoff_error",
        "request_id": "req-2",
        "handoff_id": "h001",
        "code": "policy_denied",
    }
    assert runtime.status(guest_actor, "req-3", "h001")["status"] == "offered"


def test_policy_exception_is_safe_and_preserves_offer(
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
        raise RuntimeError("private policy detail")

    runtime = _runtime(tmp_path, clock, id_sequence, lookup, policy=fail)
    runtime.prepare(guest_actor, "req-1", "task-123", "Review report")
    result = runtime.accept(owner_actor, "req-2", "h001", True)
    assert result["type"] == "handoff_error"
    assert result["code"] == "unavailable"
    assert "private" not in str(result)
    assert runtime.status(guest_actor, "req-3", "h001")["status"] == "offered"


def test_guest_cross_session_cannot_read_or_cancel_offer(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    guest_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    runtime = _runtime(tmp_path, clock, id_sequence, lookup)
    runtime.prepare(guest_actor, "req-1", "task-123", "Review report")

    other_guest = ActorContext(
        actor_id="guest",
        actor=Actor.GUEST,
        session_id="other-session",
        source="websocket",
    )
    assert runtime.status(other_guest, "req-2", "h001") == {
        "type": "handoff_error",
        "request_id": "req-2",
        "handoff_id": "h001",
        "code": "handoff_not_found",
    }
    assert runtime.cancel(other_guest, "req-3", "h001") == {
        "type": "handoff_error",
        "request_id": "req-3",
        "handoff_id": "h001",
        "code": "handoff_not_found",
    }
    assert runtime.accept(other_guest, "req-4", "h001", True) == {
        "type": "handoff_error",
        "request_id": "req-4",
        "handoff_id": "h001",
        "code": "unauthorized",
    }
    assert runtime.status(guest_actor, "req-5", "h001")["status"] == "offered"


def test_owner_accept_reject_cancel(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    owner_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    runtime = _runtime(tmp_path, clock, id_sequence, lookup)

    runtime.prepare(owner_actor, "req-1", "task-123", "Review report")
    assert runtime.accept(owner_actor, "req-2", "h001", True) == {
        "type": "handoff_update",
        "request_id": "req-2",
        "handoff_id": "h001",
        "status": "accepted",
    }

    runtime2 = _runtime(tmp_path, clock, id_sequence, lookup)
    runtime2.prepare(owner_actor, "req-1", "task-456", "Another task")
    assert runtime2.reject(owner_actor, "req-2", "h002") == {
        "type": "handoff_update",
        "request_id": "req-2",
        "handoff_id": "h002",
        "status": "rejected",
    }

    runtime3 = _runtime(tmp_path, clock, id_sequence, lookup)
    runtime3.prepare(owner_actor, "req-1", "task-789", "Third task")
    assert runtime3.cancel(owner_actor, "req-2", "h003") == {
        "type": "handoff_update",
        "request_id": "req-2",
        "handoff_id": "h003",
        "status": "cancelled",
    }


def test_duplicate_accept_reject_cancel_status_are_idempotent_or_conflict(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    owner_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    runtime = _runtime(tmp_path, clock, id_sequence, lookup)

    runtime.prepare(owner_actor, "req-1", "task-123", "Review report")
    assert runtime.accept(owner_actor, "req-2", "h001", True)["status"] == "accepted"
    assert runtime.accept(owner_actor, "req-3", "h001", True) == {
        "type": "handoff_error",
        "request_id": "req-3",
        "handoff_id": "h001",
        "code": "handoff_conflict",
    }
    assert runtime.reject(owner_actor, "req-4", "h001",) == {
        "type": "handoff_error",
        "request_id": "req-4",
        "handoff_id": "h001",
        "code": "handoff_conflict",
    }
    assert runtime.cancel(owner_actor, "req-5", "h001",) == {
        "type": "handoff_error",
        "request_id": "req-5",
        "handoff_id": "h001",
        "code": "handoff_conflict",
    }
    assert runtime.status(owner_actor, "req-6", "h001")["status"] == "accepted"


def test_request_id_is_echoed_exactly(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    owner_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    runtime = _runtime(tmp_path, clock, id_sequence, lookup)

    offer = runtime.prepare(owner_actor, "req-abc-123", "task-123", "Review report")
    assert offer["request_id"] == "req-abc-123"

    update = runtime.reject(owner_actor, "req-def-456", "h001")
    assert update["request_id"] == "req-def-456"


def test_expiry_at_exact_clock_boundary(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    owner_actor,
):
    clock, value = fake_clock
    lookup, _ = preview_lookup
    runtime = _runtime(tmp_path, clock, id_sequence, lookup)

    runtime.prepare(owner_actor, "req-1", "task-123", "Review report")
    value["now"] = 1000.0 + 15 * 60 + 1.0
    result = runtime.accept(owner_actor, "req-2", "h001", True)
    assert result == {
        "type": "handoff_error",
        "request_id": "req-2",
        "handoff_id": "h001",
        "code": "handoff_expired",
    }
    assert runtime.status(owner_actor, "req-3", "h001")["status"] == "expired"


def test_task_lookup_exception_sanitization(
    tmp_path,
    fake_clock,
    id_sequence,
    owner_actor,
):
    clock, _ = fake_clock

    def fail_lookup(actor: ActorContext, task_id: str) -> Optional[FrozenHandoffPreview]:
        raise RuntimeError("private lookup detail")

    runtime = _runtime(tmp_path, clock, id_sequence, fail_lookup)
    result = runtime.prepare(owner_actor, "req-1", "task-123", "summary")
    assert result["type"] == "handoff_error"
    assert result["code"] == "unavailable"
    assert "private" not in str(result)


def test_invalid_handoff_id_returns_safe_error(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    owner_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    runtime = _runtime(tmp_path, clock, id_sequence, lookup)
    runtime.prepare(owner_actor, "req-1", "task-123", "Review report")
    result = runtime.status(owner_actor, "req-2", "BAD-ID")
    assert result["type"] == "handoff_error"
    assert result["code"] == "handoff_not_found"


def test_all_outbound_messages_validate_against_protocol(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    guest_actor,
    owner_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    runtime = _runtime(tmp_path, clock, id_sequence, lookup)

    messages = []
    messages.append(
        runtime.prepare(guest_actor, "req-1", "task-123", "Review report")
    )
    messages.append(
        runtime.prepare(guest_actor, "req-2", "task-123", "Review report")
    )
    messages.append(runtime.accept(owner_actor, "req-3", "h001", True))
    messages.append(runtime.reject(owner_actor, "req-4", "h001"))
    messages.append(runtime.status(owner_actor, "req-5", "h001"))
    messages.append(runtime.status(owner_actor, "req-6", "no-such-id"))

    for message in messages:
        assert validate_server_message(message) is None


def test_outbound_messages_contain_no_authority_fields(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    owner_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    runtime = _runtime(tmp_path, clock, id_sequence, lookup)

    offer = runtime.prepare(owner_actor, "req-1", "task-123", "Review report")
    text = str(offer)
    assert "approval_id" not in text.lower()
    assert "grant" not in text.lower()
    assert "ticket" not in text.lower()
    assert "execution" not in text.lower()


def test_expire_due_returns_expired_count(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    owner_actor,
):
    clock, value = fake_clock
    lookup, _ = preview_lookup
    runtime = _runtime(tmp_path, clock, id_sequence, lookup)

    runtime.prepare(owner_actor, "req-1", "task-123", "Review report")
    value["now"] = 1000.0 + 15 * 60 + 1.0
    assert runtime.expire_due() == 1
    assert runtime.status(owner_actor, "req-2", "h001")["status"] == "expired"


def test_runtime_constructor_rejects_non_service():
    with pytest.raises(TypeError):
        HandoffRuntime("not-a-service")  # type: ignore[arg-type]


def test_runtime_repr_is_content_free(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    owner_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    runtime = _runtime(tmp_path, clock, id_sequence, lookup)
    rep = repr(runtime)
    assert "db_path" not in rep
    assert "handoffs.db" not in rep
