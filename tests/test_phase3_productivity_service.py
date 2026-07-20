"""Tests for core.productivity.service and core.productivity.approval_store."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from core.action_policy import Actor, ActorContext
from core.productivity import (
    ActionProposal,
    ActionTarget,
    PreviewField,
    ProductivityAction,
    ProductivityCode,
    ProductivityService,
    ServiceResult,
    SqliteApprovalStore,
    TargetKind,
)
from core.productivity.authorization import ApprovalScope, snapshot_digest


@pytest.fixture
def owner_context() -> ActorContext:
    return ActorContext(actor_id="owner_1", actor=Actor.OWNER, session_id="session_1")


@pytest.fixture
def other_owner_context() -> ActorContext:
    return ActorContext(actor_id="owner_2", actor=Actor.OWNER, session_id="session_2")


@pytest.fixture
def guest_context() -> ActorContext:
    return ActorContext(actor_id="guest_1", actor=Actor.GUEST, session_id="session_1")


@pytest.fixture
def proposal(owner_context: ActorContext) -> ActionProposal:
    return ActionProposal(
        proposal_id="prop_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        actor=owner_context,
        targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
        preview_fields=(PreviewField("query", "Search query", "hikari phase 3"),),
        created_at=1000.0,
        expires_at=2000.0,
    )


@pytest.fixture
def store(tmp_path: Path) -> SqliteApprovalStore:
    db_path = tmp_path / "approvals.db"
    return SqliteApprovalStore(str(db_path))


@pytest.fixture
def service(store: SqliteApprovalStore) -> ProductivityService:
    return ProductivityService(store)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_register_proposal_requires_owner(
    service: ProductivityService, guest_context: ActorContext, proposal: ActionProposal
) -> None:
    result = service.register_proposal(guest_context, proposal, now=1500.0)
    assert result.code is ProductivityCode.UNAUTHORIZED_ACTOR


def test_register_proposal_requires_matching_actor(
    service: ProductivityService, other_owner_context: ActorContext, proposal: ActionProposal
) -> None:
    result = service.register_proposal(other_owner_context, proposal, now=1500.0)
    assert result.code is ProductivityCode.UNAUTHORIZED_ACTOR


def test_register_proposal_rejects_expired(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    result = service.register_proposal(owner_context, proposal, now=2500.0)
    assert result.code is ProductivityCode.PROPOSAL_EXPIRED


def test_register_proposal_rejects_duplicates(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    assert service.register_proposal(owner_context, proposal, now=1500.0).code is ProductivityCode.OK
    result = service.register_proposal(owner_context, proposal, now=1500.0)
    assert result.code is ProductivityCode.DUPLICATE_PROPOSAL


def test_register_proposal_enforces_capacity(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    for i in range(64):
        prop = ActionProposal(
            proposal_id=f"prop_{i}",
            action=ProductivityAction.BROWSER_RESEARCH,
            actor=owner_context,
            targets=(ActionTarget(TargetKind.WEB_DOMAIN, f"example{i}.com"),),
            preview_fields=(PreviewField("query", "Search query", str(i)),),
            created_at=1000.0,
            expires_at=2000.0,
        )
        assert service.register_proposal(owner_context, prop, now=1500.0).code is ProductivityCode.OK

    overflow = ActionProposal(
        proposal_id="prop_overflow",
        action=ProductivityAction.BROWSER_RESEARCH,
        actor=owner_context,
        targets=(ActionTarget(TargetKind.WEB_DOMAIN, "overflow.com"),),
        preview_fields=(PreviewField("query", "Search query", "overflow"),),
        created_at=1000.0,
        expires_at=2000.0,
    )
    result = service.register_proposal(owner_context, overflow, now=1500.0)
    assert result.code is ProductivityCode.REGISTRY_FULL


def test_get_confirmation_preview_returns_preview(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    result = service.get_confirmation_preview(owner_context, "prop_1", now=1500.0)
    assert result.code is ProductivityCode.OK
    assert result.payload is not None
    preview = result.payload
    assert preview["proposal_id"] == "prop_1"
    assert preview["action"] == "browser.research"


def test_get_confirmation_preview_rejects_cross_session(
    service: ProductivityService, owner_context: ActorContext, other_owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    result = service.get_confirmation_preview(other_owner_context, "prop_1", now=1500.0)
    assert result.code is ProductivityCode.PROPOSAL_NOT_FOUND


def test_get_proposal_expiry_is_bounded_and_session_scoped(
    service: ProductivityService,
    owner_context: ActorContext,
    proposal: ActionProposal,
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    result = service.get_proposal_expiry(owner_context, "prop_1", now=1500.0)
    assert result == ServiceResult(ProductivityCode.OK, 2000.0)

    other_session = ActorContext(
        actor_id=owner_context.actor_id,
        actor=Actor.OWNER,
        session_id="session_2",
    )
    assert (
        service.get_proposal_expiry(other_session, "prop_1", now=1500.0).code
        is ProductivityCode.PROPOSAL_NOT_FOUND
    )
    assert (
        service.get_proposal_expiry(owner_context, "prop_1", now=2000.0).code
        is ProductivityCode.PROPOSAL_EXPIRED
    )


def test_cancel_proposal_removes_from_registry(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    result = service.cancel_proposal(owner_context, "prop_1", now=1500.0)
    assert result.code is ProductivityCode.OK
    result = service.get_confirmation_preview(owner_context, "prop_1", now=1500.0)
    assert result.code is ProductivityCode.PROPOSAL_NOT_FOUND


def test_purge_expired_proposals(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    purged = service.purge_expired_proposals(now=2500.0)
    assert purged == 1
    result = service.get_confirmation_preview(owner_context, "prop_1", now=2500.0)
    assert result.code is ProductivityCode.PROPOSAL_NOT_FOUND


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------


def test_confirm_once_issues_approval(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    result = service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=1800.0,
    )
    assert result.code is ProductivityCode.OK

    status = service.status(owner_context, "prop_1", now=1600.0)
    assert status.code is ProductivityCode.OK
    assert status.payload == {"state": "confirmed"}


def test_confirm_once_rejects_expired_proposal(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    result = service.confirm(
        owner_context, "prop_1", "approval_1", now=2500.0,
        scope=ApprovalScope.ONCE, expiry=2600.0,
    )
    assert result.code is ProductivityCode.PROPOSAL_EXPIRED


def test_confirm_once_rejects_non_owner(
    service: ProductivityService, guest_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(guest_context, proposal, now=1500.0)
    result = service.confirm(
        guest_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=1800.0,
    )
    assert result.code is ProductivityCode.UNAUTHORIZED_ACTOR


# ---------------------------------------------------------------------------
# Consumption
# ---------------------------------------------------------------------------


def test_consume_once_success(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=1800.0,
    )

    result = service.consume(
        owner_context,
        approval_id="approval_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        now=1600.0,
    )
    assert result.code is ProductivityCode.OK

    status = service.status(owner_context, "prop_1", now=1600.0)
    assert status.payload == {"state": "consumed"}


def test_consume_once_rejects_wrong_action(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=1800.0,
    )

    result = service.consume(
        owner_context,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id="prop_1",
        now=1600.0,
    )
    assert result.code is ProductivityCode.STATE_MISMATCH


def test_consume_once_rejects_wrong_proposal(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=1800.0,
    )

    result = service.consume(
        owner_context,
        approval_id="approval_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_2",
        now=1600.0,
    )
    assert result.code is ProductivityCode.PROPOSAL_NOT_FOUND


def test_consume_once_rejects_expired_approval(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=1800.0,
    )

    # Approval expired (1800) but proposal still active (expires at 2000).
    result = service.consume(
        owner_context,
        approval_id="approval_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        now=1900.0,
    )
    assert result.code is ProductivityCode.APPROVAL_EXPIRED_OR_CONSUMED


def test_consume_once_rejects_double_consume(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=1800.0,
    )

    first = service.consume(
        owner_context,
        approval_id="approval_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        now=1600.0,
    )
    assert first.code is ProductivityCode.OK

    # The proposal is completed after the first consume, so it is no longer active.
    second = service.consume(
        owner_context,
        approval_id="approval_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        now=1600.0,
    )
    assert second.code is ProductivityCode.PROPOSAL_NOT_FOUND


def test_consume_once_rejects_cross_session(
    service: ProductivityService,
    owner_context: ActorContext,
    other_owner_context: ActorContext,
    proposal: ActionProposal,
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=1800.0,
    )

    result = service.consume(
        other_owner_context,
        approval_id="approval_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        now=1600.0,
    )
    assert result.code is ProductivityCode.PROPOSAL_NOT_FOUND


def test_consume_once_rejects_expired_proposal(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=1800.0,
    )

    result = service.consume(
        owner_context,
        approval_id="approval_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        now=2500.0,
    )
    assert result.code is ProductivityCode.PROPOSAL_EXPIRED


def test_confirm_once_rejects_approval_expiry_beyond_proposal(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    result = service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=3000.0,
    )
    assert result.code is ProductivityCode.INVALID_EXPIRY


def test_consume_once_rejects_snapshot_mismatch(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=1800.0,
    )

    # Replace the registered proposal with a different one sharing the same ID.
    # Do not cancel the original proposal, as that would revoke the approval.
    different_proposal = ActionProposal(
        proposal_id="prop_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        actor=owner_context,
        targets=(ActionTarget(TargetKind.WEB_DOMAIN, "other.example.com"),),
        preview_fields=(PreviewField("query", "Search query", "different"),),
        created_at=1000.0,
        expires_at=2000.0,
    )
    service._registry._proposals["prop_1"] = different_proposal

    result = service.consume(
        owner_context,
        approval_id="approval_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        now=1600.0,
    )
    assert result.code is ProductivityCode.STATE_MISMATCH


def test_failed_consume_leaves_remaining_uses_unchanged(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=1800.0,
    )

    result = service.consume(
        owner_context,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id="prop_1",
        now=1600.0,
    )
    assert result.code is ProductivityCode.STATE_MISMATCH

    approval = service._store.get("approval_1", owner_context.actor_id)
    assert approval is not None
    assert approval.remaining_uses == 1


def test_status_does_not_disclose_cross_session_approval(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=1800.0,
    )

    other_session = ActorContext(
        actor_id=owner_context.actor_id, actor=Actor.OWNER, session_id="session_2"
    )
    status = service.status(other_session, "prop_1", now=1600.0)
    assert status.code is ProductivityCode.PROPOSAL_NOT_FOUND


# ---------------------------------------------------------------------------
# Approval store persistence
# ---------------------------------------------------------------------------


def test_store_persists_approval_fields_only(tmp_path: Path, proposal: ActionProposal) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))
    approval = snapshot_to_approval(proposal, "approval_1", 1500.0, 1800.0)
    store.issue(approval)

    loaded = store.get("approval_1", proposal.actor.actor_id)
    assert loaded is not None
    assert loaded.approval_id == "approval_1"
    assert loaded.actor_id == proposal.actor.actor_id
    assert loaded.action == proposal.action
    assert loaded.proposal_id == proposal.proposal_id
    assert loaded.snapshot_digest == snapshot_digest(proposal)
    assert loaded.scope is ApprovalScope.ONCE
    assert loaded.remaining_uses == 1


def test_store_does_not_persist_proposal_payload(tmp_path: Path, proposal: ActionProposal) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))
    approval = snapshot_to_approval(proposal, "approval_1", 1500.0, 1800.0)
    store.issue(approval)
    store.close()

    conn = sqlite3.connect(str(db_path))
    text = conn.execute("SELECT sql FROM sqlite_master WHERE type='table'").fetchall()
    conn.close()
    schema = "\n".join(row[0] for row in text)
    assert "preview" not in schema.lower()
    assert "target" not in schema.lower()
    assert "email" not in schema.lower()

    raw = db_path.read_bytes()
    assert b"hikari phase 3" not in raw
    assert b"example.com" not in raw


def test_store_file_permissions(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "approvals.db"
    store = SqliteApprovalStore(str(db_path))
    store.close()

    assert (tmp_path / "nested").stat().st_mode & 0o777 == 0o700
    assert db_path.stat().st_mode & 0o777 == 0o600


def test_store_consume_once_is_atomic(tmp_path: Path, proposal: ActionProposal) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))
    approval = snapshot_to_approval(proposal, "approval_1", 1500.0, 1800.0)
    store.issue(approval)

    result = store.consume_once("approval_1", proposal.actor.actor_id, now=1600.0)
    assert result.success is True
    assert result.approval is not None
    assert result.approval.remaining_uses == 1

    second = store.consume_once("approval_1", proposal.actor.actor_id, now=1600.0)
    assert second.success is False


# ---------------------------------------------------------------------------
# Restart / replay
# ---------------------------------------------------------------------------


def test_service_survives_restart(tmp_path: Path, owner_context: ActorContext, proposal: ActionProposal) -> None:
    db_path = tmp_path / "approvals.db"

    store1 = SqliteApprovalStore(str(db_path))
    service1 = ProductivityService(store1)
    service1.register_proposal(owner_context, proposal, now=1500.0)
    service1.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=1800.0,
    )
    store1.close()

    store2 = SqliteApprovalStore(str(db_path))
    service2 = ProductivityService(store2)
    # The in-memory proposal must be re-registered after restart.
    service2.register_proposal(owner_context, proposal, now=1500.0)
    result = service2.consume(
        owner_context,
        approval_id="approval_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        now=1600.0,
    )
    assert result.code is ProductivityCode.OK


def test_consume_once_rejects_missing_proposal_after_restart(
    tmp_path: Path, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"

    store1 = SqliteApprovalStore(str(db_path))
    service1 = ProductivityService(store1)
    service1.register_proposal(owner_context, proposal, now=1500.0)
    service1.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=1800.0,
    )
    store1.close()

    store2 = SqliteApprovalStore(str(db_path))
    service2 = ProductivityService(store2)
    # Proposal is not re-registered after restart.
    result = service2.consume(
        owner_context,
        approval_id="approval_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        now=1600.0,
    )
    assert result.code is ProductivityCode.PROPOSAL_NOT_FOUND


# ---------------------------------------------------------------------------
# Scoped approvals
# ---------------------------------------------------------------------------


def test_confirm_session_scope_issues_session_bound_approval(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    result = service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.SESSION, expiry=1800.0,
    )
    assert result.code is ProductivityCode.OK
    approval = service._store.get("approval_1", owner_context.actor_id)
    assert approval is not None
    assert approval.scope is ApprovalScope.SESSION
    assert approval.session_id == owner_context.session_id
    assert approval.remaining_uses is None


def test_confirm_duration_scope_issues_actor_bound_approval(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    result = service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.DURATION, expiry=1800.0,
    )
    assert result.code is ProductivityCode.OK
    approval = service._store.get("approval_1", owner_context.actor_id)
    assert approval is not None
    assert approval.scope is ApprovalScope.DURATION
    assert approval.session_id is None
    assert approval.remaining_uses is None


def test_confirm_precise_persistent_requires_acknowledgement(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    result = service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.PRECISE_PERSISTENT,
    )
    assert result.code is ProductivityCode.INVALID_ACKNOWLEDGEMENT

    result = service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.PRECISE_PERSISTENT, acknowledge=True,
    )
    assert result.code is ProductivityCode.OK
    approval = service._store.get("approval_1", owner_context.actor_id)
    assert approval is not None
    assert approval.scope is ApprovalScope.PRECISE_PERSISTENT
    assert approval.session_id is None
    assert approval.expiry is None
    assert approval.remaining_uses is None


def test_confirm_precise_persistent_rejects_expiry(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    result = service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.PRECISE_PERSISTENT, expiry=1800.0, acknowledge=True,
    )
    assert result.code is ProductivityCode.INVALID_EXPIRY


def test_confirm_rejects_invalid_expiry_values(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    for bad_expiry in (True, float("nan"), float("inf"), 1000.0, 3000.0):
        result = service.confirm(
            owner_context, "prop_1", "approval_1", now=1500.0,
            scope=ApprovalScope.ONCE, expiry=bad_expiry,  # type: ignore[arg-type]
        )
        assert result.code in (ProductivityCode.INVALID_EXPIRY, ProductivityCode.PROPOSAL_EXPIRED)


def test_consume_session_scope_enforces_session_mismatch(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.SESSION, expiry=1800.0,
    )

    other_session = ActorContext(
        actor_id=owner_context.actor_id,
        actor=Actor.OWNER,
        session_id="session_2",
    )
    result = service.consume(
        other_session,
        approval_id="approval_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        now=1600.0,
    )
    # The registry is session-scoped; cross-session access is indistinguishable
    # from a missing proposal to avoid disclosing approval existence.
    assert result.code is ProductivityCode.PROPOSAL_NOT_FOUND


def test_consume_duration_scope_allows_multiple_uses_until_expiry(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.DURATION, expiry=1800.0,
    )

    for _ in range(3):
        result = service.consume(
            owner_context,
            approval_id="approval_1",
            action=ProductivityAction.BROWSER_RESEARCH,
            proposal_id="prop_1",
            now=1600.0,
        )
        assert result.code is ProductivityCode.OK


    result = service.consume(
        owner_context,
        approval_id="approval_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        now=1900.0,
    )
    assert result.code is ProductivityCode.APPROVAL_EXPIRED_OR_CONSUMED


def test_consume_precise_persistent_revocation_prevents_use(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.PRECISE_PERSISTENT, acknowledge=True,
    )

    result = service.consume(
        owner_context,
        approval_id="approval_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        now=1600.0,
    )
    assert result.code is ProductivityCode.OK

    service.cancel_proposal(owner_context, "prop_1", now=1600.0)

    approval = service._store.get("approval_1", owner_context.actor_id)
    assert approval is not None
    assert approval.revoked is True


def test_precise_persistent_remains_usable_and_revocable_after_preview_expiry(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context,
        "prop_1",
        "approval_1",
        now=1500.0,
        scope=ApprovalScope.PRECISE_PERSISTENT,
        acknowledge=True,
    )

    assert service.consume(
        owner_context,
        approval_id="approval_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        now=2500.0,
    ).code is ProductivityCode.OK
    assert service.cancel_proposal(
        owner_context, "prop_1", now=2500.0
    ).code is ProductivityCode.OK
    approval = service._store.get("approval_1", owner_context.actor_id)
    assert approval is not None
    assert approval.revoked is True


def test_double_confirmation_is_idempotent(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=1800.0,
    )
    service.confirm(
        owner_context, "prop_1", "approval_2", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=1800.0,
    )
    approvals = service._store.list_for_actor(owner_context.actor_id, limit=64)
    assert len(approvals) == 1
    assert approvals[0].approval_id == "approval_1"


def test_cancellation_revokes_all_approvals_for_proposal(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.SESSION, expiry=1800.0,
    )
    service.confirm(
        owner_context, "prop_1", "approval_2", now=1500.0,
        scope=ApprovalScope.DURATION, expiry=1800.0,
    )
    service.cancel_proposal(owner_context, "prop_1", now=1500.0)
    approvals = service._store.list_for_actor(owner_context.actor_id, limit=64)
    assert all(a.revoked for a in approvals)


def test_status_reports_revoked_after_cancellation(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=1800.0,
    )
    service.cancel_proposal(owner_context, "prop_1", now=1500.0)
    status = service.status(owner_context, "prop_1", now=1600.0)
    assert status.code is ProductivityCode.OK
    assert status.payload == {"state": "revoked"}


def test_cancel_proposal_revokes_durable_approvals_after_restart(
    tmp_path: Path, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store1 = SqliteApprovalStore(str(db_path))
    service1 = ProductivityService(store1)
    service1.register_proposal(owner_context, proposal, now=1500.0)
    service1.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.DURATION, expiry=1800.0,
    )
    service1.confirm(
        owner_context, "prop_1", "approval_2", now=1500.0,
        scope=ApprovalScope.PRECISE_PERSISTENT, acknowledge=True,
    )
    store1.close()

    store2 = SqliteApprovalStore(str(db_path))
    service2 = ProductivityService(store2)
    # Proposal is not re-registered; durable approvals should still be revocable.
    result = service2.cancel_proposal(owner_context, "prop_1", now=1600.0)
    assert result.code is ProductivityCode.OK

    approval1 = store2.get("approval_1", owner_context.actor_id)
    approval2 = store2.get("approval_2", owner_context.actor_id)
    assert approval1 is not None and approval1.revoked is True
    assert approval2 is not None and approval2.revoked is True


def test_cancel_proposal_without_registry_does_not_revoke_session_bound_approvals(
    tmp_path: Path, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store1 = SqliteApprovalStore(str(db_path))
    service1 = ProductivityService(store1)
    service1.register_proposal(owner_context, proposal, now=1500.0)
    service1.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.ONCE, expiry=1800.0,
    )
    service1.confirm(
        owner_context, "prop_1", "approval_2", now=1500.0,
        scope=ApprovalScope.SESSION, expiry=1800.0,
    )
    store1.close()

    store2 = SqliteApprovalStore(str(db_path))
    service2 = ProductivityService(store2)
    # Without the in-memory proposal, only durable approvals are revoked.
    result = service2.cancel_proposal(owner_context, "prop_1", now=1600.0)
    assert result.code is ProductivityCode.OK

    approval1 = store2.get("approval_1", owner_context.actor_id)
    approval2 = store2.get("approval_2", owner_context.actor_id)
    assert approval1 is not None and approval1.revoked is False
    assert approval2 is not None and approval2.revoked is False


def test_cancel_proposal_returns_ok_when_registry_absent_and_no_durable_approval(
    tmp_path: Path, owner_context: ActorContext
) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))
    service = ProductivityService(store)
    # No proposal was ever registered and no durable approval exists.
    result = service.cancel_proposal(owner_context, "prop_1", now=1600.0)
    assert result.code is ProductivityCode.OK


def test_status_does_not_disclose_durable_approval_to_other_session(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.DURATION, expiry=1800.0,
    )

    other_session = ActorContext(
        actor_id=owner_context.actor_id, actor=Actor.OWNER, session_id="session_2"
    )
    status = service.status(other_session, "prop_1", now=1600.0)
    assert status.code is ProductivityCode.PROPOSAL_NOT_FOUND


def test_status_does_not_disclose_precise_persistent_approval_to_other_session(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope=ApprovalScope.PRECISE_PERSISTENT, acknowledge=True,
    )

    other_session = ActorContext(
        actor_id=owner_context.actor_id, actor=Actor.OWNER, session_id="session_2"
    )
    status = service.status(other_session, "prop_1", now=1600.0)
    assert status.code is ProductivityCode.PROPOSAL_NOT_FOUND


def test_confirm_rejects_invalid_scope_before_store_access(
    service: ProductivityService, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    service.register_proposal(owner_context, proposal, now=1500.0)
    result = service.confirm(
        owner_context, "prop_1", "approval_1", now=1500.0,
        scope="not_a_scope",  # type: ignore[arg-type]
        expiry=1800.0,
    )
    assert result.code is ProductivityCode.INVALID_SCOPE


def test_precise_persistent_rebinds_across_restart_without_candidate_id(
    tmp_path: Path, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store1 = SqliteApprovalStore(str(db_path))
    service1 = ProductivityService(store1)
    assert service1.register_proposal(owner_context, proposal, now=1500.0).code is ProductivityCode.OK
    issued = service1.confirm(
        owner_context,
        proposal.proposal_id,
        "approval_original",
        now=1500.0,
        scope=ApprovalScope.PRECISE_PERSISTENT,
        acknowledge=True,
    )
    assert issued.payload == "approval_original"
    store1.close()

    resumed_actor = ActorContext(
        actor_id=owner_context.actor_id,
        actor=Actor.OWNER,
        session_id="session_2",
    )
    resumed_proposal = ActionProposal(
        proposal_id="prop_2",
        action=proposal.action,
        actor=resumed_actor,
        targets=proposal.targets,
        preview_fields=proposal.preview_fields,
        created_at=3000.0,
        expires_at=4000.0,
        task_id=proposal.task_id,
    )
    store2 = SqliteApprovalStore(str(db_path))
    service2 = ProductivityService(store2)
    assert service2.register_proposal(
        resumed_actor, resumed_proposal, now=3500.0
    ).code is ProductivityCode.OK
    candidate_calls = 0

    def candidate_id() -> str:
        nonlocal candidate_calls
        candidate_calls += 1
        return "approval_candidate"

    rebound = service2.confirm(
        resumed_actor,
        resumed_proposal.proposal_id,
        candidate_id,
        now=3500.0,
        scope=ApprovalScope.PRECISE_PERSISTENT,
        acknowledge=True,
    )

    assert rebound == ServiceResult(ProductivityCode.OK, "approval_original")
    assert candidate_calls == 0
    assert service2.consume(
        resumed_actor,
        "approval_original",
        resumed_proposal.action,
        resumed_proposal.proposal_id,
        now=3501.0,
    ).code is ProductivityCode.OK


def test_precise_persistent_rebind_requires_acknowledgement_on_new_proposal(
    tmp_path: Path, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store1 = SqliteApprovalStore(str(db_path))
    service1 = ProductivityService(store1)
    service1.register_proposal(owner_context, proposal, now=1500.0)
    service1.confirm(
        owner_context,
        proposal.proposal_id,
        "approval_original",
        now=1500.0,
        scope=ApprovalScope.PRECISE_PERSISTENT,
        acknowledge=True,
    )
    store1.close()

    new_proposal = ActionProposal(
        proposal_id="prop_2",
        action=proposal.action,
        actor=owner_context,
        targets=proposal.targets,
        preview_fields=proposal.preview_fields,
        created_at=1501.0,
        expires_at=2500.0,
        task_id=proposal.task_id,
    )
    store2 = SqliteApprovalStore(str(db_path))
    service2 = ProductivityService(store2)
    service2.register_proposal(owner_context, new_proposal, now=1600.0)

    result = service2.confirm(
        owner_context,
        "prop_2",
        lambda: "approval_candidate",
        now=1600.0,
        scope=ApprovalScope.PRECISE_PERSISTENT,
        acknowledge=False,
    )

    assert result.code is ProductivityCode.INVALID_ACKNOWLEDGEMENT
    original = store2.get("approval_original", owner_context.actor_id)
    assert original is not None
    assert original.proposal_id == "prop_1"


def test_precise_persistent_does_not_rebind_changed_snapshot_or_actor(
    tmp_path: Path, owner_context: ActorContext, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store1 = SqliteApprovalStore(str(db_path))
    service1 = ProductivityService(store1)
    service1.register_proposal(owner_context, proposal, now=1500.0)
    service1.confirm(
        owner_context,
        proposal.proposal_id,
        "approval_original",
        now=1500.0,
        scope=ApprovalScope.PRECISE_PERSISTENT,
        acknowledge=True,
    )
    store1.close()

    other_actor = ActorContext(
        actor_id="owner_2", actor=Actor.OWNER, session_id="session_2"
    )
    changed = ActionProposal(
        proposal_id="prop_2",
        action=proposal.action,
        actor=other_actor,
        targets=proposal.targets,
        preview_fields=(PreviewField("query", "Search query", "different"),),
        created_at=1501.0,
        expires_at=2500.0,
        task_id=proposal.task_id,
    )
    store2 = SqliteApprovalStore(str(db_path))
    service2 = ProductivityService(store2)
    service2.register_proposal(other_actor, changed, now=1600.0)
    result = service2.confirm(
        other_actor,
        changed.proposal_id,
        "approval_new",
        now=1600.0,
        scope=ApprovalScope.PRECISE_PERSISTENT,
        acknowledge=True,
    )

    assert result == ServiceResult(ProductivityCode.OK, "approval_new")
    original = store2.get("approval_original", owner_context.actor_id)
    assert original is not None and original.proposal_id == "prop_1"


@pytest.mark.parametrize(
    "scope",
    [ApprovalScope.ONCE, ApprovalScope.SESSION, ApprovalScope.DURATION],
)
def test_nonpersistent_scopes_do_not_rebind_across_new_proposals(
    tmp_path: Path,
    owner_context: ActorContext,
    proposal: ActionProposal,
    scope: ApprovalScope,
) -> None:
    db_path = tmp_path / f"{scope.value}.db"
    store1 = SqliteApprovalStore(str(db_path))
    service1 = ProductivityService(store1)
    service1.register_proposal(owner_context, proposal, now=1500.0)
    service1.confirm(
        owner_context,
        proposal.proposal_id,
        "approval_original",
        now=1500.0,
        scope=scope,
        expiry=1800.0,
    )
    store1.close()

    new_proposal = ActionProposal(
        proposal_id="prop_2",
        action=proposal.action,
        actor=owner_context,
        targets=proposal.targets,
        preview_fields=proposal.preview_fields,
        created_at=1501.0,
        expires_at=2500.0,
        task_id=proposal.task_id,
    )
    store2 = SqliteApprovalStore(str(db_path))
    service2 = ProductivityService(store2)
    service2.register_proposal(owner_context, new_proposal, now=1600.0)
    result = service2.confirm(
        owner_context,
        new_proposal.proposal_id,
        "approval_new",
        now=1600.0,
        scope=scope,
        expiry=1800.0,
    )

    assert result == ServiceResult(ProductivityCode.OK, "approval_new")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def snapshot_to_approval(
    proposal: ActionProposal, approval_id: str, issued_at: float, expiry: float
) -> ProductivityApproval:
    from core.productivity.authorization import issue_for_proposal

    return issue_for_proposal(
        proposal,
        approval_id=approval_id,
        scope=ApprovalScope.ONCE,
        issued_at=issued_at,
        expiry=expiry,
        remaining_uses=1,
    )
