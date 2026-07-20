"""Pure, immutable Phase 3 productivity-action authorization contracts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional, Tuple

from core.action_policy import Actor
from core.productivity.contracts import ActionProposal, ProductivityAction


class ApprovalScope(StrEnum):
    """Bounded authorization scopes for a productivity action approval."""

    ONCE = "once"
    SESSION = "session"
    DURATION = "duration"
    PRECISE_PERSISTENT = "precise_persistent"


_MAX_IDENTIFIER_LENGTH = 80
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
# Largest integer that can be represented exactly as a float and is still a
# plausible Unix timestamp. Rejecting larger integers avoids OverflowError.
_MAX_SAFE_TIMESTAMP = 2 ** 53


def _validate_identifier(value: Optional[str], field: str) -> None:
    if value is None:
        raise ValueError(f"{field} is required")
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"invalid {field}")


def _validate_finite_timestamp(value: float, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    # Reject huge integers without converting to float to avoid OverflowError.
    if isinstance(value, int) and abs(value) > _MAX_SAFE_TIMESTAMP:
        raise ValueError(f"{field} is too large")
    # Floats are checked for NaN/inf.
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise ValueError(f"{field} must be finite")


def _validate_snapshot_digest(value: str) -> None:
    if not isinstance(value, str) or not _HEX_DIGEST_RE.fullmatch(value):
        raise ValueError("snapshot digest must be 64 lowercase hex characters")


def snapshot_digest(proposal: ActionProposal) -> str:
    """Return a deterministic SHA-256 digest of the proposal snapshot.

    The snapshot includes only the stable, user-facing, non-secret parts of the
    proposal: action, ordered targets, ordered preview fields, and task ID.
    Actor/session identifiers and proposal lifecycle fields are intentionally
    excluded. This keeps ordinary approvals bound by their separate proposal ID
    while allowing an explicitly acknowledged precise-persistent grant to be
    rebound to a newly generated proposal with the exact same visible snapshot.

    The canonical payload is never logged or returned.
    """
    if not isinstance(proposal, ActionProposal):
        raise ValueError("proposal must be an ActionProposal")

    targets: list[dict] = [
        {"kind": target.kind.value, "value": target.value}
        for target in proposal.targets
    ]
    preview_fields: list[dict] = [
        {
            "key": field.key,
            "label": field.label,
            "value": field.value,
            "truncated": field.truncated,
        }
        for field in proposal.preview_fields
    ]

    payload = {
        "action": proposal.action.value,
        "targets": targets,
        "preview_fields": preview_fields,
        "task_id": proposal.task_id,
    }

    try:
        canonical = json.dumps(
            payload,
            separators=(",", ":"),
            ensure_ascii=False,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("proposal contains unsupported or mutable values") from exc

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProductivityApproval:
    """Immutable binding of an actor/session to a specific proposal snapshot.

    The approval is fail-closed: any mismatch, expiry, revocation, or scope
    violation causes consumption to be denied. Only owner actors may receive
    or consume an approval.
    """

    approval_id: str
    actor_id: str
    actor: Actor
    session_id: Optional[str]
    action: ProductivityAction
    proposal_id: str
    snapshot_digest: str
    issued_at: float
    scope: ApprovalScope
    expiry: Optional[float] = None
    remaining_uses: Optional[int] = None
    revoked: bool = False

    def __post_init__(self) -> None:
        _validate_identifier(self.approval_id, "approval_id")
        _validate_identifier(self.actor_id, "actor_id")
        if not isinstance(self.actor, Actor):
            raise ValueError("invalid actor")
        if self.actor is not Actor.OWNER:
            raise ValueError("only owner actors may hold an approval")
        if self.session_id is not None:
            _validate_identifier(self.session_id, "session_id")
        if not isinstance(self.action, ProductivityAction):
            raise ValueError("invalid action")
        _validate_identifier(self.proposal_id, "proposal_id")
        _validate_snapshot_digest(self.snapshot_digest)
        _validate_finite_timestamp(self.issued_at, "issued_at")
        if not isinstance(self.scope, ApprovalScope):
            raise ValueError("invalid scope")

        if self.expiry is not None:
            _validate_finite_timestamp(self.expiry, "expiry")

        if self.remaining_uses is not None:
            if not isinstance(self.remaining_uses, int) or isinstance(
                self.remaining_uses, bool
            ):
                raise ValueError("remaining_uses must be an integer")
            if self.remaining_uses < 0:
                raise ValueError("remaining_uses must be non-negative")

        if not isinstance(self.revoked, bool):
            raise ValueError("revoked must be a boolean")

        self._validate_scope_fields()

    def _validate_scope_fields(self) -> None:
        scope = self.scope
        expiry = self.expiry
        remaining = self.remaining_uses
        session = self.session_id

        if scope is ApprovalScope.ONCE:
            if session is None:
                raise ValueError("once scope requires a session_id")
            if expiry is None:
                raise ValueError("once scope requires an expiry")
            if remaining not in (0, 1):
                raise ValueError("once scope requires remaining_uses in {0, 1}")
        elif scope is ApprovalScope.SESSION:
            if session is None:
                raise ValueError("session scope requires a session_id")
            if expiry is None:
                raise ValueError("session scope requires an expiry")
            if remaining is not None:
                raise ValueError("session scope requires remaining_uses to be None")
        elif scope is ApprovalScope.DURATION:
            if session is not None:
                raise ValueError("duration scope requires session_id to be None")
            if expiry is None:
                raise ValueError("duration scope requires an expiry")
            if remaining is not None:
                raise ValueError("duration scope requires remaining_uses to be None")
        elif scope is ApprovalScope.PRECISE_PERSISTENT:
            if session is not None:
                raise ValueError("precise_persistent scope requires session_id to be None")
            if expiry is not None:
                raise ValueError("precise_persistent scope requires expiry to be None")
            if remaining is not None:
                raise ValueError("precise_persistent scope requires remaining_uses to be None")

        if expiry is not None and expiry <= self.issued_at:
            raise ValueError("expiry must be greater than issued_at")

    def is_expired(self, now: float) -> bool:
        """Return True if this approval has expired at the given timestamp."""
        _validate_finite_timestamp(now, "now")
        if self.expiry is None:
            return False
        return now >= self.expiry

    def revoke(self) -> "ProductivityApproval":
        """Return a new approval with revoked set to True."""
        return ProductivityApproval(
            approval_id=self.approval_id,
            actor_id=self.actor_id,
            actor=self.actor,
            session_id=self.session_id,
            action=self.action,
            proposal_id=self.proposal_id,
            snapshot_digest=self.snapshot_digest,
            issued_at=self.issued_at,
            scope=self.scope,
            expiry=self.expiry,
            remaining_uses=self.remaining_uses,
            revoked=True,
        )

    def __repr__(self) -> str:
        return "ProductivityApproval(<redacted>)"


def validate_issue(
    *,
    approval_id: str,
    actor_id: str,
    actor: Actor,
    session_id: Optional[str],
    action: ProductivityAction,
    proposal_id: str,
    snapshot_digest_value: str,
    issued_at: float,
    scope: ApprovalScope,
    expiry: Optional[float] = None,
    remaining_uses: Optional[int] = None,
) -> ProductivityApproval:
    """Validate and return a new immutable productivity approval.

    This helper is the pure issue-time validation boundary. It rejects
    non-owner actors and invalid scope fields before an approval is bound.
    """
    return ProductivityApproval(
        approval_id=approval_id,
        actor_id=actor_id,
        actor=actor,
        session_id=session_id,
        action=action,
        proposal_id=proposal_id,
        snapshot_digest=snapshot_digest_value,
        issued_at=issued_at,
        scope=scope,
        expiry=expiry,
        remaining_uses=remaining_uses,
        revoked=False,
    )


def issue_for_proposal(
    proposal: ActionProposal,
    *,
    approval_id: str,
    scope: ApprovalScope,
    issued_at: float,
    expiry: Optional[float] = None,
    remaining_uses: Optional[int] = None,
) -> ProductivityApproval:
    """Issue an approval bound to a proposal's derived fields.

    The actor, actor_id, session binding, action, proposal ID and snapshot
    digest are all taken directly from the proposal so callers cannot combine
    mismatched fields.
    """
    if not isinstance(proposal, ActionProposal):
        raise ValueError("proposal must be an ActionProposal")

    actor_context = proposal.actor
    if scope in (ApprovalScope.ONCE, ApprovalScope.SESSION):
        session_id = actor_context.session_id
    else:
        session_id = None

    if scope is ApprovalScope.ONCE and remaining_uses is None:
        remaining_uses = 1

    return ProductivityApproval(
        approval_id=approval_id,
        actor_id=actor_context.actor_id,
        actor=actor_context.actor,
        session_id=session_id,
        action=proposal.action,
        proposal_id=proposal.proposal_id,
        snapshot_digest=snapshot_digest(proposal),
        issued_at=issued_at,
        scope=scope,
        expiry=expiry,
        remaining_uses=remaining_uses,
        revoked=False,
    )


def _fail(reason: str) -> Tuple[bool, Optional[ProductivityApproval], str]:
    return False, None, reason


def evaluate_consume(
    approval: ProductivityApproval,
    *,
    actor_id: str,
    actor: Actor,
    session_id: Optional[str],
    action: ProductivityAction,
    proposal_id: str,
    snapshot_digest_value: str,
    now: float,
) -> Tuple[bool, Optional[ProductivityApproval], str]:
    """Evaluate whether an approval may be consumed for a specific request.

    Returns a tuple of (allowed, updated_approval, reason). If allowed is True,
    updated_approval is the approval state after consumption. The original
    approval is never mutated.
    """
    if not isinstance(approval, ProductivityApproval):
        return _fail("invalid approval")
    if actor is not Actor.OWNER:
        return _fail("only owner actors may consume approvals")

    _validate_finite_timestamp(now, "now")

    if approval.revoked:
        return _fail("approval revoked")
    if approval.is_expired(now):
        return _fail("approval expired")

    if approval.actor_id != actor_id:
        return _fail("actor mismatch")
    if approval.action != action:
        return _fail("action mismatch")
    if approval.proposal_id != proposal_id:
        return _fail("proposal mismatch")
    if approval.snapshot_digest != snapshot_digest_value:
        return _fail("snapshot mismatch")

    if approval.scope in (ApprovalScope.ONCE, ApprovalScope.SESSION):
        if approval.session_id != session_id:
            return _fail("session mismatch")

    if approval.scope is ApprovalScope.ONCE:
        if approval.remaining_uses != 1:
            return _fail("once approval already consumed")
        consumed = ProductivityApproval(
            approval_id=approval.approval_id,
            actor_id=approval.actor_id,
            actor=approval.actor,
            session_id=approval.session_id,
            action=approval.action,
            proposal_id=approval.proposal_id,
            snapshot_digest=approval.snapshot_digest,
            issued_at=approval.issued_at,
            scope=approval.scope,
            expiry=approval.expiry,
            remaining_uses=0,
            revoked=approval.revoked,
        )
        return True, consumed, "allowed once"

    return True, approval, "allowed"
