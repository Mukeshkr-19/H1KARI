"""Pure, immutable Phase 5 safety and authority contracts.

All dataclasses are frozen. No I/O, no side effects, no authentication.
Time-dependent evaluation uses injected time. Unknown inputs fail closed.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional, Tuple

from core.action_policy import Actor, ActorContext, validate_actor_context

# --- Canonical limits ---------------------------------------------------------

_MAX_IDENTIFIER_LENGTH = 80
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_ACTOR_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_SAFE_TIMESTAMP = 2**53

# --- Enums --------------------------------------------------------------------


class Phase5Actor(StrEnum):
    """Actor roles for Phase 5 capabilities."""

    OWNER = "owner"
    CHILD = "child"
    TRUSTED_HELPER = "trusted_helper"
    GUEST = "guest"
    SYSTEM = "system"


class Capability(StrEnum):
    """Bounded Phase 5 capabilities."""

    TEACH_ME = "teach_me"
    GUIDE_MY_HANDS = "guide_my_hands"
    CARE = "care"
    CHILD_MODE = "child_mode"
    TRUSTED_HELPER_ACCESS = "trusted_helper_access"


class Outcome(StrEnum):
    """Policy decision outcomes."""

    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"
    EXPIRED = "expired"
    REVOKED = "revoked"
    OUT_OF_SCOPE = "out_of_scope"
    AUTHENTICATION_REQUIRED = "authentication_required"


class RiskLevel(StrEnum):
    """Risk classification for capabilities."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    PROHIBITED = "prohibited"


class DecisionReason(StrEnum):
    """Explicit reason codes for every decision."""

    # Allow reasons
    OWNER_LOW_RISK = "owner_low_risk"
    OWNER_APPROVED = "owner_approved"
    OWNER_APPROVAL_REQUIRED = "owner_approval_required"
    HELPER_GRANT_VALID = "helper_grant_valid"

    # Deny reasons
    DEFAULT_DENY = "default_deny"
    UNKNOWN_ACTOR = "unknown_actor"
    GUEST_DENIED = "guest_denied"
    CHILD_CANNOT_WEAKEN = "child_cannot_weaken"
    CHILD_PURCHASE_BLOCKED = "child_purchase_blocked"
    CHILD_COMMUNICATION_BLOCKED = "child_communication_blocked"
    CHILD_OWNER_MEMORY_BLOCKED = "child_owner_memory_blocked"
    CHILD_DANGEROUS_BLOCKED = "child_dangerous_blocked"
    CHILD_AUDIT_BYPASS_BLOCKED = "child_audit_bypass_blocked"
    CHILD_HELPER_GRANT_BLOCKED = "child_helper_grant_blocked"
    HELPER_NO_GRANT = "helper_no_grant"
    HELPER_GRANT_EXPIRED = "helper_grant_expired"
    HELPER_GRANT_REVOKED = "helper_grant_revoked"
    HELPER_SCOPE_MISMATCH = "helper_scope_mismatch"
    HELPER_DELEGATION_BLOCKED = "helper_delegation_blocked"
    HELPER_SCOPE_EXPANSION_BLOCKED = "helper_scope_expansion_blocked"
    HELPER_UNRELATED_MEMORY_BLOCKED = "helper_unrelated_memory_blocked"
    HELPER_SILENT_RENEWAL_BLOCKED = "helper_silent_renewal_blocked"
    TEACH_ME_PROPOSAL_ONLY = "teach_me_proposal_only"
    TEACH_ME_NO_DIRECT_INSTALL = "teach_me_no_direct_install"
    TEACH_ME_ASSISTANT_AUTHORITY_DENIED = "teach_me_assistant_authority_denied"
    GUIDE_HANDS_APPROVAL_REQUIRED = "guide_hands_approval_required"
    GUIDE_HANDS_UNCERTAINTY = "guide_hands_uncertainty"
    GUIDE_HANDS_NO_FALSE_COMPLETION = "guide_hands_no_false_completion"
    CARE_NO_DIAGNOSIS = "care_no_diagnosis"
    CARE_EMERGENCY_HANDLING = "care_emergency_handling"
    CARE_NO_FALSE_CONTACT = "care_no_false_contact"
    APPROVAL_BYPASS_BLOCKED = "approval_bypass_blocked"
    AUDIT_BYPASS_BLOCKED = "audit_bypass_blocked"
    BRAIN_WRITE_DENIED = "brain_write_denied"
    UNKNOWN_CAPABILITY = "unknown_capability"
    INVALID_TIME = "invalid_time"
    INVALID_INPUT = "invalid_input"


# --- Validators ---------------------------------------------------------------


def _validate_identifier(value: Optional[str], field: str) -> None:
    if value is None:
        raise ValueError(f"{field} is required")
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"invalid {field}")


def _validate_actor_identifier(value: Optional[str], field: str) -> None:
    if value is None:
        raise ValueError(f"{field} is required")
    if not isinstance(value, str) or not _ACTOR_IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"invalid {field}")


def _validate_finite_timestamp(value: float, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    if isinstance(value, int) and abs(value) > _MAX_SAFE_TIMESTAMP:
        raise ValueError(f"{field} is too large")
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise ValueError(f"{field} must be finite")


def _validate_hex_digest(value: str, field: str) -> None:
    if not isinstance(value, str) or not _HEX_DIGEST_RE.fullmatch(value):
        raise ValueError(f"{field} must be 64 lowercase hex characters")


def _validate_capability(value: object) -> Capability:
    if not isinstance(value, Capability):
        raise ValueError("invalid capability")
    return value


def _validate_actor(value: object) -> Phase5Actor:
    if not isinstance(value, Phase5Actor):
        raise ValueError("invalid actor")
    return value


# --- Core Contracts -----------------------------------------------------------


@dataclass(frozen=True)
class Phase5ActorContext:
    """Server-derived identity for Phase 5 evaluation.

    Authentication is caller-supplied. This package performs no authentication.
    """

    actor_id: str
    actor: Phase5Actor
    session_id: str
    source: str = "phase5"

    def __post_init__(self) -> None:
        _validate_actor_identifier(self.actor_id, "actor_id")
        _validate_actor(self.actor)
        _validate_actor_identifier(self.session_id, "session_id")
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("source is required")

    def to_action_policy_context(self) -> ActorContext:
        """Convert to core action_policy ActorContext for audit integration."""
        core_actor_map = {
            Phase5Actor.OWNER: Actor.OWNER,
            Phase5Actor.CHILD: Actor.GUEST,
            Phase5Actor.TRUSTED_HELPER: Actor.GUEST,
            Phase5Actor.GUEST: Actor.GUEST,
            Phase5Actor.SYSTEM: Actor.SYSTEM,
        }
        return ActorContext(
            actor_id=self.actor_id,
            actor=core_actor_map[self.actor],
            session_id=self.session_id,
            source=self.source,
        )

    def __repr__(self) -> str:
        return f"Phase5ActorContext(actor={self.actor.value!r})"


@dataclass(frozen=True)
class ScopeConstraint:
    """Immutable scope constraint for a grant or capability."""

    capability: Capability
    data_subject: Optional[str] = None
    resource_pattern: Optional[str] = None
    max_duration_seconds: Optional[int] = None
    allowed_actions: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_capability(self.capability)
        if self.data_subject is not None:
            if not isinstance(self.data_subject, str) or not self.data_subject:
                raise ValueError("data_subject must be non-empty string")
        if self.resource_pattern is not None:
            if not isinstance(self.resource_pattern, str) or not self.resource_pattern:
                raise ValueError("resource_pattern must be non-empty string")
        if self.max_duration_seconds is not None:
            if not isinstance(self.max_duration_seconds, int) or isinstance(self.max_duration_seconds, bool):
                raise ValueError("max_duration_seconds must be integer")
            if self.max_duration_seconds <= 0:
                raise ValueError("max_duration_seconds must be positive")
        if not isinstance(self.allowed_actions, tuple):
            raise ValueError("allowed_actions must be tuple")
        for action in self.allowed_actions:
            if not isinstance(action, str) or not action:
                raise ValueError("allowed_actions must be non-empty strings")

    def matches(self, capability: Capability, data_subject: Optional[str] = None, resource: Optional[str] = None, action: Optional[str] = None) -> bool:
        """Check if this constraint matches the given request."""
        if self.capability != capability:
            return False
        if self.data_subject is not None and self.data_subject != data_subject:
            return False
        if self.resource_pattern is not None:
            if resource is None or not resource.startswith(
                self.resource_pattern.rstrip("*")
            ):
                return False
        if self.allowed_actions:
            if action is None or action not in self.allowed_actions:
                return False
        return True

    def __repr__(self) -> str:
        return f"ScopeConstraint(capability={self.capability.value!r})"


@dataclass(frozen=True)
class CapabilityGrant:
    """Immutable, scoped, expiring grant for a trusted helper.

    - Expiration required (no permanent grants)
    - Revocation supported
    - No delegation
    - No scope expansion
    """

    grant_id: str
    helper_actor_id: str
    owner_actor_id: str
    capability: Capability
    scope: ScopeConstraint
    issued_at: float
    expires_at: float
    revoked: bool = False
    revoked_at: Optional[float] = None

    def __post_init__(self) -> None:
        _validate_identifier(self.grant_id, "grant_id")
        _validate_actor_identifier(self.helper_actor_id, "helper_actor_id")
        _validate_actor_identifier(self.owner_actor_id, "owner_actor_id")
        _validate_capability(self.capability)
        if not isinstance(self.scope, ScopeConstraint):
            raise ValueError("scope must be ScopeConstraint")
        _validate_finite_timestamp(self.issued_at, "issued_at")
        if self.expires_at is None:
            raise ValueError("expiration required")
        _validate_finite_timestamp(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be greater than issued_at")
        if not isinstance(self.revoked, bool):
            raise ValueError("revoked must be boolean")
        if self.revoked_at is not None:
            _validate_finite_timestamp(self.revoked_at, "revoked_at")
            if self.revoked_at < self.issued_at:
                raise ValueError("revoked_at must not precede issued_at")

    def is_expired(self, now: float) -> bool:
        _validate_finite_timestamp(now, "now")
        return now >= self.expires_at

    def is_revoked(self) -> bool:
        return self.revoked

    def is_valid(self, now: float) -> bool:
        return not self.is_revoked() and not self.is_expired(now)

    def revoke(self, now: float) -> "CapabilityGrant":
        _validate_finite_timestamp(now, "now")
        return CapabilityGrant(
            grant_id=self.grant_id,
            helper_actor_id=self.helper_actor_id,
            owner_actor_id=self.owner_actor_id,
            capability=self.capability,
            scope=self.scope,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            revoked=True,
            revoked_at=now,
        )

    def __repr__(self) -> str:
        return f"CapabilityGrant(capability={self.capability.value!r}, valid={not self.revoked})"


@dataclass(frozen=True)
class ConsentRecord:
    """Immutable record of explicit owner consent for a capability.

    Consent is distinct from grants: consent is the owner's affirmative
    authorization; grants are the technical enforcement mechanism.
    """

    consent_id: str
    owner_actor_id: str
    capability: Capability
    scope: ScopeConstraint
    granted_at: float
    expires_at: Optional[float] = None
    revoked: bool = False
    revoked_at: Optional[float] = None

    def __post_init__(self) -> None:
        _validate_identifier(self.consent_id, "consent_id")
        _validate_actor_identifier(self.owner_actor_id, "owner_actor_id")
        _validate_capability(self.capability)
        if not isinstance(self.scope, ScopeConstraint):
            raise ValueError("scope must be ScopeConstraint")
        _validate_finite_timestamp(self.granted_at, "granted_at")
        if self.expires_at is not None:
            _validate_finite_timestamp(self.expires_at, "expires_at")
            if self.expires_at <= self.granted_at:
                raise ValueError("expires_at must be greater than granted_at")
        if not isinstance(self.revoked, bool):
            raise ValueError("revoked must be boolean")
        if self.revoked_at is not None:
            _validate_finite_timestamp(self.revoked_at, "revoked_at")

    def is_expired(self, now: float) -> bool:
        _validate_finite_timestamp(now, "now")
        if self.expires_at is None:
            return False
        return now >= self.expires_at

    def is_valid(self, now: float) -> bool:
        return not self.revoked and not self.is_expired(now)

    def __repr__(self) -> str:
        return f"ConsentRecord(capability={self.capability.value!r}, valid={not self.revoked})"


@dataclass(frozen=True)
class Phase5Request:
    """Immutable request for a Phase 5 capability evaluation.

    Authentication (actor identity) is caller-supplied. This package
    performs no authentication.
    """

    request_id: str
    actor: Phase5ActorContext
    capability: Capability
    data_subject: Optional[str] = None
    resource: Optional[str] = None
    action: Optional[str] = None
    metadata: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_identifier(self.request_id, "request_id")
        if not isinstance(self.actor, Phase5ActorContext):
            raise ValueError("actor must be Phase5ActorContext")
        _validate_capability(self.capability)
        if self.data_subject is not None:
            if not isinstance(self.data_subject, str) or not self.data_subject:
                raise ValueError("data_subject must be non-empty string")
        if self.resource is not None:
            if not isinstance(self.resource, str) or not self.resource:
                raise ValueError("resource must be non-empty string")
        if self.action is not None:
            if not isinstance(self.action, str) or not self.action:
                raise ValueError("action must be non-empty string")
        if not isinstance(self.metadata, tuple):
            raise ValueError("metadata must be tuple")

    def __repr__(self) -> str:
        return f"Phase5Request(capability={self.capability.value!r}, actor={self.actor.actor.value!r})"


@dataclass(frozen=True)
class Phase5Decision:
    """Immutable policy decision with explicit reason and provenance."""

    request_id: str
    capability: Capability
    actor_id: str
    actor: Phase5Actor
    outcome: Outcome
    reason: DecisionReason
    granted_at: float
    expires_at: Optional[float] = None
    grant_id: Optional[str] = None
    audit_id: Optional[str] = None

    def __post_init__(self) -> None:
        _validate_identifier(self.request_id, "request_id")
        _validate_actor_identifier(self.actor_id, "actor_id")
        _validate_actor(self.actor)
        _validate_capability(self.capability)
        if not isinstance(self.outcome, Outcome):
            raise ValueError("invalid outcome")
        if not isinstance(self.reason, DecisionReason):
            raise ValueError("invalid reason")
        _validate_finite_timestamp(self.granted_at, "granted_at")
        if self.expires_at is not None:
            _validate_finite_timestamp(self.expires_at, "expires_at")
        if self.grant_id is not None:
            _validate_identifier(self.grant_id, "grant_id")
        if self.audit_id is not None:
            _validate_identifier(self.audit_id, "audit_id")

    def is_terminal_denial(self) -> bool:
        return self.outcome in {
            Outcome.DENY,
            Outcome.EXPIRED,
            Outcome.REVOKED,
            Outcome.OUT_OF_SCOPE,
        }

    def __repr__(self) -> str:
        return f"Phase5Decision(outcome={self.outcome.value!r}, reason={self.reason.value!r})"


# --- Policy Evaluation Helpers ------------------------------------------------


CAPABILITY_RISK: dict[Capability, RiskLevel] = {
    Capability.TEACH_ME: RiskLevel.LOW,
    Capability.GUIDE_MY_HANDS: RiskLevel.MEDIUM,
    Capability.CARE: RiskLevel.HIGH,
    Capability.CHILD_MODE: RiskLevel.LOW,
    Capability.TRUSTED_HELPER_ACCESS: RiskLevel.MEDIUM,
}

CAPABILITY_REQUIRES_APPROVAL: frozenset[Capability] = frozenset({
    Capability.GUIDE_MY_HANDS,
    Capability.CARE,
    Capability.TRUSTED_HELPER_ACCESS,
})

ACTOR_PROHIBITED: dict[Phase5Actor, frozenset[Capability]] = {
    Phase5Actor.GUEST: frozenset(Capability),
    Phase5Actor.SYSTEM: frozenset(Capability),
    Phase5Actor.CHILD: frozenset({
        Capability.TRUSTED_HELPER_ACCESS,
        Capability.CARE,
    }),
}

CHILD_BLOCKED_ACTIONS: frozenset[str] = frozenset({
    "purchase",
    "account_change",
    "external_communication",
    "send_message",
    "dangerous_instruction",
    "audit_disable",
    "approval_disable",
    "helper_grant",
})


def evaluate_phase5_request(
    request: Phase5Request,
    *,
    grants: Tuple[CapabilityGrant, ...] = (),
    consents: Tuple[ConsentRecord, ...] = (),
    now: float,
) -> Phase5Decision:
    """Evaluate a Phase 5 capability request.

    This is the single entry point for Phase 5 policy decisions.
    All decisions are deterministic, fail-closed, and auditable.

    Args:
        request: The capability request to evaluate.
        grants: Active capability grants (for trusted helpers).
        consents: Active owner consents.
        now: Injected timestamp for time-dependent evaluation.

    Returns:
        Phase5Decision with outcome, explicit reason, and provenance.
    """
    _validate_finite_timestamp(now, "now")

    if not isinstance(request, Phase5Request):
        return Phase5Decision(
            request_id="invalid",
            capability=Capability.TEACH_ME,
            actor_id="invalid",
            actor=Phase5Actor.GUEST,
            outcome=Outcome.DENY,
            reason=DecisionReason.INVALID_INPUT,
            granted_at=now,
        )

    actor = request.actor
    capability = request.capability

    valid_consent = next(
        (
            consent
            for consent in sorted(consents, key=lambda item: item.consent_id)
            if consent.owner_actor_id == actor.actor_id
            and consent.capability is capability
            and consent.is_valid(now)
            and consent.scope.matches(
                capability,
                request.data_subject,
                request.resource,
                request.action,
            )
        ),
        None,
    )

    # 1. Unknown capability
    if capability not in CAPABILITY_RISK:
        return Phase5Decision(
            request_id=request.request_id,
            capability=capability,
            actor_id=actor.actor_id,
            actor=actor.actor,
            outcome=Outcome.DENY,
            reason=DecisionReason.UNKNOWN_CAPABILITY,
            granted_at=now,
        )

    # 2. Unknown/autonomous actors have no authority
    if actor.actor in {Phase5Actor.SYSTEM}:
        return Phase5Decision(
            request_id=request.request_id,
            capability=capability,
            actor_id=actor.actor_id,
            actor=actor.actor,
            outcome=Outcome.DENY,
            reason=DecisionReason.UNKNOWN_ACTOR,
            granted_at=now,
        )

    # 3. Guest receives no owner/helper authority
    if actor.actor is Phase5Actor.GUEST:
        return Phase5Decision(
            request_id=request.request_id,
            capability=capability,
            actor_id=actor.actor_id,
            actor=actor.actor,
            outcome=Outcome.DENY,
            reason=DecisionReason.GUEST_DENIED,
            granted_at=now,
        )

    # 4. Child mode invariants - cannot weaken policy
    if actor.actor is Phase5Actor.CHILD:
        if capability in {Capability.TEACH_ME, Capability.GUIDE_MY_HANDS, Capability.CARE}:
            if request.data_subject and request.data_subject != "child":
                return Phase5Decision(
                    request_id=request.request_id,
                    capability=capability,
                    actor_id=actor.actor_id,
                    actor=actor.actor,
                    outcome=Outcome.DENY,
                    reason=DecisionReason.CHILD_OWNER_MEMORY_BLOCKED,
                    granted_at=now,
                )
        if request.action:
            action_lower = request.action.lower()
            if "purchase" in action_lower:
                return Phase5Decision(
                    request_id=request.request_id,
                    capability=capability,
                    actor_id=actor.actor_id,
                    actor=actor.actor,
                    outcome=Outcome.DENY,
                    reason=DecisionReason.CHILD_PURCHASE_BLOCKED,
                    granted_at=now,
                )
            if any(c in action_lower for c in ("send_message", "communicat", "external_communication")):
                return Phase5Decision(
                    request_id=request.request_id,
                    capability=capability,
                    actor_id=actor.actor_id,
                    actor=actor.actor,
                    outcome=Outcome.DENY,
                    reason=DecisionReason.CHILD_COMMUNICATION_BLOCKED,
                    granted_at=now,
                )
            if "audit" in action_lower or "approval" in action_lower:
                return Phase5Decision(
                    request_id=request.request_id,
                    capability=capability,
                    actor_id=actor.actor_id,
                    actor=actor.actor,
                    outcome=Outcome.DENY,
                    reason=DecisionReason.CHILD_AUDIT_BYPASS_BLOCKED,
                    granted_at=now,
                )
        if capability is Capability.TRUSTED_HELPER_ACCESS:
            return Phase5Decision(
                request_id=request.request_id,
                capability=capability,
                actor_id=actor.actor_id,
                actor=actor.actor,
                outcome=Outcome.DENY,
                reason=DecisionReason.CHILD_HELPER_GRANT_BLOCKED,
                granted_at=now,
            )
        if request.metadata and any("weaken" in m.lower() or "bypass" in m.lower() for m in request.metadata):
            return Phase5Decision(
                request_id=request.request_id,
                capability=capability,
                actor_id=actor.actor_id,
                actor=actor.actor,
                outcome=Outcome.DENY,
                reason=DecisionReason.CHILD_CANNOT_WEAKEN,
                granted_at=now,
            )
        if request.action and any(d in request.action.lower() for d in ("dangerous", "harm", "weapon", "illegal")):
            return Phase5Decision(
                request_id=request.request_id,
                capability=capability,
                actor_id=actor.actor_id,
                actor=actor.actor,
                outcome=Outcome.DENY,
                reason=DecisionReason.CHILD_DANGEROUS_BLOCKED,
                granted_at=now,
            )

    # 5. Trusted helper requires valid grant
    if actor.actor is Phase5Actor.TRUSTED_HELPER:
        matching_grants = [g for g in grants if g.capability == capability and g.helper_actor_id == actor.actor_id]
        if not matching_grants:
            return Phase5Decision(
                request_id=request.request_id,
                capability=capability,
                actor_id=actor.actor_id,
                actor=actor.actor,
                outcome=Outcome.DENY,
                reason=DecisionReason.HELPER_NO_GRANT,
                granted_at=now,
            )
        valid_grants = sorted(
            (grant for grant in matching_grants if grant.is_valid(now)),
            key=lambda grant: (grant.expires_at, grant.grant_id),
        )
        if not valid_grants:
            for grant in matching_grants:
                if grant.is_revoked():
                    return Phase5Decision(
                        request_id=request.request_id,
                        capability=capability,
                        actor_id=actor.actor_id,
                        actor=actor.actor,
                        outcome=Outcome.REVOKED,
                        reason=DecisionReason.HELPER_GRANT_REVOKED,
                        granted_at=now,
                        grant_id=grant.grant_id,
                    )
                if grant.is_expired(now):
                    return Phase5Decision(
                        request_id=request.request_id,
                        capability=capability,
                        actor_id=actor.actor_id,
                        actor=actor.actor,
                        outcome=Outcome.EXPIRED,
                        reason=DecisionReason.HELPER_GRANT_EXPIRED,
                        granted_at=now,
                        grant_id=grant.grant_id,
                    )
            return Phase5Decision(
                request_id=request.request_id,
                capability=capability,
                actor_id=actor.actor_id,
                actor=actor.actor,
                outcome=Outcome.DENY,
                reason=DecisionReason.HELPER_SCOPE_MISMATCH,
                granted_at=now,
            )
        # Helper cannot access unrelated owner memory, regardless of broad scope.
        privileged_subjects = {"owner"}
        if request.data_subject in privileged_subjects and not any(
            grant.scope.data_subject == request.data_subject for grant in valid_grants
        ):
            return Phase5Decision(
                request_id=request.request_id,
                capability=capability,
                actor_id=actor.actor_id,
                actor=actor.actor,
                outcome=Outcome.DENY,
                reason=DecisionReason.HELPER_UNRELATED_MEMORY_BLOCKED,
                granted_at=now,
                grant_id=valid_grants[0].grant_id,
            )
        valid_grant = next(
            (
                grant
                for grant in valid_grants
                if grant.scope.matches(
                    capability,
                    request.data_subject,
                    request.resource,
                    request.action,
                )
            ),
            None,
        )
        if valid_grant is None:
            return Phase5Decision(
                request_id=request.request_id,
                capability=capability,
                actor_id=actor.actor_id,
                actor=actor.actor,
                outcome=Outcome.DENY,
                reason=DecisionReason.HELPER_SCOPE_MISMATCH,
                granted_at=now,
            )
        # Helper cannot delegate
        if request.metadata and any("delegat" in m.lower() for m in request.metadata):
            return Phase5Decision(
                request_id=request.request_id,
                capability=capability,
                actor_id=actor.actor_id,
                actor=actor.actor,
                outcome=Outcome.DENY,
                reason=DecisionReason.HELPER_DELEGATION_BLOCKED,
                granted_at=now,
                grant_id=valid_grant.grant_id,
            )
        # Helper cannot expand scope
        if request.metadata and any("expand" in m.lower() or "escalat" in m.lower() for m in request.metadata):
            return Phase5Decision(
                request_id=request.request_id,
                capability=capability,
                actor_id=actor.actor_id,
                actor=actor.actor,
                outcome=Outcome.DENY,
                reason=DecisionReason.HELPER_SCOPE_EXPANSION_BLOCKED,
                granted_at=now,
                grant_id=valid_grant.grant_id,
            )
        # Helper grant cannot be silently renewed
        if request.metadata and any("renew" in m.lower() for m in request.metadata):
            return Phase5Decision(
                request_id=request.request_id,
                capability=capability,
                actor_id=actor.actor_id,
                actor=actor.actor,
                outcome=Outcome.DENY,
                reason=DecisionReason.HELPER_SILENT_RENEWAL_BLOCKED,
                granted_at=now,
                grant_id=valid_grant.grant_id,
            )
        return Phase5Decision(
            request_id=request.request_id,
            capability=capability,
            actor_id=actor.actor_id,
            actor=actor.actor,
            outcome=Outcome.ALLOW,
            reason=DecisionReason.HELPER_GRANT_VALID,
            granted_at=now,
            grant_id=valid_grant.grant_id,
            expires_at=valid_grant.expires_at,
        )

    # 6. Owner evaluation
    if actor.actor is Phase5Actor.OWNER:
        risk = CAPABILITY_RISK[capability]

        # Low risk: allow (with Teach Me specific checks)
        if risk is RiskLevel.LOW:
            if capability == Capability.TEACH_ME:
                if request.action and any(i in request.action.lower() for i in ("install", "deploy", "activate", "publish")):
                    return Phase5Decision(
                        request_id=request.request_id,
                        capability=capability,
                        actor_id=actor.actor_id,
                        actor=actor.actor,
                        outcome=Outcome.DENY,
                        reason=DecisionReason.TEACH_ME_NO_DIRECT_INSTALL,
                        granted_at=now,
                    )
                if request.metadata and any("assistant" in m.lower() or "generated" in m.lower() for m in request.metadata):
                    return Phase5Decision(
                        request_id=request.request_id,
                        capability=capability,
                        actor_id=actor.actor_id,
                        actor=actor.actor,
                        outcome=Outcome.ALLOW,
                        reason=DecisionReason.TEACH_ME_ASSISTANT_AUTHORITY_DENIED,
                        granted_at=now,
                    )
                return Phase5Decision(
                    request_id=request.request_id,
                    capability=capability,
                    actor_id=actor.actor_id,
                    actor=actor.actor,
                    outcome=Outcome.ALLOW,
                    reason=DecisionReason.TEACH_ME_PROPOSAL_ONLY,
                    granted_at=now,
                )

            if capability == Capability.CHILD_MODE:
                return Phase5Decision(
                    request_id=request.request_id,
                    capability=capability,
                    actor_id=actor.actor_id,
                    actor=actor.actor,
                    outcome=Outcome.ALLOW,
                    reason=DecisionReason.OWNER_LOW_RISK,
                    granted_at=now,
                )

            return Phase5Decision(
                request_id=request.request_id,
                capability=capability,
                actor_id=actor.actor_id,
                actor=actor.actor,
                outcome=Outcome.ALLOW,
                reason=DecisionReason.OWNER_LOW_RISK,
                granted_at=now,
            )

        # MEDIUM risk: specific capability checks FIRST
        if risk is RiskLevel.MEDIUM:
            if capability == Capability.GUIDE_MY_HANDS:
                # Consequential step requires approval
                if request.action and any(c in request.action.lower() for c in ("execute", "perform", "apply", "confirm")):
                    if valid_consent is not None:
                        return Phase5Decision(
                            request_id=request.request_id,
                            capability=capability,
                            actor_id=actor.actor_id,
                            actor=actor.actor,
                            outcome=Outcome.ALLOW,
                            reason=DecisionReason.OWNER_APPROVED,
                            granted_at=now,
                            expires_at=valid_consent.expires_at,
                        )
                    return Phase5Decision(
                        request_id=request.request_id,
                        capability=capability,
                        actor_id=actor.actor_id,
                        actor=actor.actor,
                        outcome=Outcome.REQUIRE_APPROVAL,
                        reason=DecisionReason.GUIDE_HANDS_APPROVAL_REQUIRED,
                        granted_at=now,
                    )
                # Uncertainty is explicit
                if request.metadata and any("uncertain" in m.lower() or "unsure" in m.lower() for m in request.metadata):
                    return Phase5Decision(
                        request_id=request.request_id,
                        capability=capability,
                        actor_id=actor.actor_id,
                        actor=actor.actor,
                        outcome=Outcome.ALLOW,
                        reason=DecisionReason.GUIDE_HANDS_UNCERTAINTY,
                        granted_at=now,
                    )
                # No false completion claims
                if request.metadata and any("complete" in m.lower() or "done" in m.lower() for m in request.metadata):
                    if not request.metadata or not any("evidence" in m.lower() or "confirmed" in m.lower() for m in request.metadata):
                        return Phase5Decision(
                            request_id=request.request_id,
                            capability=capability,
                            actor_id=actor.actor_id,
                            actor=actor.actor,
                            outcome=Outcome.DENY,
                            reason=DecisionReason.GUIDE_HANDS_NO_FALSE_COMPLETION,
                            granted_at=now,
                        )
                return Phase5Decision(
                    request_id=request.request_id,
                    capability=capability,
                    actor_id=actor.actor_id,
                    actor=actor.actor,
                    outcome=Outcome.ALLOW,
                    reason=DecisionReason.OWNER_LOW_RISK,
                    granted_at=now,
                )

            if capability == Capability.TRUSTED_HELPER_ACCESS:
                return Phase5Decision(
                    request_id=request.request_id,
                    capability=capability,
                    actor_id=actor.actor_id,
                    actor=actor.actor,
                    outcome=Outcome.REQUIRE_APPROVAL,
                    reason=DecisionReason.OWNER_APPROVAL_REQUIRED,
                    granted_at=now,
                )

        # High/Prohibited risk: specific capability checks FIRST
        if risk in {RiskLevel.HIGH, RiskLevel.PROHIBITED}:
            if capability == Capability.CARE:
                # No medical diagnosis
                if request.action and any(d in request.action.lower() for d in ("diagnos", "prescrib", "treat", "medical")):
                    return Phase5Decision(
                        request_id=request.request_id,
                        capability=capability,
                        actor_id=actor.actor_id,
                        actor=actor.actor,
                        outcome=Outcome.DENY,
                        reason=DecisionReason.CARE_NO_DIAGNOSIS,
                        granted_at=now,
                    )
                # No false external contact claims
                has_contact_claim = request.metadata and any("contacted" in m.lower() or "called" in m.lower() or "notified" in m.lower() for m in request.metadata)
                has_evidence = request.metadata and any("evidence" in m.lower() or "confirmed" in m.lower() for m in request.metadata)
                if has_contact_claim and not has_evidence:
                    return Phase5Decision(
                        request_id=request.request_id,
                        capability=capability,
                        actor_id=actor.actor_id,
                        actor=actor.actor,
                        outcome=Outcome.DENY,
                        reason=DecisionReason.CARE_NO_FALSE_CONTACT,
                        granted_at=now,
                    )
                # CARE always requires approval (HIGH risk capability)
                if valid_consent is not None:
                    return Phase5Decision(
                        request_id=request.request_id,
                        capability=capability,
                        actor_id=actor.actor_id,
                        actor=actor.actor,
                        outcome=Outcome.ALLOW,
                        reason=DecisionReason.OWNER_APPROVED,
                        granted_at=now,
                        expires_at=valid_consent.expires_at,
                    )
                return Phase5Decision(
                    request_id=request.request_id,
                    capability=capability,
                    actor_id=actor.actor_id,
                    actor=actor.actor,
                    outcome=Outcome.REQUIRE_APPROVAL,
                    reason=DecisionReason.OWNER_APPROVAL_REQUIRED,
                    granted_at=now,
                )

            if capability == Capability.TRUSTED_HELPER_ACCESS:
                return Phase5Decision(
                    request_id=request.request_id,
                    capability=capability,
                    actor_id=actor.actor_id,
                    actor=actor.actor,
                    outcome=Outcome.REQUIRE_APPROVAL,
                    reason=DecisionReason.OWNER_APPROVAL_REQUIRED,
                    granted_at=now,
                )

    # Default deny
    return Phase5Decision(
        request_id=request.request_id,
        capability=capability,
        actor_id=actor.actor_id,
        actor=actor.actor,
        outcome=Outcome.DENY,
        reason=DecisionReason.DEFAULT_DENY,
        granted_at=now,
    )


# --- Safety Invariant Checks --------------------------------------------------


def check_approval_bypass(request: Phase5Request, decision: Phase5Decision) -> bool:
    """Verify that existing action approval cannot be bypassed.

    Returns True if the decision respects approval boundaries.
    """
    # REQUIRE_APPROVAL is the correct outcome for capabilities requiring approval
    # Only block if ALLOW is returned for a capability that requires approval
    if (
        decision.outcome == Outcome.ALLOW
        and request.capability in CAPABILITY_REQUIRES_APPROVAL
        and decision.reason is not DecisionReason.OWNER_APPROVED
    ):
        return False
    return True


def check_audit_bypass(decision: Phase5Decision) -> bool:
    """Verify that audit cannot be bypassed.

    Every Phase 5 decision must be auditable.
    """
    return bool(decision.audit_id)


def check_brain_write_denied(request: Phase5Request, decision: Phase5Decision) -> bool:
    """Verify that Phase 5 never directly mutates accepted Brain v2 memory.

    Returns True if the decision respects the Brain write boundary.
    """
    # Only applies to OWNER actors - helpers use grants which are separately validated
    if request.actor.actor is Phase5Actor.OWNER and request.capability == Capability.TEACH_ME and decision.outcome == Outcome.ALLOW:
        return decision.reason in {DecisionReason.TEACH_ME_PROPOSAL_ONLY, DecisionReason.TEACH_ME_ASSISTANT_AUTHORITY_DENIED}
    return True


__all__ = [
    "Phase5Actor",
    "Capability",
    "Outcome",
    "RiskLevel",
    "DecisionReason",
    "Phase5ActorContext",
    "ScopeConstraint",
    "CapabilityGrant",
    "ConsentRecord",
    "Phase5Request",
    "Phase5Decision",
    "CAPABILITY_RISK",
    "CAPABILITY_REQUIRES_APPROVAL",
    "ACTOR_PROHIBITED",
    "CHILD_BLOCKED_ACTIONS",
    "evaluate_phase5_request",
    "check_approval_bypass",
    "check_audit_bypass",
    "check_brain_write_denied",
]
