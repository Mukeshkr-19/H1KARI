"""Pure, deterministic Phase 5 session lifecycle contracts.

This module is runtime-neutral: it performs no authentication, no I/O, no
network calls, no persistence, and reads no wall-clock time.  All timestamps,
identities, grants, consents, and revocation states are caller-supplied.

Sessions are immutable value objects.  Activation and transitions return a
``SessionDecision`` that carries the resulting ``AccessSession`` and the
``SessionTransition`` that was applied.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional, Tuple

from core.phase5.contracts import (
    Capability,
    CapabilityGrant,
    ConsentRecord,
    Outcome,
    Phase5Actor,
)

# --- Validation helpers (duplicated locally to keep this module self-contained) --

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_ACTOR_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_MAX_SAFE_TIMESTAMP = 2**53


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


# --- Enums -------------------------------------------------------------------


class SessionType(StrEnum):
    """Supported session kinds."""

    OWNER = "owner"
    CHILD = "child"
    TRUSTED_HELPER = "trusted_helper"


class SessionState(StrEnum):
    """Lifecycle states for an access session."""

    INACTIVE = "inactive"
    PENDING_OWNER_APPROVAL = "pending_owner_approval"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    LOCKED = "locked"
    CLOSED = "closed"


class AuthoritySource(StrEnum):
    """Source of authority used to create or reactivate a session."""

    OWNER_DIRECT = "owner_direct"
    HELPER_GRANT = "helper_grant"
    CHILD_ACTIVATION = "child_activation"


class SessionDecisionReason(StrEnum):
    """Stable machine-readable reasons for every session decision."""

    # Allow / success
    OWNER_ACTIVATION_ALLOWED = "owner_activation_allowed"
    CHILD_ACTIVATION_ALLOWED = "child_activation_allowed"
    HELPER_ACTIVATION_ALLOWED = "helper_activation_allowed"
    OWNER_APPROVAL_GRANTED = "owner_approval_granted"
    SESSION_CLOSED = "session_closed"
    SESSION_LOCKED = "session_locked"
    SESSION_REVOKED = "session_revoked"
    SESSION_EXPIRED = "session_expired"

    # Deny / fail-closed
    DEFAULT_DENY = "default_deny"
    UNKNOWN_SESSION_TYPE = "unknown_session_type"
    UNKNOWN_STATE = "unknown_state"
    UNKNOWN_ACTOR = "unknown_actor"
    INVALID_INPUT = "invalid_input"
    INVALID_TIME = "invalid_time"
    OWNER_ONLY_ACTIVATION = "owner_only_activation"
    CHILD_EVIDENCE_REQUIRED = "child_evidence_required"
    HELPER_GRANT_REQUIRED = "helper_grant_required"
    HELPER_GRANT_EXPIRED = "helper_grant_expired"
    HELPER_GRANT_REVOKED = "helper_grant_revoked"
    HELPER_SCOPE_MISMATCH = "helper_scope_mismatch"
    PERMANENT_SESSION_BLOCKED = "permanent_session_blocked"
    SESSION_TOO_LONG = "session_too_long"
    SCOPE_TOO_BROAD = "scope_too_broad"
    TRANSITION_NOT_ALLOWED = "transition_not_allowed"
    SESSION_EXPIRED_CANNOT_RENEW = "session_expired_cannot_renew"
    SESSION_REVOKED_CANNOT_REACTIVATE = "session_revoked_cannot_reactivate"
    SESSION_LOCKED_CANNOT_REACTIVATE = "session_locked_cannot_reactivate"
    SESSION_CLOSED_CANNOT_TRANSITION = "session_closed_cannot_transition"
    DELEGATION_BLOCKED = "delegation_blocked"
    SCOPE_EXPANSION_BLOCKED = "scope_expansion_blocked"
    CHILD_HELPER_GRANT_BLOCKED = "child_helper_grant_blocked"
    CHILD_OWNER_MEMORY_BLOCKED = "child_owner_memory_blocked"
    AUTHORITY_MISMATCH = "authority_mismatch"
    APPROVAL_REQUIRED = "approval_required"
    CLOSING_IDEMPOTENT = "closing_idempotent"


# --- Core Contracts ----------------------------------------------------------


@dataclass(frozen=True)
class SessionPolicy:
    """Immutable policy limits for session creation and lifetime.

    All durations are in seconds.  No default or maximum duration may be
    ``None``; callers must supply finite bounds for every session type.
    """

    owner_max_duration_seconds: int = 3600
    child_max_duration_seconds: int = 1800
    helper_max_duration_seconds: int = 3600
    child_allowed_capabilities: Tuple[Capability, ...] = (
        Capability.CHILD_MODE,
        Capability.TEACH_ME,
        Capability.GUIDE_MY_HANDS,
        Capability.CARE,
    )

    def __post_init__(self) -> None:
        for field, value in (
            ("owner_max_duration_seconds", self.owner_max_duration_seconds),
            ("child_max_duration_seconds", self.child_max_duration_seconds),
            ("helper_max_duration_seconds", self.helper_max_duration_seconds),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{field} must be a positive integer")
        if not isinstance(self.child_allowed_capabilities, tuple):
            raise ValueError("child_allowed_capabilities must be a tuple")
        for cap in self.child_allowed_capabilities:
            if not isinstance(cap, Capability):
                raise ValueError("child_allowed_capabilities must contain Capability values")

    def max_duration_for(self, session_type: SessionType) -> int:
        if session_type is SessionType.OWNER:
            return self.owner_max_duration_seconds
        if session_type is SessionType.CHILD:
            return self.child_max_duration_seconds
        if session_type is SessionType.TRUSTED_HELPER:
            return self.helper_max_duration_seconds
        raise ValueError("unknown session type")


@dataclass(frozen=True)
class SessionAuthoritySnapshot:
    """Caller-supplied proof of authority for a session.

    The content of ``activation_evidence`` and ``consent`` is never shown in the
    repr; only the source type and owner role are exposed.
    """

    source: AuthoritySource
    owner_actor_id: str
    grant: Optional[CapabilityGrant] = None
    activation_evidence: Optional[str] = None
    consent: Optional[ConsentRecord] = None

    def __post_init__(self) -> None:
        _validate_actor_identifier(self.owner_actor_id, "owner_actor_id")
        if not isinstance(self.source, AuthoritySource):
            raise ValueError("invalid authority source")
        if self.grant is not None and not isinstance(self.grant, CapabilityGrant):
            raise ValueError("grant must be a CapabilityGrant or None")
        if self.activation_evidence is not None and not isinstance(self.activation_evidence, str):
            raise ValueError("activation_evidence must be a string or None")
        if self.consent is not None and not isinstance(self.consent, ConsentRecord):
            raise ValueError("consent must be a ConsentRecord or None")
        if self.source is AuthoritySource.HELPER_GRANT and self.grant is None:
            raise ValueError("helper authority requires a grant")
        if self.source is AuthoritySource.CHILD_ACTIVATION and not self.activation_evidence:
            raise ValueError("child activation requires evidence")

    def __repr__(self) -> str:
        return f"SessionAuthoritySnapshot(source={self.source.value!r})"


@dataclass(frozen=True)
class SessionTransition:
    """Immutable record of a single state transition."""

    transition_id: str
    session_id: str
    from_state: SessionState
    to_state: SessionState
    reason: SessionDecisionReason
    actor_id: str
    timestamp: float

    def __post_init__(self) -> None:
        _validate_identifier(self.transition_id, "transition_id")
        _validate_identifier(self.session_id, "session_id")
        if not isinstance(self.from_state, SessionState):
            raise ValueError("invalid from_state")
        if not isinstance(self.to_state, SessionState):
            raise ValueError("invalid to_state")
        if not isinstance(self.reason, SessionDecisionReason):
            raise ValueError("invalid reason")
        _validate_actor_identifier(self.actor_id, "actor_id")
        _validate_finite_timestamp(self.timestamp, "timestamp")

    def __repr__(self) -> str:
        return (
            f"SessionTransition({self.from_state.value!r} -> {self.to_state.value!r}, "
            f"reason={self.reason.value!r})"
        )


@dataclass(frozen=True)
class AccessSession:
    """Immutable access session value object.

    No tokens, consent content, or private memory are emitted by ``__repr__``.
    """

    session_id: str
    session_type: SessionType
    owner_actor_id: str
    session_actor_id: str
    state: SessionState
    created_at: float
    expires_at: float
    capabilities: Tuple[Capability, ...]
    authority_snapshot: SessionAuthoritySnapshot
    transitions: Tuple[SessionTransition, ...]
    revoked_at: Optional[float] = None
    locked_at: Optional[float] = None
    closed_at: Optional[float] = None

    def __post_init__(self) -> None:
        _validate_identifier(self.session_id, "session_id")
        if not isinstance(self.session_type, SessionType):
            raise ValueError("invalid session_type")
        _validate_actor_identifier(self.owner_actor_id, "owner_actor_id")
        _validate_actor_identifier(self.session_actor_id, "session_actor_id")
        if not isinstance(self.state, SessionState):
            raise ValueError("invalid state")
        _validate_finite_timestamp(self.created_at, "created_at")
        _validate_finite_timestamp(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be greater than created_at")
        if not isinstance(self.capabilities, tuple):
            raise ValueError("capabilities must be a tuple")
        if any(not isinstance(capability, Capability) for capability in self.capabilities):
            raise ValueError("capabilities must contain Capability values")
        if not isinstance(self.authority_snapshot, SessionAuthoritySnapshot):
            raise ValueError("invalid authority_snapshot")
        if self.authority_snapshot.owner_actor_id != self.owner_actor_id:
            raise ValueError("authority owner does not match session owner")
        if not isinstance(self.transitions, tuple):
            raise ValueError("transitions must be a tuple")
        if any(not isinstance(transition, SessionTransition) for transition in self.transitions):
            raise ValueError("transitions must contain SessionTransition values")
        for attr, field in (
            (self.revoked_at, "revoked_at"),
            (self.locked_at, "locked_at"),
            (self.closed_at, "closed_at"),
        ):
            if attr is not None:
                _validate_finite_timestamp(attr, field)

    def is_terminal(self) -> bool:
        return self.state in {SessionState.CLOSED, SessionState.EXPIRED, SessionState.REVOKED}

    def is_active(self, now: float) -> bool:
        _validate_finite_timestamp(now, "now")
        if self.state is not SessionState.ACTIVE:
            return False
        return now < self.expires_at

    def __repr__(self) -> str:
        return (
            f"AccessSession(type={self.session_type.value!r}, "
            f"state={self.state.value!r})"
        )


@dataclass(frozen=True)
class SessionActivationRequest:
    """Caller-supplied request to create a new access session."""

    request_id: str
    session_type: SessionType
    owner_actor_id: str
    session_actor_id: str
    requested_capabilities: Tuple[Capability, ...]
    requested_expires_at: float
    authority_snapshot: SessionAuthoritySnapshot
    previous_session_id: Optional[str] = None
    metadata: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_identifier(self.request_id, "request_id")
        if not isinstance(self.session_type, SessionType):
            raise ValueError("invalid session_type")
        _validate_actor_identifier(self.owner_actor_id, "owner_actor_id")
        _validate_actor_identifier(self.session_actor_id, "session_actor_id")
        if not isinstance(self.requested_capabilities, tuple):
            raise ValueError("requested_capabilities must be a tuple")
        for cap in self.requested_capabilities:
            if not isinstance(cap, Capability):
                raise ValueError("requested_capabilities must contain Capability values")
        _validate_finite_timestamp(self.requested_expires_at, "requested_expires_at")
        if not isinstance(self.authority_snapshot, SessionAuthoritySnapshot):
            raise ValueError("invalid authority_snapshot")
        if self.previous_session_id is not None:
            _validate_identifier(self.previous_session_id, "previous_session_id")
        if not isinstance(self.metadata, tuple):
            raise ValueError("metadata must be a tuple")
        if any(not isinstance(item, str) for item in self.metadata):
            raise ValueError("metadata must contain strings")

    def __repr__(self) -> str:
        return (
            f"SessionActivationRequest(type={self.session_type.value!r}, "
            f"capabilities={len(self.requested_capabilities)})"
        )


@dataclass(frozen=True)
class SessionDecision:
    """Result of a session activation or transition.

    The resulting ``AccessSession`` is present for successful activations and
    transitions; it is ``None`` when the request is denied.
    """

    request_id: str
    session_id: str
    outcome: Outcome
    reason: SessionDecisionReason
    session: Optional[AccessSession]
    transition: Optional[SessionTransition]
    timestamp: float

    def __post_init__(self) -> None:
        _validate_identifier(self.request_id, "request_id")
        _validate_identifier(self.session_id, "session_id")
        if not isinstance(self.outcome, Outcome):
            raise ValueError("invalid outcome")
        if not isinstance(self.reason, SessionDecisionReason):
            raise ValueError("invalid reason")
        if self.session is not None and not isinstance(self.session, AccessSession):
            raise ValueError("session must be an AccessSession or None")
        if self.transition is not None and not isinstance(self.transition, SessionTransition):
            raise ValueError("transition must be a SessionTransition or None")
        _validate_finite_timestamp(self.timestamp, "timestamp")

    def __repr__(self) -> str:
        return (
            f"SessionDecision(outcome={self.outcome.value!r}, "
            f"reason={self.reason.value!r})"
        )


# --- Policy implementation ---------------------------------------------------


def default_session_policy() -> SessionPolicy:
    """Return the default session policy."""
    return SessionPolicy()


def _deny(
    request_id: str,
    session_id: str,
    reason: SessionDecisionReason,
    now: float,
) -> SessionDecision:
    if not session_id:
        session_id = "invalid"
    return SessionDecision(
        request_id=request_id,
        session_id=session_id,
        outcome=Outcome.DENY,
        reason=reason,
        session=None,
        transition=None,
        timestamp=now,
    )


def _validate_request_time_fields(request: SessionActivationRequest, now: float) -> Optional[SessionDecision]:
    try:
        _validate_finite_timestamp(now, "now")
    except ValueError:
        return _deny(request.request_id, "", SessionDecisionReason.INVALID_TIME, 0.0)
    try:
        _validate_finite_timestamp(request.requested_expires_at, "requested_expires_at")
    except ValueError:
        return _deny(request.request_id, "", SessionDecisionReason.INVALID_TIME, now)
    if request.requested_expires_at <= now:
        return _deny(request.request_id, "", SessionDecisionReason.SESSION_EXPIRED, now)
    return None


def _delegation_or_expansion_attempt(metadata: Tuple[str, ...]) -> Optional[SessionDecisionReason]:
    for item in metadata:
        if not isinstance(item, str):
            continue
        normalized = item.lower().replace(" ", "_")
        if "delegate" in normalized:
            return SessionDecisionReason.DELEGATION_BLOCKED
        if "expand" in normalized or "escalate" in normalized or "broaden" in normalized:
            return SessionDecisionReason.SCOPE_EXPANSION_BLOCKED
    return None


def _session_type_from_object(value: object) -> Optional[SessionType]:
    if isinstance(value, SessionType):
        return value
    if isinstance(value, str):
        try:
            return SessionType(value)
        except ValueError:
            return None
    return None


def _session_state_from_object(value: object) -> Optional[SessionState]:
    if isinstance(value, SessionState):
        return value
    if isinstance(value, str):
        try:
            return SessionState(value)
        except ValueError:
            return None
    return None


# Legal lifecycle transitions.  Each entry is (from_state, to_state).
_LEGAL_TRANSITIONS: frozenset[tuple[SessionState, SessionState]] = frozenset({
    # Initial activation
    (SessionState.INACTIVE, SessionState.PENDING_OWNER_APPROVAL),
    (SessionState.INACTIVE, SessionState.ACTIVE),
    # Owner approval
    (SessionState.PENDING_OWNER_APPROVAL, SessionState.ACTIVE),
    (SessionState.PENDING_OWNER_APPROVAL, SessionState.CLOSED),
    # Active lifetime
    (SessionState.ACTIVE, SessionState.LOCKED),
    (SessionState.ACTIVE, SessionState.EXPIRED),
    (SessionState.ACTIVE, SessionState.REVOKED),
    (SessionState.ACTIVE, SessionState.CLOSED),
    # Locked sessions may be released only to inactive (new owner-authorized request)
    (SessionState.LOCKED, SessionState.INACTIVE),
    (SessionState.LOCKED, SessionState.CLOSED),
    # Terminal cleanup
    (SessionState.EXPIRED, SessionState.CLOSED),
    (SessionState.REVOKED, SessionState.CLOSED),
    # Idempotent close
    (SessionState.CLOSED, SessionState.CLOSED),
})


def evaluate_activation_request(
    request: SessionActivationRequest,
    policy: SessionPolicy,
    now: float,
) -> SessionDecision:
    """Evaluate a request to create a new access session.

    The returned ``SessionDecision`` contains the resulting ``AccessSession``
    when activation succeeds.  All failure paths are fail-closed.
    """
    if not isinstance(request, SessionActivationRequest):
        safe_now = now if isinstance(now, (int, float)) and not isinstance(now, bool) and math.isfinite(now) else 0.0
        return _deny("invalid", "", SessionDecisionReason.INVALID_INPUT, safe_now)
    if not isinstance(policy, SessionPolicy):
        safe_now = now if isinstance(now, (int, float)) and not isinstance(now, bool) and math.isfinite(now) else 0.0
        return _deny(request.request_id, "", SessionDecisionReason.INVALID_INPUT, safe_now)

    bad = _validate_request_time_fields(request, now)
    if bad is not None:
        return bad

    # Unknown / unsupported session type
    session_type = _session_type_from_object(request.session_type)
    if session_type is None:
        return _deny(request.request_id, "", SessionDecisionReason.UNKNOWN_SESSION_TYPE, now)

    # Metadata-driven hard blocks (delegation, scope expansion)
    block_reason = _delegation_or_expansion_attempt(request.metadata)
    if block_reason is not None:
        return _deny(request.request_id, "", block_reason, now)

    # Expired sessions cannot renew themselves
    if request.previous_session_id is not None:
        return _deny(
            request.request_id,
            "",
            SessionDecisionReason.SESSION_EXPIRED_CANNOT_RENEW,
            now,
        )

    authority = request.authority_snapshot
    if authority.owner_actor_id != request.owner_actor_id:
        return _deny(request.request_id, "", SessionDecisionReason.AUTHORITY_MISMATCH, now)
    if not request.requested_capabilities:
        return _deny(request.request_id, "", SessionDecisionReason.SCOPE_TOO_BROAD, now)

    # Owner sessions cannot be created from helper or child authority
    if session_type is SessionType.OWNER:
        if authority.source is not AuthoritySource.OWNER_DIRECT:
            return _deny(request.request_id, "", SessionDecisionReason.OWNER_ONLY_ACTIVATION, now)
        if request.session_actor_id != request.owner_actor_id:
            return _deny(request.request_id, "", SessionDecisionReason.AUTHORITY_MISMATCH, now)

    # Child mode requires owner-controlled activation evidence
    if session_type is SessionType.CHILD:
        if authority.source is not AuthoritySource.CHILD_ACTIVATION:
            return _deny(request.request_id, "", SessionDecisionReason.CHILD_EVIDENCE_REQUIRED, now)

    # Trusted-helper sessions require a valid scoped grant
    if session_type is SessionType.TRUSTED_HELPER:
        if authority.source is not AuthoritySource.HELPER_GRANT:
            return _deny(request.request_id, "", SessionDecisionReason.HELPER_GRANT_REQUIRED, now)
        if authority.grant is None:
            return _deny(request.request_id, "", SessionDecisionReason.HELPER_GRANT_REQUIRED, now)

    # Validate requested lifetime against policy bounds
    max_duration = policy.max_duration_for(session_type)
    requested_duration = request.requested_expires_at - now
    if requested_duration > max_duration:
        return _deny(request.request_id, "", SessionDecisionReason.SESSION_TOO_LONG, now)
    if request.requested_expires_at <= now:
        return _deny(request.request_id, "", SessionDecisionReason.SESSION_EXPIRED, now)

    # No permanent sessions
    if not math.isfinite(request.requested_expires_at) or not math.isfinite(now):
        return _deny(request.request_id, "", SessionDecisionReason.INVALID_TIME, now)

    # Build the session
    session_id = request.request_id  # caller-supplied ID; no UUID generation
    created_at = now
    expires_at = request.requested_expires_at
    initial_state = SessionState.ACTIVE

    # Owner session: may require explicit approval if consent is missing
    if session_type is SessionType.OWNER:
        if authority.consent is None:
            # Owner direct sessions are allowed without a separate consent record;
            # the OWNER_DIRECT authority source itself is the owner-controlled proof.
            pass
        else:
            if not authority.consent.is_valid(now):
                return _deny(request.request_id, "", SessionDecisionReason.APPROVAL_REQUIRED, now)

    # Helper session: validate grant validity and scope
    if session_type is SessionType.TRUSTED_HELPER:
        assert authority.grant is not None
        grant = authority.grant
        if grant.is_revoked():
            return _deny(request.request_id, "", SessionDecisionReason.HELPER_GRANT_REVOKED, now)
        if grant.is_expired(now):
            return _deny(request.request_id, "", SessionDecisionReason.HELPER_GRANT_EXPIRED, now)
        if grant.helper_actor_id != request.session_actor_id:
            return _deny(request.request_id, "", SessionDecisionReason.HELPER_SCOPE_MISMATCH, now)
        if grant.owner_actor_id != request.owner_actor_id:
            return _deny(request.request_id, "", SessionDecisionReason.HELPER_SCOPE_MISMATCH, now)
        if grant.expires_at < expires_at:
            return _deny(request.request_id, "", SessionDecisionReason.SESSION_TOO_LONG, now)
        # Scope must be equal or narrower: only the grant's capability may be used.
        if any(cap is not grant.capability for cap in request.requested_capabilities):
            return _deny(request.request_id, "", SessionDecisionReason.SCOPE_TOO_BROAD, now)

    # Child session: validate capabilities are child-safe
    if session_type is SessionType.CHILD:
        allowed = frozenset(policy.child_allowed_capabilities)
        if Capability.TRUSTED_HELPER_ACCESS in request.requested_capabilities:
            return _deny(request.request_id, "", SessionDecisionReason.CHILD_HELPER_GRANT_BLOCKED, now)
        for cap in request.requested_capabilities:
            if cap not in allowed:
                return _deny(request.request_id, "", SessionDecisionReason.SCOPE_TOO_BROAD, now)

    # Determine initial reason code
    reason_map = {
        SessionType.OWNER: SessionDecisionReason.OWNER_ACTIVATION_ALLOWED,
        SessionType.CHILD: SessionDecisionReason.CHILD_ACTIVATION_ALLOWED,
        SessionType.TRUSTED_HELPER: SessionDecisionReason.HELPER_ACTIVATION_ALLOWED,
    }

    transition = SessionTransition(
        transition_id=f"{session_id}.activate",
        session_id=session_id,
        from_state=SessionState.INACTIVE,
        to_state=initial_state,
        reason=reason_map[session_type],
        actor_id=request.owner_actor_id,
        timestamp=now,
    )

    session = AccessSession(
        session_id=session_id,
        session_type=session_type,
        owner_actor_id=request.owner_actor_id,
        session_actor_id=request.session_actor_id,
        state=initial_state,
        created_at=created_at,
        expires_at=expires_at,
        capabilities=request.requested_capabilities,
        authority_snapshot=authority,
        transitions=(transition,),
    )

    return SessionDecision(
        request_id=request.request_id,
        session_id=session_id,
        outcome=Outcome.ALLOW,
        reason=reason_map[session_type],
        session=session,
        transition=transition,
        timestamp=now,
    )


def transition_session(
    session: AccessSession,
    to_state: SessionState,
    actor_id: str,
    authority: SessionAuthoritySnapshot,
    policy: SessionPolicy,
    now: float,
) -> SessionDecision:
    """Apply a legal lifecycle transition to an existing session.

    All transitions are validated against the fail-closed legal-transition
    matrix.  Terminal states (``CLOSED``, ``EXPIRED``, ``REVOKED``) require a
    new owner-authorized activation request to re-enter ``ACTIVE``.
    """
    if not isinstance(session, AccessSession):
        safe_now = now if isinstance(now, (int, float)) and not isinstance(now, bool) and math.isfinite(now) else 0.0
        return _deny("invalid", "", SessionDecisionReason.INVALID_INPUT, safe_now)
    try:
        _validate_finite_timestamp(now, "now")
        _validate_actor_identifier(actor_id, "actor_id")
    except ValueError:
        return _deny("invalid", session.session_id, SessionDecisionReason.INVALID_INPUT, 0.0)
    if not isinstance(to_state, SessionState):
        return _deny("invalid", session.session_id, SessionDecisionReason.UNKNOWN_STATE, now)
    if not isinstance(authority, SessionAuthoritySnapshot):
        return _deny("invalid", session.session_id, SessionDecisionReason.INVALID_INPUT, now)
    if not isinstance(policy, SessionPolicy):
        return _deny("invalid", session.session_id, SessionDecisionReason.INVALID_INPUT, now)
    if authority.owner_actor_id != session.owner_actor_id:
        return _deny("invalid", session.session_id, SessionDecisionReason.AUTHORITY_MISMATCH, now)
    if actor_id not in {session.owner_actor_id, session.session_actor_id}:
        return _deny("invalid", session.session_id, SessionDecisionReason.AUTHORITY_MISMATCH, now)

    from_state = session.state
    request_id = f"{session.session_id}.transition.{to_state.value}"

    # Closed sessions are immutable except for idempotent close
    if from_state is SessionState.CLOSED:
        if to_state is SessionState.CLOSED:
            return SessionDecision(
                request_id=request_id,
                session_id=session.session_id,
                outcome=Outcome.ALLOW,
                reason=SessionDecisionReason.CLOSING_IDEMPOTENT,
                session=session,
                transition=None,
                timestamp=now,
            )
        return _deny(request_id, session.session_id, SessionDecisionReason.SESSION_CLOSED_CANNOT_TRANSITION, now)

    # Locked/revoked/expired sessions cannot reactivate without a new owner-authorized request.
    # Check this before the legal-transition matrix so the fail-closed reason is explicit.
    if from_state in {SessionState.LOCKED, SessionState.REVOKED, SessionState.EXPIRED} and to_state is SessionState.ACTIVE:
        return _deny(
            request_id,
            session.session_id,
            SessionDecisionReason.SESSION_LOCKED_CANNOT_REACTIVATE
            if from_state is SessionState.LOCKED
            else SessionDecisionReason.SESSION_REVOKED_CANNOT_REACTIVATE,
            now,
        )

    # Validate transition legality
    if (from_state, to_state) not in _LEGAL_TRANSITIONS:
        return _deny(request_id, session.session_id, SessionDecisionReason.TRANSITION_NOT_ALLOWED, now)

    # Only owner authority may drive sensitive transitions
    is_owner = (
        authority.source is AuthoritySource.OWNER_DIRECT
        and authority.owner_actor_id == session.owner_actor_id
        and actor_id == session.owner_actor_id
    )

    transition_reason: SessionDecisionReason
    if to_state is SessionState.LOCKED:
        if not is_owner:
            return _deny(request_id, session.session_id, SessionDecisionReason.OWNER_ONLY_ACTIVATION, now)
        transition_reason = SessionDecisionReason.SESSION_LOCKED
    elif to_state is SessionState.REVOKED:
        if not is_owner:
            return _deny(request_id, session.session_id, SessionDecisionReason.OWNER_ONLY_ACTIVATION, now)
        transition_reason = SessionDecisionReason.SESSION_REVOKED
    elif to_state is SessionState.CLOSED:
        transition_reason = SessionDecisionReason.SESSION_CLOSED
    elif to_state is SessionState.EXPIRED:
        # Time-driven; no actor authority required
        transition_reason = SessionDecisionReason.SESSION_EXPIRED
    elif to_state is SessionState.INACTIVE:
        # Releasing a locked session requires owner authority (new request)
        if from_state is not SessionState.LOCKED:
            return _deny(request_id, session.session_id, SessionDecisionReason.TRANSITION_NOT_ALLOWED, now)
        if not is_owner:
            return _deny(request_id, session.session_id, SessionDecisionReason.OWNER_ONLY_ACTIVATION, now)
        transition_reason = SessionDecisionReason.OWNER_ACTIVATION_ALLOWED
    elif to_state is SessionState.ACTIVE:
        # From pending approval -> active requires owner authority
        if from_state is SessionState.PENDING_OWNER_APPROVAL and not is_owner:
            return _deny(request_id, session.session_id, SessionDecisionReason.OWNER_ONLY_ACTIVATION, now)
        transition_reason = SessionDecisionReason.OWNER_ACTIVATION_ALLOWED
    else:
        return _deny(request_id, session.session_id, SessionDecisionReason.TRANSITION_NOT_ALLOWED, now)

    # Time checks for expiry / invalid time
    if to_state is SessionState.EXPIRED and now < session.expires_at:
        return _deny(request_id, session.session_id, SessionDecisionReason.INVALID_TIME, now)

    transition = SessionTransition(
        transition_id=f"{session.session_id}.{from_state.value}.{to_state.value}",
        session_id=session.session_id,
        from_state=from_state,
        to_state=to_state,
        reason=transition_reason,
        actor_id=actor_id,
        timestamp=now,
    )

    # Build updated session
    kwargs: dict = {
        "state": to_state,
        "transitions": session.transitions + (transition,),
    }
    if to_state is SessionState.REVOKED:
        kwargs["revoked_at"] = now
    if to_state is SessionState.LOCKED:
        kwargs["locked_at"] = now
    if to_state is SessionState.CLOSED:
        kwargs["closed_at"] = now

    new_session = AccessSession(
        session_id=session.session_id,
        session_type=session.session_type,
        owner_actor_id=session.owner_actor_id,
        session_actor_id=session.session_actor_id,
        created_at=session.created_at,
        expires_at=session.expires_at,
        capabilities=session.capabilities,
        authority_snapshot=session.authority_snapshot,
        **kwargs,
    )

    return SessionDecision(
        request_id=request_id,
        session_id=session.session_id,
        outcome=Outcome.ALLOW,
        reason=transition_reason,
        session=new_session,
        transition=transition,
        timestamp=now,
    )


def close_session(
    session: AccessSession,
    actor_id: str,
    authority: SessionAuthoritySnapshot,
    policy: SessionPolicy,
    now: float,
) -> SessionDecision:
    """Convenience wrapper to transition a session to ``CLOSED`` idempotently."""
    return transition_session(
        session,
        SessionState.CLOSED,
        actor_id,
        authority,
        policy,
        now,
    )


__all__ = [
    "AccessSession",
    "SessionActivationRequest",
    "SessionAuthoritySnapshot",
    "SessionTransition",
    "SessionDecision",
    "SessionPolicy",
    "SessionType",
    "SessionState",
    "AuthoritySource",
    "SessionDecisionReason",
    "default_session_policy",
    "evaluate_activation_request",
    "transition_session",
    "close_session",
]
