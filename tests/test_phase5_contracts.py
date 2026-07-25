"""Tests for core.phase5.contracts."""

from __future__ import annotations

import math
import pathlib

import pytest

from core.action_policy import Actor, ActorContext
from core.phase5.contracts import (
    ACTOR_PROHIBITED,
    CAPABILITY_REQUIRES_APPROVAL,
    CAPABILITY_RISK,
    Capability,
    CapabilityGrant,
    ConsentRecord,
    DecisionReason,
    Outcome,
    Phase5Actor,
    Phase5ActorContext,
    Phase5Decision,
    Phase5Request,
    RiskLevel,
    ScopeConstraint,
    evaluate_phase5_request,
    check_approval_bypass,
    check_audit_bypass,
    check_brain_write_denied,
)


# --- Fixtures -----------------------------------------------------------------


@pytest.fixture
def owner_context() -> Phase5ActorContext:
    return Phase5ActorContext(
        actor_id="owner_1",
        actor=Phase5Actor.OWNER,
        session_id="session_1",
    )


@pytest.fixture
def child_context() -> Phase5ActorContext:
    return Phase5ActorContext(
        actor_id="child_1",
        actor=Phase5Actor.CHILD,
        session_id="session_1",
    )


@pytest.fixture
def helper_context() -> Phase5ActorContext:
    return Phase5ActorContext(
        actor_id="helper_1",
        actor=Phase5Actor.TRUSTED_HELPER,
        session_id="session_1",
    )


@pytest.fixture
def guest_context() -> Phase5ActorContext:
    return Phase5ActorContext(
        actor_id="guest_1",
        actor=Phase5Actor.GUEST,
        session_id="session_1",
    )


@pytest.fixture
def system_context() -> Phase5ActorContext:
    return Phase5ActorContext(
        actor_id="system_1",
        actor=Phase5Actor.SYSTEM,
        session_id="session_1",
    )


@pytest.fixture
def now() -> float:
    return 1000.0


@pytest.fixture
def valid_grant(helper_context: Phase5ActorContext, owner_context: Phase5ActorContext) -> CapabilityGrant:
    return CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=helper_context.actor_id,
        owner_actor_id=owner_context.actor_id,
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=900.0,
        expires_at=2000.0,
    )


@pytest.fixture
def valid_consent(owner_context: Phase5ActorContext) -> ConsentRecord:
    return ConsentRecord(
        consent_id="consent_1",
        owner_actor_id=owner_context.actor_id,
        capability=Capability.GUIDE_MY_HANDS,
        scope=ScopeConstraint(capability=Capability.GUIDE_MY_HANDS),
        granted_at=900.0,
        expires_at=2000.0,
    )


# --- Contract Validation Tests ------------------------------------------------


def test_phase5_actor_context_valid(owner_context: Phase5ActorContext) -> None:
    assert owner_context.actor is Phase5Actor.OWNER
    assert owner_context.actor_id == "owner_1"
    assert owner_context.session_id == "session_1"


def test_phase5_actor_context_rejects_invalid_actor_id() -> None:
    with pytest.raises(ValueError, match="invalid actor_id"):
        Phase5ActorContext(actor_id="", actor=Phase5Actor.OWNER, session_id="s1")


def test_phase5_actor_context_rejects_invalid_session_id() -> None:
    with pytest.raises(ValueError, match="invalid session_id"):
        Phase5ActorContext(actor_id="owner_1", actor=Phase5Actor.OWNER, session_id="")


def test_phase5_actor_context_repr_is_content_free(owner_context: Phase5ActorContext) -> None:
    rep = repr(owner_context)
    assert "owner_1" not in rep
    assert "session_1" not in rep
    assert "owner" in rep


def test_scope_constraint_matches() -> None:
    scope = ScopeConstraint(
        capability=Capability.TEACH_ME,
        data_subject="child",
        resource_pattern="memory:child:*",
        allowed_actions=("propose", "review"),
    )
    assert scope.matches(Capability.TEACH_ME, data_subject="child", resource="memory:child:lesson1", action="propose")
    assert not scope.matches(Capability.GUIDE_MY_HANDS, data_subject="child")
    assert not scope.matches(Capability.TEACH_ME, data_subject="other")
    assert not scope.matches(Capability.TEACH_ME, resource="memory:other:lesson1")


def test_scope_constraint_fails_closed_when_scoped_fields_are_missing() -> None:
    scope = ScopeConstraint(
        capability=Capability.TEACH_ME,
        resource_pattern="memory:child:*",
        allowed_actions=("propose",),
    )
    assert not scope.matches(Capability.TEACH_ME)
    assert not scope.matches(
        Capability.TEACH_ME,
        resource="memory:child:lesson1",
    )
    assert not scope.matches(Capability.TEACH_ME, action="propose")


def test_capability_grant_valid(valid_grant: CapabilityGrant) -> None:
    assert valid_grant.is_valid(1500.0)
    assert not valid_grant.is_expired(1500.0)
    assert not valid_grant.is_revoked()


def test_capability_grant_expired(valid_grant: CapabilityGrant) -> None:
    assert valid_grant.is_expired(2500.0)
    assert not valid_grant.is_valid(2500.0)


def test_capability_grant_revoked(valid_grant: CapabilityGrant) -> None:
    revoked = valid_grant.revoke(1500.0)
    assert revoked.is_revoked()
    assert not revoked.is_valid(1500.0)
    assert revoked.revoked_at == 1500.0


def test_capability_grant_requires_expiration() -> None:
    with pytest.raises(ValueError, match="expiration required"):
        CapabilityGrant(
            grant_id="grant_1",
            helper_actor_id="helper_1",
            owner_actor_id="owner_1",
            capability=Capability.TEACH_ME,
            scope=ScopeConstraint(capability=Capability.TEACH_ME),
            issued_at=1000.0,
            expires_at=None,  # type: ignore[arg-type]
        )


def test_consent_record_valid(valid_consent: ConsentRecord) -> None:
    assert valid_consent.is_valid(1500.0)
    assert not valid_consent.is_expired(1500.0)


def test_consent_record_expired(valid_consent: ConsentRecord) -> None:
    assert valid_consent.is_expired(2500.0)
    assert not valid_consent.is_valid(2500.0)


def test_consent_record_revoked(valid_consent: ConsentRecord) -> None:
    revoked = ConsentRecord(
        consent_id=valid_consent.consent_id,
        owner_actor_id=valid_consent.owner_actor_id,
        capability=valid_consent.capability,
        scope=valid_consent.scope,
        granted_at=valid_consent.granted_at,
        expires_at=valid_consent.expires_at,
        revoked=True,
        revoked_at=1500.0,
    )
    assert not revoked.is_valid(1500.0)


def test_phase5_request_valid(owner_context: Phase5ActorContext) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=owner_context,
        capability=Capability.TEACH_ME,
        data_subject="child",
        action="propose_lesson",
    )
    assert req.capability is Capability.TEACH_ME
    assert req.actor.actor is Phase5Actor.OWNER


def test_phase5_decision_repr_is_content_free(owner_context: Phase5ActorContext) -> None:
    decision = Phase5Decision(
        request_id="req_1",
        capability=Capability.TEACH_ME,
        actor_id=owner_context.actor_id,
        actor=owner_context.actor,
        outcome=Outcome.ALLOW,
        reason=DecisionReason.TEACH_ME_PROPOSAL_ONLY,
        granted_at=1000.0,
    )
    rep = repr(decision)
    assert "owner_1" not in rep
    assert "req_1" not in rep
    assert "allow" in rep
    assert "teach_me_proposal_only" in rep


# --- Capability Risk Mapping Tests --------------------------------------------


def test_capability_risk_mapping() -> None:
    assert CAPABILITY_RISK[Capability.TEACH_ME] is RiskLevel.LOW
    assert CAPABILITY_RISK[Capability.GUIDE_MY_HANDS] is RiskLevel.MEDIUM
    assert CAPABILITY_RISK[Capability.CARE] is RiskLevel.HIGH
    assert CAPABILITY_RISK[Capability.CHILD_MODE] is RiskLevel.LOW
    assert CAPABILITY_RISK[Capability.TRUSTED_HELPER_ACCESS] is RiskLevel.MEDIUM


def test_capability_requires_approval() -> None:
    assert Capability.GUIDE_MY_HANDS in CAPABILITY_REQUIRES_APPROVAL
    assert Capability.CARE in CAPABILITY_REQUIRES_APPROVAL
    assert Capability.TRUSTED_HELPER_ACCESS in CAPABILITY_REQUIRES_APPROVAL
    assert Capability.TEACH_ME not in CAPABILITY_REQUIRES_APPROVAL
    assert Capability.CHILD_MODE not in CAPABILITY_REQUIRES_APPROVAL


def test_actor_prohibited() -> None:
    assert ACTOR_PROHIBITED[Phase5Actor.GUEST] == frozenset(Capability)
    assert ACTOR_PROHIBITED[Phase5Actor.SYSTEM] == frozenset(Capability)
    assert Capability.TRUSTED_HELPER_ACCESS in ACTOR_PROHIBITED[Phase5Actor.CHILD]
    assert Capability.CARE in ACTOR_PROHIBITED[Phase5Actor.CHILD]


# --- evaluate_phase5_request Tests --------------------------------------------


def test_default_deny_unknown_capability(owner_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=owner_context,
        capability=Capability.TEACH_ME,  # Valid capability but we test unknown via invalid input
    )
    # Test with invalid request object
    decision = evaluate_phase5_request("not a request", now=now)  # type: ignore[arg-type]
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.INVALID_INPUT


def test_system_actor_denied(system_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=system_context,
        capability=Capability.TEACH_ME,
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.UNKNOWN_ACTOR


def test_guest_actor_denied(guest_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=guest_context,
        capability=Capability.TEACH_ME,
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.GUEST_DENIED


def test_owner_teach_me_proposal_only(owner_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=owner_context,
        capability=Capability.TEACH_ME,
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.ALLOW
    assert decision.reason is DecisionReason.TEACH_ME_PROPOSAL_ONLY


def test_owner_teach_me_no_direct_install(owner_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=owner_context,
        capability=Capability.TEACH_ME,
        action="install_skill",
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.TEACH_ME_NO_DIRECT_INSTALL


def test_owner_teach_me_assistant_authority_denied(owner_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=owner_context,
        capability=Capability.TEACH_ME,
        metadata=("assistant generated lesson",),
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.ALLOW
    assert decision.reason is DecisionReason.TEACH_ME_ASSISTANT_AUTHORITY_DENIED


def test_owner_child_mode_allowed(owner_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=owner_context,
        capability=Capability.CHILD_MODE,
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.ALLOW
    assert decision.reason is DecisionReason.OWNER_LOW_RISK


def test_owner_guide_hands_requires_approval(owner_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=owner_context,
        capability=Capability.GUIDE_MY_HANDS,
        action="execute_step",
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.REQUIRE_APPROVAL
    assert decision.reason is DecisionReason.GUIDE_HANDS_APPROVAL_REQUIRED


def test_owner_guide_hands_uncertainty_allowed(owner_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=owner_context,
        capability=Capability.GUIDE_MY_HANDS,
        metadata=("uncertain about next step",),
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.ALLOW
    assert decision.reason is DecisionReason.GUIDE_HANDS_UNCERTAINTY


def test_owner_guide_hands_no_false_completion(owner_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=owner_context,
        capability=Capability.GUIDE_MY_HANDS,
        metadata=("task complete",),
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.GUIDE_HANDS_NO_FALSE_COMPLETION


def test_owner_guide_hands_completion_with_evidence_allowed(owner_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=owner_context,
        capability=Capability.GUIDE_MY_HANDS,
        metadata=("task complete with evidence",),
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.ALLOW
    assert decision.reason is DecisionReason.OWNER_LOW_RISK


def test_owner_care_no_diagnosis(owner_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=owner_context,
        capability=Capability.CARE,
        action="diagnose_condition",
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.CARE_NO_DIAGNOSIS


def test_owner_care_emergency_requires_approval(owner_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=owner_context,
        capability=Capability.CARE,
        metadata=("emergency situation",),
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.REQUIRE_APPROVAL
    assert decision.reason is DecisionReason.OWNER_APPROVAL_REQUIRED


def test_owner_care_no_false_contact(owner_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=owner_context,
        capability=Capability.CARE,
        metadata=("contacted emergency services",),
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.CARE_NO_FALSE_CONTACT


def test_owner_care_contact_with_evidence_requires_approval(owner_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=owner_context,
        capability=Capability.CARE,
        metadata=("contacted emergency services confirmed",),
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.REQUIRE_APPROVAL
    assert decision.reason is DecisionReason.OWNER_APPROVAL_REQUIRED


def test_owner_trusted_helper_access_requires_approval(owner_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=owner_context,
        capability=Capability.TRUSTED_HELPER_ACCESS,
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.REQUIRE_APPROVAL
    assert decision.reason is DecisionReason.OWNER_APPROVAL_REQUIRED


def test_owner_guide_hands_valid_consent_allows_scoped_step(
    owner_context: Phase5ActorContext,
    valid_consent: ConsentRecord,
    now: float,
) -> None:
    req = Phase5Request(
        request_id="req_consent",
        actor=owner_context,
        capability=Capability.GUIDE_MY_HANDS,
        action="execute_step",
    )
    consent = ConsentRecord(
        consent_id=valid_consent.consent_id,
        owner_actor_id=valid_consent.owner_actor_id,
        capability=valid_consent.capability,
        scope=ScopeConstraint(
            capability=Capability.GUIDE_MY_HANDS,
            allowed_actions=("execute_step",),
        ),
        granted_at=valid_consent.granted_at,
        expires_at=valid_consent.expires_at,
    )
    decision = evaluate_phase5_request(req, consents=(consent,), now=now)
    assert decision.outcome is Outcome.ALLOW
    assert decision.reason is DecisionReason.OWNER_APPROVED


def test_owner_care_expired_consent_still_requires_approval(
    owner_context: Phase5ActorContext,
    now: float,
) -> None:
    consent = ConsentRecord(
        consent_id="consent_expired",
        owner_actor_id=owner_context.actor_id,
        capability=Capability.CARE,
        scope=ScopeConstraint(capability=Capability.CARE),
        granted_at=100.0,
        expires_at=900.0,
    )
    req = Phase5Request(
        request_id="req_expired",
        actor=owner_context,
        capability=Capability.CARE,
    )
    decision = evaluate_phase5_request(req, consents=(consent,), now=now)
    assert decision.outcome is Outcome.REQUIRE_APPROVAL
    assert decision.reason is DecisionReason.OWNER_APPROVAL_REQUIRED


def test_child_cannot_access_owner_memory(child_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=child_context,
        capability=Capability.TEACH_ME,
        data_subject="owner",
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.CHILD_OWNER_MEMORY_BLOCKED


def test_child_purchase_blocked(child_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=child_context,
        capability=Capability.TEACH_ME,
        action="make_purchase",
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.CHILD_PURCHASE_BLOCKED


def test_child_communication_blocked(child_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=child_context,
        capability=Capability.TEACH_ME,
        action="send_message",
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.CHILD_COMMUNICATION_BLOCKED


def test_child_audit_bypass_blocked(child_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=child_context,
        capability=Capability.TEACH_ME,
        action="disable_audit",
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.CHILD_AUDIT_BYPASS_BLOCKED


def test_child_helper_grant_blocked(child_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=child_context,
        capability=Capability.TRUSTED_HELPER_ACCESS,
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.CHILD_HELPER_GRANT_BLOCKED


def test_child_cannot_weaken_policy(child_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=child_context,
        capability=Capability.TEACH_ME,
        metadata=("weaken policy",),
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.CHILD_CANNOT_WEAKEN


def test_child_dangerous_blocked(child_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=child_context,
        capability=Capability.TEACH_ME,
        action="dangerous_instruction",
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.CHILD_DANGEROUS_BLOCKED


def test_helper_requires_grant(helper_context: Phase5ActorContext, now: float) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=helper_context,
        capability=Capability.TEACH_ME,
    )
    decision = evaluate_phase5_request(req, now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.HELPER_NO_GRANT


def test_helper_grant_expired(helper_context: Phase5ActorContext, now: float) -> None:
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=helper_context.actor_id,
        owner_actor_id="owner_1",
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=900.0,
        expires_at=950.0,
    )
    req = Phase5Request(
        request_id="req_1",
        actor=helper_context,
        capability=Capability.TEACH_ME,
    )
    decision = evaluate_phase5_request(req, grants=(grant,), now=now)
    assert decision.outcome is Outcome.EXPIRED
    assert decision.reason is DecisionReason.HELPER_GRANT_EXPIRED


def test_helper_grant_revoked(helper_context: Phase5ActorContext, now: float) -> None:
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=helper_context.actor_id,
        owner_actor_id="owner_1",
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=900.0,
        expires_at=2000.0,
        revoked=True,
        revoked_at=1500.0,
    )
    req = Phase5Request(
        request_id="req_1",
        actor=helper_context,
        capability=Capability.TEACH_ME,
    )
    decision = evaluate_phase5_request(req, grants=(grant,), now=now)
    assert decision.outcome is Outcome.REVOKED
    assert decision.reason is DecisionReason.HELPER_GRANT_REVOKED


def test_helper_scope_mismatch(helper_context: Phase5ActorContext, now: float) -> None:
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=helper_context.actor_id,
        owner_actor_id="owner_1",
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME, data_subject="child"),
        issued_at=900.0,
        expires_at=2000.0,
    )
    req = Phase5Request(
        request_id="req_1",
        actor=helper_context,
        capability=Capability.TEACH_ME,
        data_subject="other",
    )
    decision = evaluate_phase5_request(req, grants=(grant,), now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.HELPER_SCOPE_MISMATCH


def test_helper_delegation_blocked(helper_context: Phase5ActorContext, now: float) -> None:
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=helper_context.actor_id,
        owner_actor_id="owner_1",
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=900.0,
        expires_at=2000.0,
    )
    req = Phase5Request(
        request_id="req_1",
        actor=helper_context,
        capability=Capability.TEACH_ME,
        metadata=("delegate to another",),
    )
    decision = evaluate_phase5_request(req, grants=(grant,), now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.HELPER_DELEGATION_BLOCKED


def test_helper_scope_expansion_blocked(helper_context: Phase5ActorContext, now: float) -> None:
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=helper_context.actor_id,
        owner_actor_id="owner_1",
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=900.0,
        expires_at=2000.0,
    )
    req = Phase5Request(
        request_id="req_1",
        actor=helper_context,
        capability=Capability.TEACH_ME,
        metadata=("expand scope",),
    )
    decision = evaluate_phase5_request(req, grants=(grant,), now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.HELPER_SCOPE_EXPANSION_BLOCKED


def test_helper_unrelated_memory_blocked(helper_context: Phase5ActorContext, now: float) -> None:
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=helper_context.actor_id,
        owner_actor_id="owner_1",
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME, data_subject="child"),
        issued_at=900.0,
        expires_at=2000.0,
    )
    req = Phase5Request(
        request_id="req_1",
        actor=helper_context,
        capability=Capability.TEACH_ME,
        data_subject="owner",
    )
    decision = evaluate_phase5_request(req, grants=(grant,), now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.HELPER_UNRELATED_MEMORY_BLOCKED


def test_helper_silent_renewal_blocked(helper_context: Phase5ActorContext, now: float) -> None:
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=helper_context.actor_id,
        owner_actor_id="owner_1",
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=900.0,
        expires_at=2000.0,
    )
    req = Phase5Request(
        request_id="req_1",
        actor=helper_context,
        capability=Capability.TEACH_ME,
        metadata=("renew grant",),
    )
    decision = evaluate_phase5_request(req, grants=(grant,), now=now)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.HELPER_SILENT_RENEWAL_BLOCKED


def test_helper_valid_grant_allowed(helper_context: Phase5ActorContext, now: float) -> None:
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=helper_context.actor_id,
        owner_actor_id="owner_1",
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=900.0,
        expires_at=2000.0,
    )
    req = Phase5Request(
        request_id="req_1",
        actor=helper_context,
        capability=Capability.TEACH_ME,
    )
    decision = evaluate_phase5_request(req, grants=(grant,), now=now)
    assert decision.outcome is Outcome.ALLOW
    assert decision.reason is DecisionReason.HELPER_GRANT_VALID
    assert decision.grant_id == "grant_1"


def test_invalid_time_rejected(owner_context: Phase5ActorContext) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=owner_context,
        capability=Capability.TEACH_ME,
    )
    with pytest.raises(ValueError):
        evaluate_phase5_request(req, now=float("nan"))


def test_invalid_input_rejected(now: float) -> None:
    decision = evaluate_phase5_request("not a request", now=now)  # type: ignore[arg-type]
    assert decision.outcome is Outcome.DENY
    assert decision.reason is DecisionReason.INVALID_INPUT


# --- Safety Invariant Checks --------------------------------------------------


def test_check_approval_bypass(owner_context: Phase5ActorContext) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=owner_context,
        capability=Capability.GUIDE_MY_HANDS,
    )
    decision = Phase5Decision(
        request_id="req_1",
        capability=Capability.GUIDE_MY_HANDS,
        actor_id=owner_context.actor_id,
        actor=owner_context.actor,
        outcome=Outcome.ALLOW,
        reason=DecisionReason.OWNER_LOW_RISK,
        granted_at=1000.0,
    )
    assert check_approval_bypass(req, decision) is False  # ALLOW on requires-approval capability = bypass

    approved = Phase5Decision(
        request_id="req_1",
        capability=Capability.GUIDE_MY_HANDS,
        actor_id=owner_context.actor_id,
        actor=owner_context.actor,
        outcome=Outcome.ALLOW,
        reason=DecisionReason.OWNER_APPROVED,
        granted_at=1000.0,
    )
    assert check_approval_bypass(req, approved) is True

    decision2 = Phase5Decision(
        request_id="req_1",
        capability=Capability.GUIDE_MY_HANDS,
        actor_id=owner_context.actor_id,
        actor=owner_context.actor,
        outcome=Outcome.REQUIRE_APPROVAL,
        reason=DecisionReason.GUIDE_HANDS_APPROVAL_REQUIRED,
        granted_at=1000.0,
    )
    assert check_approval_bypass(req, decision2) is True


def test_check_audit_bypass(owner_context: Phase5ActorContext) -> None:
    decision = Phase5Decision(
        request_id="req_1",
        capability=Capability.TEACH_ME,
        actor_id=owner_context.actor_id,
        actor=owner_context.actor,
        outcome=Outcome.ALLOW,
        reason=DecisionReason.TEACH_ME_PROPOSAL_ONLY,
        granted_at=1000.0,
    )
    assert check_audit_bypass(decision) is False

    decision2 = Phase5Decision(
        request_id="req_1",
        capability=Capability.TEACH_ME,
        actor_id=owner_context.actor_id,
        actor=owner_context.actor,
        outcome=Outcome.ALLOW,
        reason=DecisionReason.TEACH_ME_PROPOSAL_ONLY,
        granted_at=1000.0,
        audit_id="audit_1",
    )
    assert check_audit_bypass(decision2) is True


def test_check_brain_write_denied(owner_context: Phase5ActorContext) -> None:
    req = Phase5Request(
        request_id="req_1",
        actor=owner_context,
        capability=Capability.TEACH_ME,
        action="install_skill",
    )
    decision = Phase5Decision(
        request_id="req_1",
        capability=Capability.TEACH_ME,
        actor_id=owner_context.actor_id,
        actor=owner_context.actor,
        outcome=Outcome.DENY,
        reason=DecisionReason.TEACH_ME_NO_DIRECT_INSTALL,
        granted_at=1000.0,
    )
    assert check_brain_write_denied(req, decision) is True

    decision2 = Phase5Decision(
        request_id="req_1",
        capability=Capability.TEACH_ME,
        actor_id=owner_context.actor_id,
        actor=owner_context.actor,
        outcome=Outcome.ALLOW,
        reason=DecisionReason.TEACH_ME_PROPOSAL_ONLY,
        granted_at=1000.0,
    )
    assert check_brain_write_denied(req, decision2) is True


# --- Forbidden Imports Test ---------------------------------------------------


def test_contracts_module_has_no_forbidden_imports() -> None:
    import core.phase5.contracts as contracts_module

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
    names = set(contracts_module.__dict__.keys())
    found = forbidden & names
    assert not found, f"forbidden imports present: {found}"


# --- AST-based import check ---------------------------------------------------


def test_contracts_ast_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "core" / "phase5" / "contracts.py"
    forbidden = {
        "subprocess",
        "socket",
        "threading",
        "asyncio",
        "requests",
        "http",
        "urllib",
        "browser",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert forbidden.isdisjoint(imported), f"forbidden imports: {forbidden & imported}"
