"""Tests for core.productivity.authorization."""

from __future__ import annotations

import pytest

from core.action_policy import Actor, ActorContext
from core.productivity.authorization import (
    ApprovalScope,
    ProductivityApproval,
    evaluate_consume,
    issue_for_proposal,
    snapshot_digest,
    validate_issue,
)
from core.productivity.contracts import (
    ActionProposal,
    ActionTarget,
    PreviewField,
    ProductivityAction,
    TargetKind,
)


@pytest.fixture
def owner_context() -> ActorContext:
    return ActorContext(actor_id="owner_1", actor=Actor.OWNER, session_id="session_1")


@pytest.fixture
def proposal(owner_context: ActorContext) -> ActionProposal:
    return ActionProposal(
        proposal_id="prop_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        actor=owner_context,
        targets=(
            ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),
            ActionTarget(TargetKind.WEB_DOMAIN, "example.org"),
        ),
        preview_fields=(
            PreviewField("query", "Search query", "hikari phase 3"),
            PreviewField("limit", "Result limit", "10", truncated=False),
        ),
        created_at=1000.0,
        expires_at=2000.0,
    )


@pytest.fixture
def digest(proposal: ActionProposal) -> str:
    return snapshot_digest(proposal)


def _make_approval(
    *,
    approval_id: str = "approval_1",
    scope: ApprovalScope = ApprovalScope.ONCE,
    proposal_id: str = "prop_1",
    actor_id: str = "owner_1",
    actor: Actor = Actor.OWNER,
    session_id: str | None = "session_1",
    action: ProductivityAction = ProductivityAction.BROWSER_RESEARCH,
    snapshot_digest_value: str | None = None,
    issued_at: float = 1000.0,
    expiry: float | None = 3000.0,
    remaining_uses: int | None = 1,
    revoked: bool = False,
) -> ProductivityApproval:
    return ProductivityApproval(
        approval_id=approval_id,
        actor_id=actor_id,
        actor=actor,
        session_id=session_id,
        action=action,
        proposal_id=proposal_id,
        snapshot_digest=snapshot_digest_value or "a" * 64,
        issued_at=issued_at,
        scope=scope,
        expiry=expiry,
        remaining_uses=remaining_uses,
        revoked=revoked,
    )


# ---------------------------------------------------------------------------
# snapshot_digest
# ---------------------------------------------------------------------------

def test_snapshot_digest_is_stable(proposal: ActionProposal) -> None:
    assert snapshot_digest(proposal) == snapshot_digest(proposal)


def test_snapshot_digest_changes_when_proposal_changes(proposal: ActionProposal) -> None:
    original = snapshot_digest(proposal)

    changed_targets = ActionProposal(
        proposal_id=proposal.proposal_id,
        action=proposal.action,
        actor=proposal.actor,
        targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
        preview_fields=proposal.preview_fields,
        created_at=proposal.created_at,
        expires_at=proposal.expires_at,
    )
    assert snapshot_digest(changed_targets) != original

    changed_preview = ActionProposal(
        proposal_id=proposal.proposal_id,
        action=proposal.action,
        actor=proposal.actor,
        targets=proposal.targets,
        preview_fields=(
            PreviewField("query", "Search query", "hikari phase 3"),
            PreviewField("limit", "Result limit", "11", truncated=False),
        ),
        created_at=proposal.created_at,
        expires_at=proposal.expires_at,
    )
    assert snapshot_digest(changed_preview) != original

    changed_action = ActionProposal(
        proposal_id=proposal.proposal_id,
        action=ProductivityAction.EMAIL_DRAFT,
        actor=proposal.actor,
        targets=proposal.targets,
        preview_fields=proposal.preview_fields,
        created_at=proposal.created_at,
        expires_at=proposal.expires_at,
    )
    assert snapshot_digest(changed_action) != original

    changed_task = ActionProposal(
        proposal_id=proposal.proposal_id,
        action=proposal.action,
        actor=proposal.actor,
        targets=proposal.targets,
        preview_fields=proposal.preview_fields,
        created_at=proposal.created_at,
        expires_at=proposal.expires_at,
        task_id="task_2",
    )
    assert snapshot_digest(changed_task) != original

    changed_lifecycle = ActionProposal(
        proposal_id="prop_2",
        action=proposal.action,
        actor=proposal.actor,
        targets=proposal.targets,
        preview_fields=proposal.preview_fields,
        created_at=1001.0,
        expires_at=3000.0,
    )
    assert snapshot_digest(changed_lifecycle) == original


def test_snapshot_digest_excludes_actor_and_session(proposal: ActionProposal) -> None:
    original = snapshot_digest(proposal)

    other_actor = ActionProposal(
        proposal_id=proposal.proposal_id,
        action=proposal.action,
        actor=ActorContext(actor_id="owner_2", actor=Actor.OWNER, session_id="session_2"),
        targets=proposal.targets,
        preview_fields=proposal.preview_fields,
        created_at=proposal.created_at,
        expires_at=proposal.expires_at,
    )
    assert snapshot_digest(other_actor) == original


def test_snapshot_digest_preserves_target_and_preview_order(proposal: ActionProposal) -> None:
    reordered = ActionProposal(
        proposal_id=proposal.proposal_id,
        action=proposal.action,
        actor=proposal.actor,
        targets=(
            ActionTarget(TargetKind.WEB_DOMAIN, "example.org"),
            ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),
        ),
        preview_fields=proposal.preview_fields,
        created_at=proposal.created_at,
        expires_at=proposal.expires_at,
    )
    assert snapshot_digest(reordered) != snapshot_digest(proposal)


def test_snapshot_digest_rejects_non_proposal() -> None:
    with pytest.raises(ValueError):
        snapshot_digest("not a proposal")  # type: ignore[arg-type]


def test_snapshot_digest_is_lowercase_hex(proposal: ActionProposal) -> None:
    digest = snapshot_digest(proposal)
    assert len(digest) == 64
    int(digest, 16)
    assert digest == digest.lower()


# ---------------------------------------------------------------------------
# ProductivityApproval scope validation
# ---------------------------------------------------------------------------

def test_approval_scope_once_requires_session_expiry_remaining_uses() -> None:
    _make_approval(scope=ApprovalScope.ONCE, session_id="session_1", expiry=3000.0, remaining_uses=1)

    with pytest.raises(ValueError):
        _make_approval(scope=ApprovalScope.ONCE, session_id=None, expiry=3000.0, remaining_uses=1)
    with pytest.raises(ValueError):
        _make_approval(scope=ApprovalScope.ONCE, session_id="session_1", expiry=None, remaining_uses=1)
    with pytest.raises(ValueError):
        _make_approval(scope=ApprovalScope.ONCE, session_id="session_1", expiry=3000.0, remaining_uses=None)
    with pytest.raises(ValueError):
        _make_approval(scope=ApprovalScope.ONCE, session_id="session_1", expiry=3000.0, remaining_uses=2)
    with pytest.raises(ValueError):
        _make_approval(scope=ApprovalScope.ONCE, session_id="session_1", expiry=1000.0, remaining_uses=1)


def test_approval_scope_session_requires_session_expiry_no_remaining_uses() -> None:
    _make_approval(
        scope=ApprovalScope.SESSION,
        session_id="session_1",
        expiry=3000.0,
        remaining_uses=None,
    )

    with pytest.raises(ValueError):
        _make_approval(scope=ApprovalScope.SESSION, session_id=None, expiry=3000.0, remaining_uses=None)
    with pytest.raises(ValueError):
        _make_approval(scope=ApprovalScope.SESSION, session_id="session_1", expiry=None, remaining_uses=None)
    with pytest.raises(ValueError):
        _make_approval(scope=ApprovalScope.SESSION, session_id="session_1", expiry=3000.0, remaining_uses=1)
    with pytest.raises(ValueError):
        _make_approval(scope=ApprovalScope.SESSION, session_id="session_1", expiry=1000.0, remaining_uses=None)


def test_approval_scope_duration_requires_no_session_expiry_no_remaining_uses() -> None:
    _make_approval(
        scope=ApprovalScope.DURATION,
        session_id=None,
        expiry=3000.0,
        remaining_uses=None,
    )

    with pytest.raises(ValueError):
        _make_approval(scope=ApprovalScope.DURATION, session_id="session_1", expiry=3000.0, remaining_uses=None)
    with pytest.raises(ValueError):
        _make_approval(scope=ApprovalScope.DURATION, session_id=None, expiry=None, remaining_uses=None)
    with pytest.raises(ValueError):
        _make_approval(scope=ApprovalScope.DURATION, session_id=None, expiry=3000.0, remaining_uses=1)
    with pytest.raises(ValueError):
        _make_approval(scope=ApprovalScope.DURATION, session_id=None, expiry=1000.0, remaining_uses=None)


def test_approval_scope_precise_persistent_requires_no_session_expiry_remaining_uses() -> None:
    _make_approval(
        scope=ApprovalScope.PRECISE_PERSISTENT,
        session_id=None,
        expiry=None,
        remaining_uses=None,
    )

    with pytest.raises(ValueError):
        _make_approval(
            scope=ApprovalScope.PRECISE_PERSISTENT,
            session_id="session_1",
            expiry=None,
            remaining_uses=None,
        )
    with pytest.raises(ValueError):
        _make_approval(
            scope=ApprovalScope.PRECISE_PERSISTENT,
            session_id=None,
            expiry=3000.0,
            remaining_uses=None,
        )
    with pytest.raises(ValueError):
        _make_approval(
            scope=ApprovalScope.PRECISE_PERSISTENT,
            session_id=None,
            expiry=None,
            remaining_uses=1,
        )


# ---------------------------------------------------------------------------
# ProductivityApproval field validation
# ---------------------------------------------------------------------------

def test_approval_rejects_invalid_fields() -> None:
    with pytest.raises(ValueError):
        _make_approval(approval_id="")
    with pytest.raises(ValueError):
        _make_approval(actor_id="")
    with pytest.raises(ValueError):
        _make_approval(proposal_id="")
    with pytest.raises(ValueError):
        _make_approval(snapshot_digest_value="short")
    with pytest.raises(ValueError):
        _make_approval(snapshot_digest_value="G" * 64)
    with pytest.raises(ValueError):
        _make_approval(snapshot_digest_value="ABCDEF" + "0" * 58)
    with pytest.raises(ValueError):
        _make_approval(issued_at=float("nan"))
    with pytest.raises(ValueError):
        _make_approval(issued_at=float("inf"))
    with pytest.raises(ValueError):
        _make_approval(expiry=float("inf"))
    with pytest.raises(ValueError):
        _make_approval(remaining_uses=-1)
    with pytest.raises(ValueError):
        _make_approval(revoked="yes")  # type: ignore[arg-type]


def test_approval_rejects_non_owner_actor() -> None:
    for actor in (Actor.GUEST, Actor.SYSTEM, Actor.UNKNOWN):
        with pytest.raises(ValueError):
            _make_approval(actor=actor)


def test_approval_is_expired(proposal: ActionProposal, digest: str) -> None:
    approval = _make_approval(
        scope=ApprovalScope.DURATION,
        session_id=None,
        expiry=2000.0,
        remaining_uses=None,
        snapshot_digest_value=digest,
    )
    assert approval.is_expired(1999.0) is False
    assert approval.is_expired(2000.0) is True
    assert approval.is_expired(2001.0) is True


def test_approval_is_expired_rejects_invalid_now(proposal: ActionProposal, digest: str) -> None:
    approval = _make_approval(
        scope=ApprovalScope.DURATION,
        session_id=None,
        expiry=2000.0,
        remaining_uses=None,
        snapshot_digest_value=digest,
    )
    with pytest.raises(ValueError):
        approval.is_expired(float("nan"))
    with pytest.raises(ValueError):
        approval.is_expired(float("inf"))
    with pytest.raises(ValueError):
        approval.is_expired(True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        approval.is_expired("now")  # type: ignore[arg-type]


def test_timestamp_validation_rejects_huge_integers() -> None:
    huge = 10 ** 400
    with pytest.raises(ValueError):
        _make_approval(issued_at=huge)


# ---------------------------------------------------------------------------
# Privacy-safe repr
# ---------------------------------------------------------------------------

def test_approval_repr_is_privacy_safe(proposal: ActionProposal, digest: str) -> None:
    approval = _make_approval(
        scope=ApprovalScope.SESSION,
        session_id="session_1",
        expiry=3000.0,
        remaining_uses=None,
        snapshot_digest_value=digest,
    )
    rep = repr(approval)
    assert "approval_id" not in rep
    assert "actor_id" not in rep
    assert "session_id" not in rep
    assert approval.snapshot_digest not in rep
    assert approval.session_id not in rep
    assert approval.actor_id not in rep
    assert approval.proposal_id not in rep
    assert str(approval.issued_at) not in rep
    assert rep == "ProductivityApproval(<redacted>)"
    assert len(rep) < 80
    assert approval.snapshot_digest not in rep
    assert approval.proposal_id not in rep
    assert str(approval.issued_at) not in rep


# ---------------------------------------------------------------------------
# evaluate_consume
# ---------------------------------------------------------------------------

def test_evaluate_consume_once_allows_valid_request(proposal: ActionProposal, digest: str) -> None:
    approval = _make_approval(
        scope=ApprovalScope.ONCE,
        session_id="session_1",
        expiry=3000.0,
        remaining_uses=1,
        snapshot_digest_value=digest,
    )
    allowed, consumed, reason = evaluate_consume(
        approval,
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id="session_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        snapshot_digest_value=digest,
        now=1500.0,
    )
    assert allowed is True
    assert consumed is not None
    assert consumed.remaining_uses == 0
    assert reason == "allowed once"


def test_evaluate_consume_session_allows_valid_request(proposal: ActionProposal, digest: str) -> None:
    approval = _make_approval(
        scope=ApprovalScope.SESSION,
        session_id="session_1",
        expiry=3000.0,
        remaining_uses=None,
        snapshot_digest_value=digest,
    )
    allowed, consumed, reason = evaluate_consume(
        approval,
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id="session_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        snapshot_digest_value=digest,
        now=1500.0,
    )
    assert allowed is True
    assert consumed is approval
    assert reason == "allowed"


def test_evaluate_consume_duration_allows_valid_request(proposal: ActionProposal, digest: str) -> None:
    approval = _make_approval(
        scope=ApprovalScope.DURATION,
        session_id=None,
        expiry=3000.0,
        remaining_uses=None,
        snapshot_digest_value=digest,
    )
    allowed, consumed, reason = evaluate_consume(
        approval,
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id=None,
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        snapshot_digest_value=digest,
        now=1500.0,
    )
    assert allowed is True
    assert consumed is approval
    assert reason == "allowed"


def test_evaluate_consume_precise_persistent_allows_valid_request(
    proposal: ActionProposal, digest: str
) -> None:
    approval = _make_approval(
        scope=ApprovalScope.PRECISE_PERSISTENT,
        session_id=None,
        expiry=None,
        remaining_uses=None,
        snapshot_digest_value=digest,
    )
    allowed, consumed, reason = evaluate_consume(
        approval,
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id=None,
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        snapshot_digest_value=digest,
        now=1500.0,
    )
    assert allowed is True
    assert consumed is approval
    assert reason == "allowed"


def test_evaluate_consume_fails_revoked(proposal: ActionProposal, digest: str) -> None:
    approval = _make_approval(
        scope=ApprovalScope.SESSION,
        session_id="session_1",
        expiry=3000.0,
        remaining_uses=None,
        snapshot_digest_value=digest,
        revoked=True,
    )
    allowed, consumed, reason = evaluate_consume(
        approval,
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id="session_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        snapshot_digest_value=digest,
        now=1500.0,
    )
    assert allowed is False
    assert consumed is None
    assert reason == "approval revoked"


def test_evaluate_consume_fails_expired(proposal: ActionProposal, digest: str) -> None:
    approval = _make_approval(
        scope=ApprovalScope.DURATION,
        session_id=None,
        issued_at=500.0,
        expiry=1000.0,
        remaining_uses=None,
        snapshot_digest_value=digest,
    )
    allowed, consumed, reason = evaluate_consume(
        approval,
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id=None,
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        snapshot_digest_value=digest,
        now=1500.0,
    )
    assert allowed is False
    assert consumed is None
    assert reason == "approval expired"


def test_evaluate_consume_fails_actor_mismatch(proposal: ActionProposal, digest: str) -> None:
    approval = _make_approval(
        scope=ApprovalScope.SESSION,
        session_id="session_1",
        expiry=3000.0,
        remaining_uses=None,
        snapshot_digest_value=digest,
    )
    allowed, consumed, reason = evaluate_consume(
        approval,
        actor_id="owner_2",
        actor=Actor.OWNER,
        session_id="session_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        snapshot_digest_value=digest,
        now=1500.0,
    )
    assert allowed is False
    assert reason == "actor mismatch"


def test_evaluate_consume_fails_action_mismatch(proposal: ActionProposal, digest: str) -> None:
    approval = _make_approval(
        scope=ApprovalScope.SESSION,
        session_id="session_1",
        expiry=3000.0,
        remaining_uses=None,
        snapshot_digest_value=digest,
    )
    allowed, consumed, reason = evaluate_consume(
        approval,
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id="session_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id="prop_1",
        snapshot_digest_value=digest,
        now=1500.0,
    )
    assert allowed is False
    assert reason == "action mismatch"


def test_evaluate_consume_fails_proposal_mismatch(proposal: ActionProposal, digest: str) -> None:
    approval = _make_approval(
        scope=ApprovalScope.SESSION,
        session_id="session_1",
        expiry=3000.0,
        remaining_uses=None,
        snapshot_digest_value=digest,
    )
    allowed, consumed, reason = evaluate_consume(
        approval,
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id="session_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_2",
        snapshot_digest_value=digest,
        now=1500.0,
    )
    assert allowed is False
    assert reason == "proposal mismatch"


def test_evaluate_consume_fails_snapshot_mismatch(proposal: ActionProposal, digest: str) -> None:
    approval = _make_approval(
        scope=ApprovalScope.SESSION,
        session_id="session_1",
        expiry=3000.0,
        remaining_uses=None,
        snapshot_digest_value=digest,
    )
    allowed, consumed, reason = evaluate_consume(
        approval,
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id="session_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        snapshot_digest_value="b" * 64,
        now=1500.0,
    )
    assert allowed is False
    assert reason == "snapshot mismatch"


def test_evaluate_consume_fails_session_mismatch_once(proposal: ActionProposal, digest: str) -> None:
    approval = _make_approval(
        scope=ApprovalScope.ONCE,
        session_id="session_1",
        expiry=3000.0,
        remaining_uses=1,
        snapshot_digest_value=digest,
    )
    allowed, consumed, reason = evaluate_consume(
        approval,
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id="session_2",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        snapshot_digest_value=digest,
        now=1500.0,
    )
    assert allowed is False
    assert reason == "session mismatch"


def test_evaluate_consume_fails_session_mismatch_session(proposal: ActionProposal, digest: str) -> None:
    approval = _make_approval(
        scope=ApprovalScope.SESSION,
        session_id="session_1",
        expiry=3000.0,
        remaining_uses=None,
        snapshot_digest_value=digest,
    )
    allowed, consumed, reason = evaluate_consume(
        approval,
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id="session_2",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        snapshot_digest_value=digest,
        now=1500.0,
    )
    assert allowed is False
    assert reason == "session mismatch"


def test_evaluate_consume_fails_consumed_once(proposal: ActionProposal, digest: str) -> None:
    approval = _make_approval(
        scope=ApprovalScope.ONCE,
        session_id="session_1",
        expiry=3000.0,
        remaining_uses=1,
        snapshot_digest_value=digest,
    )
    allowed, consumed, _ = evaluate_consume(
        approval,
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id="session_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        snapshot_digest_value=digest,
        now=1500.0,
    )
    assert allowed is True
    assert consumed is not None
    allowed, consumed, reason = evaluate_consume(
        consumed,
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id="session_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        snapshot_digest_value=digest,
        now=1500.0,
    )
    assert allowed is False
    assert reason == "once approval already consumed"


def test_evaluate_consume_rejects_invalid_now(proposal: ActionProposal, digest: str) -> None:
    approval = _make_approval(
        scope=ApprovalScope.SESSION,
        session_id="session_1",
        expiry=3000.0,
        remaining_uses=None,
        snapshot_digest_value=digest,
    )
    with pytest.raises(ValueError):
        evaluate_consume(
            approval,
            actor_id="owner_1",
            actor=Actor.OWNER,
            session_id="session_1",
            action=ProductivityAction.BROWSER_RESEARCH,
            proposal_id="prop_1",
            snapshot_digest_value=digest,
            now=float("nan"),
        )


def test_evaluate_consume_rejects_invalid_approval() -> None:
    allowed, consumed, reason = evaluate_consume(
        "not an approval",  # type: ignore[arg-type]
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id="session_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        snapshot_digest_value="a" * 64,
        now=1500.0,
    )
    assert allowed is False
    assert reason == "invalid approval"


def test_evaluate_consume_rejects_non_owner_actor(proposal: ActionProposal, digest: str) -> None:
    approval = _make_approval(
        scope=ApprovalScope.SESSION,
        session_id="session_1",
        expiry=3000.0,
        remaining_uses=None,
        snapshot_digest_value=digest,
    )
    for actor in (Actor.GUEST, Actor.SYSTEM, Actor.UNKNOWN):
        allowed, consumed, reason = evaluate_consume(
            approval,
            actor_id="owner_1",
            actor=actor,
            session_id="session_1",
            action=ProductivityAction.BROWSER_RESEARCH,
            proposal_id="prop_1",
            snapshot_digest_value=digest,
            now=1500.0,
        )
        assert allowed is False
        assert reason == "only owner actors may consume approvals"


def test_evaluate_consume_precise_persistent_rejects_changed_snapshot(
    proposal: ActionProposal, digest: str
) -> None:
    approval = _make_approval(
        scope=ApprovalScope.PRECISE_PERSISTENT,
        session_id=None,
        expiry=None,
        remaining_uses=None,
        snapshot_digest_value=digest,
    )
    other_digest = snapshot_digest(
        ActionProposal(
            proposal_id=proposal.proposal_id,
            action=proposal.action,
            actor=proposal.actor,
            targets=(ActionTarget(TargetKind.WEB_DOMAIN, "attacker.com"),),
            preview_fields=proposal.preview_fields,
            created_at=proposal.created_at,
            expires_at=proposal.expires_at,
        )
    )
    allowed, consumed, reason = evaluate_consume(
        approval,
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id=None,
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        snapshot_digest_value=other_digest,
        now=1500.0,
    )
    assert allowed is False
    assert reason == "snapshot mismatch"


def test_evaluate_consume_precise_persistent_rejects_different_action(
    proposal: ActionProposal, digest: str
) -> None:
    approval = _make_approval(
        scope=ApprovalScope.PRECISE_PERSISTENT,
        session_id=None,
        expiry=None,
        remaining_uses=None,
        snapshot_digest_value=digest,
    )
    allowed, consumed, reason = evaluate_consume(
        approval,
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id=None,
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id="prop_1",
        snapshot_digest_value=digest,
        now=1500.0,
    )
    assert allowed is False
    assert reason == "action mismatch"


def test_evaluate_consume_precise_persistent_rejects_revoked(
    proposal: ActionProposal, digest: str
) -> None:
    approval = _make_approval(
        scope=ApprovalScope.PRECISE_PERSISTENT,
        session_id=None,
        expiry=None,
        remaining_uses=None,
        snapshot_digest_value=digest,
        revoked=True,
    )
    allowed, consumed, reason = evaluate_consume(
        approval,
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id=None,
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        snapshot_digest_value=digest,
        now=1500.0,
    )
    assert allowed is False
    assert reason == "approval revoked"


# ---------------------------------------------------------------------------
# validate_issue and issue_for_proposal
# ---------------------------------------------------------------------------

def test_validate_issue_returns_productivity_approval(proposal: ActionProposal, digest: str) -> None:
    approval = validate_issue(
        approval_id="approval_1",
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id="session_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id="prop_1",
        snapshot_digest_value=digest,
        issued_at=1000.0,
        scope=ApprovalScope.ONCE,
        expiry=3000.0,
        remaining_uses=1,
    )
    assert isinstance(approval, ProductivityApproval)
    assert approval.actor is Actor.OWNER


def test_validate_issue_rejects_non_owner_actor(proposal: ActionProposal, digest: str) -> None:
    for actor in (Actor.GUEST, Actor.SYSTEM, Actor.UNKNOWN):
        with pytest.raises(ValueError):
            validate_issue(
                approval_id="approval_1",
                actor_id="owner_1",
                actor=actor,
                session_id="session_1",
                action=ProductivityAction.BROWSER_RESEARCH,
                proposal_id="prop_1",
                snapshot_digest_value=digest,
                issued_at=1000.0,
                scope=ApprovalScope.ONCE,
                expiry=3000.0,
                remaining_uses=1,
            )


def test_issue_for_proposal_derives_fields(proposal: ActionProposal) -> None:
    approval = issue_for_proposal(
        proposal,
        approval_id="approval_1",
        scope=ApprovalScope.ONCE,
        issued_at=1000.0,
        expiry=3000.0,
    )
    assert approval.actor_id == proposal.actor.actor_id
    assert approval.actor == proposal.actor.actor
    assert approval.session_id == proposal.actor.session_id
    assert approval.action == proposal.action
    assert approval.proposal_id == proposal.proposal_id
    assert approval.snapshot_digest == snapshot_digest(proposal)
    assert approval.scope is ApprovalScope.ONCE
    assert approval.remaining_uses == 1


def test_issue_for_proposal_session_binding_for_session_scope(proposal: ActionProposal) -> None:
    approval = issue_for_proposal(
        proposal,
        approval_id="approval_1",
        scope=ApprovalScope.SESSION,
        issued_at=1000.0,
        expiry=3000.0,
    )
    assert approval.session_id == proposal.actor.session_id
    assert approval.remaining_uses is None


def test_issue_for_proposal_no_session_for_duration(proposal: ActionProposal) -> None:
    approval = issue_for_proposal(
        proposal,
        approval_id="approval_1",
        scope=ApprovalScope.DURATION,
        issued_at=1000.0,
        expiry=3000.0,
    )
    assert approval.session_id is None
    assert approval.remaining_uses is None


def test_issue_for_proposal_no_session_expiry_remaining_for_precise_persistent(
    proposal: ActionProposal,
) -> None:
    approval = issue_for_proposal(
        proposal,
        approval_id="approval_1",
        scope=ApprovalScope.PRECISE_PERSISTENT,
        issued_at=1000.0,
    )
    assert approval.session_id is None
    assert approval.expiry is None
    assert approval.remaining_uses is None


def test_issue_for_proposal_rejects_non_owner_actor(proposal: ActionProposal) -> None:
    guest_proposal = ActionProposal(
        proposal_id=proposal.proposal_id,
        action=proposal.action,
        actor=ActorContext(actor_id="guest_1", actor=Actor.GUEST, session_id="session_1"),
        targets=proposal.targets,
        preview_fields=proposal.preview_fields,
        created_at=proposal.created_at,
        expires_at=proposal.expires_at,
    )
    with pytest.raises(ValueError):
        issue_for_proposal(
            guest_proposal,
            approval_id="approval_1",
            scope=ApprovalScope.ONCE,
            issued_at=1000.0,
            expiry=3000.0,
        )


# ---------------------------------------------------------------------------
# Forbidden imports
# ---------------------------------------------------------------------------

def test_authorization_module_has_no_forbidden_imports() -> None:
    import core.productivity.authorization as auth_module

    forbidden = {
        "time",
        "uuid",
        "socket",
        "urllib",
        "http",
        "requests",
        "subprocess",
        "os",
        "pathlib",
        "sqlite3",
        "pickle",
    }
    names = set(auth_module.__dict__.keys())
    found = forbidden & names
    assert not found, f"forbidden imports present: {found}"
