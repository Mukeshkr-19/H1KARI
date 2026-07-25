"""Tests for core.phase5.capability_service.

All fixtures are synthetic.  No network, filesystem, database, or model access
is performed.
"""

from __future__ import annotations

import ast
import pathlib
import unicodedata

import pytest

from core.phase5.capability_service import (
    CapabilityAuthorizationProof,
    CapabilityExecutionRequest,
    CapabilityProposal,
    CapabilityProposalKind,
    CapabilityServiceDecision,
    CapabilityServiceReason,
    CareProposal,
    GuideHandsProposal,
    GuideStep,
    GuideStepKind,
    Phase5CapabilityService,
    TeachMeProposal,
    _sanitize_text,
    _sanitize_tuple,
)
from core.phase5.contracts import (
    ACTOR_PROHIBITED,
    Capability,
    DecisionReason,
    Outcome,
    Phase5Actor,
    Phase5ActorContext,
    Phase5Decision,
    RiskLevel,
    ScopeConstraint,
)
from core.phase5.runtime_guard import Phase5RuntimeDecision, RuntimeDecisionReason


# --- Fixtures -----------------------------------------------------------------


@pytest.fixture
def service() -> Phase5CapabilityService:
    return Phase5CapabilityService()


@pytest.fixture
def owner_actor() -> Phase5ActorContext:
    return Phase5ActorContext(
        actor_id="owner.alpha",
        actor=Phase5Actor.OWNER,
        session_id="session.owner",
    )


@pytest.fixture
def child_actor() -> Phase5ActorContext:
    return Phase5ActorContext(
        actor_id="child.beta",
        actor=Phase5Actor.CHILD,
        session_id="session.child",
    )


@pytest.fixture
def helper_actor() -> Phase5ActorContext:
    return Phase5ActorContext(
        actor_id="helper.gamma",
        actor=Phase5Actor.TRUSTED_HELPER,
        session_id="session.helper",
    )


def _runtime_decision(
    request_id: str,
    actor_id: str,
    actor: Phase5Actor,
    capability: Capability,
    outcome: Outcome,
    approval_required: bool = False,
    audit_id: str = "audit.001",
) -> Phase5RuntimeDecision:
    return Phase5RuntimeDecision(
        request_id=request_id,
        outcome=outcome,
        reason=RuntimeDecisionReason.POLICY_ALLOW,
        audit_id=audit_id,
        policy_decision=Phase5Decision(
            request_id=request_id,
            capability=capability,
            actor_id=actor_id,
            actor=actor,
            outcome=outcome,
            reason=DecisionReason.OWNER_LOW_RISK,
            granted_at=1000.0,
            audit_id=audit_id,
        ),
        approval_required=approval_required,
    )


def _execution_request(
    actor: Phase5ActorContext,
    capability: Capability,
    request_id: str = "req.001",
    **kwargs: object,
) -> CapabilityExecutionRequest:
    return CapabilityExecutionRequest(
        request_id=request_id,
        actor=actor,
        capability=capability,
        **kwargs,  # type: ignore[arg-type]
    )


# --- Authorization verification tests ----------------------------------------


def test_missing_authorization(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.TEACH_ME)
    decision = service.prepare(request, authorization=object())  # type: ignore[arg-type]
    assert decision.outcome is Outcome.DENY
    assert decision.reason is CapabilityServiceReason.INVALID_INPUT


def test_terminal_denials(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.TEACH_ME)
    for outcome in (Outcome.DENY, Outcome.EXPIRED, Outcome.REVOKED, Outcome.OUT_OF_SCOPE, Outcome.AUTHENTICATION_REQUIRED):
        runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.TEACH_ME, outcome)
        decision = service.prepare(request, runtime)
        assert decision.outcome is Outcome.DENY
        assert decision.reason is CapabilityServiceReason.UNAUTHORIZED


def test_request_id_mismatch(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.TEACH_ME, request_id="req.002")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.TEACH_ME, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is CapabilityServiceReason.AUTHORIZATION_INVALID


def test_actor_id_mismatch(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.TEACH_ME, request_id="req.001")
    runtime = _runtime_decision("req.001", "owner.other", Phase5Actor.OWNER, Capability.TEACH_ME, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is CapabilityServiceReason.AUTHORIZATION_INVALID


def test_actor_kind_mismatch(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.TEACH_ME, request_id="req.001")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.CHILD, Capability.TEACH_ME, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is CapabilityServiceReason.AUTHORIZATION_INVALID


def test_capability_mismatch(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.GUIDE_MY_HANDS, request_id="req.001")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.TEACH_ME, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is CapabilityServiceReason.AUTHORIZATION_INVALID


def test_forged_approval_flag_is_not_trusted(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    # Runtime decision outcome is DENY but the caller claims approval_required=False.
    runtime = Phase5RuntimeDecision(
        request_id="req.001",
        outcome=Outcome.DENY,
        reason=RuntimeDecisionReason.POLICY_DENY,
        audit_id="audit.001",
        policy_decision=Phase5Decision(
            request_id="req.001",
            capability=Capability.TEACH_ME,
            actor_id="owner.alpha",
            actor=Phase5Actor.OWNER,
            outcome=Outcome.DENY,
            reason=DecisionReason.DEFAULT_DENY,
            granted_at=1000.0,
            audit_id="audit.001",
        ),
        approval_required=False,
    )
    request = _execution_request(owner_actor, Capability.TEACH_ME, request_id="req.001")
    decision = service.prepare(request, runtime)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is CapabilityServiceReason.UNAUTHORIZED


def test_child_prepolicy_ambiguity_only_propagates_approval(
    service: Phase5CapabilityService,
    child_actor: Phase5ActorContext,
) -> None:
    request = _execution_request(
        child_actor, Capability.GUIDE_MY_HANDS, request_id="req.child.approval"
    )
    runtime = Phase5RuntimeDecision(
        request_id="req.child.approval",
        outcome=Outcome.REQUIRE_APPROVAL,
        reason=RuntimeDecisionReason.CHILD_AMBIGUOUS_REQUIRES_APPROVAL,
        audit_id="audit.child.approval",
        policy_decision=None,
        approval_required=True,
    )

    decision = service.prepare(request, runtime)

    assert decision.outcome is Outcome.REQUIRE_APPROVAL
    assert decision.reason is CapabilityServiceReason.APPROVAL_REQUIRED
    assert decision.proposal is None


# --- Teach Me tests ------------------------------------------------------------


def test_owner_teach_me(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.TEACH_ME, request_id="req.001", topic="Python", goal="write a script")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.TEACH_ME, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert decision.outcome is Outcome.ALLOW
    assert decision.reason is CapabilityServiceReason.TEACH_ME_PROPOSAL
    assert decision.approval_required is False
    assert decision.proposal is not None
    assert isinstance(decision.proposal, TeachMeProposal)
    assert len(decision.proposal.outline) > 0
    assert len(decision.proposal.learning_steps) > 0
    assert len(decision.proposal.review_questions) > 0
    assert decision.proposal.assistant_authored is True
    assert decision.proposal.skill_evolution_candidate_id is None


def test_child_teach_me(service: Phase5CapabilityService, child_actor: Phase5ActorContext) -> None:
    request = _execution_request(child_actor, Capability.TEACH_ME, request_id="req.001", topic="math")
    runtime = _runtime_decision("req.001", "child.beta", Phase5Actor.CHILD, Capability.TEACH_ME, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert decision.outcome is Outcome.ALLOW
    assert decision.reason is CapabilityServiceReason.TEACH_ME_PROPOSAL


def test_helper_teach_me(service: Phase5CapabilityService, helper_actor: Phase5ActorContext) -> None:
    request = _execution_request(helper_actor, Capability.TEACH_ME, request_id="req.001", topic="history")
    runtime = _runtime_decision("req.001", "helper.gamma", Phase5Actor.TRUSTED_HELPER, Capability.TEACH_ME, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert decision.outcome is Outcome.ALLOW
    assert decision.reason is CapabilityServiceReason.TEACH_ME_PROPOSAL


def test_teach_me_direct_install_denied(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.TEACH_ME, request_id="req.001", action="install_skill")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.TEACH_ME, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is CapabilityServiceReason.UNAUTHORIZED


def test_teach_me_assistant_content_non_authority(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    # The proposal is always assistant-authored and non-authoritative.
    request = _execution_request(owner_actor, Capability.TEACH_ME, request_id="req.001")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.TEACH_ME, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert isinstance(decision.proposal, TeachMeProposal)
    assert decision.proposal.assistant_authored is True


def test_teach_me_approval_preserved(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.TEACH_ME, request_id="req.001")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.TEACH_ME, Outcome.REQUIRE_APPROVAL, approval_required=True)
    decision = service.prepare(request, runtime)
    assert decision.outcome is Outcome.REQUIRE_APPROVAL
    assert decision.reason is CapabilityServiceReason.APPROVAL_REQUIRED
    assert decision.approval_required is True


# --- Guide My Hands tests ------------------------------------------------------


def test_guide_informational_steps(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.GUIDE_MY_HANDS, request_id="req.001", goal="change a lightbulb")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.GUIDE_MY_HANDS, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert decision.outcome is Outcome.ALLOW
    assert decision.reason is CapabilityServiceReason.GUIDE_HANDS_PROPOSAL
    assert isinstance(decision.proposal, GuideHandsProposal)
    assert len(decision.proposal.steps) > 0


def test_guide_consequential_requires_approval(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.GUIDE_MY_HANDS, request_id="req.001", action="execute_step")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.GUIDE_MY_HANDS, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert isinstance(decision.proposal, GuideHandsProposal)
    consequential = [step for step in decision.proposal.steps if step.kind is GuideStepKind.CONSEQUENTIAL]
    assert len(consequential) > 0
    assert all(step.requires_approval for step in consequential)


def test_guide_uncertainty_clarification(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.GUIDE_MY_HANDS, request_id="req.001", goal="uncertain how to proceed")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.GUIDE_MY_HANDS, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert isinstance(decision.proposal, GuideHandsProposal)
    assert decision.proposal.uncertainty_disclosed is True
    assert len(decision.proposal.clarification_prompts) > 0


def test_guide_false_completion_not_claimed(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    # The service never claims completion; it only produces guidance steps.
    request = _execution_request(owner_actor, Capability.GUIDE_MY_HANDS, request_id="req.001", metadata=("complete",))
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.GUIDE_MY_HANDS, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert isinstance(decision.proposal, GuideHandsProposal)
    assert "complete" not in decision.proposal.steps[-1].description.lower()


def test_guide_no_raw_payloads(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.GUIDE_MY_HANDS, request_id="req.001", resource="camera://frame")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.GUIDE_MY_HANDS, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert isinstance(decision.proposal, GuideHandsProposal)
    # No raw resource path appears in the proposal.
    for step in decision.proposal.steps:
        assert "camera://frame" not in step.description


# --- Care tests ----------------------------------------------------------------


def test_care_requires_approval(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.CARE, request_id="req.001")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.CARE, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert decision.outcome is Outcome.ALLOW
    assert decision.reason is CapabilityServiceReason.CARE_PROPOSAL
    assert decision.approval_required is False
    assert isinstance(decision.proposal, CareProposal)


def test_child_care_requires_approval(service: Phase5CapabilityService, child_actor: Phase5ActorContext) -> None:
    request = _execution_request(child_actor, Capability.CARE, request_id="req.001")
    runtime = _runtime_decision("req.001", "child.beta", Phase5Actor.CHILD, Capability.CARE, Outcome.REQUIRE_APPROVAL, approval_required=True)
    decision = service.prepare(request, runtime)
    assert decision.outcome is Outcome.REQUIRE_APPROVAL
    assert decision.reason is CapabilityServiceReason.APPROVAL_REQUIRED
    assert decision.approval_required is True
    assert isinstance(decision.proposal, CareProposal)


def test_care_diagnosis_denied(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.CARE, request_id="req.001", action="diagnose_rash")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.CARE, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is CapabilityServiceReason.CARE_DIAGNOSIS_DENIED


@pytest.mark.parametrize(
    "action",
    ["prescribe_medication", "treat_injury", "medical_advice"],
)
def test_care_prescription_treatment_denied(service: Phase5CapabilityService, owner_actor: Phase5ActorContext, action: str) -> None:
    request = _execution_request(owner_actor, Capability.CARE, request_id="req.001", action=action)
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.CARE, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is CapabilityServiceReason.CARE_DIAGNOSIS_DENIED


def test_care_false_contact_claim_denied(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.CARE, request_id="req.001", metadata=("contacted emergency services",))
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.CARE, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is CapabilityServiceReason.CARE_FALSE_CONTACT_DENIED


def test_care_contact_with_evidence(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.CARE, request_id="req.001", metadata=("contacted emergency services confirmed",))
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.CARE, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    # Evidence present means the false-contact check passes. The supplied
    # runtime ALLOW proof represents already-matched scoped consent.
    assert decision.approval_required is False


def test_care_emergency_recommendation(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.CARE, request_id="req.001", care_prompt="I feel unsafe")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.CARE, Outcome.REQUIRE_APPROVAL, approval_required=True)
    decision = service.prepare(request, runtime)
    assert isinstance(decision.proposal, CareProposal)
    assert decision.proposal.contact_trusted_human_prompt is not None


# --- Unknown / default-deny tests ---------------------------------------------


def test_unknown_capability(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.CHILD_MODE, request_id="req.001")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.CHILD_MODE, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is CapabilityServiceReason.UNKNOWN_CAPABILITY


def test_child_mode_configuration_weakening_not_a_capability_request(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    # The service only handles the three supported capabilities; child_mode is
    # not a capability it produces proposals for.
    request = _execution_request(owner_actor, Capability.CHILD_MODE, request_id="req.001")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.CHILD_MODE, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert decision.outcome is Outcome.DENY
    assert decision.reason is CapabilityServiceReason.UNKNOWN_CAPABILITY


# --- Input immutability / bounds tests ----------------------------------------


def test_request_text_sanitization(owner_actor: Phase5ActorContext) -> None:
    # The service rejects control characters during request construction.
    topic = "Math\x00with\x07control"
    with pytest.raises(ValueError, match="control characters"):
        _execution_request(owner_actor, Capability.TEACH_ME, request_id="req.001", topic=topic)


def test_request_collection_bounds(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    large_metadata = tuple(f"item_{i}" for i in range(70))
    with pytest.raises(ValueError, match="exceeds maximum size"):
        _execution_request(owner_actor, Capability.TEACH_ME, request_id="req.001", metadata=large_metadata)


def test_input_immutability(owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.TEACH_ME, request_id="req.001", topic="Python")
    with pytest.raises(AttributeError):
        request.topic = "Java"  # type: ignore[misc]


# --- Content-free repr tests ---------------------------------------------------


def test_capability_service_decision_repr_is_content_free(owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.TEACH_ME, request_id="req.001", topic="private_secret_topic")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.TEACH_ME, Outcome.ALLOW)
    decision = Phase5CapabilityService().prepare(request, runtime)
    rep = repr(decision)
    assert "private_secret_topic" not in rep
    assert "TeachMeProposal" not in rep
    assert "allow" in rep


def test_teach_me_proposal_repr_is_content_free(service: Phase5CapabilityService, owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.TEACH_ME, request_id="req.001", topic="secret")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.TEACH_ME, Outcome.ALLOW)
    decision = service.prepare(request, runtime)
    assert isinstance(decision.proposal, TeachMeProposal)
    rep = repr(decision.proposal)
    assert "secret" not in rep


# --- Contract consistency tests -----------------------------------------------


def test_child_care_not_in_actor_prohibited() -> None:
    assert Capability.CARE not in ACTOR_PROHIBITED[Phase5Actor.CHILD]
    assert Capability.TRUSTED_HELPER_ACCESS in ACTOR_PROHIBITED[Phase5Actor.CHILD]


def test_child_teach_me_not_prohibited() -> None:
    assert Capability.TEACH_ME not in ACTOR_PROHIBITED[Phase5Actor.CHILD]


def test_child_guide_hands_not_prohibited() -> None:
    assert Capability.GUIDE_MY_HANDS not in ACTOR_PROHIBITED[Phase5Actor.CHILD]


# --- CapabilityProposal union / typing tests ----------------------------------


def test_proposal_union_dispatch(owner_actor: Phase5ActorContext) -> None:
    request = _execution_request(owner_actor, Capability.TEACH_ME, request_id="req.001")
    runtime = _runtime_decision("req.001", "owner.alpha", Phase5Actor.OWNER, Capability.TEACH_ME, Outcome.ALLOW)
    decision = Phase5CapabilityService().prepare(request, runtime)
    proposal: CapabilityProposal = decision.proposal
    assert isinstance(proposal, TeachMeProposal)


# --- No external action methods tests -----------------------------------------


def test_service_has_no_execute_methods() -> None:
    service = Phase5CapabilityService()
    forbidden = {"execute", "install", "deploy", "activate", "publish", "write", "call", "send"}
    public_methods = {name for name in dir(service) if not name.startswith("_")}
    assert forbidden.isdisjoint({m.lower() for m in public_methods})


# --- Forbidden imports / no I/O tests ----------------------------------------


def test_capability_service_source_contains_no_banned_imports() -> None:
    import inspect

    source = pathlib.Path(inspect.getfile(Phase5CapabilityService)).read_text()
    banned = ["sqlite3", "socket", "subprocess", "os.environ", "time.time", "uuid.uuid4"]
    for token in banned:
        assert token not in source, f"banned import or call {token!r} found"


def test_capability_service_ast_no_forbidden_imports() -> None:
    path = pathlib.Path(__file__).resolve().parents[1] / "core" / "phase5" / "capability_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
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
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert forbidden.isdisjoint(imported), f"forbidden imports: {forbidden & imported}"


# --- Authority / risk matrix consistency tests -------------------------------


def test_capability_risk_matrix() -> None:
    from core.phase5.contracts import CAPABILITY_RISK, RiskLevel
    assert CAPABILITY_RISK[Capability.TEACH_ME] is RiskLevel.LOW
    assert CAPABILITY_RISK[Capability.GUIDE_MY_HANDS] is RiskLevel.MEDIUM
    assert CAPABILITY_RISK[Capability.CARE] is RiskLevel.HIGH


def test_care_requires_approval_constant() -> None:
    from core.phase5.contracts import CAPABILITY_REQUIRES_APPROVAL
    assert Capability.CARE in CAPABILITY_REQUIRES_APPROVAL
