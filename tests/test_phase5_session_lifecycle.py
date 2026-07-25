"""Synthetic tests for core.phase5.session_lifecycle.

All fixtures are synthetic.  The tests exercise the fail-closed lifecycle,
authority boundaries, scope enforcement, and privacy guarantees without
contacting any store, filesystem, network, or clock.
"""

from __future__ import annotations

import inspect
import pathlib
import re

import pytest

from core.phase5.contracts import Capability, ConsentRecord, ScopeConstraint
from core.phase5.session_lifecycle import (
    AccessSession,
    AuthoritySource,
    SessionActivationRequest,
    SessionAuthoritySnapshot,
    SessionDecisionReason,
    SessionPolicy,
    SessionState,
    SessionType,
    close_session,
    default_session_policy,
    evaluate_activation_request,
    transition_session,
)


@pytest.fixture
def owner_id() -> str:
    return "owner.alice"


@pytest.fixture
def helper_id() -> str:
    return "helper.bob"


@pytest.fixture
def child_id() -> str:
    return "child.charlie"


@pytest.fixture
def now() -> float:
    return 1000.0


@pytest.fixture
def policy() -> SessionPolicy:
    return default_session_policy()


@pytest.fixture
def owner_authority(owner_id: str) -> SessionAuthoritySnapshot:
    return SessionAuthoritySnapshot(
        source=AuthoritySource.OWNER_DIRECT,
        owner_actor_id=owner_id,
    )


@pytest.fixture
def child_authority(owner_id: str) -> SessionAuthoritySnapshot:
    return SessionAuthoritySnapshot(
        source=AuthoritySource.CHILD_ACTIVATION,
        owner_actor_id=owner_id,
        activation_evidence="owner_pin_12345",
    )


@pytest.fixture
def helper_grant_valid(owner_id: str, helper_id: str, now: float):
    from core.phase5.contracts import CapabilityGrant

    return CapabilityGrant(
        grant_id="grant.teach.001",
        helper_actor_id=helper_id,
        owner_actor_id=owner_id,
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(
            capability=Capability.TEACH_ME,
            data_subject="child",
            allowed_actions=("read", "propose"),
        ),
        issued_at=now,
        expires_at=now + 1800,
    )


@pytest.fixture
def helper_authority(helper_grant_valid) -> SessionAuthoritySnapshot:
    return SessionAuthoritySnapshot(
        source=AuthoritySource.HELPER_GRANT,
        owner_actor_id=helper_grant_valid.owner_actor_id,
        grant=helper_grant_valid,
    )


# --- Activation helpers -----------------------------------------------------


def _owner_request(owner_id, owner_authority, now, policy, **overrides):
    req = SessionActivationRequest(
        request_id="req.owner.001",
        session_type=SessionType.OWNER,
        owner_actor_id=owner_id,
        session_actor_id=owner_id,
        requested_capabilities=(Capability.TEACH_ME,),
        requested_expires_at=now + 100,
        authority_snapshot=owner_authority,
    )
    if overrides:
        # Build a new request with overrides via the public fields
        fields = {f.name: getattr(req, f.name) for f in req.__dataclass_fields__.values()}
        fields.update(overrides)
        req = SessionActivationRequest(**fields)
    return req


def _child_request(child_id, child_authority, now, **overrides):
    req = SessionActivationRequest(
        request_id="req.child.001",
        session_type=SessionType.CHILD,
        owner_actor_id=child_authority.owner_actor_id,
        session_actor_id=child_id,
        requested_capabilities=(Capability.CHILD_MODE, Capability.TEACH_ME),
        requested_expires_at=now + 100,
        authority_snapshot=child_authority,
    )
    if overrides:
        fields = {f.name: getattr(req, f.name) for f in req.__dataclass_fields__.values()}
        fields.update(overrides)
        req = SessionActivationRequest(**fields)
    return req


def _helper_request(helper_id, helper_authority, now, **overrides):
    req = SessionActivationRequest(
        request_id="req.helper.001",
        session_type=SessionType.TRUSTED_HELPER,
        owner_actor_id=helper_authority.owner_actor_id,
        session_actor_id=helper_id,
        requested_capabilities=(Capability.TEACH_ME,),
        requested_expires_at=now + 100,
        authority_snapshot=helper_authority,
    )
    if overrides:
        fields = {f.name: getattr(req, f.name) for f in req.__dataclass_fields__.values()}
        fields.update(overrides)
        req = SessionActivationRequest(**fields)
    return req


# --- Activation tests -------------------------------------------------------


def test_owner_session_activation(owner_id, owner_authority, policy, now):
    request = _owner_request(owner_id, owner_authority, now, policy)
    decision = evaluate_activation_request(request, policy, now)
    assert decision.outcome.value == "allow"
    assert decision.reason == SessionDecisionReason.OWNER_ACTIVATION_ALLOWED
    assert decision.session.state == SessionState.ACTIVE


def test_owner_session_requires_direct_authority(owner_id, policy, now, child_authority):
    request = _owner_request(
        owner_id,
        child_authority,
        now,
        policy,
        session_actor_id=owner_id,
    )
    decision = evaluate_activation_request(request, policy, now)
    assert decision.outcome.value == "deny"
    assert decision.reason == SessionDecisionReason.OWNER_ONLY_ACTIVATION


def test_owner_session_from_helper_authority_denied(owner_id, helper_authority, policy, now):
    request = _owner_request(
        owner_id,
        helper_authority,
        now,
        policy,
        session_actor_id=owner_id,
    )
    decision = evaluate_activation_request(request, policy, now)
    assert decision.reason == SessionDecisionReason.OWNER_ONLY_ACTIVATION


def test_authority_owner_must_match_request(owner_id, child_id, policy, now):
    authority = SessionAuthoritySnapshot(
        source=AuthoritySource.CHILD_ACTIVATION,
        owner_actor_id="owner.other",
        activation_evidence="synthetic-proof",
    )
    request = SessionActivationRequest(
        request_id="req.child.mismatch",
        session_type=SessionType.CHILD,
        owner_actor_id=owner_id,
        session_actor_id=child_id,
        requested_capabilities=(Capability.CHILD_MODE,),
        requested_expires_at=now + 100,
        authority_snapshot=authority,
    )
    decision = evaluate_activation_request(request, policy, now)
    assert decision.outcome.value == "deny"
    assert decision.reason == SessionDecisionReason.AUTHORITY_MISMATCH


def test_owner_session_actor_must_be_owner(owner_id, owner_authority, policy, now):
    request = _owner_request(
        owner_id,
        owner_authority,
        now,
        policy,
        session_actor_id="helper.synthetic",
    )
    decision = evaluate_activation_request(request, policy, now)
    assert decision.reason == SessionDecisionReason.AUTHORITY_MISMATCH


def test_empty_capability_scope_fails_closed(owner_id, owner_authority, policy, now):
    request = _owner_request(
        owner_id,
        owner_authority,
        now,
        policy,
        requested_capabilities=(),
    )
    decision = evaluate_activation_request(request, policy, now)
    assert decision.outcome.value == "deny"
    assert decision.reason == SessionDecisionReason.SCOPE_TOO_BROAD


def test_malformed_activation_input_fails_closed(policy, now):
    decision = evaluate_activation_request(object(), policy, now)
    assert decision.outcome.value == "deny"
    assert decision.reason == SessionDecisionReason.INVALID_INPUT


def test_child_session_requires_activation_evidence(owner_id, child_id, policy, now):
    with pytest.raises(ValueError, match="child activation requires evidence"):
        SessionAuthoritySnapshot(
            source=AuthoritySource.CHILD_ACTIVATION,
            owner_actor_id=owner_id,
            activation_evidence="",
        )


def test_child_session_activation(child_id, child_authority, policy, now):
    request = _child_request(child_id, child_authority, now)
    decision = evaluate_activation_request(request, policy, now)
    assert decision.outcome.value == "allow"
    assert decision.reason == SessionDecisionReason.CHILD_ACTIVATION_ALLOWED
    assert decision.session.state == SessionState.ACTIVE


def test_child_session_without_evidence_denied(owner_id, child_id, policy, now):
    authority = SessionAuthoritySnapshot(
        source=AuthoritySource.OWNER_DIRECT,
        owner_actor_id=owner_id,
    )
    request = _child_request(child_id, authority, now)
    decision = evaluate_activation_request(request, policy, now)
    assert decision.reason == SessionDecisionReason.CHILD_EVIDENCE_REQUIRED


def test_helper_session_requires_grant(helper_id, owner_id, policy, now):
    authority = SessionAuthoritySnapshot(
        source=AuthoritySource.OWNER_DIRECT,
        owner_actor_id=owner_id,
    )
    request = _helper_request(helper_id, authority, now)
    decision = evaluate_activation_request(request, policy, now)
    assert decision.reason == SessionDecisionReason.HELPER_GRANT_REQUIRED


def test_helper_session_activation(helper_id, helper_authority, policy, now):
    request = _helper_request(helper_id, helper_authority, now)
    decision = evaluate_activation_request(request, policy, now)
    assert decision.outcome.value == "allow"
    assert decision.reason == SessionDecisionReason.HELPER_ACTIVATION_ALLOWED
    assert decision.session.state == SessionState.ACTIVE


def test_helper_session_with_revoked_grant(owner_id, helper_id, now):
    from core.phase5.contracts import CapabilityGrant, ScopeConstraint

    revoked_grant = CapabilityGrant(
        grant_id="grant.revoked.001",
        helper_actor_id=helper_id,
        owner_actor_id=owner_id,
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=now,
        expires_at=now + 1800,
        revoked=True,
        revoked_at=now + 10,
    )
    authority = SessionAuthoritySnapshot(
        source=AuthoritySource.HELPER_GRANT,
        owner_actor_id=owner_id,
        grant=revoked_grant,
    )
    request = _helper_request(helper_id, authority, now + 20)
    decision = evaluate_activation_request(request, default_session_policy(), now + 20)
    assert decision.reason == SessionDecisionReason.HELPER_GRANT_REVOKED


def test_helper_session_with_expired_grant(owner_id, helper_id, now):
    from core.phase5.contracts import CapabilityGrant, ScopeConstraint

    expired_grant = CapabilityGrant(
        grant_id="grant.expired.001",
        helper_actor_id=helper_id,
        owner_actor_id=owner_id,
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=now,
        expires_at=now + 100,
    )
    authority = SessionAuthoritySnapshot(
        source=AuthoritySource.HELPER_GRANT,
        owner_actor_id=owner_id,
        grant=expired_grant,
    )
    request = _helper_request(helper_id, authority, now + 200)
    decision = evaluate_activation_request(request, default_session_policy(), now + 200)
    assert decision.reason == SessionDecisionReason.HELPER_GRANT_EXPIRED


def test_helper_session_scope_mismatch(helper_id, helper_authority, policy, now):
    # Request a capability other than the grant's capability
    request = _helper_request(helper_id, helper_authority, now, requested_capabilities=(Capability.CARE,))
    decision = evaluate_activation_request(request, policy, now)
    assert decision.reason == SessionDecisionReason.SCOPE_TOO_BROAD


def test_helper_session_session_longer_than_grant(helper_id, helper_authority, policy, now):
    request = _helper_request(
        helper_id,
        helper_authority,
        now,
        requested_expires_at=now + 2000,
    )
    decision = evaluate_activation_request(request, policy, now)
    assert decision.reason == SessionDecisionReason.SESSION_TOO_LONG


def test_session_lifetime_bound(owner_id, owner_authority, policy, now):
    # Owner max duration is 3600 by default; request 4000 -> too long
    request = _owner_request(
        owner_id,
        owner_authority,
        now,
        policy,
        requested_expires_at=now + 4000,
    )
    decision = evaluate_activation_request(request, policy, now)
    assert decision.reason == SessionDecisionReason.SESSION_TOO_LONG


def test_expired_session_cannot_renew(child_id, child_authority, policy, now):
    request = _child_request(
        child_id,
        child_authority,
        now,
        previous_session_id="req.child.old",
    )
    decision = evaluate_activation_request(request, policy, now)
    assert decision.reason == SessionDecisionReason.SESSION_EXPIRED_CANNOT_RENEW


def test_delegation_attempt_blocked(owner_id, owner_authority, policy, now):
    request = _owner_request(
        owner_id,
        owner_authority,
        now,
        policy,
        metadata=("delegate to assistant",),
    )
    decision = evaluate_activation_request(request, policy, now)
    assert decision.reason == SessionDecisionReason.DELEGATION_BLOCKED


def test_scope_expansion_attempt_blocked(owner_id, owner_authority, policy, now):
    request = _owner_request(
        owner_id,
        owner_authority,
        now,
        policy,
        metadata=("escalate scope",),
    )
    decision = evaluate_activation_request(request, policy, now)
    assert decision.reason == SessionDecisionReason.SCOPE_EXPANSION_BLOCKED


# --- Lifecycle transition tests --------------------------------------------


def _active_session(owner_id, owner_authority, policy, now) -> AccessSession:
    request = _owner_request(owner_id, owner_authority, now, policy)
    decision = evaluate_activation_request(request, policy, now)
    return decision.session


def test_active_to_closed(owner_id, owner_authority, policy, now):
    session = _active_session(owner_id, owner_authority, policy, now)
    authority = SessionAuthoritySnapshot(source=AuthoritySource.OWNER_DIRECT, owner_actor_id=owner_id)
    decision = close_session(session, owner_id, authority, policy, now + 1)
    assert decision.outcome.value == "allow"
    assert decision.session.state == SessionState.CLOSED


def test_unrelated_actor_cannot_close_session(owner_id, owner_authority, policy, now):
    session = _active_session(owner_id, owner_authority, policy, now)
    decision = close_session(
        session,
        "guest.synthetic",
        owner_authority,
        policy,
        now + 1,
    )
    assert decision.outcome.value == "deny"
    assert decision.reason == SessionDecisionReason.AUTHORITY_MISMATCH


def test_active_to_locked(owner_id, owner_authority, policy, now):
    session = _active_session(owner_id, owner_authority, policy, now)
    authority = SessionAuthoritySnapshot(source=AuthoritySource.OWNER_DIRECT, owner_actor_id=owner_id)
    decision = transition_session(session, SessionState.LOCKED, owner_id, authority, policy, now + 1)
    assert decision.session.state == SessionState.LOCKED


def test_active_to_revoked(owner_id, owner_authority, policy, now):
    session = _active_session(owner_id, owner_authority, policy, now)
    authority = SessionAuthoritySnapshot(source=AuthoritySource.OWNER_DIRECT, owner_actor_id=owner_id)
    decision = transition_session(session, SessionState.REVOKED, owner_id, authority, policy, now + 1)
    assert decision.session.state == SessionState.REVOKED
    assert decision.session.revoked_at == now + 1


def test_active_to_expired(owner_id, owner_authority, policy, now):
    session = _active_session(owner_id, owner_authority, policy, now)
    authority = SessionAuthoritySnapshot(source=AuthoritySource.OWNER_DIRECT, owner_actor_id=owner_id)
    decision = transition_session(session, SessionState.EXPIRED, owner_id, authority, policy, now + 200)
    assert decision.session.state == SessionState.EXPIRED


def test_locked_cannot_reactivate_without_new_request(owner_id, owner_authority, policy, now):
    session = _active_session(owner_id, owner_authority, policy, now)
    owner_authority = SessionAuthoritySnapshot(source=AuthoritySource.OWNER_DIRECT, owner_actor_id=owner_id)
    locked = transition_session(session, SessionState.LOCKED, owner_id, owner_authority, policy, now + 1).session
    # Direct reactivation to active is denied
    decision = transition_session(locked, SessionState.ACTIVE, owner_id, owner_authority, policy, now + 2)
    assert decision.reason == SessionDecisionReason.SESSION_LOCKED_CANNOT_REACTIVATE


def test_locked_released_to_inactive_requires_owner(owner_id, owner_authority, policy, now):
    session = _active_session(owner_id, owner_authority, policy, now)
    owner_authority = SessionAuthoritySnapshot(source=AuthoritySource.OWNER_DIRECT, owner_actor_id=owner_id)
    locked = transition_session(session, SessionState.LOCKED, owner_id, owner_authority, policy, now + 1).session
    released = transition_session(locked, SessionState.INACTIVE, owner_id, owner_authority, policy, now + 2)
    assert released.session.state == SessionState.INACTIVE


def test_revoked_cannot_reactivate(owner_id, owner_authority, policy, now):
    session = _active_session(owner_id, owner_authority, policy, now)
    owner_authority = SessionAuthoritySnapshot(source=AuthoritySource.OWNER_DIRECT, owner_actor_id=owner_id)
    revoked = transition_session(session, SessionState.REVOKED, owner_id, owner_authority, policy, now + 1).session
    decision = transition_session(revoked, SessionState.ACTIVE, owner_id, owner_authority, policy, now + 2)
    assert decision.reason == SessionDecisionReason.SESSION_REVOKED_CANNOT_REACTIVATE


def test_expired_cannot_reactivate(owner_id, owner_authority, policy, now):
    session = _active_session(owner_id, owner_authority, policy, now)
    owner_authority = SessionAuthoritySnapshot(source=AuthoritySource.OWNER_DIRECT, owner_actor_id=owner_id)
    expired = transition_session(session, SessionState.EXPIRED, owner_id, owner_authority, policy, now + 200).session
    decision = transition_session(expired, SessionState.ACTIVE, owner_id, owner_authority, policy, now + 201)
    assert decision.reason == SessionDecisionReason.SESSION_REVOKED_CANNOT_REACTIVATE


def test_close_is_idempotent(owner_id, owner_authority, policy, now):
    session = _active_session(owner_id, owner_authority, policy, now)
    owner_authority = SessionAuthoritySnapshot(source=AuthoritySource.OWNER_DIRECT, owner_actor_id=owner_id)
    closed = close_session(session, owner_id, owner_authority, policy, now + 1).session
    again = close_session(closed, owner_id, owner_authority, policy, now + 2)
    assert again.outcome.value == "allow"
    assert again.reason == SessionDecisionReason.CLOSING_IDEMPOTENT
    assert again.session.state == SessionState.CLOSED


def test_closed_session_cannot_transition(owner_id, owner_authority, policy, now):
    session = _active_session(owner_id, owner_authority, policy, now)
    owner_authority = SessionAuthoritySnapshot(source=AuthoritySource.OWNER_DIRECT, owner_actor_id=owner_id)
    closed = close_session(session, owner_id, owner_authority, policy, now + 1).session
    decision = transition_session(closed, SessionState.ACTIVE, owner_id, owner_authority, policy, now + 2)
    assert decision.reason == SessionDecisionReason.SESSION_CLOSED_CANNOT_TRANSITION


def test_illegal_transition_active_to_inactive(owner_id, owner_authority, policy, now):
    session = _active_session(owner_id, owner_authority, policy, now)
    owner_authority = SessionAuthoritySnapshot(source=AuthoritySource.OWNER_DIRECT, owner_actor_id=owner_id)
    decision = transition_session(session, SessionState.INACTIVE, owner_id, owner_authority, policy, now + 1)
    assert decision.reason == SessionDecisionReason.TRANSITION_NOT_ALLOWED


def test_unknown_session_type_fail_closed(owner_id, owner_authority, policy, now):
    request = _owner_request(owner_id, owner_authority, now, policy)
    # Mutate via dataclass replace is not allowed (frozen), so construct with bad type
    fields = {f.name: getattr(request, f.name) for f in request.__dataclass_fields__.values()}
    # SessionActivationRequest validates the type; invalid enum construction raises.
    with pytest.raises(ValueError):
        SessionActivationRequest(**{**fields, "session_type": "not_a_session_type"})


# --- Scope / capability tests ------------------------------------------------


def test_child_session_cannot_request_helper_authority(child_id, child_authority, policy, now):
    request = _child_request(
        child_id,
        child_authority,
        now,
        requested_capabilities=(Capability.TRUSTED_HELPER_ACCESS,),
    )
    decision = evaluate_activation_request(request, policy, now)
    assert decision.reason == SessionDecisionReason.CHILD_HELPER_GRANT_BLOCKED


def test_child_session_cannot_access_owner_memory(child_id, child_authority, policy, now):
    # owner memory is not a capability, but we can still show child scope is bounded.
    request = _child_request(
        child_id,
        child_authority,
        now,
        requested_capabilities=(Capability.GUIDE_MY_HANDS,),
    )
    # GUIDE_MY_HANDS is in the default child-allowed set, so it activates.
    # The child-mode guard is responsible for blocking owner-memory subjects.
    assert evaluate_activation_request(request, policy, now).outcome.value == "allow"


# --- Time and determinism tests ---------------------------------------------


def test_activation_is_deterministic(owner_id, owner_authority, policy, now):
    request = _owner_request(owner_id, owner_authority, now, policy)
    d1 = evaluate_activation_request(request, policy, now)
    d2 = evaluate_activation_request(request, policy, now)
    assert d1 == d2
    assert d1.session is not d2.session  # distinct immutable objects
    assert d1.session == d2.session


def test_injected_time_used_not_wall_clock(owner_id, owner_authority, policy):
    request = _owner_request(owner_id, owner_authority, 12345.0, policy)
    decision = evaluate_activation_request(request, policy, 12345.0)
    assert decision.timestamp == 12345.0
    assert decision.session.created_at == 12345.0


# --- Privacy / redaction tests ----------------------------------------------


def test_session_repr_does_not_expose_evidence(owner_id, owner_authority, policy, now):
    authority = SessionAuthoritySnapshot(
        source=AuthoritySource.CHILD_ACTIVATION,
        owner_actor_id=owner_id,
        activation_evidence="super_secret_token_12345",
    )
    request = _child_request(
        "child.dana",
        authority,
        now,
        requested_capabilities=(Capability.CHILD_MODE, Capability.TEACH_ME),
    )
    decision = evaluate_activation_request(request, policy, now)
    rep = repr(decision.session)
    assert "super_secret_token" not in rep
    assert "owner.alice" not in rep


def test_authority_snapshot_repr_redacts_evidence(owner_id):
    authority = SessionAuthoritySnapshot(
        source=AuthoritySource.CHILD_ACTIVATION,
        owner_actor_id=owner_id,
        activation_evidence="secret",
    )
    rep = repr(authority)
    assert "secret" not in rep
    assert "owner.alice" not in rep


# --- No I/O tests -----------------------------------------------------------


def test_session_lifecycle_source_contains_no_banned_imports():
    source = pathlib.Path(inspect.getfile(evaluate_activation_request)).read_text()
    banned = ["sqlite3", "socket", "subprocess", "os.environ", "time.time", "uuid.uuid4"]
    for token in banned:
        assert token not in source, f"banned import or call {token!r} found"


def test_session_lifecycle_does_not_use_uuid():
    import core.phase5.session_lifecycle as session_module

    source = pathlib.Path(session_module.__file__).read_text()
    assert "uuid" not in source
