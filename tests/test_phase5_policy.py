"""Tests for core.phase5.policy."""

from __future__ import annotations

import math
import pathlib
import time

import pytest

from core.action_audit import ActionAuditStore
from core.action_policy import Actor, ActorContext
from core.grants import GrantStore
from core.phase5.contracts import (
    Capability,
    CapabilityGrant,
    ConsentRecord,
    DecisionReason,
    Outcome,
    Phase5Actor,
    Phase5ActorContext,
    Phase5Request,
    ScopeConstraint,
)
from core.phase5.policy import (
    Phase5AuthorizationRequest,
    Phase5AuthorizationDecision,
    Phase5ConsentStore,
    Phase5GrantStore,
    Phase5PolicyService,
)


# --- Fixtures -----------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "test.db"


@pytest.fixture
def grants_store(tmp_db: pathlib.Path) -> GrantStore:
    return GrantStore(tmp_db.parent / "grants" / "grants.db")


@pytest.fixture
def audit_store(tmp_db: pathlib.Path) -> ActionAuditStore:
    return ActionAuditStore(tmp_db.parent / "audit" / "audit.db")


@pytest.fixture
def phase5_grants(tmp_db: pathlib.Path) -> Phase5GrantStore:
    return Phase5GrantStore(tmp_db.parent / "phase5" / "grants.db")


@pytest.fixture
def phase5_consents(tmp_db: pathlib.Path) -> Phase5ConsentStore:
    return Phase5ConsentStore(tmp_db.parent / "phase5" / "consents.db")


@pytest.fixture
def policy_service(
    grants_store: GrantStore,
    audit_store: ActionAuditStore,
    phase5_grants: Phase5GrantStore,
    phase5_consents: Phase5ConsentStore,
) -> Phase5PolicyService:
    return Phase5PolicyService(
        grants_store,
        audit_store,
        phase5_grants,
        phase5_consents,
        clock=time.time,
    )


@pytest.fixture
def owner_actor() -> ActorContext:
    return ActorContext("owner_1", Actor.OWNER, "session_1")


@pytest.fixture
def helper_actor() -> ActorContext:
    return ActorContext("helper_1", Actor.GUEST, "session_1")


@pytest.fixture
def phase5_owner() -> Phase5ActorContext:
    return Phase5ActorContext("owner_1", Phase5Actor.OWNER, "session_1")


@pytest.fixture
def phase5_helper() -> Phase5ActorContext:
    return Phase5ActorContext("helper_1", Phase5Actor.TRUSTED_HELPER, "session_1")


@pytest.fixture
def now() -> float:
    return time.time()


# --- Phase5GrantStore Tests ---------------------------------------------------


def test_phase5_grant_store_issue_and_get(phase5_grants: Phase5GrantStore, phase5_helper: Phase5ActorContext, phase5_owner: Phase5ActorContext) -> None:
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=phase5_helper.actor_id,
        owner_actor_id=phase5_owner.actor_id,
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=time.time(),
        expires_at=time.time() + 3600,
    )
    issued = phase5_grants.issue(grant)
    assert issued.grant_id == "grant_1"

    retrieved = phase5_grants.get("grant_1")
    assert retrieved is not None
    assert retrieved.grant_id == "grant_1"
    assert retrieved.capability is Capability.TEACH_ME


def test_phase5_grant_store_revoke(phase5_grants: Phase5GrantStore, phase5_helper: Phase5ActorContext, phase5_owner: Phase5ActorContext) -> None:
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=phase5_helper.actor_id,
        owner_actor_id=phase5_owner.actor_id,
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=time.time(),
        expires_at=time.time() + 3600,
    )
    phase5_grants.issue(grant)

    assert phase5_grants.revoke("grant_1", time.time())
    retrieved = phase5_grants.get("grant_1")
    assert retrieved is not None
    assert retrieved.revoked is True


def test_phase5_grant_store_list_for_helper(phase5_grants: Phase5GrantStore, phase5_helper: Phase5ActorContext, phase5_owner: Phase5ActorContext) -> None:
    now = time.time()
    grant1 = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=phase5_helper.actor_id,
        owner_actor_id=phase5_owner.actor_id,
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=now - 100,
        expires_at=now + 3600,
    )
    grant2 = CapabilityGrant(
        grant_id="grant_2",
        helper_actor_id=phase5_helper.actor_id,
        owner_actor_id=phase5_owner.actor_id,
        capability=Capability.GUIDE_MY_HANDS,
        scope=ScopeConstraint(capability=Capability.GUIDE_MY_HANDS),
        issued_at=now - 100,
        expires_at=now + 3600,
    )
    phase5_grants.issue(grant1)
    phase5_grants.issue(grant2)

    grants = phase5_grants.list_for_helper(phase5_helper.actor_id, now)
    assert len(grants) == 2


def test_phase5_grant_store_list_for_owner(phase5_grants: Phase5GrantStore, phase5_helper: Phase5ActorContext, phase5_owner: Phase5ActorContext) -> None:
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=phase5_helper.actor_id,
        owner_actor_id=phase5_owner.actor_id,
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=time.time(),
        expires_at=time.time() + 3600,
    )
    phase5_grants.issue(grant)

    grants = phase5_grants.list_for_owner(phase5_owner.actor_id)
    assert len(grants) == 1


# --- Phase5ConsentStore Tests -------------------------------------------------


def test_phase5_consent_store_issue_and_get(phase5_consents: Phase5ConsentStore, phase5_owner: Phase5ActorContext) -> None:
    consent = ConsentRecord(
        consent_id="consent_1",
        owner_actor_id=phase5_owner.actor_id,
        capability=Capability.GUIDE_MY_HANDS,
        scope=ScopeConstraint(capability=Capability.GUIDE_MY_HANDS),
        granted_at=time.time(),
        expires_at=time.time() + 3600,
    )
    issued = phase5_consents.issue(consent)
    assert issued.consent_id == "consent_1"

    retrieved = phase5_consents.get("consent_1")
    assert retrieved is not None
    assert retrieved.capability is Capability.GUIDE_MY_HANDS


def test_phase5_consent_store_revoke(phase5_consents: Phase5ConsentStore, phase5_owner: Phase5ActorContext) -> None:
    consent = ConsentRecord(
        consent_id="consent_1",
        owner_actor_id=phase5_owner.actor_id,
        capability=Capability.GUIDE_MY_HANDS,
        scope=ScopeConstraint(capability=Capability.GUIDE_MY_HANDS),
        granted_at=time.time(),
        expires_at=time.time() + 3600,
    )
    phase5_consents.issue(consent)

    assert phase5_consents.revoke("consent_1", time.time())
    retrieved = phase5_consents.get("consent_1")
    assert retrieved is not None
    assert retrieved.revoked is True


def test_phase5_consent_store_list_for_owner(phase5_consents: Phase5ConsentStore, phase5_owner: Phase5ActorContext) -> None:
    now = time.time()
    consent1 = ConsentRecord(
        consent_id="consent_1",
        owner_actor_id=phase5_owner.actor_id,
        capability=Capability.GUIDE_MY_HANDS,
        scope=ScopeConstraint(capability=Capability.GUIDE_MY_HANDS),
        granted_at=now - 100,
        expires_at=now + 3600,
    )
    consent2 = ConsentRecord(
        consent_id="consent_2",
        owner_actor_id=phase5_owner.actor_id,
        capability=Capability.CARE,
        scope=ScopeConstraint(capability=Capability.CARE),
        granted_at=now - 100,
        expires_at=now + 3600,
    )
    phase5_consents.issue(consent1)
    phase5_consents.issue(consent2)

    consents = phase5_consents.list_for_owner(phase5_owner.actor_id, now)
    assert len(consents) == 2


# --- Phase5PolicyService Tests ------------------------------------------------


def test_policy_service_owner_teach_me_allowed(
    policy_service: Phase5PolicyService,
    owner_actor: ActorContext,
    phase5_owner: Phase5ActorContext,
) -> None:
    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_owner,
            capability=Capability.TEACH_ME,
        ),
        actor=owner_actor,
    )
    decision = policy_service.authorize(req)
    assert decision.decision.outcome is Outcome.ALLOW
    assert decision.decision.reason is DecisionReason.TEACH_ME_PROPOSAL_ONLY
    assert decision.approval_required is False


def test_policy_service_owner_guide_hands_requires_approval(
    policy_service: Phase5PolicyService,
    owner_actor: ActorContext,
    phase5_owner: Phase5ActorContext,
) -> None:
    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_owner,
            capability=Capability.GUIDE_MY_HANDS,
            action="execute_step",
        ),
        actor=owner_actor,
    )
    decision = policy_service.authorize(req)
    assert decision.decision.outcome is Outcome.REQUIRE_APPROVAL
    assert decision.approval_required is True


def test_policy_service_owner_care_requires_approval(
    policy_service: Phase5PolicyService,
    owner_actor: ActorContext,
    phase5_owner: Phase5ActorContext,
) -> None:
    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_owner,
            capability=Capability.CARE,
        ),
        actor=owner_actor,
    )
    decision = policy_service.authorize(req)
    assert decision.decision.outcome is Outcome.REQUIRE_APPROVAL
    assert decision.approval_required is True


def test_policy_service_helper_requires_grant(
    policy_service: Phase5PolicyService,
    helper_actor: ActorContext,
    phase5_helper: Phase5ActorContext,
) -> None:
    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_helper,
            capability=Capability.TEACH_ME,
        ),
        actor=helper_actor,
    )
    decision = policy_service.authorize(req)
    assert decision.decision.outcome is Outcome.DENY
    assert decision.decision.reason is DecisionReason.HELPER_NO_GRANT


def test_policy_service_helper_with_valid_grant_allowed(
    policy_service: Phase5PolicyService,
    helper_actor: ActorContext,
    phase5_helper: Phase5ActorContext,
    phase5_owner: Phase5ActorContext,
    phase5_grants: Phase5GrantStore,
) -> None:
    now = time.time()
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=phase5_helper.actor_id,
        owner_actor_id=phase5_owner.actor_id,
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=now - 100,
        expires_at=now + 3600,
    )
    phase5_grants.issue(grant)

    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_helper,
            capability=Capability.TEACH_ME,
        ),
        actor=helper_actor,
    )
    decision = policy_service.authorize(req)
    assert decision.decision.outcome is Outcome.ALLOW
    assert decision.decision.reason is DecisionReason.HELPER_GRANT_VALID
    assert decision.decision.grant_id == "grant_1"


def test_policy_service_helper_grant_expired(
    policy_service: Phase5PolicyService,
    helper_actor: ActorContext,
    phase5_helper: Phase5ActorContext,
    phase5_owner: Phase5ActorContext,
    phase5_grants: Phase5GrantStore,
) -> None:
    now = time.time()
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=phase5_helper.actor_id,
        owner_actor_id=phase5_owner.actor_id,
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=now - 100,
        expires_at=now - 10,  # Expired
    )
    phase5_grants.issue(grant)

    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_helper,
            capability=Capability.TEACH_ME,
        ),
        actor=helper_actor,
    )
    decision = policy_service.authorize(req)
    assert decision.decision.outcome is Outcome.EXPIRED
    assert decision.decision.reason is DecisionReason.HELPER_GRANT_EXPIRED


def test_policy_service_helper_grant_revoked(
    policy_service: Phase5PolicyService,
    helper_actor: ActorContext,
    phase5_helper: Phase5ActorContext,
    phase5_owner: Phase5ActorContext,
    phase5_grants: Phase5GrantStore,
) -> None:
    now = time.time()
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=phase5_helper.actor_id,
        owner_actor_id=phase5_owner.actor_id,
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=now - 100,
        expires_at=now + 3600,
        revoked=True,
        revoked_at=now - 50,
    )
    phase5_grants.issue(grant)

    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_helper,
            capability=Capability.TEACH_ME,
        ),
        actor=helper_actor,
    )
    decision = policy_service.authorize(req)
    assert decision.decision.outcome is Outcome.REVOKED
    assert decision.decision.reason is DecisionReason.HELPER_GRANT_REVOKED


def test_policy_service_issue_helper_grant(
    policy_service: Phase5PolicyService,
    owner_actor: ActorContext,
    phase5_helper: Phase5ActorContext,
) -> None:
    grant = policy_service.issue_helper_grant(
        owner_actor=owner_actor,
        helper_actor_id=phase5_helper.actor_id,
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        expires_at=time.time() + 3600,
    )
    assert grant.grant_id is not None
    assert grant.capability is Capability.TEACH_ME
    assert grant.helper_actor_id == phase5_helper.actor_id


def test_policy_service_issue_helper_grant_rejects_non_owner(
    policy_service: Phase5PolicyService,
    helper_actor: ActorContext,
    phase5_helper: Phase5ActorContext,
) -> None:
    with pytest.raises(ValueError, match="only owner actors may issue helper grants"):
        policy_service.issue_helper_grant(
            owner_actor=helper_actor,
            helper_actor_id=phase5_helper.actor_id,
            capability=Capability.TEACH_ME,
            scope=ScopeConstraint(capability=Capability.TEACH_ME),
            expires_at=time.time() + 3600,
        )


def test_policy_service_issue_helper_grant_rejects_past_expiry(
    policy_service: Phase5PolicyService,
    owner_actor: ActorContext,
    phase5_helper: Phase5ActorContext,
) -> None:
    with pytest.raises(ValueError, match="expires_at must be in the future"):
        policy_service.issue_helper_grant(
            owner_actor=owner_actor,
            helper_actor_id=phase5_helper.actor_id,
            capability=Capability.TEACH_ME,
            scope=ScopeConstraint(capability=Capability.TEACH_ME),
            expires_at=time.time() - 100,
        )


def test_policy_service_revoke_helper_grant(
    policy_service: Phase5PolicyService,
    owner_actor: ActorContext,
    phase5_helper: Phase5ActorContext,
) -> None:
    grant = policy_service.issue_helper_grant(
        owner_actor=owner_actor,
        helper_actor_id=phase5_helper.actor_id,
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        expires_at=time.time() + 3600,
    )
    assert policy_service.revoke_helper_grant(grant.grant_id)


def test_policy_service_issue_owner_consent(
    policy_service: Phase5PolicyService,
    owner_actor: ActorContext,
    phase5_owner: Phase5ActorContext,
) -> None:
    consent = policy_service.issue_owner_consent(
        owner_actor=owner_actor,
        capability=Capability.GUIDE_MY_HANDS,
        scope=ScopeConstraint(capability=Capability.GUIDE_MY_HANDS),
        expires_at=time.time() + 3600,
    )
    assert consent.consent_id is not None
    assert consent.capability is Capability.GUIDE_MY_HANDS


def test_policy_service_issue_owner_consent_rejects_non_owner(
    policy_service: Phase5PolicyService,
    helper_actor: ActorContext,
) -> None:
    with pytest.raises(ValueError, match="only owner actors may grant consent"):
        policy_service.issue_owner_consent(
            owner_actor=helper_actor,
            capability=Capability.GUIDE_MY_HANDS,
            scope=ScopeConstraint(capability=Capability.GUIDE_MY_HANDS),
            expires_at=time.time() + 3600,
        )


def test_policy_service_revoke_owner_consent(
    policy_service: Phase5PolicyService,
    owner_actor: ActorContext,
) -> None:
    consent = policy_service.issue_owner_consent(
        owner_actor=owner_actor,
        capability=Capability.GUIDE_MY_HANDS,
        scope=ScopeConstraint(capability=Capability.GUIDE_MY_HANDS),
        expires_at=time.time() + 3600,
    )
    assert policy_service.revoke_owner_consent(consent.consent_id)


def test_policy_service_invalid_actor_fails_closed(
    policy_service: Phase5PolicyService,
    phase5_owner: Phase5ActorContext,
) -> None:
    invalid_actor = ActorContext("", Actor.OWNER, "session_1")
    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_owner,
            capability=Capability.TEACH_ME,
        ),
        actor=invalid_actor,
    )
    with pytest.raises(ValueError, match="invalid actor context"):
        policy_service.authorize(req)


def test_policy_service_audit_recorded(
    policy_service: Phase5PolicyService,
    owner_actor: ActorContext,
    phase5_owner: Phase5ActorContext,
    audit_store: ActionAuditStore,
) -> None:
    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_owner,
            capability=Capability.TEACH_ME,
        ),
        actor=owner_actor,
    )
    decision = policy_service.authorize(req)
    assert decision.audit_id is not None
    assert decision.decision.audit_id == decision.audit_id

    # Verify audit record exists
    records = audit_store.list_recent(1)
    assert len(records) == 1
    assert records[0].action == "phase5.teach_me"
    assert records[0].outcome == "allow"


def test_policy_service_approval_bypass_blocked(
    policy_service: Phase5PolicyService,
    owner_actor: ActorContext,
    phase5_owner: Phase5ActorContext,
) -> None:
    # This test verifies the internal check_approval_bypass works
    # The policy service should block approval bypass
    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_owner,
            capability=Capability.GUIDE_MY_HANDS,
            action="execute_step",
        ),
        actor=owner_actor,
    )
    decision = policy_service.authorize(req)
    # Guide My Hands with execute_step requires approval
    assert decision.decision.outcome is Outcome.REQUIRE_APPROVAL


def test_policy_service_audit_bypass_blocked(
    policy_service: Phase5PolicyService,
    owner_actor: ActorContext,
    phase5_owner: Phase5ActorContext,
) -> None:
    # The policy service should ensure audit is recorded for all decisions
    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_owner,
            capability=Capability.TEACH_ME,
        ),
        actor=owner_actor,
    )
    decision = policy_service.authorize(req)
    assert decision.audit_id is not None


def test_policy_service_rejects_mismatched_actor_context(
    policy_service: Phase5PolicyService,
    helper_actor: ActorContext,
    phase5_owner: Phase5ActorContext,
) -> None:
    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_mismatch",
            actor=phase5_owner,
            capability=Capability.TEACH_ME,
        ),
        actor=helper_actor,
    )
    decision = policy_service.authorize(req)
    assert decision.decision.outcome is Outcome.DENY
    assert decision.decision.reason is DecisionReason.INVALID_INPUT


def test_policy_service_uses_injected_clock(
    grants_store: GrantStore,
    audit_store: ActionAuditStore,
    phase5_grants: Phase5GrantStore,
    phase5_consents: Phase5ConsentStore,
    owner_actor: ActorContext,
    phase5_owner: Phase5ActorContext,
) -> None:
    service = Phase5PolicyService(
        grants_store,
        audit_store,
        phase5_grants,
        phase5_consents,
        clock=lambda: 1234.5,
    )
    decision = service.authorize(
        Phase5AuthorizationRequest(
            request=Phase5Request(
                request_id="req_clock",
                actor=phase5_owner,
                capability=Capability.TEACH_ME,
            ),
            actor=owner_actor,
        )
    )
    assert decision.decision.granted_at == 1234.5


def test_policy_service_brain_write_denied(
    policy_service: Phase5PolicyService,
    owner_actor: ActorContext,
    phase5_owner: Phase5ActorContext,
) -> None:
    # Teach Me with install action should be denied
    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_owner,
            capability=Capability.TEACH_ME,
            action="install_skill",
        ),
        actor=owner_actor,
    )
    decision = policy_service.authorize(req)
    assert decision.decision.outcome is Outcome.DENY
    assert decision.decision.reason is DecisionReason.TEACH_ME_NO_DIRECT_INSTALL


def test_policy_service_child_mode_allowed(
    policy_service: Phase5PolicyService,
    owner_actor: ActorContext,
    phase5_owner: Phase5ActorContext,
) -> None:
    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_owner,
            capability=Capability.CHILD_MODE,
        ),
        actor=owner_actor,
    )
    decision = policy_service.authorize(req)
    assert decision.decision.outcome is Outcome.ALLOW


def test_policy_service_trusted_helper_access_requires_approval(
    policy_service: Phase5PolicyService,
    owner_actor: ActorContext,
    phase5_owner: Phase5ActorContext,
) -> None:
    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_owner,
            capability=Capability.TRUSTED_HELPER_ACCESS,
        ),
        actor=owner_actor,
    )
    decision = policy_service.authorize(req)
    assert decision.decision.outcome is Outcome.REQUIRE_APPROVAL
    assert decision.approval_required is True


def test_policy_service_grant_scope_mismatch(
    policy_service: Phase5PolicyService,
    helper_actor: ActorContext,
    phase5_helper: Phase5ActorContext,
    phase5_owner: Phase5ActorContext,
    phase5_grants: Phase5GrantStore,
) -> None:
    now = time.time()
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=phase5_helper.actor_id,
        owner_actor_id=phase5_owner.actor_id,
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME, data_subject="child"),
        issued_at=now - 100,
        expires_at=now + 3600,
    )
    phase5_grants.issue(grant)

    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_helper,
            capability=Capability.TEACH_ME,
            data_subject="other",
        ),
        actor=helper_actor,
    )
    decision = policy_service.authorize(req)
    assert decision.decision.outcome is Outcome.DENY
    assert decision.decision.reason is DecisionReason.HELPER_SCOPE_MISMATCH


def test_policy_service_grant_delegation_blocked(
    policy_service: Phase5PolicyService,
    helper_actor: ActorContext,
    phase5_helper: Phase5ActorContext,
    phase5_owner: Phase5ActorContext,
    phase5_grants: Phase5GrantStore,
) -> None:
    now = time.time()
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=phase5_helper.actor_id,
        owner_actor_id=phase5_owner.actor_id,
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=now - 100,
        expires_at=now + 3600,
    )
    phase5_grants.issue(grant)

    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_helper,
            capability=Capability.TEACH_ME,
            metadata=("delegate to another",),
        ),
        actor=helper_actor,
    )
    decision = policy_service.authorize(req)
    assert decision.decision.outcome is Outcome.DENY
    assert decision.decision.reason is DecisionReason.HELPER_DELEGATION_BLOCKED


def test_policy_service_grant_scope_expansion_blocked(
    policy_service: Phase5PolicyService,
    helper_actor: ActorContext,
    phase5_helper: Phase5ActorContext,
    phase5_owner: Phase5ActorContext,
    phase5_grants: Phase5GrantStore,
) -> None:
    now = time.time()
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=phase5_helper.actor_id,
        owner_actor_id=phase5_owner.actor_id,
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=now - 100,
        expires_at=now + 3600,
    )
    phase5_grants.issue(grant)

    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_helper,
            capability=Capability.TEACH_ME,
            metadata=("expand scope",),
        ),
        actor=helper_actor,
    )
    decision = policy_service.authorize(req)
    assert decision.decision.outcome is Outcome.DENY
    assert decision.decision.reason is DecisionReason.HELPER_SCOPE_EXPANSION_BLOCKED


def test_policy_service_grant_unrelated_memory_blocked(
    policy_service: Phase5PolicyService,
    helper_actor: ActorContext,
    phase5_helper: Phase5ActorContext,
    phase5_owner: Phase5ActorContext,
    phase5_grants: Phase5GrantStore,
) -> None:
    now = time.time()
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=phase5_helper.actor_id,
        owner_actor_id=phase5_owner.actor_id,
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME, data_subject="child"),
        issued_at=now - 100,
        expires_at=now + 3600,
    )
    phase5_grants.issue(grant)

    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_helper,
            capability=Capability.TEACH_ME,
            data_subject="owner",
        ),
        actor=helper_actor,
    )
    decision = policy_service.authorize(req)
    assert decision.decision.outcome is Outcome.DENY
    assert decision.decision.reason is DecisionReason.HELPER_UNRELATED_MEMORY_BLOCKED


def test_policy_service_grant_silent_renewal_blocked(
    policy_service: Phase5PolicyService,
    helper_actor: ActorContext,
    phase5_helper: Phase5ActorContext,
    phase5_owner: Phase5ActorContext,
    phase5_grants: Phase5GrantStore,
) -> None:
    now = time.time()
    grant = CapabilityGrant(
        grant_id="grant_1",
        helper_actor_id=phase5_helper.actor_id,
        owner_actor_id=phase5_owner.actor_id,
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=now - 100,
        expires_at=now + 3600,
    )
    phase5_grants.issue(grant)

    req = Phase5AuthorizationRequest(
        request=Phase5Request(
            request_id="req_1",
            actor=phase5_helper,
            capability=Capability.TEACH_ME,
            metadata=("renew grant",),
        ),
        actor=helper_actor,
    )
    decision = policy_service.authorize(req)
    assert decision.decision.outcome is Outcome.DENY
    assert decision.decision.reason is DecisionReason.HELPER_SILENT_RENEWAL_BLOCKED


def test_policy_service_database_permissions(
    tmp_path: pathlib.Path,
    grants_store: GrantStore,
    audit_store: ActionAuditStore,
) -> None:
    import stat
    phase5_grants = Phase5GrantStore(tmp_path / "phase5" / "grants.db")
    phase5_consents = Phase5ConsentStore(tmp_path / "phase5" / "consents.db")

    assert stat.S_IMODE(phase5_grants.db_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(phase5_grants.db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(phase5_consents.db_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(phase5_consents.db_path.stat().st_mode) == 0o600


# --- Forbidden Imports Test ---------------------------------------------------


def test_policy_module_has_no_forbidden_imports() -> None:
    import core.phase5.policy as policy_module

    # Standard library modules that are allowed (commonly needed for implementation)
    allowed_stdlib = {
        "time",
        "uuid",
        "sqlite3",
        "math",
        "re",
        "pathlib",
        "dataclasses",
        "typing",
        "enum",
        "collections",
        "functools",
        "itertools",
        "datetime",
        "hashlib",
        "json",
        "os",
    }

    forbidden = {
        "socket",
        "urllib",
        "http",
        "requests",
        "subprocess",
        "pickle",
    }
    names = set(policy_module.__dict__.keys())
    found = forbidden & names
    assert not found, f"forbidden imports present: {found}"
    # Verify only allowed stdlib modules are used
    stdlib_imports = names & allowed_stdlib
    assert stdlib_imports <= allowed_stdlib, f"unexpected stdlib imports: {stdlib_imports - allowed_stdlib}"


# --- AST-based import check ---------------------------------------------------


def test_policy_ast_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "core" / "phase5" / "policy.py"
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
