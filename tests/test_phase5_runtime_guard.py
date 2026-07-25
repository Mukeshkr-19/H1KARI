"""Tests for core.phase5.runtime_guard.

All fixtures are synthetic. Tests use temporary SQLite databases and do not
contact external systems.
"""

from __future__ import annotations

import pathlib
from typing import Optional

import pytest

from core.action_audit import ActionAuditStore
from core.action_policy import Actor, ActorContext
from core.grants import GrantStore
from core.phase5.child_mode import ChildModeDecisionReason
from core.phase5.contracts import (
    Capability,
    CapabilityGrant,
    ConsentRecord,
    Outcome,
    Phase5ActorContext,
    ScopeConstraint,
)
from core.phase5.policy import (
    Phase5ConsentStore,
    Phase5GrantStore,
    Phase5PolicyService,
)
from core.phase5.runtime_guard import (
    Phase5RuntimeContext,
    Phase5RuntimeDecision,
    Phase5RuntimeGuard,
    Phase5RuntimeRequest,
    RuntimeDecisionReason,
)
from core.phase5.session_lifecycle import (
    AccessSession,
    AuthoritySource,
    SessionActivationRequest,
    SessionAuthoritySnapshot,
    SessionState,
    SessionType,
    close_session,
    default_session_policy,
    evaluate_activation_request,
    transition_session,
)


@pytest.fixture
def now() -> float:
    return 1000.0


@pytest.fixture
def tmp_db(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "test.db"


@pytest.fixture
def policy_service(tmp_path: pathlib.Path) -> Phase5PolicyService:
    grants = GrantStore(tmp_path / "grants.db")
    audit = ActionAuditStore(tmp_path / "audit.db")
    phase5_grants = Phase5GrantStore(tmp_path / "phase5_grants.db")
    phase5_consents = Phase5ConsentStore(tmp_path / "phase5_consents.db")
    return Phase5PolicyService(
        grants,
        audit,
        phase5_grants,
        phase5_consents,
        clock=lambda: 1000.0,
        id_factory=lambda: "audit.001",
    )


@pytest.fixture
def guard(policy_service: Phase5PolicyService) -> Phase5RuntimeGuard:
    return Phase5RuntimeGuard(policy_service)


@pytest.fixture
def owner_actor() -> ActorContext:
    return ActorContext("owner.alpha", Actor.OWNER, "session.owner")


@pytest.fixture
def child_actor() -> ActorContext:
    return ActorContext("child.beta", Actor.GUEST, "session.child")


@pytest.fixture
def helper_actor() -> ActorContext:
    return ActorContext("helper.gamma", Actor.GUEST, "session.helper")


@pytest.fixture
def owner_authority() -> SessionAuthoritySnapshot:
    return SessionAuthoritySnapshot(
        source=AuthoritySource.OWNER_DIRECT,
        owner_actor_id="owner.alpha",
    )


@pytest.fixture
def child_authority() -> SessionAuthoritySnapshot:
    return SessionAuthoritySnapshot(
        source=AuthoritySource.CHILD_ACTIVATION,
        owner_actor_id="owner.alpha",
        activation_evidence="owner_pin_12345",
    )


def _helper_grant(now: float) -> CapabilityGrant:
    return CapabilityGrant(
        grant_id="grant.001",
        helper_actor_id="helper.gamma",
        owner_actor_id="owner.alpha",
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(
            capability=Capability.TEACH_ME,
            data_subject="child",
            allowed_actions=("read", "propose"),
        ),
        issued_at=now,
        expires_at=now + 1800,
    )


def _owner_session(owner_actor: ActorContext, owner_authority: SessionAuthoritySnapshot, now: float) -> AccessSession:
    request = SessionActivationRequest(
        request_id="req.owner.session",
        session_type=SessionType.OWNER,
        owner_actor_id=owner_actor.actor_id,
        session_actor_id=owner_actor.actor_id,
        requested_capabilities=(Capability.TEACH_ME, Capability.GUIDE_MY_HANDS),
        requested_expires_at=now + 100,
        authority_snapshot=owner_authority,
    )
    decision = evaluate_activation_request(request, default_session_policy(), now)
    assert decision.session is not None
    return decision.session


def _child_session(child_actor: ActorContext, child_authority: SessionAuthoritySnapshot, now: float) -> AccessSession:
    request = SessionActivationRequest(
        request_id="req.child.session",
        session_type=SessionType.CHILD,
        owner_actor_id=child_authority.owner_actor_id,
        session_actor_id=child_actor.actor_id,
        requested_capabilities=(Capability.CHILD_MODE, Capability.TEACH_ME, Capability.GUIDE_MY_HANDS),
        requested_expires_at=now + 100,
        authority_snapshot=child_authority,
    )
    decision = evaluate_activation_request(request, default_session_policy(), now)
    assert decision.session is not None
    return decision.session


def _helper_session(helper_actor: ActorContext, now: float, policy_service: Phase5PolicyService) -> AccessSession:
    grant = _helper_grant(now)
    policy_service.phase5_grants.issue(grant)
    authority = SessionAuthoritySnapshot(
        source=AuthoritySource.HELPER_GRANT,
        owner_actor_id="owner.alpha",
        grant=grant,
    )
    request = SessionActivationRequest(
        request_id="req.helper.session",
        session_type=SessionType.TRUSTED_HELPER,
        owner_actor_id="owner.alpha",
        session_actor_id=helper_actor.actor_id,
        requested_capabilities=(Capability.TEACH_ME,),
        requested_expires_at=now + 100,
        authority_snapshot=authority,
    )
    decision = evaluate_activation_request(request, default_session_policy(), now)
    assert decision.session is not None
    return decision.session


def _runtime_request(
    request_id: str,
    capability: Capability,
    actor: ActorContext,
    now: float,
    session: Optional[AccessSession] = None,
    **kwargs: object,
) -> Phase5RuntimeRequest:
    return Phase5RuntimeRequest(
        request_id=request_id,
        capability=capability,
        context=Phase5RuntimeContext(actor_context=actor),
        now=now,
        access_session=session,
        **kwargs,  # type: ignore[arg-type]
    )


# --- Owner tests ---------------------------------------------------------------


def test_owner_low_risk_allow(guard: Phase5RuntimeGuard, owner_actor: ActorContext, now: float) -> None:
    request = _runtime_request("req.owner.teach", Capability.TEACH_ME, owner_actor, now)
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.ALLOW
    assert decision.audit_id is not None
    assert decision.reason is RuntimeDecisionReason.POLICY_ALLOW


def test_owner_approval_required_capability(guard: Phase5RuntimeGuard, owner_actor: ActorContext, now: float) -> None:
    request = _runtime_request(
        "req.owner.guide",
        Capability.GUIDE_MY_HANDS,
        owner_actor,
        now,
        action="execute_step",
    )
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.REQUIRE_APPROVAL
    assert decision.reason is RuntimeDecisionReason.POLICY_APPROVAL_REQUIRED
    assert decision.approval_required is True


# --- Child tests ---------------------------------------------------------------


def test_child_safe_request(guard: Phase5RuntimeGuard, child_actor: ActorContext, child_authority: SessionAuthoritySnapshot, now: float) -> None:
    session = _child_session(child_actor, child_authority, now)
    request = _runtime_request(
        "req.child.safe",
        Capability.TEACH_ME,
        child_actor,
        now,
        session,
        action="educational",
        data_subject="child",
    )
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.ALLOW
    assert decision.reason is RuntimeDecisionReason.POLICY_ALLOW
    assert decision.audit_id is not None


@pytest.mark.parametrize(
    ("category", "expected_reason"),
    [
        ("owner_memory", ChildModeDecisionReason.OWNER_MEMORY_BLOCKED),
        ("purchase", ChildModeDecisionReason.PURCHASE_BLOCKED),
        ("external_call", ChildModeDecisionReason.COMMUNICATION_BLOCKED),
        ("email", ChildModeDecisionReason.COMMUNICATION_BLOCKED),
        ("dangerous", ChildModeDecisionReason.DANGEROUS_BLOCKED),
        ("weapon", ChildModeDecisionReason.WEAPON_HAZARD_BLOCKED),
        ("audit_bypass", ChildModeDecisionReason.AUDIT_BYPASS_BLOCKED),
        ("approval_bypass", ChildModeDecisionReason.APPROVAL_BYPASS_BLOCKED),
        ("identity_bypass", ChildModeDecisionReason.IDENTITY_BYPASS_BLOCKED),
        ("grant_creation", ChildModeDecisionReason.GRANT_CREATION_BLOCKED),
        ("policy_weakening", ChildModeDecisionReason.POLICY_WEAKENING_BLOCKED),
        ("unrestricted_browsing", ChildModeDecisionReason.BROWSING_DOWNLOAD_BLOCKED),
        ("secret_access", ChildModeDecisionReason.SECRET_CREDENTIAL_BLOCKED),
    ],
)
def test_child_hard_deny_category(
    guard: Phase5RuntimeGuard,
    child_actor: ActorContext,
    child_authority: SessionAuthoritySnapshot,
    now: float,
    category: str,
    expected_reason: ChildModeDecisionReason,
) -> None:
    session = _child_session(child_actor, child_authority, now)
    request = _runtime_request(
        "req.child.deny",
        Capability.TEACH_ME,
        child_actor,
        now,
        session,
        action=category,
    )
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.CHILD_HARD_DENY
    assert decision.child_reason is expected_reason
    assert decision.audit_id is not None


def test_child_ambiguous_requires_approval(
    guard: Phase5RuntimeGuard,
    child_actor: ActorContext,
    child_authority: SessionAuthoritySnapshot,
    now: float,
) -> None:
    session = _child_session(child_actor, child_authority, now)
    request = _runtime_request(
        "req.child.ambiguous",
        Capability.TEACH_ME,
        child_actor,
        now,
        session,
        action="unknown_thing",
        data_subject="child",
    )
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.REQUIRE_APPROVAL
    assert decision.reason is RuntimeDecisionReason.CHILD_AMBIGUOUS_REQUIRES_APPROVAL
    assert decision.audit_id is not None


def test_child_session_required(guard: Phase5RuntimeGuard, child_actor: ActorContext, now: float) -> None:
    request = _runtime_request("req.child.nosession", Capability.TEACH_ME, child_actor, now)
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.SESSION_REQUIRED


def test_child_wrong_session_actor(guard: Phase5RuntimeGuard, child_actor: ActorContext, child_authority: SessionAuthoritySnapshot, now: float) -> None:
    session = _child_session(child_actor, child_authority, now)
    wrong_actor = ActorContext("other.child", Actor.GUEST, "session.child")
    request = _runtime_request("req.child.wrong", Capability.TEACH_ME, wrong_actor, now, session)
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.SESSION_ACTOR_MISMATCH


def test_child_expired_session(guard: Phase5RuntimeGuard, child_actor: ActorContext, child_authority: SessionAuthoritySnapshot, now: float) -> None:
    session = _child_session(child_actor, child_authority, now)
    request = _runtime_request("req.child.expired", Capability.TEACH_ME, child_actor, now + 200, session)
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.SESSION_EXPIRED


def test_child_locked_session(guard: Phase5RuntimeGuard, child_actor: ActorContext, child_authority: SessionAuthoritySnapshot, now: float) -> None:
    session = _child_session(child_actor, child_authority, now)
    owner_authority = SessionAuthoritySnapshot(source=AuthoritySource.OWNER_DIRECT, owner_actor_id="owner.alpha")
    locked = transition_session(session, SessionState.LOCKED, "owner.alpha", owner_authority, default_session_policy(), now + 1).session
    request = _runtime_request("req.child.locked", Capability.TEACH_ME, child_actor, now + 2, locked)
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.SESSION_LOCKED


def test_child_revoked_session(guard: Phase5RuntimeGuard, child_actor: ActorContext, child_authority: SessionAuthoritySnapshot, now: float) -> None:
    session = _child_session(child_actor, child_authority, now)
    owner_authority = SessionAuthoritySnapshot(source=AuthoritySource.OWNER_DIRECT, owner_actor_id="owner.alpha")
    revoked = transition_session(session, SessionState.REVOKED, "owner.alpha", owner_authority, default_session_policy(), now + 1).session
    request = _runtime_request("req.child.revoked", Capability.TEACH_ME, child_actor, now + 2, revoked)
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.SESSION_REVOKED


def test_child_closed_session(guard: Phase5RuntimeGuard, child_actor: ActorContext, child_authority: SessionAuthoritySnapshot, now: float) -> None:
    session = _child_session(child_actor, child_authority, now)
    owner_authority = SessionAuthoritySnapshot(source=AuthoritySource.OWNER_DIRECT, owner_actor_id="owner.alpha")
    closed = close_session(session, "owner.alpha", owner_authority, default_session_policy(), now + 1).session
    request = _runtime_request("req.child.closed", Capability.TEACH_ME, child_actor, now + 2, closed)
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.SESSION_CLOSED


def test_child_capability_not_in_session(guard: Phase5RuntimeGuard, child_actor: ActorContext, child_authority: SessionAuthoritySnapshot, now: float) -> None:
    session = _child_session(child_actor, child_authority, now)
    request = _runtime_request("req.child.scope", Capability.CARE, child_actor, now, session)
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.CAPABILITY_NOT_IN_SESSION


def test_directly_constructed_overlong_child_session_is_denied(
    guard: Phase5RuntimeGuard,
    child_actor: ActorContext,
    child_authority: SessionAuthoritySnapshot,
    now: float,
) -> None:
    session = AccessSession(
        session_id="session.overlong",
        session_type=SessionType.CHILD,
        owner_actor_id="owner.alpha",
        session_actor_id=child_actor.actor_id,
        state=SessionState.ACTIVE,
        created_at=now,
        expires_at=now + default_session_policy().child_max_duration_seconds + 1,
        capabilities=(Capability.TEACH_ME,),
        authority_snapshot=child_authority,
        transitions=(),
    )
    decision = guard.authorize(
        _runtime_request("req.child.overlong", Capability.TEACH_ME, child_actor, now, session)
    )
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.SESSION_POLICY_VIOLATION


def test_session_authority_source_must_match_session_type(
    guard: Phase5RuntimeGuard,
    child_actor: ActorContext,
    owner_authority: SessionAuthoritySnapshot,
    now: float,
) -> None:
    session = AccessSession(
        session_id="session.wrongauthority",
        session_type=SessionType.CHILD,
        owner_actor_id="owner.alpha",
        session_actor_id=child_actor.actor_id,
        state=SessionState.ACTIVE,
        created_at=now,
        expires_at=now + 100,
        capabilities=(Capability.TEACH_ME,),
        authority_snapshot=owner_authority,
        transitions=(),
    )
    decision = guard.authorize(
        _runtime_request("req.child.authority", Capability.TEACH_ME, child_actor, now, session)
    )
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.SESSION_AUTHORITY_MISMATCH


# --- Helper tests --------------------------------------------------------------


def test_helper_valid_scoped_request(
    guard: Phase5RuntimeGuard,
    helper_actor: ActorContext,
    policy_service: Phase5PolicyService,
    now: float,
) -> None:
    session = _helper_session(helper_actor, now, policy_service)
    request = _runtime_request(
        "req.helper.valid",
        Capability.TEACH_ME,
        helper_actor,
        now,
        session,
        action="read",
        data_subject="child",
    )
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.ALLOW
    assert decision.reason is RuntimeDecisionReason.POLICY_ALLOW
    assert decision.audit_id is not None


def test_helper_session_required(guard: Phase5RuntimeGuard, helper_actor: ActorContext, now: float) -> None:
    request = _runtime_request("req.helper.nosession", Capability.TEACH_ME, helper_actor, now)
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.SESSION_REQUIRED


def test_helper_actor_mismatch(
    guard: Phase5RuntimeGuard,
    helper_actor: ActorContext,
    policy_service: Phase5PolicyService,
    now: float,
) -> None:
    session = _helper_session(helper_actor, now, policy_service)
    wrong_actor = ActorContext("other.helper", Actor.GUEST, "session.helper")
    request = _runtime_request("req.helper.mismatch", Capability.TEACH_ME, wrong_actor, now, session)
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.SESSION_ACTOR_MISMATCH


def test_helper_owner_mismatch(
    guard: Phase5RuntimeGuard,
    helper_actor: ActorContext,
    policy_service: Phase5PolicyService,
    now: float,
) -> None:
    # Issue a grant whose owner differs from the session's claimed owner.
    bad_grant = CapabilityGrant(
        grant_id="grant.badowner",
        helper_actor_id="helper.gamma",
        owner_actor_id="owner.other",
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME, data_subject="child"),
        issued_at=now,
        expires_at=now + 1000,
    )
    policy_service.phase5_grants.issue(bad_grant)
    authority = SessionAuthoritySnapshot(
        source=AuthoritySource.HELPER_GRANT,
        owner_actor_id="owner.alpha",
        grant=bad_grant,
    )
    # Build the session directly so the authority snapshot owner matches the
    # session owner while the stored grant owner is different.
    session = AccessSession(
        session_id="session.badowner",
        session_type=SessionType.TRUSTED_HELPER,
        owner_actor_id="owner.alpha",
        session_actor_id=helper_actor.actor_id,
        state=SessionState.ACTIVE,
        created_at=now,
        expires_at=now + 100,
        capabilities=(Capability.TEACH_ME,),
        authority_snapshot=authority,
        transitions=(),
    )
    request_rt = _runtime_request("req.helper.owner", Capability.TEACH_ME, helper_actor, now, session)
    decision = guard.authorize(request_rt)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.HELPER_SCOPE_MISMATCH


def test_helper_expired_grant(
    guard: Phase5RuntimeGuard,
    helper_actor: ActorContext,
    policy_service: Phase5PolicyService,
    now: float,
) -> None:
    # Issue a grant that expires quickly. Use a longer-lived snapshot to create
    # an session that is still active, while the stored grant is expired.
    from dataclasses import replace

    short_grant = CapabilityGrant(
        grant_id="grant.short",
        helper_actor_id="helper.gamma",
        owner_actor_id="owner.alpha",
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME, data_subject="child"),
        issued_at=now,
        expires_at=now + 50,
    )
    policy_service.phase5_grants.issue(short_grant)
    long_grant = replace(short_grant, expires_at=now + 200)
    authority = SessionAuthoritySnapshot(
        source=AuthoritySource.HELPER_GRANT,
        owner_actor_id="owner.alpha",
        grant=long_grant,
    )
    session_request = SessionActivationRequest(
        request_id="req.helper.short",
        session_type=SessionType.TRUSTED_HELPER,
        owner_actor_id="owner.alpha",
        session_actor_id=helper_actor.actor_id,
        requested_capabilities=(Capability.TEACH_ME,),
        requested_expires_at=now + 100,
        authority_snapshot=authority,
    )
    session = evaluate_activation_request(session_request, default_session_policy(), now).session
    assert session is not None
    # At now+60 the stored grant is expired, but the session is still active.
    request = _runtime_request("req.helper.expired", Capability.TEACH_ME, helper_actor, now + 60, session)
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.HELPER_GRANT_EXPIRED


def test_helper_revoked_grant(
    guard: Phase5RuntimeGuard,
    helper_actor: ActorContext,
    policy_service: Phase5PolicyService,
    now: float,
) -> None:
    session = _helper_session(helper_actor, now, policy_service)
    policy_service.revoke_helper_grant("grant.001")
    request = _runtime_request("req.helper.revoked", Capability.TEACH_ME, helper_actor, now, session)
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.HELPER_GRANT_REVOKED


def test_helper_data_subject_mismatch(
    guard: Phase5RuntimeGuard,
    helper_actor: ActorContext,
    policy_service: Phase5PolicyService,
    now: float,
) -> None:
    session = _helper_session(helper_actor, now, policy_service)
    request = _runtime_request(
        "req.helper.scope",
        Capability.TEACH_ME,
        helper_actor,
        now,
        session,
        data_subject="owner",
    )
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.HELPER_SCOPE_MISMATCH


def test_helper_action_mismatch(
    guard: Phase5RuntimeGuard,
    helper_actor: ActorContext,
    policy_service: Phase5PolicyService,
    now: float,
) -> None:
    session = _helper_session(helper_actor, now, policy_service)
    request = _runtime_request(
        "req.helper.action",
        Capability.TEACH_ME,
        helper_actor,
        now,
        session,
        action="delete",
        data_subject="child",
    )
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.HELPER_SCOPE_MISMATCH


def test_helper_delegation_blocked(
    guard: Phase5RuntimeGuard,
    helper_actor: ActorContext,
    policy_service: Phase5PolicyService,
    now: float,
) -> None:
    session = _helper_session(helper_actor, now, policy_service)
    request = _runtime_request(
        "req.helper.delegate",
        Capability.TEACH_ME,
        helper_actor,
        now,
        session,
        action="read",
        data_subject="child",
        metadata=("delegate to another helper",),
    )
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY
    assert decision.audit_id is not None


def test_helper_renewal_blocked(
    guard: Phase5RuntimeGuard,
    helper_actor: ActorContext,
    policy_service: Phase5PolicyService,
    now: float,
) -> None:
    session = _helper_session(helper_actor, now, policy_service)
    request = _runtime_request(
        "req.helper.renew",
        Capability.TEACH_ME,
        helper_actor,
        now,
        session,
        action="read",
        data_subject="child",
        metadata=("renew grant",),
    )
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY
    assert decision.audit_id is not None


def test_helper_capability_not_in_session(
    guard: Phase5RuntimeGuard,
    helper_actor: ActorContext,
    policy_service: Phase5PolicyService,
    now: float,
) -> None:
    session = _helper_session(helper_actor, now, policy_service)
    request = _runtime_request("req.helper.cap", Capability.CARE, helper_actor, now, session)
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.CAPABILITY_NOT_IN_SESSION


# --- Fail-closed / structural tests -------------------------------------------


def test_denial_cannot_be_elevated(
    guard: Phase5RuntimeGuard,
    child_actor: ActorContext,
    child_authority: SessionAuthoritySnapshot,
    now: float,
) -> None:
    session = _child_session(child_actor, child_authority, now)
    request = _runtime_request(
        "req.child.elevate",
        Capability.TEACH_ME,
        child_actor,
        now,
        session,
        action="purchase",
    )
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY


def test_approval_not_silently_elevated_to_allow(
    guard: Phase5RuntimeGuard,
    child_actor: ActorContext,
    child_authority: SessionAuthoritySnapshot,
    now: float,
) -> None:
    session = _child_session(child_actor, child_authority, now)
    request = _runtime_request(
        "req.child.approval",
        Capability.TEACH_ME,
        child_actor,
        now,
        session,
        action="unknown_thing",
    )
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.REQUIRE_APPROVAL
    assert decision.approval_required is True


def test_malformed_request_denied(guard: Phase5RuntimeGuard, owner_actor: ActorContext, now: float) -> None:
    # Passing a non-request object should fail structural validation and be denied.
    decision = guard.authorize(object())  # type: ignore[arg-type]
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.INVALID_INPUT


def test_invalid_injected_time_denied(guard: Phase5RuntimeGuard, owner_actor: ActorContext, now: float) -> None:
    request = Phase5RuntimeRequest(
        request_id="req.badtime",
        capability=Capability.TEACH_ME,
        context=Phase5RuntimeContext(actor_context=owner_actor),
        now=float("nan"),
    )
    decision = guard.authorize(request)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is RuntimeDecisionReason.INVALID_INPUT


# --- Audit tests ---------------------------------------------------------------


def test_audit_id_present_for_all_valid_requests(guard: Phase5RuntimeGuard, owner_actor: ActorContext, now: float) -> None:
    request = _runtime_request("req.audit", Capability.TEACH_ME, owner_actor, now)
    decision = guard.authorize(request)
    assert decision.audit_id is not None


def test_audit_written_exactly_once(
    guard: Phase5RuntimeGuard,
    policy_service: Phase5PolicyService,
    owner_actor: ActorContext,
    now: float,
) -> None:
    request = _runtime_request("req.audit.once", Capability.TEACH_ME, owner_actor, now)
    guard.authorize(request)
    records = policy_service.audit.list_recent(100)
    assert len([r for r in records if r.task_id == "req.audit.once"]) == 1


def test_audit_payload_contains_no_private_content(
    guard: Phase5RuntimeGuard,
    policy_service: Phase5PolicyService,
    owner_actor: ActorContext,
    now: float,
) -> None:
    request = _runtime_request(
        "req.audit.private",
        Capability.TEACH_ME,
        owner_actor,
        now,
        resource="super_secret_resource_123",
    )
    guard.authorize(request)
    records = policy_service.audit.list_recent(1)
    assert len(records) == 1
    assert "super_secret_resource_123" not in (records[0].resource_ref or "")
    assert records[0].resource_ref.startswith("sha256.")


def test_runtime_guard_uses_request_time_for_policy_evaluation(
    tmp_path: pathlib.Path,
    owner_actor: ActorContext,
) -> None:
    service = Phase5PolicyService(
        GrantStore(tmp_path / "g.db"),
        ActionAuditStore(tmp_path / "a.db"),
        Phase5GrantStore(tmp_path / "p5g.db"),
        Phase5ConsentStore(tmp_path / "p5c.db"),
        clock=lambda: 12345.0,
        id_factory=lambda: "runtime.audit.001",
    )
    guard = Phase5RuntimeGuard(service)
    request = _runtime_request("req.det", Capability.TEACH_ME, owner_actor, 12345.0)
    decision = guard.authorize(request)
    assert decision.audit_id is not None
    records = service.audit.list_recent(1)
    assert records[0].audit_id == decision.audit_id
    assert decision.policy_decision is not None
    assert decision.policy_decision.granted_at == 12345.0


# --- Environment / safety tests ------------------------------------------------


def test_runtime_guard_source_contains_no_banned_imports() -> None:
    import inspect
    import pathlib

    source = pathlib.Path(inspect.getfile(Phase5RuntimeGuard)).read_text()
    banned = ["sqlite3", "socket", "subprocess", "os.environ", "time.time", "uuid.uuid4"]
    for token in banned:
        assert token not in source, f"banned import or call {token!r} found"


def test_runtime_guard_does_not_use_uuid() -> None:
    import core.phase5.runtime_guard as runtime_module
    import pathlib

    source = pathlib.Path(runtime_module.__file__).read_text()
    assert "uuid" not in source
