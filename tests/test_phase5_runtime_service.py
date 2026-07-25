"""Synthetic test coverage for Phase5RuntimeService coordinator and invariants."""

import pytest
from pathlib import Path

from core.action_audit import ActionAuditStore
from core.action_policy import Actor, ActorContext
from core.grants import GrantStore
from core.phase5.contracts import (
    Capability,
    CapabilityGrant,
    Outcome,
    Phase5Actor,
    Phase5ActorContext,
    Phase5Request,
    RiskLevel,
    ScopeConstraint,
)
from core.phase5.policy import Phase5ConsentStore, Phase5GrantStore, Phase5PolicyService
from core.phase5.runtime_guard import (
    Phase5RuntimeContext,
    Phase5RuntimeGuard,
    Phase5RuntimeRequest,
    RuntimeDecisionReason,
)
from core.phase5.runtime_service import Phase5RuntimeService
from core.phase5.session_lifecycle import (
    AccessSession,
    AuthoritySource,
    SessionActivationRequest,
    SessionAuthoritySnapshot,
    SessionDecisionReason,
    SessionPolicy,
    SessionState,
    SessionType,
)
from core.phase5.session_store import Phase5SessionStore


def _setup_service(tmp_path: Path, current_time: float = 1000.0) -> Phase5RuntimeService:
    grants = GrantStore(db_path=tmp_path / "core_grants.db")
    audit = ActionAuditStore(db_path=tmp_path / "core_audit.db")
    p5_grants = Phase5GrantStore(db_path=tmp_path / "p5_grants.db")
    p5_consents = Phase5ConsentStore(db_path=tmp_path / "p5_consents.db")

    policy_service = Phase5PolicyService(
        grants=grants,
        audit=audit,
        phase5_grants=p5_grants,
        phase5_consents=p5_consents,
        clock=lambda: current_time,
    )
    guard = Phase5RuntimeGuard(policy_service=policy_service)
    store = Phase5SessionStore(db_path=tmp_path / "sessions.db")

    return Phase5RuntimeService(
        policy_service=policy_service,
        runtime_guard=guard,
        session_store=store,
        clock=lambda: current_time,
    )


def test_runtime_service_construction_no_side_effects(tmp_path: Path):
    service = _setup_service(tmp_path)
    assert repr(service) == "Phase5RuntimeService()"


def test_activate_owner_session_and_authorize(tmp_path: Path):
    service = _setup_service(tmp_path, current_time=1000.0)

    # 1. Activate Owner Session
    auth = SessionAuthoritySnapshot(
        source=AuthoritySource.OWNER_DIRECT,
        owner_actor_id="owner_1",
    )
    act_req = SessionActivationRequest(
        request_id="req_act_1",
        session_type=SessionType.OWNER,
        owner_actor_id="owner_1",
        session_actor_id="owner_1",
        requested_capabilities=(Capability.TEACH_ME,),
        requested_expires_at=2000.0,
        authority_snapshot=auth,
    )
    actor_ctx = ActorContext(actor=Actor.OWNER, actor_id="owner_1", session_id="req_act_1")

    decision = service.activate_session(actor_ctx, act_req)
    assert decision.outcome == Outcome.ALLOW
    assert decision.session is not None
    assert decision.session.session_id == "req_act_1"

    # Verify session is persisted in store
    loaded = service.session_store.get_session("req_act_1")
    assert loaded is not None

    # 2. Authorize Runtime Request using loaded session
    rt_ctx = Phase5RuntimeContext(actor_context=actor_ctx)
    rt_req = Phase5RuntimeRequest(
        request_id="req_rt_1",
        capability=Capability.TEACH_ME,
        context=rt_ctx,
        action="propose_lesson",
        user_initiated=True,
        now=1100.0,
    )

    rt_dec = service.authorize(rt_req)
    assert rt_dec.outcome == Outcome.ALLOW
    assert rt_dec.reason in (RuntimeDecisionReason.OWNER_ALLOWED, RuntimeDecisionReason.POLICY_ALLOW)


def test_child_activation_and_hard_deny(tmp_path: Path):
    service = _setup_service(tmp_path, current_time=1000.0)

    auth = SessionAuthoritySnapshot(
        source=AuthoritySource.CHILD_ACTIVATION,
        owner_actor_id="owner_1",
        activation_evidence="owner_signed_evidence",
    )
    act_req = SessionActivationRequest(
        request_id="req_child_1",
        session_type=SessionType.CHILD,
        owner_actor_id="owner_1",
        session_actor_id="child_1",
        requested_capabilities=(Capability.CHILD_MODE,),
        requested_expires_at=1500.0,
        authority_snapshot=auth,
    )
    owner_ctx = ActorContext(actor=Actor.OWNER, actor_id="owner_1", session_id="owner-session")
    actor_ctx = ActorContext(actor=Actor.UNKNOWN, actor_id="child_1", session_id="req_child_1")

    # Only the owner transport identity may activate a child session.
    act_dec = service.activate_session(owner_ctx, act_req)
    assert act_dec.outcome == Outcome.ALLOW

    # Authorize child request attempting a hard-blocked action (purchase)
    rt_ctx = Phase5RuntimeContext(actor_context=actor_ctx)
    rt_req = Phase5RuntimeRequest(
        request_id="req_rt_child_purchase",
        capability=Capability.CHILD_MODE,
        context=rt_ctx,
        action="buy_game_item",
        now=1100.0,
    )

    rt_dec = service.authorize(rt_req)
    assert rt_dec.outcome == Outcome.DENY
    assert rt_dec.reason == RuntimeDecisionReason.CHILD_HARD_DENY


def test_helper_grant_revocation_invalidates_session_immediately(tmp_path: Path):
    service = _setup_service(tmp_path, current_time=1000.0)

    # 1. Owner issues helper grant
    scope = ScopeConstraint(capability=Capability.TEACH_ME)
    grant = CapabilityGrant(
        grant_id="grant_h1",
        helper_actor_id="helper_1",
        owner_actor_id="owner_1",
        capability=Capability.TEACH_ME,
        scope=scope,
        issued_at=1000.0,
        expires_at=3000.0,
    )
    service.policy_service.phase5_grants.issue(grant)

    # 2. Activate Helper Session
    auth = SessionAuthoritySnapshot(
        source=AuthoritySource.HELPER_GRANT,
        owner_actor_id="owner_1",
        grant=grant,
    )
    act_req = SessionActivationRequest(
        request_id="sess_helper_1",
        session_type=SessionType.TRUSTED_HELPER,
        owner_actor_id="owner_1",
        session_actor_id="helper_1",
        requested_capabilities=(Capability.TEACH_ME,),
        requested_expires_at=2000.0,
        authority_snapshot=auth,
    )
    owner_actor_ctx = ActorContext(actor=Actor.OWNER, actor_id="owner_1", session_id="owner-session")
    helper_actor_ctx = ActorContext(actor=Actor.UNKNOWN, actor_id="helper_1", session_id="sess_helper_1")
    act_dec = service.activate_session(owner_actor_ctx, act_req)
    assert act_dec.outcome == Outcome.ALLOW

    # 3. Revoke Helper Access
    rev_ok = service.revoke_helper_access("grant_h1", owner_actor_id="owner_1")
    assert rev_ok is True

    # 4. Verify Helper Session state is REVOKED
    sess_after = service.session_store.get_session("sess_helper_1")
    assert sess_after.state == SessionState.REVOKED

    # 5. Authorize request with revoked session attached -> SESSION_REVOKED
    rt_ctx = Phase5RuntimeContext(actor_context=helper_actor_ctx)
    rt_req_revoked = Phase5RuntimeRequest(
        request_id="req_rt_revoked",
        capability=Capability.TEACH_ME,
        context=rt_ctx,
        action="propose_lesson",
        access_session=sess_after,
        now=1200.0,
    )
    rt_dec = service.authorize(rt_req_revoked)
    assert rt_dec.reason == RuntimeDecisionReason.SESSION_REVOKED


def test_list_owner_sessions_owner_only(tmp_path: Path):
    service = _setup_service(tmp_path, current_time=1000.0)

    auth = SessionAuthoritySnapshot(source=AuthoritySource.OWNER_DIRECT, owner_actor_id="owner_1")
    act_req = SessionActivationRequest(
        request_id="sess_own_list",
        session_type=SessionType.OWNER,
        owner_actor_id="owner_1",
        session_actor_id="owner_1",
        requested_capabilities=(Capability.TEACH_ME,),
        requested_expires_at=2000.0,
        authority_snapshot=auth,
    )
    service.activate_session(ActorContext(actor=Actor.OWNER, actor_id="owner_1", session_id="sess_own_list"), act_req)

    # Owner query -> returned
    list_owner = service.list_owner_sessions("owner_1", caller_actor_id="owner_1")
    assert len(list_owner) == 1

    # Non-owner query -> empty
    list_other = service.list_owner_sessions("owner_1", caller_actor_id="intruder")
    assert len(list_other) == 0


def test_expire_due_sessions(tmp_path: Path):
    service = _setup_service(tmp_path, current_time=1000.0)

    auth = SessionAuthoritySnapshot(source=AuthoritySource.OWNER_DIRECT, owner_actor_id="owner_1")
    act_req = SessionActivationRequest(
        request_id="sess_expire_due",
        session_type=SessionType.OWNER,
        owner_actor_id="owner_1",
        session_actor_id="owner_1",
        requested_capabilities=(Capability.TEACH_ME,),
        requested_expires_at=1200.0,
        authority_snapshot=auth,
    )
    service.activate_session(ActorContext(actor=Actor.OWNER, actor_id="owner_1", session_id="sess_expire_due"), act_req)

    # At time 1300 -> expires
    service._clock = lambda: 1300.0
    expired_count = service.expire_due_sessions()
    assert expired_count == 1

    sess = service.session_store.get_session("sess_expire_due")
    assert sess.state == SessionState.EXPIRED


def test_injected_clock_consistency(tmp_path: Path):
    current_time = 1500.0
    service = _setup_service(tmp_path, current_time=current_time)

    assert service.now() == 1500.0


def test_non_owner_cannot_activate_scoped_session(tmp_path: Path):
    service = _setup_service(tmp_path, current_time=1000.0)
    request = SessionActivationRequest(
        request_id="sess-child-denied",
        session_type=SessionType.CHILD,
        owner_actor_id="owner_1",
        session_actor_id="child_1",
        requested_capabilities=(Capability.TEACH_ME,),
        requested_expires_at=1200.0,
        authority_snapshot=SessionAuthoritySnapshot(
            source=AuthoritySource.CHILD_ACTIVATION,
            owner_actor_id="owner_1",
            activation_evidence="claimed-owner-evidence",
        ),
    )

    decision = service.activate_session(
        ActorContext(actor=Actor.UNKNOWN, actor_id="child_1", session_id="child-session"),
        request,
    )

    assert decision.outcome is Outcome.DENY
    assert decision.reason is SessionDecisionReason.AUTHORITY_MISMATCH
    assert service.session_store.get_session("sess-child-denied") is None


def test_owner_cannot_revoke_another_owners_helper_grant(tmp_path: Path):
    service = _setup_service(tmp_path, current_time=1000.0)
    grant = CapabilityGrant(
        grant_id="grant-other-owner",
        helper_actor_id="helper_1",
        owner_actor_id="owner_2",
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=900.0,
        expires_at=1500.0,
    )
    service.policy_service.phase5_grants.issue(grant)

    assert service.revoke_helper_access(grant.grant_id, "owner_1") is False
    assert service.policy_service.get_helper_grant(grant.grant_id).revoked is False
