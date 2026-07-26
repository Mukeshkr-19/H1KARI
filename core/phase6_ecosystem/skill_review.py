"""Reviewed skill evolution lifecycle and state machine for Phase 6.

Enforces strict lifecycle progression from proposal through validation, owner review,
install readiness, and revocation without installing or executing code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Optional, Sequence, Tuple

from core.action_policy import Actor, ActorContext, validate_actor_context
from core.phase6_ecosystem.skill_package import (
    PackageValidationOutcome,
    PackageValidationReason,
    PublisherTrust,
    SignatureEvidence,
    SkillInstallPlan,
    SkillPackageCandidate,
    SkillPackageManifest,
    SkillReview,
    SkillRevocation,
    SkillRollbackMetadata,
)


class SkillLifecycleState(StrEnum):
    """Fixed lifecycle states for a skill package evolution."""

    PROPOSED = "proposed"
    VALIDATED = "validated"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    INSTALL_READY = "install_ready"
    INSTALLED_RECORDED = "installed_recorded"
    REVOKED = "revoked"


class SkillEvolutionReason(StrEnum):
    """Fixed, non-attributable reason codes for lifecycle decisions."""

    OK = "ok"
    TEACH_ME_BOUND = "teach_me_bound"
    ASSISTANT_SELF_APPROVAL_DENIED = "assistant_self_approval_denied"
    UNAUTHORIZED_REVIEWER = "unauthorized_reviewer"
    DIGEST_MISMATCH = "digest_mismatch"
    PERMISSIONS_CHANGED = "permissions_changed"
    PACKAGE_REVOKED = "package_revoked"
    REJECTED_RESUBMISSION = "rejected_resubmission"
    MISSING_ROLLBACK = "missing_rollback"
    INVALID_TRANSITION = "invalid_transition"
    POLICY_DENIED = "policy_denied"


@dataclass(frozen=True)
class SkillReviewAuditEvent:
    """Privacy-safe audit record for skill evolution state transitions."""

    event_id: str
    package_id: str
    version: str
    from_state: SkillLifecycleState
    to_state: SkillLifecycleState
    reason: SkillEvolutionReason
    actor_id: str
    timestamp: float

    def __repr__(self) -> str:
        return "SkillReviewAuditEvent()"


class SkillEvolutionStateRecord:
    """State machine container representing a single skill's lifecycle state."""

    def __init__(
        self,
        candidate: SkillPackageCandidate,
        initial_state: SkillLifecycleState = SkillLifecycleState.PROPOSED,
    ):
        self.candidate = candidate
        self.state = initial_state
        self.review: Optional[SkillReview] = None
        self.install_plan: Optional[SkillInstallPlan] = None
        self.audit_log: list[SkillReviewAuditEvent] = []
        self.rejection_digests: set[str] = set()
        self.used_review_ids: set[str] = set()

    def manifest(self) -> SkillPackageManifest:
        return self.candidate.manifest

    def digest(self) -> str:
        return self.candidate.manifest.canonical_digest()

    def record_transition(
        self,
        event_id: str,
        to_state: SkillLifecycleState,
        reason: SkillEvolutionReason,
        actor_id: str,
        timestamp: float,
    ) -> SkillReviewAuditEvent:
        if len(self.audit_log) >= 256:
            raise ValueError("audit_history_exhausted")
        event = SkillReviewAuditEvent(
            event_id=event_id,
            package_id=self.manifest().package_id,
            version=self.manifest().version,
            from_state=self.state,
            to_state=to_state,
            reason=reason,
            actor_id=actor_id,
            timestamp=timestamp,
        )
        self.state = to_state
        self.audit_log.append(event)
        return event

    def __repr__(self) -> str:
        return "SkillEvolutionStateRecord()"


class SkillEvolutionCoordinator:
    """Pure coordinator governing skill evolution lifecycle state transitions."""

    def propose_package(
        self,
        event_id: str,
        candidate: SkillPackageCandidate,
        actor_context: ActorContext,
        now: float,
    ) -> Tuple[SkillEvolutionStateRecord, SkillEvolutionReason]:
        """Propose a new skill package candidate (e.g. from Teach Me or developer).

        Phase 5 Teach Me or assistant proposals stop at PROPOSED.
        """
        valid, _ = validate_actor_context(actor_context)
        if not valid:
            record = SkillEvolutionStateRecord(candidate, SkillLifecycleState.PROPOSED)
            record.record_transition(event_id, SkillLifecycleState.PROPOSED, SkillEvolutionReason.UNAUTHORIZED_REVIEWER, "system", now)
            return record, SkillEvolutionReason.UNAUTHORIZED_REVIEWER

        record = SkillEvolutionStateRecord(candidate, SkillLifecycleState.PROPOSED)
        record.record_transition(event_id, SkillLifecycleState.PROPOSED, SkillEvolutionReason.TEACH_ME_BOUND, actor_context.actor_id, now)
        return record, SkillEvolutionReason.OK

    def validate_package(
        self,
        event_id: str,
        record: SkillEvolutionStateRecord,
        actor_context: ActorContext,
        now: float,
    ) -> SkillEvolutionReason:
        """Validate package candidate manifest and digests."""
        if record.state != SkillLifecycleState.PROPOSED:
            record.record_transition(event_id, record.state, SkillEvolutionReason.INVALID_TRANSITION, actor_context.actor_id, now)
            return SkillEvolutionReason.INVALID_TRANSITION

        outcome, _, _ = record.candidate.validate_candidate()
        if outcome != PackageValidationOutcome.VALID:
            record.record_transition(event_id, SkillLifecycleState.REJECTED, SkillEvolutionReason.POLICY_DENIED, actor_context.actor_id, now)
            return SkillEvolutionReason.POLICY_DENIED

        record.record_transition(event_id, SkillLifecycleState.VALIDATED, SkillEvolutionReason.OK, actor_context.actor_id, now)
        return SkillEvolutionReason.OK

    def submit_for_review(
        self,
        event_id: str,
        record: SkillEvolutionStateRecord,
        actor_context: ActorContext,
        now: float,
    ) -> SkillEvolutionReason:
        """Submit validated package for human owner review."""
        if record.state != SkillLifecycleState.VALIDATED:
            record.record_transition(event_id, record.state, SkillEvolutionReason.INVALID_TRANSITION, actor_context.actor_id, now)
            return SkillEvolutionReason.INVALID_TRANSITION

        # Reject resubmission of previously rejected digest without new candidate
        if record.digest() in record.rejection_digests:
            record.record_transition(event_id, SkillLifecycleState.REJECTED, SkillEvolutionReason.REJECTED_RESUBMISSION, actor_context.actor_id, now)
            return SkillEvolutionReason.REJECTED_RESUBMISSION

        record.record_transition(event_id, SkillLifecycleState.AWAITING_REVIEW, SkillEvolutionReason.OK, actor_context.actor_id, now)
        return SkillEvolutionReason.OK

    def approve_review(
        self,
        event_id: str,
        record: SkillEvolutionStateRecord,
        review: SkillReview,
        actor_context: ActorContext,
        now: float,
    ) -> SkillEvolutionReason:
        """Approve review for a package. Requires authenticated owner context."""
        valid, _ = validate_actor_context(actor_context)
        if not valid or actor_context.actor != Actor.OWNER:
            record.record_transition(event_id, record.state, SkillEvolutionReason.ASSISTANT_SELF_APPROVAL_DENIED, actor_context.actor_id if valid else "unknown", now)
            return SkillEvolutionReason.ASSISTANT_SELF_APPROVAL_DENIED

        if record.state != SkillLifecycleState.AWAITING_REVIEW:
            record.record_transition(event_id, record.state, SkillEvolutionReason.INVALID_TRANSITION, actor_context.actor_id, now)
            return SkillEvolutionReason.INVALID_TRANSITION

        if not review.is_valid(record.digest(), now):
            record.record_transition(event_id, record.state, SkillEvolutionReason.DIGEST_MISMATCH, actor_context.actor_id, now)
            return SkillEvolutionReason.DIGEST_MISMATCH
        if (
            review.reviewer_actor_id != actor_context.actor_id
            or review.reviewer_role != "owner"
            or review.package_id != record.manifest().package_id
            or review.version != record.manifest().version
            or review.publisher_id != record.manifest().publisher_id
        ):
            record.record_transition(event_id, record.state, SkillEvolutionReason.UNAUTHORIZED_REVIEWER, actor_context.actor_id, now)
            return SkillEvolutionReason.UNAUTHORIZED_REVIEWER

        record.review = review
        record.record_transition(event_id, SkillLifecycleState.APPROVED, SkillEvolutionReason.OK, actor_context.actor_id, now)
        return SkillEvolutionReason.OK

    def reject_review(
        self,
        event_id: str,
        record: SkillEvolutionStateRecord,
        actor_context: ActorContext,
        now: float,
    ) -> SkillEvolutionReason:
        """Reject review for a package."""
        valid, _ = validate_actor_context(actor_context)
        if not valid or actor_context.actor != Actor.OWNER:
            record.record_transition(event_id, record.state, SkillEvolutionReason.UNAUTHORIZED_REVIEWER, actor_context.actor_id if valid else "unknown", now)
            return SkillEvolutionReason.UNAUTHORIZED_REVIEWER
        if record.state not in (SkillLifecycleState.AWAITING_REVIEW, SkillLifecycleState.PROPOSED, SkillLifecycleState.VALIDATED):
            record.record_transition(event_id, record.state, SkillEvolutionReason.INVALID_TRANSITION, actor_context.actor_id, now)
            return SkillEvolutionReason.INVALID_TRANSITION

        record.rejection_digests.add(record.digest())
        record.record_transition(event_id, SkillLifecycleState.REJECTED, SkillEvolutionReason.OK, actor_context.actor_id, now)
        return SkillEvolutionReason.OK

    def create_install_plan(
        self,
        event_id: str,
        record: SkillEvolutionStateRecord,
        plan_id: str,
        signature_evidence: SignatureEvidence,
        publisher_trust: PublisherTrust,
        signature_verifier: Callable[[bytes, SignatureEvidence], bool],
        revocations: Sequence[SkillRevocation],
        rollback_metadata: Optional[SkillRollbackMetadata],
        is_replacement: bool,
        actor_context: ActorContext,
        now: float,
    ) -> Tuple[Optional[SkillInstallPlan], SkillEvolutionReason]:
        """Validate all gates and assemble an install plan."""
        valid, _ = validate_actor_context(actor_context)
        if not valid or actor_context.actor != Actor.OWNER:
            record.record_transition(event_id, record.state, SkillEvolutionReason.UNAUTHORIZED_REVIEWER, actor_context.actor_id if valid else "unknown", now)
            return None, SkillEvolutionReason.UNAUTHORIZED_REVIEWER
        if not isinstance(is_replacement, bool):
            return None, SkillEvolutionReason.POLICY_DENIED
        if record.state != SkillLifecycleState.APPROVED or record.review is None:
            record.record_transition(event_id, record.state, SkillEvolutionReason.INVALID_TRANSITION, actor_context.actor_id, now)
            return None, SkillEvolutionReason.INVALID_TRANSITION

        plan = SkillInstallPlan(
            plan_id=plan_id,
            candidate=record.candidate,
            review=record.review,
            signature_evidence=signature_evidence,
            publisher_trust=publisher_trust,
            rollback_metadata=rollback_metadata,
        )

        outcome, reason, _ = plan.validate_plan(
            signature_verifier=signature_verifier,
            revocations=revocations,
            is_replacement=is_replacement,
            now=now,
        )

        if outcome != PackageValidationOutcome.VALID:
            record.record_transition(event_id, SkillLifecycleState.REJECTED, SkillEvolutionReason.POLICY_DENIED, actor_context.actor_id, now)
            return None, SkillEvolutionReason.POLICY_DENIED

        if record.review.review_id in record.used_review_ids:
            record.record_transition(event_id, SkillLifecycleState.REJECTED, SkillEvolutionReason.POLICY_DENIED, actor_context.actor_id, now)
            return None, SkillEvolutionReason.POLICY_DENIED

        record.install_plan = plan
        record.used_review_ids.add(record.review.review_id)
        record.record_transition(event_id, SkillLifecycleState.INSTALL_READY, SkillEvolutionReason.OK, actor_context.actor_id, now)
        return plan, SkillEvolutionReason.OK

    def record_installation(
        self,
        event_id: str,
        record: SkillEvolutionStateRecord,
        actor_context: ActorContext,
        now: float,
    ) -> SkillEvolutionReason:
        """Record installation in state machine audit log (no filesystem mutation)."""
        valid, _ = validate_actor_context(actor_context)
        if not valid or actor_context.actor != Actor.OWNER:
            record.record_transition(event_id, record.state, SkillEvolutionReason.UNAUTHORIZED_REVIEWER, actor_context.actor_id if valid else "unknown", now)
            return SkillEvolutionReason.UNAUTHORIZED_REVIEWER
        if record.state != SkillLifecycleState.INSTALL_READY or record.install_plan is None:
            record.record_transition(event_id, record.state, SkillEvolutionReason.INVALID_TRANSITION, actor_context.actor_id, now)
            return SkillEvolutionReason.INVALID_TRANSITION

        record.record_transition(event_id, SkillLifecycleState.INSTALLED_RECORDED, SkillEvolutionReason.OK, actor_context.actor_id, now)
        return SkillEvolutionReason.OK

    def revoke(
        self,
        event_id: str,
        record: SkillEvolutionStateRecord,
        actor_context: ActorContext,
        now: float,
    ) -> SkillEvolutionReason:
        """Revoke a skill package."""
        valid, _ = validate_actor_context(actor_context)
        if not valid or actor_context.actor != Actor.OWNER:
            record.record_transition(event_id, record.state, SkillEvolutionReason.UNAUTHORIZED_REVIEWER, actor_context.actor_id if valid else "unknown", now)
            return SkillEvolutionReason.UNAUTHORIZED_REVIEWER
        record.record_transition(event_id, SkillLifecycleState.REVOKED, SkillEvolutionReason.PACKAGE_REVOKED, actor_context.actor_id, now)
        return SkillEvolutionReason.OK

    def __repr__(self) -> str:
        return "SkillEvolutionCoordinator()"
