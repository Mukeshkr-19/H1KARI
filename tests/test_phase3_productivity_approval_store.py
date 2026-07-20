"""Tests for core.productivity.approval_store safety and bounded queries."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from core.action_policy import Actor, ActorContext
from core.productivity import (
    ActionProposal,
    ActionTarget,
    ApprovalScope,
    PreviewField,
    ProductivityAction,
    ProductivityApproval,
    SqliteApprovalStore,
    TargetKind,
)
from core.productivity.approval_store import ApprovalStoreError
from core.productivity.authorization import issue_for_proposal, snapshot_digest


@pytest.fixture
def proposal(tmp_path: Path) -> ActionProposal:
    owner = ActorContext(actor_id="owner_1", actor=Actor.OWNER, session_id="session_1")
    return ActionProposal(
        proposal_id="prop_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        actor=owner,
        targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
        preview_fields=(PreviewField("query", "Search query", "hikari phase 3"),),
        created_at=1000.0,
        expires_at=2000.0,
    )


def _make_approval(
    proposal: ActionProposal,
    approval_id: str,
    scope: ApprovalScope = ApprovalScope.ONCE,
    issued_at: float = 1500.0,
    expiry: float | None = 1800.0,
) -> ProductivityApproval:
    return issue_for_proposal(
        proposal,
        approval_id=approval_id,
        scope=scope,
        issued_at=issued_at,
        expiry=expiry,
    )


def test_constructor_creates_database_with_restrictive_permissions(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "approvals.db"
    store = SqliteApprovalStore(str(db_path))
    store.close()

    assert (tmp_path / "nested").stat().st_mode & 0o777 == 0o700
    assert db_path.stat().st_mode & 0o777 == 0o600


def test_constructor_fails_when_parent_is_not_a_directory(tmp_path: Path) -> None:
    file_path = tmp_path / "not_a_directory"
    file_path.write_text("x")
    db_path = file_path / "approvals.db"

    with pytest.raises(ApprovalStoreError):
        SqliteApprovalStore(str(db_path))


def test_constructor_fails_when_database_path_is_a_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "approvals_dir"
    db_path.mkdir()

    with pytest.raises(ApprovalStoreError):
        SqliteApprovalStore(str(db_path))


def test_constructor_fails_with_corrupt_database_file(tmp_path: Path) -> None:
    db_path = tmp_path / "approvals.db"
    db_path.write_bytes(b"this is not a sqlite database")

    with pytest.raises(ApprovalStoreError):
        SqliteApprovalStore(str(db_path))


def test_error_messages_do_not_contain_database_path(tmp_path: Path) -> None:
    db_path = tmp_path / "approvals.db"
    db_path.write_bytes(b"not sqlite")

    with pytest.raises(ApprovalStoreError) as exc_info:
        SqliteApprovalStore(str(db_path))

    message = str(exc_info.value)
    assert str(db_path) not in message
    assert "sqlite" not in message.lower()
    assert "approvals.db" not in message


def test_issue_and_get_round_trip(tmp_path: Path, proposal: ActionProposal) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))
    approval = _make_approval(proposal, "approval_1")
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


def test_get_returns_none_for_missing_approval(
    tmp_path: Path, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))

    assert store.get("missing", proposal.actor.actor_id) is None


def test_issue_rejects_duplicate_approval_id(tmp_path: Path, proposal: ActionProposal) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))
    approval = _make_approval(proposal, "approval_1")
    store.issue(approval)

    with pytest.raises(ApprovalStoreError):
        store.issue(approval)


def test_find_current_returns_exact_scope_and_session(
    tmp_path: Path, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))

    once = _make_approval(proposal, "approval_once")
    session = issue_for_proposal(
        proposal,
        approval_id="approval_session",
        scope=ApprovalScope.SESSION,
        issued_at=1500.0,
        expiry=1800.0,
    )
    duration = issue_for_proposal(
        proposal,
        approval_id="approval_duration",
        scope=ApprovalScope.DURATION,
        issued_at=1500.0,
        expiry=1800.0,
    )

    store.issue(once)
    store.issue(session)
    store.issue(duration)

    found_once = store.find_current(
        proposal.actor.actor_id, proposal.proposal_id, ApprovalScope.ONCE, session_id="session_1"
    )
    assert found_once is not None
    assert found_once.approval_id == "approval_once"

    found_session = store.find_current(
        proposal.actor.actor_id, proposal.proposal_id, ApprovalScope.SESSION, session_id="session_1"
    )
    assert found_session is not None
    assert found_session.approval_id == "approval_session"

    found_duration = store.find_current(
        proposal.actor.actor_id, proposal.proposal_id, ApprovalScope.DURATION
    )
    assert found_duration is not None
    assert found_duration.approval_id == "approval_duration"


def test_find_current_does_not_cross_sessions(
    tmp_path: Path, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))
    approval = _make_approval(proposal, "approval_1")
    store.issue(approval)

    found = store.find_current(
        proposal.actor.actor_id,
        proposal.proposal_id,
        ApprovalScope.ONCE,
        session_id="session_2",
    )
    assert found is None


def test_find_current_for_proposal_is_deterministic(
    tmp_path: Path, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))

    older = _make_approval(proposal, "approval_older", issued_at=1400.0)
    newer = _make_approval(proposal, "approval_newer", issued_at=1500.0)
    store.issue(older)
    store.issue(newer)

    found = store.find_current_for_proposal(
        proposal.actor.actor_id, proposal.proposal_id, session_id="session_1"
    )
    assert found is not None
    assert found.approval_id == "approval_newer"


def test_find_current_for_proposal_skips_revoked(
    tmp_path: Path, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))

    revoked = _make_approval(proposal, "approval_revoked")
    active = _make_approval(proposal, "approval_active", issued_at=1500.0)
    store.issue(revoked)
    store.issue(active)
    store.revoke("approval_revoked", proposal.actor.actor_id)

    found = store.find_current_for_proposal(
        proposal.actor.actor_id, proposal.proposal_id, session_id="session_1"
    )
    assert found is not None
    assert found.approval_id == "approval_active"


def test_find_current_for_proposal_prefers_older_active_over_newer_revoked(
    tmp_path: Path, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))

    older_active = _make_approval(proposal, "older_active", issued_at=1400.0)
    newer_revoked = _make_approval(proposal, "newer_revoked", issued_at=1500.0)
    store.issue(older_active)
    store.issue(newer_revoked)
    store.revoke("newer_revoked", proposal.actor.actor_id)

    found = store.find_current_for_proposal(
        proposal.actor.actor_id, proposal.proposal_id, session_id="session_1"
    )
    assert found is not None
    assert found.approval_id == "older_active"


def test_find_current_for_proposal_enforces_session_visibility(
    tmp_path: Path, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))

    once = _make_approval(proposal, "approval_once", issued_at=1400.0)
    session = issue_for_proposal(
        proposal,
        approval_id="approval_session",
        scope=ApprovalScope.SESSION,
        issued_at=1500.0,
        expiry=1800.0,
    )
    store.issue(once)
    store.issue(session)

    found = store.find_current_for_proposal(
        proposal.actor.actor_id, proposal.proposal_id, session_id="session_1"
    )
    assert found is not None
    assert found.approval_id == "approval_session"

    found_other = store.find_current_for_proposal(
        proposal.actor.actor_id, proposal.proposal_id, session_id="session_2"
    )
    assert found_other is None


def test_revoke_durable_for_proposal_only_revokes_duration_and_persistent(
    tmp_path: Path, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))

    once = _make_approval(proposal, "approval_once")
    session = issue_for_proposal(
        proposal,
        approval_id="approval_session",
        scope=ApprovalScope.SESSION,
        issued_at=1500.0,
        expiry=1800.0,
    )
    duration = issue_for_proposal(
        proposal,
        approval_id="approval_duration",
        scope=ApprovalScope.DURATION,
        issued_at=1500.0,
        expiry=1800.0,
    )
    persistent = issue_for_proposal(
        proposal,
        approval_id="approval_persistent",
        scope=ApprovalScope.PRECISE_PERSISTENT,
        issued_at=1500.0,
    )
    store.issue(once)
    store.issue(session)
    store.issue(duration)
    store.issue(persistent)

    count = store.revoke_durable_for_proposal(proposal.actor.actor_id, proposal.proposal_id)
    assert count == 2

    assert store.get("approval_once", proposal.actor.actor_id).revoked is False
    assert store.get("approval_session", proposal.actor.actor_id).revoked is False
    assert store.get("approval_duration", proposal.actor.actor_id).revoked is True
    assert store.get("approval_persistent", proposal.actor.actor_id).revoked is True


def test_revoke_all_for_proposal_does_not_load_approvals(
    tmp_path: Path, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))

    for i in range(5):
        approval = _make_approval(proposal, f"approval_{i}", issued_at=1400.0 + i)
        store.issue(approval)

    count = store.revoke_all_for_proposal(proposal.actor.actor_id, proposal.proposal_id)
    assert count == 5

    remaining = store.list_for_actor(proposal.actor.actor_id, limit=64)
    assert all(a.revoked for a in remaining)


def test_revoke_all_for_proposal_is_blind_across_sessions(
    tmp_path: Path, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))
    approval = _make_approval(proposal, "approval_1")
    store.issue(approval)

    # Same actor_id, different session still revokes because the store is keyed
    # by actor_id/proposal_id. The service layer enforces session isolation.
    count = store.revoke_all_for_proposal(proposal.actor.actor_id, proposal.proposal_id)
    assert count == 1


def test_revoke_all_for_proposal_does_not_touch_other_actors(
    tmp_path: Path, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))
    approval = _make_approval(proposal, "approval_1")
    store.issue(approval)

    count = store.revoke_all_for_proposal("other_owner", proposal.proposal_id)
    assert count == 0

    loaded = store.get("approval_1", proposal.actor.actor_id)
    assert loaded is not None
    assert loaded.revoked is False


def test_list_for_actor_requires_bounded_limit(tmp_path: Path, proposal: ActionProposal) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))

    with pytest.raises(ApprovalStoreError):
        store.list_for_actor(proposal.actor.actor_id, limit=0)

    with pytest.raises(ApprovalStoreError):
        store.list_for_actor(proposal.actor.actor_id, limit=257)

    with pytest.raises(ApprovalStoreError):
        store.list_for_actor(proposal.actor.actor_id, limit=-1)


def test_list_for_actor_orders_results_deterministically(
    tmp_path: Path, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))

    for i in range(3):
        approval = _make_approval(proposal, f"approval_{i}", issued_at=1400.0 + i)
        store.issue(approval)

    results = store.list_for_actor(proposal.actor.actor_id, limit=64)
    assert [a.approval_id for a in results] == ["approval_2", "approval_1", "approval_0"]


def test_malformed_row_does_not_lead_values(
    tmp_path: Path, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))
    approval = _make_approval(proposal, "approval_1")
    store.issue(approval)
    store.close()

    # Corrupt the scope column to an invalid enum value.
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE approvals SET scope = ?", ("invalid_scope",))
    conn.commit()
    conn.close()

    store2 = SqliteApprovalStore(str(db_path))
    with pytest.raises(ApprovalStoreError):
        store2.get("approval_1", proposal.actor.actor_id)


def test_closed_store_raises_safe_error(tmp_path: Path) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))
    store.close()

    with pytest.raises(ApprovalStoreError):
        store.get("approval_1", "owner_1")


def test_store_does_not_persist_proposal_payload(
    tmp_path: Path, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))
    approval = _make_approval(proposal, "approval_1")
    store.issue(approval)
    store.close()

    raw = db_path.read_bytes()
    assert b"hikari phase 3" not in raw
    assert b"example.com" not in raw


def test_consume_once_is_atomic(tmp_path: Path, proposal: ActionProposal) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))
    approval = _make_approval(proposal, "approval_1")
    store.issue(approval)

    result = store.consume_once("approval_1", proposal.actor.actor_id, now=1600.0)
    assert result.success is True
    assert result.approval is not None
    assert result.approval.remaining_uses == 1

    second = store.consume_once("approval_1", proposal.actor.actor_id, now=1600.0)
    assert second.success is False


def test_revoke_returns_false_when_already_revoked(
    tmp_path: Path, proposal: ActionProposal
) -> None:
    db_path = tmp_path / "approvals.db"
    store = SqliteApprovalStore(str(db_path))
    approval = _make_approval(proposal, "approval_1")
    store.issue(approval)

    assert store.revoke("approval_1", proposal.actor.actor_id) is True
    assert store.revoke("approval_1", proposal.actor.actor_id) is False


def test_rebind_precise_persistent_keeps_server_issued_id(
    tmp_path: Path, proposal: ActionProposal
) -> None:
    store = SqliteApprovalStore(str(tmp_path / "approvals.db"))
    approval = _make_approval(
        proposal,
        "approval_persistent",
        scope=ApprovalScope.PRECISE_PERSISTENT,
        expiry=None,
    )
    store.issue(approval)

    rebound = store.rebind_precise_persistent(
        proposal.actor.actor_id,
        proposal.action,
        snapshot_digest(proposal),
        "prop_2",
    )

    assert rebound is not None
    assert rebound.approval_id == "approval_persistent"
    assert rebound.proposal_id == "prop_2"
    assert store.find_current(
        proposal.actor.actor_id,
        "prop_1",
        ApprovalScope.PRECISE_PERSISTENT,
    ) is None
    assert store.find_current(
        proposal.actor.actor_id,
        "prop_2",
        ApprovalScope.PRECISE_PERSISTENT,
    ) == rebound


@pytest.mark.parametrize(
    ("actor_id", "action", "digest_override"),
    [
        ("owner_2", ProductivityAction.BROWSER_RESEARCH, None),
        ("owner_1", ProductivityAction.EMAIL_DRAFT, None),
        ("owner_1", ProductivityAction.BROWSER_RESEARCH, "b" * 64),
    ],
)
def test_rebind_precise_persistent_rejects_binding_mismatch(
    tmp_path: Path,
    proposal: ActionProposal,
    actor_id: str,
    action: ProductivityAction,
    digest_override: str | None,
) -> None:
    store = SqliteApprovalStore(str(tmp_path / "approvals.db"))
    store.issue(
        _make_approval(
            proposal,
            "approval_persistent",
            scope=ApprovalScope.PRECISE_PERSISTENT,
            expiry=None,
        )
    )

    assert store.rebind_precise_persistent(
        actor_id,
        action,
        digest_override or snapshot_digest(proposal),
        "prop_2",
    ) is None
    loaded = store.get("approval_persistent", proposal.actor.actor_id)
    assert loaded is not None
    assert loaded.proposal_id == "prop_1"


def test_rebind_precise_persistent_rejects_revoked_and_other_scopes(
    tmp_path: Path, proposal: ActionProposal
) -> None:
    store = SqliteApprovalStore(str(tmp_path / "approvals.db"))
    persistent = _make_approval(
        proposal,
        "approval_persistent",
        scope=ApprovalScope.PRECISE_PERSISTENT,
        expiry=None,
    )
    once = _make_approval(proposal, "approval_once")
    store.issue(persistent)
    store.issue(once)
    assert store.revoke("approval_persistent", proposal.actor.actor_id)

    assert store.rebind_precise_persistent(
        proposal.actor.actor_id,
        proposal.action,
        snapshot_digest(proposal),
        "prop_2",
    ) is None
    loaded_once = store.get("approval_once", proposal.actor.actor_id)
    assert loaded_once is not None
    assert loaded_once.proposal_id == "prop_1"


def test_rebind_precise_persistent_rejects_invalid_identifiers_safely(
    tmp_path: Path, proposal: ActionProposal
) -> None:
    store = SqliteApprovalStore(str(tmp_path / "approvals.db"))

    with pytest.raises(ApprovalStoreError) as exc_info:
        store.rebind_precise_persistent(
            "INVALID ACTOR",
            proposal.action,
            snapshot_digest(proposal),
            "prop_2",
        )

    assert str(exc_info.value) == "invalid approval rebind request"
    assert "INVALID ACTOR" not in str(exc_info.value)
