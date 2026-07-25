"""Phase 5 capability service: pure, proposal-only workflows.

This module provides typed, immutable, production-usable proposal workflows
for the three supported capabilities:

- Teach Me
- Guide My Hands
- Care

The service operates behind an already-computed ``Phase5RuntimeDecision``.
It performs no external actions, no I/O, no network calls, no database
access, no model calls, and no external process or filesystem operations.  All
identity, grants, and authorization decisions are caller-supplied.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional, Tuple, Union

from core.phase5.contracts import (
    Capability,
    Outcome,
    Phase5Actor,
    Phase5ActorContext,
    _validate_actor_identifier,
    _validate_identifier,
)
from core.phase5.runtime_guard import Phase5RuntimeDecision

# --- Canonical limits ---------------------------------------------------------

_MAX_TEXT_LENGTH = 4096
_MAX_SHORT_TEXT_LENGTH = 1024
_MAX_COLLECTION_SIZE = 64
_MAX_IDENTIFIER_LENGTH = 80


# --- Enums --------------------------------------------------------------------


class CapabilityProposalKind(StrEnum):
    """Discriminator for typed capability proposals."""

    TEACH_ME = "teach_me"
    GUIDE_MY_HANDS = "guide_my_hands"
    CARE = "care"


class CapabilityServiceReason(StrEnum):
    """Stable machine-readable reasons for capability-service decisions."""

    AUTHORIZATION_INVALID = "authorization_invalid"
    UNAUTHORIZED = "unauthorized"
    APPROVAL_REQUIRED = "approval_required"
    TEACH_ME_PROPOSAL = "teach_me_proposal"
    GUIDE_HANDS_PROPOSAL = "guide_hands_proposal"
    CARE_PROPOSAL = "care_proposal"
    CARE_DIAGNOSIS_DENIED = "care_diagnosis_denied"
    CARE_FALSE_CONTACT_DENIED = "care_false_contact_denied"
    UNKNOWN_CAPABILITY = "unknown_capability"
    INVALID_INPUT = "invalid_input"
    INTERNAL_ERROR = "internal_error"


class GuideStepKind(StrEnum):
    """Kind of a Guide My Hands step."""

    INFORMATIONAL = "informational"
    OBSERVATIONAL = "observational"
    CONSEQUENTIAL = "consequential"


# --- Validation / sanitization ------------------------------------------------


def _sanitize_text(value: Optional[str], field: str, max_length: int = _MAX_TEXT_LENGTH) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    # Strip leading/trailing whitespace and truncate before deeper checks.
    value = value.strip()[:max_length]
    if not value:
        return ""
    # Reject control characters except common whitespace.
    for char in value:
        category = unicodedata.category(char)
        if category in {"Cc", "Cf", "Cs", "Co", "Cn"} and char not in {"\t", "\n", "\r"}:
            raise ValueError(f"{field} contains control characters")
    # Normalize line endings and collapse repeated whitespace.
    value = re.sub(r"[\t\r]+", " ", value)
    value = re.sub(r" {2,}", " ", value)
    return value.strip()


def _sanitize_short_text(value: Optional[str], field: str) -> str:
    return _sanitize_text(value, field, max_length=_MAX_SHORT_TEXT_LENGTH)


def _sanitize_tuple(value: Tuple[str, ...], field: str, max_size: int = _MAX_COLLECTION_SIZE) -> Tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{field} must be a tuple")
    if len(value) > max_size:
        raise ValueError(f"{field} exceeds maximum size of {max_size}")
    sanitized: list[str] = []
    for item in value:
        sanitized.append(_sanitize_short_text(item, field))
    return tuple(sanitized)


# --- Typed proposals ------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityAuthorizationProof:
    """Bounded proof extracted from a runtime decision.

    The proof is content-free: it never contains the runtime decision's raw
    policy payload or sensitive identifiers beyond the minimum needed for
    matching.
    """

    request_id: str
    actor_id: str
    actor: Phase5Actor
    capability: Capability
    outcome: Outcome
    approval_required: bool
    audit_id: Optional[str]

    def __post_init__(self) -> None:
        _validate_identifier(self.request_id, "request_id")
        _validate_actor_identifier(self.actor_id, "actor_id")
        if not isinstance(self.actor, Phase5Actor):
            raise ValueError("invalid actor")
        if not isinstance(self.capability, Capability):
            raise ValueError("invalid capability")
        if not isinstance(self.outcome, Outcome):
            raise ValueError("invalid outcome")
        if not isinstance(self.approval_required, bool):
            raise ValueError("approval_required must be boolean")
        if self.audit_id is not None:
            _validate_identifier(self.audit_id, "audit_id")

    def __repr__(self) -> str:
        return (
            f"CapabilityAuthorizationProof(capability={self.capability.value!r}, "
            f"outcome={self.outcome.value!r})"
        )

    @classmethod
    def from_runtime_decision(cls, decision: Phase5RuntimeDecision) -> "CapabilityAuthorizationProof":
        if decision.policy_decision is None:
            raise ValueError("runtime decision has no policy decision")
        return cls(
            request_id=decision.request_id,
            actor_id=decision.policy_decision.actor_id,
            actor=decision.policy_decision.actor,
            capability=decision.policy_decision.capability,
            outcome=decision.outcome,
            approval_required=decision.approval_required,
            audit_id=decision.audit_id,
        )


@dataclass(frozen=True)
class CapabilityExecutionRequest:
    """Caller-supplied request to prepare a capability proposal.

    The service does not execute the described action.  It only validates
    authorization and returns a typed, bounded proposal.
    """

    request_id: str
    actor: Phase5ActorContext
    capability: Capability
    action: Optional[str] = None
    resource: Optional[str] = None
    data_subject: Optional[str] = None
    topic: Optional[str] = None
    goal: Optional[str] = None
    care_prompt: Optional[str] = None
    metadata: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_identifier(self.request_id, "request_id")
        if not isinstance(self.actor, Phase5ActorContext):
            raise ValueError("actor must be Phase5ActorContext")
        if not isinstance(self.capability, Capability):
            raise ValueError("invalid capability")
        # Bound and sanitize optional text fields in-place (strings are
        # immutable so this re-binds local names via object.__setattr__ path).
        # Because the dataclass is frozen, post-init mutation is not allowed;
        # therefore validation only occurs here and values are stored as-is.
        object.__setattr__(self, "action", _sanitize_short_text(self.action, "action"))
        object.__setattr__(self, "resource", _sanitize_short_text(self.resource, "resource"))
        object.__setattr__(self, "data_subject", _sanitize_short_text(self.data_subject, "data_subject"))
        object.__setattr__(self, "topic", _sanitize_text(self.topic, "topic"))
        object.__setattr__(self, "goal", _sanitize_text(self.goal, "goal"))
        object.__setattr__(self, "care_prompt", _sanitize_text(self.care_prompt, "care_prompt"))
        object.__setattr__(self, "metadata", _sanitize_tuple(self.metadata, "metadata"))

    def __repr__(self) -> str:
        return (
            f"CapabilityExecutionRequest(capability={self.capability.value!r}, "
            f"actor={self.actor.actor.value!r})"
        )


@dataclass(frozen=True)
class TeachMeProposal:
    """Proposal for the Teach Me capability.

    This is a lesson or skill-evolution candidate only.  It cannot be
    installed, deployed, activated, published, or treated as owner authority.
    """

    outline: Tuple[str, ...]
    learning_steps: Tuple[str, ...]
    review_questions: Tuple[str, ...]
    skill_evolution_candidate_id: Optional[str] = None
    assistant_authored: bool = True

    def __post_init__(self) -> None:
        if not all(isinstance(item, str) for item in self.outline):
            raise ValueError("outline must contain strings")
        if not all(isinstance(item, str) for item in self.learning_steps):
            raise ValueError("learning_steps must contain strings")
        if not all(isinstance(item, str) for item in self.review_questions):
            raise ValueError("review_questions must contain strings")
        if len(self.outline) > _MAX_COLLECTION_SIZE:
            raise ValueError("outline exceeds maximum size")
        if len(self.learning_steps) > _MAX_COLLECTION_SIZE:
            raise ValueError("learning_steps exceeds maximum size")
        if len(self.review_questions) > _MAX_COLLECTION_SIZE:
            raise ValueError("review_questions exceeds maximum size")
        if self.skill_evolution_candidate_id is not None:
            _validate_identifier(self.skill_evolution_candidate_id, "skill_evolution_candidate_id")

    def __repr__(self) -> str:
        return (
            f"TeachMeProposal(outline={len(self.outline)}, "
            f"steps={len(self.learning_steps)}, "
            f"questions={len(self.review_questions)})"
        )


@dataclass(frozen=True)
class GuideStep:
    """A single guidance step."""

    description: str
    kind: GuideStepKind
    requires_approval: bool

    def __post_init__(self) -> None:
        if not isinstance(self.description, str):
            raise ValueError("description must be a string")
        if not isinstance(self.kind, GuideStepKind):
            raise ValueError("invalid kind")
        if not isinstance(self.requires_approval, bool):
            raise ValueError("requires_approval must be boolean")

    def __repr__(self) -> str:
        return f"GuideStep(kind={self.kind.value!r})"


@dataclass(frozen=True)
class GuideHandsProposal:
    """Proposal for the Guide My Hands capability.

    Contains ordered, bounded guidance steps.  No step performs a physical or
    OS action.  Consequential steps require explicit approval.
    """

    steps: Tuple[GuideStep, ...]
    observation_requests: Tuple[str, ...]
    clarification_prompts: Tuple[str, ...]
    uncertainty_disclosed: bool = False

    def __post_init__(self) -> None:
        if len(self.steps) > _MAX_COLLECTION_SIZE:
            raise ValueError("steps exceeds maximum size")
        if not all(isinstance(s, GuideStep) for s in self.steps):
            raise ValueError("steps must contain GuideStep values")
        if len(self.observation_requests) > _MAX_COLLECTION_SIZE:
            raise ValueError("observation_requests exceeds maximum size")
        if len(self.clarification_prompts) > _MAX_COLLECTION_SIZE:
            raise ValueError("clarification_prompts exceeds maximum size")

    def __repr__(self) -> str:
        return (
            f"GuideHandsProposal(steps={len(self.steps)}, "
            f"observations={len(self.observation_requests)})"
        )


@dataclass(frozen=True)
class CareProposal:
    """Proposal for the Care capability.

    Provides supportive assistance only.  No diagnosis, prescription,
    treatment, or fabricated contact claims.  Emergency language produces a
    bounded recommendation and an approval/escalation state.
    """

    supportive_language: Tuple[str, ...]
    check_in_questions: Tuple[str, ...]
    contact_trusted_human_prompt: Optional[str]
    emergency_recommendation: Optional[str]
    approval_required: bool = True

    def __post_init__(self) -> None:
        if not all(isinstance(item, str) for item in self.supportive_language):
            raise ValueError("supportive_language must contain strings")
        if not all(isinstance(item, str) for item in self.check_in_questions):
            raise ValueError("check_in_questions must contain strings")
        if len(self.supportive_language) > _MAX_COLLECTION_SIZE:
            raise ValueError("supportive_language exceeds maximum size")
        if len(self.check_in_questions) > _MAX_COLLECTION_SIZE:
            raise ValueError("check_in_questions exceeds maximum size")
        if not isinstance(self.approval_required, bool):
            raise ValueError("approval_required must be boolean")

    def __repr__(self) -> str:
        return (
            f"CareProposal(supportive={len(self.supportive_language)}, "
            f"check_ins={len(self.check_in_questions)})"
        )


CapabilityProposal = Union[TeachMeProposal, GuideHandsProposal, CareProposal]


@dataclass(frozen=True)
class CapabilityServiceDecision:
    """Immutable result of a capability-service prepare call.

    No raw user content or runtime secrets are emitted by ``__repr__``.
    """

    request_id: str
    outcome: Outcome
    reason: CapabilityServiceReason
    approval_required: bool
    proposal: Optional[CapabilityProposal] = None
    audit_id: Optional[str] = None

    def __post_init__(self) -> None:
        _validate_identifier(self.request_id, "request_id")
        if not isinstance(self.outcome, Outcome):
            raise ValueError("invalid outcome")
        if not isinstance(self.reason, CapabilityServiceReason):
            raise ValueError("invalid reason")
        if not isinstance(self.approval_required, bool):
            raise ValueError("approval_required must be boolean")
        if self.proposal is not None and not isinstance(
            self.proposal, (TeachMeProposal, GuideHandsProposal, CareProposal)
        ):
            raise ValueError("invalid proposal type")
        if self.audit_id is not None:
            _validate_identifier(self.audit_id, "audit_id")

    def __repr__(self) -> str:
        return (
            f"CapabilityServiceDecision(outcome={self.outcome.value!r}, "
            f"reason={self.reason.value!r})"
        )


# --- Service implementation ---------------------------------------------------


class Phase5CapabilityService:
    """Stateless, pure capability-service layer.

    ``prepare`` validates the supplied runtime authorization and returns a
    typed, bounded proposal.  It does not execute actions, perform I/O, or
    mutate any external state.
    """

    def prepare(
        self,
        request: CapabilityExecutionRequest,
        authorization: Phase5RuntimeDecision,
    ) -> CapabilityServiceDecision:
        """Prepare a capability proposal behind a runtime authorization."""
        # 1. Validate structural input.
        if not isinstance(request, CapabilityExecutionRequest):
            return self._deny(request, CapabilityServiceReason.INVALID_INPUT)
        if not isinstance(authorization, Phase5RuntimeDecision):
            return self._deny(request, CapabilityServiceReason.INVALID_INPUT)

        # Child ambiguity is intentionally decided before the policy layer and
        # therefore has no policy decision to turn into a proof. It may only
        # propagate an approval requirement; it can never produce a proposal.
        if authorization.policy_decision is None:
            if (
                authorization.request_id == request.request_id
                and authorization.outcome is Outcome.REQUIRE_APPROVAL
                and authorization.approval_required
            ):
                return CapabilityServiceDecision(
                    request_id=request.request_id,
                    outcome=Outcome.REQUIRE_APPROVAL,
                    reason=CapabilityServiceReason.APPROVAL_REQUIRED,
                    approval_required=True,
                    audit_id=authorization.audit_id,
                )
            return self._deny(request, CapabilityServiceReason.AUTHORIZATION_INVALID)

        # 2. Extract and verify authorization proof.
        try:
            proof = CapabilityAuthorizationProof.from_runtime_decision(authorization)
        except ValueError:
            return self._deny(request, CapabilityServiceReason.AUTHORIZATION_INVALID)

        # 3. Reject terminal denial outcomes.
        if _is_terminal_deny(proof.outcome):
            return self._deny(request, CapabilityServiceReason.UNAUTHORIZED)

        # 4. Verify request/authorization match.
        if proof.request_id != request.request_id:
            return self._deny(request, CapabilityServiceReason.AUTHORIZATION_INVALID)
        if proof.actor_id != request.actor.actor_id:
            return self._deny(request, CapabilityServiceReason.AUTHORIZATION_INVALID)
        if proof.actor != request.actor.actor:
            return self._deny(request, CapabilityServiceReason.AUTHORIZATION_INVALID)
        if proof.capability != request.capability:
            return self._deny(request, CapabilityServiceReason.AUTHORIZATION_INVALID)

        # 5. Dispatch only supported capabilities.
        if request.capability is Capability.TEACH_ME:
            return self._prepare_teach_me(request, proof)
        if request.capability is Capability.GUIDE_MY_HANDS:
            return self._prepare_guide_my_hands(request, proof)
        if request.capability is Capability.CARE:
            return self._prepare_care(request, proof)

        return self._deny(request, CapabilityServiceReason.UNKNOWN_CAPABILITY)

    # ------------------------------------------------------------------
    # Capability-specific preparers
    # ------------------------------------------------------------------

    def _prepare_teach_me(
        self,
        request: CapabilityExecutionRequest,
        proof: CapabilityAuthorizationProof,
    ) -> CapabilityServiceDecision:
        # Direct-install attempts are denied even if the runtime decision were
        # somehow permissive; the service layer enforces proposal-only semantics.
        if request.action and any(
            keyword in request.action.lower()
            for keyword in ("install", "deploy", "activate", "publish")
        ):
            return CapabilityServiceDecision(
                request_id=request.request_id,
                outcome=Outcome.DENY,
                reason=CapabilityServiceReason.UNAUTHORIZED,
                approval_required=False,
                audit_id=proof.audit_id,
            )

        # If the runtime decision says approval is required (e.g., child
        # ambiguous or unapproved helper), preserve it.
        if proof.outcome is Outcome.REQUIRE_APPROVAL:
            return CapabilityServiceDecision(
                request_id=request.request_id,
                outcome=Outcome.REQUIRE_APPROVAL,
                reason=CapabilityServiceReason.APPROVAL_REQUIRED,
                approval_required=True,
                audit_id=proof.audit_id,
            )

        # Build a bounded proposal.  Content is generated from caller-supplied
        # topic/goal, but is bounded and sanitized.
        outline = _teach_me_outline(request.topic, request.goal)
        learning_steps = _teach_me_steps(request.topic, request.goal)
        review_questions = _teach_me_review(request.topic, request.goal)
        proposal = TeachMeProposal(
            outline=outline,
            learning_steps=learning_steps,
            review_questions=review_questions,
            skill_evolution_candidate_id=None,
            assistant_authored=True,
        )
        return CapabilityServiceDecision(
            request_id=request.request_id,
            outcome=Outcome.ALLOW,
            reason=CapabilityServiceReason.TEACH_ME_PROPOSAL,
            approval_required=False,
            proposal=proposal,
            audit_id=proof.audit_id,
        )

    def _prepare_guide_my_hands(
        self,
        request: CapabilityExecutionRequest,
        proof: CapabilityAuthorizationProof,
    ) -> CapabilityServiceDecision:
        # Approval-required runtime decisions must remain proposals for approval.
        if proof.outcome is Outcome.REQUIRE_APPROVAL:
            return CapabilityServiceDecision(
                request_id=request.request_id,
                outcome=Outcome.REQUIRE_APPROVAL,
                reason=CapabilityServiceReason.APPROVAL_REQUIRED,
                approval_required=True,
                audit_id=proof.audit_id,
            )

        steps = _guide_steps_from_request(request)
        observation_requests: Tuple[str, ...] = ()
        clarification_prompts: Tuple[str, ...] = ()
        uncertainty_disclosed = False

        if request.goal and "uncertain" in request.goal.lower():
            clarification_prompts = (
                "Please describe what you observe so the next step can be chosen safely.",
            )
            uncertainty_disclosed = True

        proposal = GuideHandsProposal(
            steps=steps,
            observation_requests=observation_requests,
            clarification_prompts=clarification_prompts,
            uncertainty_disclosed=uncertainty_disclosed,
        )
        return CapabilityServiceDecision(
            request_id=request.request_id,
            outcome=Outcome.ALLOW,
            reason=CapabilityServiceReason.GUIDE_HANDS_PROPOSAL,
            approval_required=False,
            proposal=proposal,
            audit_id=proof.audit_id,
        )

    def _prepare_care(
        self,
        request: CapabilityExecutionRequest,
        proof: CapabilityAuthorizationProof,
    ) -> CapabilityServiceDecision:
        # Hard-deny diagnosis/prescription/treatment/medical instructions.
        if request.action and any(
            keyword in request.action.lower()
            for keyword in ("diagnos", "prescrib", "treat", "medical")
        ):
            return CapabilityServiceDecision(
                request_id=request.request_id,
                outcome=Outcome.DENY,
                reason=CapabilityServiceReason.CARE_DIAGNOSIS_DENIED,
                approval_required=False,
                audit_id=proof.audit_id,
            )

        # Hard-deny claims that emergency services were already contacted.
        if request.metadata and any(
            term in item.lower()
            for item in request.metadata
            for term in ("contacted", "called", "notified")
        ):
            if not any(
                evidence in item.lower()
                for item in request.metadata
                for evidence in ("evidence", "confirmed")
            ):
                return CapabilityServiceDecision(
                    request_id=request.request_id,
                    outcome=Outcome.DENY,
                    reason=CapabilityServiceReason.CARE_FALSE_CONTACT_DENIED,
                    approval_required=False,
                    audit_id=proof.audit_id,
                )

        emergency_recommendation = _care_emergency_recommendation(request)

        # Care is always treated as approval-gated at the service layer, even
        # when the runtime decision is ALLOW.  This preserves the HIGH-risk
        # boundary and ensures no executable care output is produced without an
        # explicit owner approval signal.
        if proof.outcome is Outcome.REQUIRE_APPROVAL:
            proposal = CareProposal(
                supportive_language=(_FIXED_SUPPORTIVE_MESSAGE,),
                check_in_questions=_FIXED_CHECK_IN_QUESTIONS,
                contact_trusted_human_prompt=_FIXED_CONTACT_PROMPT,
                emergency_recommendation=emergency_recommendation,
                approval_required=True,
            )
            return CapabilityServiceDecision(
                request_id=request.request_id,
                outcome=Outcome.REQUIRE_APPROVAL,
                reason=CapabilityServiceReason.APPROVAL_REQUIRED,
                approval_required=True,
                proposal=proposal,
                audit_id=proof.audit_id,
            )

        # ALLOW means the runtime already matched explicit scoped consent.
        proposal = CareProposal(
            supportive_language=(_FIXED_SUPPORTIVE_MESSAGE,),
            check_in_questions=_FIXED_CHECK_IN_QUESTIONS,
            contact_trusted_human_prompt=_FIXED_CONTACT_PROMPT,
            emergency_recommendation=emergency_recommendation,
            approval_required=False,
        )
        return CapabilityServiceDecision(
            request_id=request.request_id,
            outcome=Outcome.ALLOW,
            reason=CapabilityServiceReason.CARE_PROPOSAL,
            approval_required=False,
            proposal=proposal,
            audit_id=proof.audit_id,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _deny(
        self,
        request: CapabilityExecutionRequest,
        reason: CapabilityServiceReason,
    ) -> CapabilityServiceDecision:
        request_id = request.request_id if isinstance(request, CapabilityExecutionRequest) else "invalid"
        return CapabilityServiceDecision(
            request_id=request_id,
            outcome=Outcome.DENY,
            reason=reason,
            approval_required=False,
        )


# --- Fixed safe content -------------------------------------------------------

_FIXED_SUPPORTIVE_MESSAGE = (
    "I'm here to support you. If you need help, consider reaching out to a "
    "trusted person nearby."
)

_FIXED_CHECK_IN_QUESTIONS: Tuple[str, ...] = (
    "How are you feeling right now?",
    "Is there a trusted person with you?",
)

_FIXED_CONTACT_PROMPT = (
    "If you feel unsafe or unwell, please ask a trusted adult or contact a "
    "local emergency service."
)

_FIXED_EMERGENCY_MESSAGE = (
    "If this is an emergency, contact a trusted adult or local emergency "
    "services right away. I cannot call them for you."
)

_EmergencyTerms = frozenset({
    "emergency",
    "urgent",
    "unsafe",
    "hurt",
    "injured",
    "bleeding",
    "danger",
    "crisis",
    "scared",
    "afraid",
})


def _care_emergency_recommendation(request: CapabilityExecutionRequest) -> Optional[str]:
    """Return a fixed emergency recommendation if the request signals urgency."""
    text_parts: list[str] = []
    if request.care_prompt:
        text_parts.append(request.care_prompt.lower())
    if request.goal:
        text_parts.append(request.goal.lower())
    if request.metadata:
        text_parts.extend(item.lower() for item in request.metadata)
    if not text_parts:
        return None
    if any(term in part for part in text_parts for term in _EmergencyTerms):
        return _FIXED_EMERGENCY_MESSAGE
    return None


# --- Proposal builders --------------------------------------------------------

def _teach_me_outline(topic: Optional[str], goal: Optional[str]) -> Tuple[str, ...]:
    base = "Explore fundamentals"
    if topic:
        base = f"Explore {topic} fundamentals"
    if goal:
        base = f"{base} toward {goal}"
    return (
        "Introduce the topic",
        base,
        "Practice with examples",
        "Review and reflect",
    )


def _teach_me_steps(topic: Optional[str], goal: Optional[str]) -> Tuple[str, ...]:
    subject = topic or "the topic"
    target = goal or "your goal"
    return (
        f"Read an age-appropriate introduction to {subject}.",
        f"Identify the key concepts needed for {target}.",
        f"Work through a short guided example about {subject}.",
        f"Summarize what you learned about {subject}.",
    )


def _teach_me_review(topic: Optional[str], goal: Optional[str]) -> Tuple[str, ...]:
    subject = topic or "the topic"
    return (
        f"What is the most important idea about {subject}?",
        f"How does {subject} relate to {goal or 'your goal'}?",
        "Name one thing you are still curious about.",
    )


def _guide_steps_from_request(request: CapabilityExecutionRequest) -> Tuple[GuideStep, ...]:
    steps: list[GuideStep] = [
        GuideStep(
            description="Confirm the goal and any safety constraints before proceeding.",
            kind=GuideStepKind.INFORMATIONAL,
            requires_approval=False,
        ),
        GuideStep(
            description="Observe the current state and describe what you see.",
            kind=GuideStepKind.OBSERVATIONAL,
            requires_approval=False,
        ),
    ]
    if request.action and any(
        keyword in request.action.lower()
        for keyword in ("execute", "perform", "apply", "confirm")
    ):
        steps.append(
            GuideStep(
                description="This step may have consequences; explicit approval is required.",
                kind=GuideStepKind.CONSEQUENTIAL,
                requires_approval=True,
            )
        )
    else:
        steps.append(
            GuideStep(
                description="Follow the next informational step.",
                kind=GuideStepKind.INFORMATIONAL,
                requires_approval=False,
            )
        )
    return tuple(steps)


# --- Utility --------------------------------------------------------------------


def _is_terminal_deny(outcome: Outcome) -> bool:
    return outcome in {
        Outcome.DENY,
        Outcome.EXPIRED,
        Outcome.REVOKED,
        Outcome.OUT_OF_SCOPE,
        Outcome.AUTHENTICATION_REQUIRED,
    }


__all__ = [
    "CapabilityAuthorizationProof",
    "CapabilityExecutionRequest",
    "CapabilityProposal",
    "CapabilityProposalKind",
    "CapabilityServiceDecision",
    "CapabilityServiceReason",
    "GuideStep",
    "GuideStepKind",
    "GuideHandsProposal",
    "CareProposal",
    "TeachMeProposal",
    "Phase5CapabilityService",
]
