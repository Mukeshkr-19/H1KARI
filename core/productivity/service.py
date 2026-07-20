"""Bounded Phase 3 productivity action service."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Optional

from core.action_policy import Actor, ActorContext
from core.productivity.approval_store import ApprovalStoreError, SqliteApprovalStore
from core.productivity.authorization import (
    ApprovalScope,
    ProductivityApproval,
    evaluate_consume,
    issue_for_proposal,
    snapshot_digest,
)
from core.productivity.contracts import ActionProposal, ProductivityAction


class ProductivityCode(StrEnum):
    """Stable bounded result codes for the productivity service."""

    OK = "ok"
    UNAUTHORIZED_ACTOR = "unauthorized_actor"
    REGISTRY_FULL = "registry_full"
    DUPLICATE_PROPOSAL = "duplicate_proposal"
    PROPOSAL_NOT_FOUND = "proposal_not_found"
    PROPOSAL_EXPIRED = "proposal_expired"
    INVALID_SCOPE = "invalid_scope"
    APPROVAL_NOT_FOUND = "approval_not_found"
    APPROVAL_EXPIRED_OR_CONSUMED = "approval_expired_or_consumed"
    INVALID_EXPIRY = "invalid_expiry"
    INVALID_ACKNOWLEDGEMENT = "invalid_acknowledgement"
    STATE_MISMATCH = "state_mismatch"
    CONSUMPTION_FAILED = "consumption_failed"


@dataclass(frozen=True)
class ServiceResult:
    """Service operation result with a stable code and optional payload."""

    code: ProductivityCode
    payload: Optional[object] = None


class _ProposalRegistry:
    """Bounded in-memory registry of active proposals.

    The registry stores ``ActionProposal`` objects only in memory. It never
    writes proposal targets, preview fields, or other user payload to disk.
    """

    _MAX_ACTIVE = 64

    def __init__(self) -> None:
        self._proposals: dict[str, ActionProposal] = {}

    def register(self, proposal: ActionProposal, now: float) -> ProductivityCode:
        if proposal.proposal_id in self._proposals:
            return ProductivityCode.DUPLICATE_PROPOSAL
        if len(self._proposals) >= self._MAX_ACTIVE:
            return ProductivityCode.REGISTRY_FULL
        if proposal.is_expired(now):
            return ProductivityCode.PROPOSAL_EXPIRED
        self._proposals[proposal.proposal_id] = proposal
        return ProductivityCode.OK

    def get(self, proposal_id: str, actor: ActorContext, now: float) -> Optional[ActionProposal]:
        proposal = self._get_raw(proposal_id, actor)
        if proposal is None:
            return None
        if proposal.is_expired(now):
            return None
        return proposal

    def _get_raw(self, proposal_id: str, actor: ActorContext) -> Optional[ActionProposal]:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return None
        if proposal.actor.actor_id != actor.actor_id:
            return None
        if proposal.actor.session_id != actor.session_id:
            return None
        return proposal

    def remove(self, proposal_id: str, actor: ActorContext) -> bool:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return False
        if proposal.actor.actor_id != actor.actor_id:
            return False
        if proposal.actor.session_id != actor.session_id:
            return False
        del self._proposals[proposal_id]
        return True

    def purge_expired(self, now: float) -> int:
        expired = [
            proposal_id
            for proposal_id, proposal in self._proposals.items()
            if proposal.is_expired(now)
        ]
        for proposal_id in expired:
            self._proposals.pop(proposal_id, None)
        return len(expired)

    def complete(self, proposal_id: str) -> None:
        self._proposals.pop(proposal_id, None)


class ProductivityService:
    """Bounded Phase 3 productivity action service.

    Supports ``ApprovalScope.ONCE``, ``SESSION``, ``DURATION`` and
    ``PRECISE_PERSISTENT`` approvals. Each approval authorizes exactly one
    execution while remaining within its scope constraints.
    """

    def __init__(self, store: SqliteApprovalStore) -> None:
        self._store = store
        self._registry = _ProposalRegistry()

    # ------------------------------------------------------------------
    # Proposal lifecycle
    # ------------------------------------------------------------------

    def register_proposal(
        self,
        actor: ActorContext,
        proposal: ActionProposal,
        now: float,
    ) -> ServiceResult:
        """Register an active proposal in memory."""
        if actor.actor is not Actor.OWNER:
            return ServiceResult(ProductivityCode.UNAUTHORIZED_ACTOR)
        if proposal.actor.actor is not Actor.OWNER:
            return ServiceResult(ProductivityCode.UNAUTHORIZED_ACTOR)
        if proposal.actor.actor_id != actor.actor_id:
            return ServiceResult(ProductivityCode.UNAUTHORIZED_ACTOR)
        if proposal.actor.session_id != actor.session_id:
            return ServiceResult(ProductivityCode.UNAUTHORIZED_ACTOR)
        code = self._registry.register(proposal, now)
        return ServiceResult(code)

    def get_confirmation_preview(
        self,
        actor: ActorContext,
        proposal_id: str,
        now: float,
    ) -> ServiceResult:
        """Return the user-facing preview for an active proposal."""
        if actor.actor is not Actor.OWNER:
            return ServiceResult(ProductivityCode.UNAUTHORIZED_ACTOR)
        proposal = self._registry.get(proposal_id, actor, now)
        if proposal is None:
            return ServiceResult(ProductivityCode.PROPOSAL_NOT_FOUND)
        return ServiceResult(ProductivityCode.OK, proposal.user_preview())

    def get_proposal_expiry(
        self,
        actor: ActorContext,
        proposal_id: str,
        now: float,
    ) -> ServiceResult:
        """Return only the active proposal deadline for bounded orchestration."""
        if actor.actor is not Actor.OWNER:
            return ServiceResult(ProductivityCode.UNAUTHORIZED_ACTOR)
        proposal = self._registry._get_raw(proposal_id, actor)
        if proposal is None:
            return ServiceResult(ProductivityCode.PROPOSAL_NOT_FOUND)
        if proposal.is_expired(now):
            return ServiceResult(ProductivityCode.PROPOSAL_EXPIRED)
        return ServiceResult(ProductivityCode.OK, float(proposal.expires_at))

    def cancel_proposal(
        self,
        actor: ActorContext,
        proposal_id: str,
        now: float,
    ) -> ServiceResult:
        """Cancel an active proposal and revoke any associated approval."""
        if actor.actor is not Actor.OWNER:
            return ServiceResult(ProductivityCode.UNAUTHORIZED_ACTOR)
        proposal = self._registry._get_raw(proposal_id, actor)
        if proposal is None:
            # The in-memory proposal is gone, but durable actor-bound approvals
            # may still exist. Revoke only DURATION/PRECISE_PERSISTENT approvals
            # so that session-bound ONCE/SESSION approvals in another session
            # are not touched.
            self._store.revoke_durable_for_proposal(actor.actor_id, proposal_id)
            return ServiceResult(ProductivityCode.OK)
        self._registry.remove(proposal_id, actor)
        # Revoke every approval tied to this proposal without loading them.
        self._store.revoke_all_for_proposal(actor.actor_id, proposal_id)
        return ServiceResult(ProductivityCode.OK)

    def purge_expired_proposals(self, now: float) -> int:
        """Remove expired proposals from the in-memory registry."""
        return self._registry.purge_expired(now)

    # ------------------------------------------------------------------
    # Confirmation / approval
    # ------------------------------------------------------------------

    def confirm(
        self,
        actor: ActorContext,
        proposal_id: str,
        approval_id: str | Callable[[], str],
        now: float,
        scope: ApprovalScope,
        *,
        expiry: Optional[float] = None,
        acknowledge: bool = False,
    ) -> ServiceResult:
        """Confirm a proposal and persist a durable scoped approval."""
        if actor.actor is not Actor.OWNER:
            return ServiceResult(ProductivityCode.UNAUTHORIZED_ACTOR)

        if not isinstance(scope, ApprovalScope):
            return ServiceResult(ProductivityCode.INVALID_SCOPE)

        proposal = self._registry._get_raw(proposal_id, actor)
        if proposal is None:
            return ServiceResult(ProductivityCode.PROPOSAL_NOT_FOUND)
        if proposal.is_expired(now):
            return ServiceResult(ProductivityCode.PROPOSAL_EXPIRED)

        if scope is ApprovalScope.PRECISE_PERSISTENT:
            if expiry is not None:
                return ServiceResult(ProductivityCode.INVALID_EXPIRY)
            if not acknowledge:
                return ServiceResult(ProductivityCode.INVALID_ACKNOWLEDGEMENT)
        else:
            if expiry is None:
                return ServiceResult(ProductivityCode.INVALID_EXPIRY)
            if isinstance(expiry, bool) or not isinstance(expiry, (int, float)):
                return ServiceResult(ProductivityCode.INVALID_EXPIRY)
            if math.isnan(expiry) or math.isinf(expiry):
                return ServiceResult(ProductivityCode.INVALID_EXPIRY)
            if expiry <= now:
                return ServiceResult(ProductivityCode.PROPOSAL_EXPIRED)
            if expiry > proposal.expires_at:
                return ServiceResult(ProductivityCode.INVALID_EXPIRY)

        # Idempotency never bypasses the scope-specific acknowledgement and
        # expiry contract above.
        existing = self._store.find_current(
            actor.actor_id, proposal_id, scope, session_id=actor.session_id
        )
        if existing is not None and not existing.revoked:
            return ServiceResult(ProductivityCode.OK, existing.approval_id)

        if scope is ApprovalScope.PRECISE_PERSISTENT:
            try:
                rebound = self._store.rebind_precise_persistent(
                    actor.actor_id,
                    proposal.action,
                    snapshot_digest(proposal),
                    proposal.proposal_id,
                )
            except ApprovalStoreError:
                return ServiceResult(ProductivityCode.STATE_MISMATCH)
            if rebound is not None:
                return ServiceResult(ProductivityCode.OK, rebound.approval_id)

        try:
            resolved_approval_id = approval_id() if callable(approval_id) else approval_id
            approval = issue_for_proposal(
                proposal,
                approval_id=resolved_approval_id,
                scope=scope,
                issued_at=now,
                expiry=expiry,
            )
        except ValueError:
            return ServiceResult(ProductivityCode.STATE_MISMATCH)
        self._store.issue(approval)
        return ServiceResult(ProductivityCode.OK, approval.approval_id)

    # ------------------------------------------------------------------
    # Consumption
    # ------------------------------------------------------------------

    def consume(
        self,
        actor: ActorContext,
        approval_id: str,
        action: ProductivityAction,
        proposal_id: str,
        now: float,
    ) -> ServiceResult:
        """Consume a durable scoped approval for an action.

        The caller must supply the action and proposal_id so the service can
        verify exact binding. The original in-memory proposal must be active and
        unexpired, and its recomputed snapshot digest is used for the binding
        check.
        """
        if actor.actor is not Actor.OWNER:
            return ServiceResult(ProductivityCode.UNAUTHORIZED_ACTOR)

        # Require the original in-memory proposal before any store access.
        # Use the raw lookup so we can distinguish "missing" from "expired".
        proposal = self._registry._get_raw(proposal_id, actor)
        if proposal is None:
            return ServiceResult(ProductivityCode.PROPOSAL_NOT_FOUND)
        approval = self._store.get(approval_id, actor.actor_id)
        if approval is None:
            return ServiceResult(ProductivityCode.APPROVAL_NOT_FOUND)

        if (
            proposal.is_expired(now)
            and approval.scope is not ApprovalScope.PRECISE_PERSISTENT
        ):
            return ServiceResult(ProductivityCode.PROPOSAL_EXPIRED)

        if approval.revoked or approval.is_expired(now):
            return ServiceResult(ProductivityCode.APPROVAL_EXPIRED_OR_CONSUMED)

        # Recompute the digest from the active proposal; never compare the
        # approval digest to itself.
        expected_digest = snapshot_digest(proposal)
        allowed, _, _ = evaluate_consume(
            approval,
            actor_id=actor.actor_id,
            actor=actor.actor,
            session_id=actor.session_id,
            action=action,
            proposal_id=proposal_id,
            snapshot_digest_value=expected_digest,
            now=now,
        )
        if not allowed:
            return ServiceResult(ProductivityCode.STATE_MISMATCH)

        if approval.scope is ApprovalScope.ONCE:
            result = self._store.consume_once(approval_id, actor.actor_id, now)
            if not result.success:
                return ServiceResult(ProductivityCode.APPROVAL_EXPIRED_OR_CONSUMED)
            self._registry.complete(proposal_id)
        else:
            # Session, duration and persistent approvals are verified but not
            # tied to a single proposal execution. The caller must cancel the
            # proposal explicitly when it is no longer needed.
            result = self._store.consume(approval_id, actor.actor_id, now)
            if not result.success:
                return ServiceResult(ProductivityCode.APPROVAL_EXPIRED_OR_CONSUMED)

        return ServiceResult(ProductivityCode.OK)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(
        self,
        actor: ActorContext,
        proposal_id: str,
        now: float,
    ) -> ServiceResult:
        """Return the status of a proposal/approval pair."""
        if actor.actor is not Actor.OWNER:
            return ServiceResult(ProductivityCode.UNAUTHORIZED_ACTOR)

        proposal = self._registry.get(proposal_id, actor, now)

        approval = self._store.find_current_for_proposal(
            actor.actor_id, proposal_id, session_id=actor.session_id
        )

        # DURATION/PRECISE_PERSISTENT approvals are only disclosed to the
        # session that currently holds the in-memory proposal. This prevents a
        # different session that shares the same actor_id from observing durable
        # approvals, and it avoids disclosing durable state after restart.
        # ONCE/SESSION approvals carry their own session_id, so they can still
        # be safely disclosed to the matching session even when the in-memory
        # proposal has been consumed or removed.
        if (
            approval is not None
            and approval.scope
            in (
                ApprovalScope.DURATION,
                ApprovalScope.PRECISE_PERSISTENT,
            )
            and proposal is None
        ):
            approval = None

        if approval is None:
            if proposal is not None:
                return ServiceResult(ProductivityCode.OK, {"state": "pending"})
            return ServiceResult(ProductivityCode.PROPOSAL_NOT_FOUND)

        if approval.revoked:
            return ServiceResult(ProductivityCode.OK, {"state": "revoked"})
        if approval.is_expired(now):
            return ServiceResult(ProductivityCode.OK, {"state": "expired"})
        if approval.remaining_uses == 0:
            return ServiceResult(ProductivityCode.OK, {"state": "consumed"})
        return ServiceResult(ProductivityCode.OK, {"state": "confirmed"})

    def close(self) -> None:
        """Deterministically close the underlying store."""
        self._store.close()
