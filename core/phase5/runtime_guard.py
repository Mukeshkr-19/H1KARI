"""Phase 5 runtime authorization guard.

The runtime guard composes the existing ``Phase5PolicyService`` with session
lifecycle and child-mode checks. It authorizes or rejects runtime requests; it
does not execute any action.

All decisions are deterministic and caller-supplied (actor identity, session
snapshot, injected time). No wall-clock reads, no I/O beyond the injected
audit/grant stores, and no external actions occur in this module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional, Tuple

from core.action_audit import opaque_resource_reference
from core.action_policy import Actor, ActorContext, PolicyOutcome, validate_actor_context
from core.phase5.child_mode import (
    ChildActionDescriptor,
    ChildModeDecision,
    ChildModeDecisionReason,
    classify_child_action,
)
from core.phase5.contracts import (
    Capability,
    DecisionReason,
    Outcome,
    Phase5Actor,
    Phase5ActorContext,
    Phase5Decision,
    Phase5Request,
    _validate_actor_identifier,
    _validate_finite_timestamp,
    _validate_identifier,
)
from core.phase5.policy import Phase5AuthorizationRequest, Phase5PolicyService
from core.phase5.session_lifecycle import (
    AccessSession,
    AuthoritySource,
    SessionState,
    SessionType,
    default_session_policy,
    SessionPolicy,
)


class RuntimeDecisionReason(StrEnum):
    """Stable, machine-readable reasons for runtime guard decisions."""

    # Success / policy reasons
    OWNER_ALLOWED = "owner_allowed"
    POLICY_ALLOW = "policy_allow"
    POLICY_APPROVAL_REQUIRED = "policy_approval_required"

    # Input / structural
    INVALID_INPUT = "invalid_input"
    INVALID_TIME = "invalid_time"
    INVALID_ACTOR = "invalid_actor"

    # Session-level hard denials
    SESSION_REQUIRED = "session_required"
    SESSION_EXPIRED = "session_expired"
    SESSION_REVOKED = "session_revoked"
    SESSION_LOCKED = "session_locked"
    SESSION_CLOSED = "session_closed"
    SESSION_ACTOR_MISMATCH = "session_actor_mismatch"
    SESSION_OWNER_MISMATCH = "session_owner_mismatch"
    SESSION_AUTHORITY_MISMATCH = "session_authority_mismatch"
    SESSION_POLICY_VIOLATION = "session_policy_violation"
    CAPABILITY_NOT_IN_SESSION = "capability_not_in_session"

    # Child-mode
    CHILD_HARD_DENY = "child_hard_deny"
    CHILD_AMBIGUOUS_REQUIRES_APPROVAL = "child_ambiguous_requires_approval"

    # Helper grant
    HELPER_GRANT_INVALID = "helper_grant_invalid"
    HELPER_GRANT_EXPIRED = "helper_grant_expired"
    HELPER_GRANT_REVOKED = "helper_grant_revoked"
    HELPER_SCOPE_MISMATCH = "helper_scope_mismatch"

    # Policy denials (runtime wrapping of policy outcomes)
    POLICY_DENY = "policy_deny"
    POLICY_OUT_OF_SCOPE = "policy_out_of_scope"
    POLICY_EXPIRED = "policy_expired"
    POLICY_REVOKED = "policy_revoked"
    INTERNAL_ERROR = "internal_error"


def _outcome_to_policy_outcome(outcome: Outcome) -> PolicyOutcome:
    mapping = {
        Outcome.ALLOW: PolicyOutcome.ALLOW,
        Outcome.REQUIRE_APPROVAL: PolicyOutcome.REQUIRE_CONFIRMATION,
    }
    return mapping.get(outcome, PolicyOutcome.DENY)


def _is_terminal_deny(outcome: Outcome) -> bool:
    return outcome in {
        Outcome.DENY,
        Outcome.EXPIRED,
        Outcome.REVOKED,
        Outcome.OUT_OF_SCOPE,
        Outcome.AUTHENTICATION_REQUIRED,
    }


@dataclass(frozen=True)
class Phase5RuntimeContext:
    """Transport-derived identity supplied by the caller.

    No sensitive fields are emitted by ``__repr__``.
    """

    actor_context: ActorContext
    source: str = "runtime"

    def __post_init__(self) -> None:
        if not isinstance(self.actor_context, ActorContext):
            raise ValueError("actor_context must be ActorContext")
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("source is required")

    def __repr__(self) -> str:
        return f"Phase5RuntimeContext(source={self.source!r})"


@dataclass(frozen=True)
class Phase5RuntimeRequest:
    """Bounded runtime authorization request.

    No raw content is rendered by ``__repr__``.
    """

    request_id: str
    capability: Capability
    context: Phase5RuntimeContext
    action: Optional[str] = None
    resource: Optional[str] = None
    data_subject: Optional[str] = None
    metadata: Tuple[str, ...] = ()
    user_initiated: bool = False
    access_session: Optional[AccessSession] = None
    now: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str):
            raise ValueError("request_id must be a string")
        if not isinstance(self.capability, Capability):
            raise ValueError("invalid capability")
        if not isinstance(self.context, Phase5RuntimeContext):
            raise ValueError("invalid context")
        if self.action is not None and (not isinstance(self.action, str) or not self.action):
            raise ValueError("action must be a non-empty string or None")
        if self.resource is not None and (not isinstance(self.resource, str) or not self.resource):
            raise ValueError("resource must be a non-empty string or None")
        if self.data_subject is not None and (not isinstance(self.data_subject, str) or not self.data_subject):
            raise ValueError("data_subject must be a non-empty string or None")
        if not isinstance(self.metadata, tuple):
            raise ValueError("metadata must be a tuple")
        for item in self.metadata:
            if not isinstance(item, str):
                raise ValueError("metadata must contain strings")
        if not isinstance(self.user_initiated, bool):
            raise ValueError("user_initiated must be boolean")
        if self.access_session is not None and not isinstance(self.access_session, AccessSession):
            raise ValueError("access_session must be AccessSession or None")
        # ``now`` is validated by the guard so that malformed time values are
        # reported as runtime decisions rather than constructor exceptions.

    def __repr__(self) -> str:
        return (
            f"Phase5RuntimeRequest(capability={self.capability.value!r}, "
            f"user_initiated={self.user_initiated})"
        )


@dataclass(frozen=True)
class Phase5RuntimeDecision:
    """Final runtime authorization decision.

    Contains the underlying policy decision, child-mode classification, and the
    audit id. ``__repr__`` is content-free.
    """

    request_id: str
    outcome: Outcome
    reason: RuntimeDecisionReason
    audit_id: Optional[str] = None
    policy_decision: Optional[Phase5Decision] = None
    child_reason: Optional[ChildModeDecisionReason] = None
    approval_required: bool = False

    def __post_init__(self) -> None:
        _validate_identifier(self.request_id, "request_id")
        if not isinstance(self.outcome, Outcome):
            raise ValueError("invalid outcome")
        if not isinstance(self.reason, RuntimeDecisionReason):
            raise ValueError("invalid reason")
        if self.policy_decision is not None and not isinstance(self.policy_decision, Phase5Decision):
            raise ValueError("policy_decision must be Phase5Decision or None")
        if self.child_reason is not None and not isinstance(self.child_reason, ChildModeDecisionReason):
            raise ValueError("child_reason must be ChildModeDecisionReason or None")
        if self.audit_id is not None:
            _validate_identifier(self.audit_id, "audit_id")
        if not isinstance(self.approval_required, bool):
            raise ValueError("approval_required must be boolean")

    def __repr__(self) -> str:
        return (
            f"Phase5RuntimeDecision(outcome={self.outcome.value!r}, "
            f"reason={self.reason.value!r})"
        )


class Phase5RuntimeGuard:
    """Production-facing runtime authorization guard for Phase 5.

    The guard is stateless except for the injected ``Phase5PolicyService`` and
    an optional ``SessionPolicy`` used only for session bounds/scope checks.
    """

    def __init__(
        self,
        policy_service: Phase5PolicyService,
        session_policy: Optional[SessionPolicy] = None,
    ):
        if policy_service is None:
            raise ValueError("policy_service is required")
        self.policy_service = policy_service
        self.session_policy = session_policy if session_policy is not None else default_session_policy()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def authorize(self, request: Phase5RuntimeRequest) -> Phase5RuntimeDecision:
        """Authorize a runtime request using the fail-closed pipeline."""
        # 1. Structural input and injected time validation
        try:
            _validate_runtime_request(request)
        except (ValueError, AttributeError, TypeError):
            return self._runtime_decision(
                request,
                Outcome.DENY,
                RuntimeDecisionReason.INVALID_INPUT,
                audit_id=None,
            )

        # 2. Validate transport-derived actor context
        valid_actor, _ = validate_actor_context(request.context.actor_context)
        if not valid_actor:
            return self._runtime_decision(
                request,
                Outcome.DENY,
                RuntimeDecisionReason.INVALID_ACTOR,
                audit_id=None,
            )

        # 3-5. Derive the effective Phase5 actor and owner, with session checks
        derived = self._derive_effective_actor(request)
        if derived.error is not None:
            audit_id = self._record_runtime_audit(request, Outcome.DENY, derived.error)
            return self._runtime_decision(
                request,
                Outcome.DENY,
                derived.error,
                audit_id=audit_id,
            )

        phase5_actor = derived.phase5_actor
        owner_actor_id = derived.owner_actor_id
        session = request.access_session

        # 6. Immediate session state checks (active, unexpired)
        if session is not None:
            state_reason = self._check_session_state(session, request.now)
            if state_reason is not None:
                audit_id = self._record_runtime_audit(request, Outcome.DENY, state_reason)
                return self._runtime_decision(
                    request,
                    Outcome.DENY,
                    state_reason,
                    audit_id=audit_id,
                )

        # 7. Requested capability must be inside the session scope
        if session is not None:
            if request.capability not in session.capabilities:
                audit_id = self._record_runtime_audit(request, Outcome.DENY, RuntimeDecisionReason.CAPABILITY_NOT_IN_SESSION)
                return self._runtime_decision(
                    request,
                    Outcome.DENY,
                    RuntimeDecisionReason.CAPABILITY_NOT_IN_SESSION,
                    audit_id=audit_id,
                )

        # 8-10. Child-mode classification
        if phase5_actor is Phase5Actor.CHILD:
            child_classification = self._classify_child_request(request)
            if child_classification.outcome is Outcome.DENY:
                audit_id = self._record_runtime_audit(
                    request, Outcome.DENY, RuntimeDecisionReason.CHILD_HARD_DENY
                )
                return Phase5RuntimeDecision(
                    request_id=request.request_id,
                    outcome=Outcome.DENY,
                    reason=RuntimeDecisionReason.CHILD_HARD_DENY,
                    audit_id=audit_id,
                    policy_decision=None,
                    child_reason=child_classification.reason,
                    approval_required=False,
                )
            if child_classification.outcome is Outcome.REQUIRE_APPROVAL:
                audit_id = self._record_runtime_audit(
                    request,
                    Outcome.REQUIRE_APPROVAL,
                    RuntimeDecisionReason.CHILD_AMBIGUOUS_REQUIRES_APPROVAL,
                )
                return Phase5RuntimeDecision(
                    request_id=request.request_id,
                    outcome=Outcome.REQUIRE_APPROVAL,
                    reason=RuntimeDecisionReason.CHILD_AMBIGUOUS_REQUIRES_APPROVAL,
                    audit_id=audit_id,
                    policy_decision=None,
                    child_reason=child_classification.reason,
                    approval_required=True,
                )
            # A safe classification grants no authority by itself. The policy
            # decision remains authoritative and may still deny or require
            # approval.

        # 11. Trusted-helper current grant validation
        if phase5_actor is Phase5Actor.TRUSTED_HELPER:
            grant_error = self._validate_helper_grant(request, session)
            if grant_error is not None:
                audit_id = self._record_runtime_audit(request, Outcome.DENY, grant_error)
                return self._runtime_decision(
                    request,
                    Outcome.DENY,
                    grant_error,
                    audit_id=audit_id,
                )

        # 12-15. Convert to Phase5AuthorizationRequest and call policy service
        policy_decision, audit_id = self._call_policy(request, phase5_actor, owner_actor_id)

        runtime_reason = self._map_policy_reason(policy_decision.outcome, policy_decision.reason)
        return Phase5RuntimeDecision(
            request_id=request.request_id,
            outcome=policy_decision.outcome,
            reason=runtime_reason,
            audit_id=audit_id,
            policy_decision=policy_decision,
            approval_required=policy_decision.outcome is Outcome.REQUIRE_APPROVAL,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _call_policy(
        self,
        request: Phase5RuntimeRequest,
        phase5_actor: Phase5Actor,
        owner_actor_id: str,
    ) -> tuple[Phase5Decision, str]:
        """Build a Phase5AuthorizationRequest, call the policy service, and return decision + audit id."""
        phase5_actor_ctx = Phase5ActorContext(
            actor_id=request.context.actor_context.actor_id,
            actor=phase5_actor,
            session_id=request.context.actor_context.session_id,
            source=request.context.source,
        )
        grant_id: Optional[str] = None
        if request.access_session is not None and request.access_session.authority_snapshot.grant is not None:
            grant_id = request.access_session.authority_snapshot.grant.grant_id

        p5_request = Phase5Request(
            request_id=request.request_id,
            actor=phase5_actor_ctx,
            capability=request.capability,
            data_subject=request.data_subject,
            resource=request.resource,
            action=request.action,
            metadata=request.metadata,
        )
        auth_request = Phase5AuthorizationRequest(
            request=p5_request,
            actor=request.context.actor_context,
            user_initiated=request.user_initiated,
            grant_id=grant_id,
        )
        auth_decision = self.policy_service.authorize(
            auth_request,
            evaluation_time=request.now,
        )
        return auth_decision.decision, auth_decision.audit_id

    def _derive_effective_actor(
        self,
        request: Phase5RuntimeRequest,
    ) -> "_DerivedActor":
        core_actor = request.context.actor_context
        session = request.access_session

        if session is None:
            if core_actor.actor is Actor.OWNER:
                return _DerivedActor(Phase5Actor.OWNER, core_actor.actor_id, None)
            return _DerivedActor(None, "", RuntimeDecisionReason.SESSION_REQUIRED)

        if not isinstance(session, AccessSession):
            return _DerivedActor(None, "", RuntimeDecisionReason.INVALID_INPUT)

        if session.session_type is SessionType.OWNER:
            if core_actor.actor is not Actor.OWNER or core_actor.actor_id != session.owner_actor_id:
                return _DerivedActor(None, "", RuntimeDecisionReason.SESSION_ACTOR_MISMATCH)
            return _DerivedActor(Phase5Actor.OWNER, session.owner_actor_id, None)

        if session.session_type is SessionType.CHILD:
            if core_actor.actor_id != session.session_actor_id:
                return _DerivedActor(None, "", RuntimeDecisionReason.SESSION_ACTOR_MISMATCH)
            return _DerivedActor(Phase5Actor.CHILD, session.owner_actor_id, None)

        if session.session_type is SessionType.TRUSTED_HELPER:
            if core_actor.actor_id != session.session_actor_id:
                return _DerivedActor(None, "", RuntimeDecisionReason.SESSION_ACTOR_MISMATCH)
            return _DerivedActor(Phase5Actor.TRUSTED_HELPER, session.owner_actor_id, None)

        return _DerivedActor(None, "", RuntimeDecisionReason.INVALID_INPUT)

    def _check_session_state(
        self,
        session: AccessSession,
        now: float,
    ) -> Optional[RuntimeDecisionReason]:
        expected_authority = {
            SessionType.OWNER: AuthoritySource.OWNER_DIRECT,
            SessionType.CHILD: AuthoritySource.CHILD_ACTIVATION,
            SessionType.TRUSTED_HELPER: AuthoritySource.HELPER_GRANT,
        }.get(session.session_type)
        if expected_authority is None or session.authority_snapshot.source is not expected_authority:
            return RuntimeDecisionReason.SESSION_AUTHORITY_MISMATCH
        if now < session.created_at:
            return RuntimeDecisionReason.SESSION_POLICY_VIOLATION
        if session.expires_at - session.created_at > self.session_policy.max_duration_for(
            session.session_type
        ):
            return RuntimeDecisionReason.SESSION_POLICY_VIOLATION
        if session.session_type is SessionType.CHILD and any(
            capability not in self.session_policy.child_allowed_capabilities
            for capability in session.capabilities
        ):
            return RuntimeDecisionReason.SESSION_POLICY_VIOLATION
        if session.state is SessionState.EXPIRED or now >= session.expires_at:
            return RuntimeDecisionReason.SESSION_EXPIRED
        if session.state is SessionState.REVOKED:
            return RuntimeDecisionReason.SESSION_REVOKED
        if session.state is SessionState.LOCKED:
            return RuntimeDecisionReason.SESSION_LOCKED
        if session.state is SessionState.CLOSED:
            return RuntimeDecisionReason.SESSION_CLOSED
        if session.state is not SessionState.ACTIVE:
            return RuntimeDecisionReason.SESSION_CLOSED
        return None

    def _validate_helper_grant(
        self,
        request: Phase5RuntimeRequest,
        session: Optional[AccessSession],
    ) -> Optional[RuntimeDecisionReason]:
        if session is None:
            return RuntimeDecisionReason.HELPER_GRANT_INVALID
        grant_snapshot = session.authority_snapshot.grant
        if grant_snapshot is None:
            return RuntimeDecisionReason.HELPER_GRANT_INVALID

        current_grant = self.policy_service.get_helper_grant(grant_snapshot.grant_id)
        if current_grant is None:
            return RuntimeDecisionReason.HELPER_GRANT_INVALID
        if current_grant.revoked:
            return RuntimeDecisionReason.HELPER_GRANT_REVOKED
        if current_grant.is_expired(request.now):
            return RuntimeDecisionReason.HELPER_GRANT_EXPIRED

        if current_grant.helper_actor_id != session.session_actor_id:
            return RuntimeDecisionReason.HELPER_SCOPE_MISMATCH
        if current_grant.owner_actor_id != session.owner_actor_id:
            return RuntimeDecisionReason.HELPER_SCOPE_MISMATCH
        if current_grant.capability is not request.capability:
            return RuntimeDecisionReason.HELPER_SCOPE_MISMATCH
        if not current_grant.scope.matches(
            request.capability,
            request.data_subject,
            request.resource,
            request.action,
        ):
            return RuntimeDecisionReason.HELPER_SCOPE_MISMATCH
        return None

    def _classify_child_request(self, request: Phase5RuntimeRequest) -> ChildModeDecision:
        # Use the action as the primary category when present; otherwise the capability.
        category = request.action if request.action else request.capability.value
        descriptor = ChildActionDescriptor(
            category=category,
            action=request.action,
            subject=request.data_subject,
            resource=request.resource,
            metadata=request.metadata,
        )
        return classify_child_action(descriptor)

    def _record_runtime_audit(
        self,
        request: Phase5RuntimeRequest,
        outcome: Outcome,
        reason: RuntimeDecisionReason,
    ) -> str:
        """Record a single audit row for a runtime-level pre-policy denial."""
        return self.policy_service.audit.record_decision(
            actor=request.context.actor_context,
            task_id=request.request_id,
            action=f"phase5.{request.capability.value}",
            resource_ref=opaque_resource_reference(request.resource),
            destination=None,
            outcome=_outcome_to_policy_outcome(outcome),
            reason=reason.value,
        )

    def _runtime_decision(
        self,
        request: object,
        outcome: Outcome,
        reason: RuntimeDecisionReason,
        *,
        audit_id: Optional[str],
    ) -> Phase5RuntimeDecision:
        request_id = getattr(request, "request_id", "invalid")
        if not isinstance(request_id, str) or not request_id:
            request_id = "invalid"
        return Phase5RuntimeDecision(
            request_id=request_id,
            outcome=outcome,
            reason=reason,
            audit_id=audit_id,
            policy_decision=None,
            child_reason=None,
            approval_required=outcome is Outcome.REQUIRE_APPROVAL,
        )

    def _map_policy_reason(self, outcome: Outcome, reason: DecisionReason) -> RuntimeDecisionReason:
        mapping = {
            Outcome.ALLOW: RuntimeDecisionReason.POLICY_ALLOW,
            Outcome.REQUIRE_APPROVAL: RuntimeDecisionReason.POLICY_APPROVAL_REQUIRED,
            Outcome.DENY: RuntimeDecisionReason.POLICY_DENY,
            Outcome.EXPIRED: RuntimeDecisionReason.POLICY_EXPIRED,
            Outcome.REVOKED: RuntimeDecisionReason.POLICY_REVOKED,
            Outcome.OUT_OF_SCOPE: RuntimeDecisionReason.POLICY_OUT_OF_SCOPE,
            Outcome.AUTHENTICATION_REQUIRED: RuntimeDecisionReason.POLICY_DENY,
        }
        return mapping.get(outcome, RuntimeDecisionReason.INTERNAL_ERROR)


# Internal value object to avoid nested tuples.
class _DerivedActor:
    def __init__(
        self,
        phase5_actor: Optional[Phase5Actor],
        owner_actor_id: str,
        error: Optional[RuntimeDecisionReason],
    ):
        self.phase5_actor = phase5_actor
        self.owner_actor_id = owner_actor_id
        self.error = error


def _validate_runtime_request(request: object) -> None:
    if not isinstance(request, Phase5RuntimeRequest):
        raise ValueError("invalid request")
    if request.context is None or request.context.actor_context is None:
        raise ValueError("missing context")
    try:
        _validate_identifier(request.request_id, "request_id")
    except ValueError as exc:
        raise ValueError("invalid request_id") from exc
    if not math.isfinite(request.now):
        raise ValueError("invalid time")
    _validate_actor_identifier(request.context.actor_context.actor_id, "actor_id")


__all__ = [
    "Phase5RuntimeContext",
    "Phase5RuntimeRequest",
    "Phase5RuntimeDecision",
    "RuntimeDecisionReason",
    "Phase5RuntimeGuard",
]
