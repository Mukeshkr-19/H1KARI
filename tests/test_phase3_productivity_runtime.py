"""Deterministic tests for the bounded Phase 3 productivity runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.action_policy import Actor, ActorContext
from core.protocol import validate_server_message
from core.productivity import (
    ActionProposal,
    ActionTarget,
    PreviewField,
    ProductivityAction,
    ProductivityRuntime,
    ProductivityService,
    SqliteApprovalStore,
    TargetKind,
)
from core.productivity.authorization import ApprovalScope


class MutableClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class ApprovalIds:
    def __init__(self, *values: object) -> None:
        self._values = list(values)
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        if not self._values:
            raise RuntimeError("no id")
        value = self._values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


@pytest.fixture
def owner() -> ActorContext:
    return ActorContext(actor_id="owner_1", actor=Actor.OWNER, session_id="session_1")


@pytest.fixture
def other_session(owner: ActorContext) -> ActorContext:
    return ActorContext(
        actor_id=owner.actor_id,
        actor=Actor.OWNER,
        session_id="session_2",
    )


@pytest.fixture
def proposal(owner: ActorContext) -> ActionProposal:
    return make_proposal(owner)


@pytest.fixture
def store(tmp_path: Path) -> SqliteApprovalStore:
    return SqliteApprovalStore(str(tmp_path / "approvals.db"))


@pytest.fixture
def service(store: SqliteApprovalStore) -> ProductivityService:
    return ProductivityService(store)


def make_proposal(
    actor: ActorContext,
    *,
    proposal_id: str = "proposal_1",
    expires_at: float = 2000.0,
    preview_count: int = 1,
) -> ActionProposal:
    return ActionProposal(
        proposal_id=proposal_id,
        action=ProductivityAction.EMAIL_DRAFT,
        actor=actor,
        targets=(ActionTarget(TargetKind.EMAIL_RECIPIENT, "user@example.com"),),
        preview_fields=tuple(
            PreviewField(f"field_{index}", f"Field {index}", "private preview")
            for index in range(preview_count)
        ),
        created_at=1000.0,
        expires_at=expires_at,
    )


def assert_valid(message: dict) -> None:
    assert validate_server_message(message) is None


def test_prepare_returns_canonical_confirmation(
    service: ProductivityService,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    message = runtime.prepare(owner, proposal)
    assert message["type"] == "productivity_confirmation_required"
    assert message["proposal_id"] == proposal.proposal_id
    assert message["allowed_scopes"] == [
        "once",
        "session",
        "duration",
        "precise_persistent",
    ]
    assert "owner_1" not in str(message)
    assert "session_1" not in str(message)
    assert "approval_1" not in str(message)
    assert_valid(message)


def test_prepare_failure_is_safe_and_never_returns_service_payload(
    service: ProductivityService,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(2500.0), ApprovalIds("approval_1"))
    message = runtime.prepare(owner, proposal)
    assert message == {
        "type": "productivity_error",
        "proposal_id": "proposal_1",
        "code": "proposal_expired",
    }
    assert_valid(message)


def test_prepare_rolls_back_when_preview_exceeds_protocol_bounds(
    service: ProductivityService,
    owner: ActorContext,
) -> None:
    proposal = make_proposal(owner, preview_count=33)
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    message = runtime.prepare(owner, proposal)
    assert message["type"] == "productivity_error"
    assert runtime.status(owner, proposal.proposal_id)["code"] == "proposal_invalid"


def test_confirm_generates_id_server_side_and_bounds_expiry(
    service: ProductivityService,
    store: SqliteApprovalStore,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    ids = ApprovalIds("approval_1")
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ids)
    runtime.prepare(owner, proposal)
    message = runtime.confirm(owner, proposal.proposal_id)
    assert message == {
        "type": "productivity_update",
        "proposal_id": "proposal_1",
        "status": "approved",
    }
    approval = store.get("approval_1", owner.actor_id)
    assert approval is not None
    assert approval.expiry == 1800.0
    assert ids.calls == 1
    assert_valid(message)


def test_confirm_uses_earlier_proposal_expiry(
    service: ProductivityService,
    store: SqliteApprovalStore,
    owner: ActorContext,
) -> None:
    proposal = make_proposal(owner, expires_at=1600.0)
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    assert runtime.confirm(owner, proposal.proposal_id)["status"] == "approved"
    approval = store.get("approval_1", owner.actor_id)
    assert approval is not None
    assert approval.expiry == 1600.0


def test_confirm_and_status_report_expiry_at_exact_proposal_deadline(
    service: ProductivityService,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    clock = MutableClock(1500.0)
    runtime = ProductivityRuntime(service, clock, ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    clock.value = proposal.expires_at
    confirmed = runtime.confirm(owner, proposal.proposal_id)
    status = runtime.status(owner, proposal.proposal_id)
    assert confirmed["code"] == "proposal_expired"
    assert status["code"] == "proposal_expired"


def test_double_confirm_is_idempotent_and_issues_one_approval(
    service: ProductivityService,
    store: SqliteApprovalStore,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    ids = ApprovalIds("approval_1", "approval_2")
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ids)
    runtime.prepare(owner, proposal)
    assert runtime.confirm(owner, proposal.proposal_id)["status"] == "approved"
    assert runtime.confirm(owner, proposal.proposal_id)["status"] == "approved"
    assert ids.calls == 1
    assert len(store.list_for_actor(owner.actor_id, limit=64)) == 1
    assert store.get("approval_2", owner.actor_id) is None


@pytest.mark.parametrize("bad_id", ["BAD", "bad:id", "bad id", "", "a" * 81, 7])
def test_confirm_rejects_invalid_factory_output_without_persisting(
    service: ProductivityService,
    store: SqliteApprovalStore,
    owner: ActorContext,
    proposal: ActionProposal,
    bad_id: object,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds(bad_id))
    runtime.prepare(owner, proposal)
    message = runtime.confirm(owner, proposal.proposal_id)
    assert message["type"] == "productivity_error"
    assert message["code"] == "proposal_invalid"
    assert store.list_for_actor(owner.actor_id, limit=64) == []
    assert_valid(message)


def test_confirm_handles_factory_failure_without_exception_details(
    service: ProductivityService,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    runtime = ProductivityRuntime(
        service,
        MutableClock(1500.0),
        ApprovalIds(RuntimeError("private factory detail")),
    )
    runtime.prepare(owner, proposal)
    message = runtime.confirm(owner, proposal.proposal_id)
    assert message["code"] == "confirm_failed"
    assert "private" not in str(message)
    assert_valid(message)


def test_cancel_is_idempotent_and_session_scoped(
    service: ProductivityService,
    store: SqliteApprovalStore,
    owner: ActorContext,
    other_session: ActorContext,
    proposal: ActionProposal,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)
    first = runtime.cancel(owner, proposal.proposal_id)
    second = runtime.cancel(owner, proposal.proposal_id)
    foreign = runtime.cancel(other_session, proposal.proposal_id)
    assert first["status"] == "cancelled"
    assert second == first
    # Cross-session cancel does not reveal whether the proposal existed and
    # does not revoke session-bound approvals belonging to another session.
    assert foreign["type"] == "productivity_update"
    assert foreign["status"] == "cancelled"
    assert_valid(foreign)
    approval = store.get("approval_1", owner.actor_id)
    assert approval is not None
    assert approval.revoked is True


def test_cancel_revokes_existing_approval(
    service: ProductivityService,
    store: SqliteApprovalStore,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)
    assert runtime.cancel(owner, proposal.proposal_id)["status"] == "cancelled"
    approval = store.get("approval_1", owner.actor_id)
    assert approval is not None
    assert approval.revoked is True
    assert runtime.status(owner, proposal.proposal_id)["status"] == "cancelled"


def test_status_maps_pending_confirmed_consumed_and_expired(
    service: ProductivityService,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    clock = MutableClock(1500.0)
    runtime = ProductivityRuntime(service, clock, ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    assert runtime.status(owner, proposal.proposal_id)["status"] == "preview"
    runtime.confirm(owner, proposal.proposal_id)
    assert runtime.status(owner, proposal.proposal_id)["status"] == "approved"
    clock.value = 1800.0
    expired = runtime.status(owner, proposal.proposal_id)
    assert expired["type"] == "productivity_error"
    assert expired["code"] == "proposal_expired"


def test_status_does_not_distinguish_missing_from_cross_session(
    service: ProductivityService,
    owner: ActorContext,
    other_session: ActorContext,
    proposal: ActionProposal,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    missing = runtime.status(other_session, "missing_1")
    foreign = runtime.status(other_session, proposal.proposal_id)
    assert foreign["type"] == missing["type"] == "productivity_error"
    assert foreign["code"] == missing["code"]
    assert foreign["code"] == "proposal_invalid"


def test_authorize_execution_consumes_once_without_executing(
    service: ProductivityService,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)
    message = runtime.authorize_execution(
        owner,
        "approval_1",
        proposal.action,
        proposal.proposal_id,
    )
    assert message == {
        "type": "productivity_update",
        "proposal_id": "proposal_1",
        "status": "executing",
    }
    assert runtime.status(owner, proposal.proposal_id)["status"] == "completed"
    replay = runtime.authorize_execution(
        owner,
        "approval_1",
        proposal.action,
        proposal.proposal_id,
    )
    assert replay["type"] == "productivity_error"
    assert replay["code"] == "proposal_invalid"
    assert_valid(message)
    assert_valid(replay)


def test_authorize_before_confirm_fails_closed(
    service: ProductivityService,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    message = runtime.authorize_execution(
        owner,
        "approval_1",
        proposal.action,
        proposal.proposal_id,
    )
    assert message["type"] == "productivity_error"
    assert message["code"] == "proposal_invalid"


@pytest.mark.parametrize("approval_id", ["BAD", "bad:id", "bad id", "", "a" * 81])
def test_authorize_rejects_invalid_approval_id_before_store_access(
    service: ProductivityService,
    owner: ActorContext,
    proposal: ActionProposal,
    approval_id: str,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    message = runtime.authorize_execution(
        owner,
        approval_id,
        proposal.action,
        proposal.proposal_id,
    )
    assert message["type"] == "productivity_error"
    assert message["code"] == "proposal_invalid"


@pytest.mark.parametrize(
    "bad_clock",
    [True, float("nan"), float("inf"), "1500", 10**1000],
)
def test_invalid_clock_values_fail_safely(
    service: ProductivityService,
    owner: ActorContext,
    proposal: ActionProposal,
    bad_clock: object,
) -> None:
    runtime = ProductivityRuntime(
        service,
        lambda: bad_clock,  # type: ignore[return-value]
        ApprovalIds("approval_1"),
    )
    message = runtime.prepare(owner, proposal)
    assert message["type"] == "productivity_error"
    assert message["code"] == "unavailable"
    assert_valid(message)


def test_invalid_inputs_return_bounded_messages(
    service: ProductivityService,
    owner: ActorContext,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    prepared = runtime.prepare(owner, "private payload")  # type: ignore[arg-type]
    status = runtime.status(owner, "BAD:ID")
    assert prepared == {
        "type": "productivity_error",
        "proposal_id": "invalid-proposal",
        "code": "unavailable",
    }
    assert status["proposal_id"] == "invalid-proposal"
    assert status["code"] == "proposal_invalid"
    assert "private payload" not in str(prepared)
    assert_valid(prepared)
    assert_valid(status)


def test_runtime_source_has_no_external_action_or_logging_imports() -> None:
    source = (
        Path(__file__).resolve().parent.parent / "core" / "productivity" / "runtime.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "subprocess",
        "requests",
        "browser_automation",
        "mac_integration",
        "smtplib",
        "logging",
        "sqlite3",
    )
    for name in forbidden:
        assert f"import {name}" not in source
        assert f"from {name}" not in source


# ---------------------------------------------------------------------------
# confirm_and_ticket / scoped approvals
# ---------------------------------------------------------------------------


def test_confirm_and_ticket_returns_private_approval_id(
    service: ProductivityService,
    store: SqliteApprovalStore,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    ticket = runtime.confirm_and_ticket(owner, proposal.proposal_id, ApprovalScope.ONCE)
    assert ticket.approval_id == "approval_1"
    assert ticket.public_message["status"] == "approved"
    assert store.get("approval_1", owner.actor_id) is not None


def test_confirm_and_ticket_is_idempotent(
    service: ProductivityService,
    store: SqliteApprovalStore,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    ids = ApprovalIds("approval_1", "approval_2")
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ids)
    runtime.prepare(owner, proposal)
    first = runtime.confirm_and_ticket(owner, proposal.proposal_id, ApprovalScope.ONCE)
    second = runtime.confirm_and_ticket(owner, proposal.proposal_id, ApprovalScope.ONCE)
    assert first.approval_id == second.approval_id == "approval_1"
    assert ids.calls == 1
    assert len(store.list_for_actor(owner.actor_id, limit=64)) == 1


def test_confirm_and_ticket_supports_session_scope(
    service: ProductivityService,
    store: SqliteApprovalStore,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    ticket = runtime.confirm_and_ticket(
        owner, proposal.proposal_id, ApprovalScope.SESSION
    )
    assert ticket.approval_id == "approval_1"
    approval = store.get("approval_1", owner.actor_id)
    assert approval.scope is ApprovalScope.SESSION
    assert approval.session_id == owner.session_id
    # Approval expiry is capped by the proposal deadline.
    assert approval.expiry == proposal.expires_at


def test_session_scope_rejects_client_duration(
    service: ProductivityService,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    ticket = runtime.confirm_and_ticket(
        owner,
        proposal.proposal_id,
        ApprovalScope.SESSION,
        duration_seconds=900,
    )
    assert ticket.approval_id is None
    assert ticket.public_message["code"] == "proposal_invalid"


def test_confirm_and_ticket_supports_duration_scope(
    service: ProductivityService,
    store: SqliteApprovalStore,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    ticket = runtime.confirm_and_ticket(
        owner, proposal.proposal_id, ApprovalScope.DURATION, duration_seconds=3600
    )
    assert ticket.approval_id == "approval_1"
    approval = store.get("approval_1", owner.actor_id)
    assert approval.scope is ApprovalScope.DURATION
    assert approval.session_id is None
    # Approval expiry is capped by the proposal deadline.
    assert approval.expiry == proposal.expires_at


@pytest.mark.parametrize("duration_seconds", [900, 3600, 28800])
def test_confirm_and_ticket_duration_choices(
    service: ProductivityService,
    store: SqliteApprovalStore,
    owner: ActorContext,
    proposal: ActionProposal,
    duration_seconds: int,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    ticket = runtime.confirm_and_ticket(
        owner,
        proposal.proposal_id,
        ApprovalScope.DURATION,
        duration_seconds=duration_seconds,
    )
    assert ticket.approval_id == "approval_1"
    approval = store.get("approval_1", owner.actor_id)
    # Approval expiry is capped by the proposal deadline.
    assert approval.expiry == proposal.expires_at


@pytest.mark.parametrize("duration_seconds", [100, 1000, 86400, None])
def test_confirm_and_ticket_rejects_invalid_duration(
    service: ProductivityService,
    owner: ActorContext,
    proposal: ActionProposal,
    duration_seconds: object,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    ticket = runtime.confirm_and_ticket(
        owner,
        proposal.proposal_id,
        ApprovalScope.DURATION,
        duration_seconds=duration_seconds,  # type: ignore[arg-type]
    )
    assert ticket.public_message["type"] == "productivity_error"
    assert ticket.public_message["code"] == "proposal_invalid"
    assert ticket.approval_id is None


def test_confirm_and_ticket_precise_persistent_requires_acknowledgement(
    service: ProductivityService,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    ticket = runtime.confirm_and_ticket(
        owner, proposal.proposal_id, ApprovalScope.PRECISE_PERSISTENT
    )
    assert ticket.public_message["code"] == "proposal_invalid"
    assert ticket.approval_id is None

    ticket = runtime.confirm_and_ticket(
        owner, proposal.proposal_id, ApprovalScope.PRECISE_PERSISTENT, acknowledge=True
    )
    assert ticket.approval_id == "approval_1"


def test_confirm_and_ticket_public_message_excludes_approval_id(
    service: ProductivityService,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    ticket = runtime.confirm_and_ticket(owner, proposal.proposal_id, ApprovalScope.ONCE)
    public = ticket.public_message
    assert "approval_1" not in str(public)
    assert "owner_1" not in str(public)
    assert "session_1" not in str(public)
    assert_valid(public)


def test_confirmation_result_repr_is_privacy_safe(
    service: ProductivityService,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    ticket = runtime.confirm_and_ticket(owner, proposal.proposal_id, ApprovalScope.ONCE)
    rep = repr(ticket)
    assert "approval_1" not in rep
    assert "owner_1" not in rep
    assert "session_1" not in rep
    assert "proposal_1" not in rep


def test_authorize_execution_with_scoped_approval(
    service: ProductivityService,
    owner: ActorContext,
    proposal: ActionProposal,
) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))
    runtime.prepare(owner, proposal)
    ticket = runtime.confirm_and_ticket(
        owner, proposal.proposal_id, ApprovalScope.DURATION, duration_seconds=900
    )
    message = runtime.authorize_execution(
        owner,
        ticket.approval_id,  # type: ignore[arg-type]
        proposal.action,
        proposal.proposal_id,
    )
    assert message == {
        "type": "productivity_update",
        "proposal_id": proposal.proposal_id,
        "status": "executing",
    }
