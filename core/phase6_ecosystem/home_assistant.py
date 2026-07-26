"""Home Assistant authority boundaries and safe contract evaluator for Phase 6.

Provides pure, non-network preparation and authorization contracts for Home Assistant
entities, services, state-changing confirmations, and evidence-backed observations.
Does not perform HTTP/MQTT/network calls or execute real Home Assistant commands.
"""

from __future__ import annotations

import re
import math
from dataclasses import dataclass
from types import MappingProxyType
from enum import StrEnum
from typing import Any, FrozenSet, Mapping, Optional, Tuple

from core.action_policy import Actor, ActorContext, validate_actor_context

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_ENTITY_ID_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")

# Default sensitive domains requiring strict owner approval
DEFAULT_SENSITIVE_DOMAINS: FrozenSet[str] = frozenset({
    "lock", "alarm_control_panel", "cover", "door", "camera", "valve", "device_tracker"
})

# Default read-only services
DEFAULT_READ_ONLY_SERVICES: FrozenSet[str] = frozenset({
    "get_state", "listen_event", "read_sensor"
})


class HAActionOutcome(StrEnum):
    """Fixed outcome for Home Assistant action evaluation."""

    ALLOW = "allow"
    REQUIRE_CONFIRMATION = "require_confirmation"
    DENY = "deny"


class HAActionReason(StrEnum):
    """Fixed, non-attributable reason codes for Home Assistant actions."""

    OK = "ok"
    PREPARE_SUCCESS = "prepare_success"
    STALE_PROPOSAL = "stale_proposal"
    EXPIRED_PROPOSAL = "expired_proposal"
    REPLAY_DETECTED = "replay_detected"
    NONCE_MISMATCH = "nonce_mismatch"
    ENTITY_NOT_ALLOWED = "entity_not_allowed"
    DOMAIN_NOT_ALLOWED = "domain_not_allowed"
    SERVICE_NOT_ALLOWED = "service_not_allowed"
    WILDCARD_NOT_ALLOWED = "wildcard_not_allowed"
    SENSITIVE_ENTITY_DENIED = "sensitive_entity_denied"
    UNAUTHORIZED_ACTOR = "unauthorized_actor"
    STATE_CHANGING_UNCONFIRMED = "state_changing_unconfirmed"
    MISSING_EVIDENCE = "missing_evidence"
    INVALID_INPUT = "invalid_input"


@dataclass(frozen=True)
class HomeAssistantEntityRef:
    """Exact reference to a Home Assistant entity (no wildcards allowed)."""

    domain: str
    entity_id: str

    def __post_init__(self) -> None:
        if "*" in self.domain or "*" in self.entity_id:
            raise ValueError("wildcard characters ('*') prohibited in entity reference")
        if not _ENTITY_ID_RE.fullmatch(self.entity_id):
            raise ValueError(f"invalid entity_id format: '{self.entity_id}'")
        if not self.entity_id.startswith(f"{self.domain}."):
            raise ValueError(f"entity_id domain mismatch: '{self.entity_id}' does not match domain '{self.domain}'")

    def __repr__(self) -> str:
        return "HomeAssistantEntityRef()"


@dataclass(frozen=True)
class HomeAssistantServiceRef:
    """Exact reference to a Home Assistant domain service."""

    domain: str
    service: str

    def __post_init__(self) -> None:
        if "*" in self.domain or "*" in self.service:
            raise ValueError("wildcard characters ('*') prohibited in service reference")
        if not _IDENTIFIER_RE.fullmatch(self.domain):
            raise ValueError("invalid domain identifier")
        if not _IDENTIFIER_RE.fullmatch(self.service):
            raise ValueError("invalid service identifier")

    def __repr__(self) -> str:
        return "HomeAssistantServiceRef()"


@dataclass(frozen=True)
class HomeAssistantCapabilityManifest:
    """Allowed capability manifest defining explicit Home Assistant boundaries."""

    allowed_domains: FrozenSet[str]
    allowed_entities: FrozenSet[str]
    allowed_services: FrozenSet[str]
    sensitive_domains: FrozenSet[str] = DEFAULT_SENSITIVE_DOMAINS
    sensitive_entities: FrozenSet[str] = frozenset()
    read_only_services: FrozenSet[str] = DEFAULT_READ_ONLY_SERVICES

    def __post_init__(self) -> None:
        for value in (
            self.allowed_domains,
            self.allowed_entities,
            self.allowed_services,
            self.sensitive_domains,
            self.sensitive_entities,
            self.read_only_services,
        ):
            if not isinstance(value, frozenset) or len(value) > 512:
                raise ValueError("manifest values must be bounded frozensets")
        for item in self.allowed_domains | self.allowed_entities | self.allowed_services:
            if not isinstance(item, str) or "*" in item or not _IDENTIFIER_RE.fullmatch(item):
                raise ValueError("wildcards ('*') prohibited in capability manifest")

    def is_allowed(self, entity_ref: HomeAssistantEntityRef, service_ref: HomeAssistantServiceRef) -> bool:
        if entity_ref.domain not in self.allowed_domains:
            return False
        if entity_ref.entity_id not in self.allowed_entities:
            return False
        if service_ref.service not in self.allowed_services:
            return False
        if service_ref.domain != entity_ref.domain:
            return False
        return True

    def is_sensitive(self, entity_ref: HomeAssistantEntityRef) -> bool:
        return (entity_ref.domain in self.sensitive_domains) or (entity_ref.entity_id in self.sensitive_entities)

    def is_state_changing(self, service_ref: HomeAssistantServiceRef) -> bool:
        return service_ref.service not in self.read_only_services

    def __repr__(self) -> str:
        return "HomeAssistantCapabilityManifest()"


@dataclass(frozen=True)
class HomeAssistantActionProposal:
    """Prepared proposal for a Home Assistant action."""

    proposal_id: str
    entity_ref: HomeAssistantEntityRef
    service_ref: HomeAssistantServiceRef
    service_data: Mapping[str, Any]
    is_state_changing: bool
    prepared_at: float
    expires_at: float
    nonce: str

    def __post_init__(self) -> None:
        if not _IDENTIFIER_RE.fullmatch(self.proposal_id) or not _IDENTIFIER_RE.fullmatch(self.nonce):
            raise ValueError("invalid proposal identifier")
        if not isinstance(self.entity_ref, HomeAssistantEntityRef) or not isinstance(self.service_ref, HomeAssistantServiceRef):
            raise ValueError("invalid Home Assistant reference")
        if not isinstance(self.service_data, Mapping) or len(self.service_data) > 32:
            raise ValueError("invalid service_data")
        frozen: dict[str, Any] = {}
        for key, value in self.service_data.items():
            if not isinstance(key, str) or not _IDENTIFIER_RE.fullmatch(key):
                raise ValueError("invalid service_data key")
            if not isinstance(value, (str, int, float, bool, type(None))):
                raise ValueError("invalid service_data value")
            if isinstance(value, str) and (len(value) > 512 or any(ord(ch) < 32 for ch in value)):
                raise ValueError("invalid service_data value")
            frozen[key] = value
        object.__setattr__(self, "service_data", MappingProxyType(frozen))
        if not isinstance(self.is_state_changing, bool):
            raise ValueError("invalid state-changing flag")
        for value in (self.prepared_at, self.expires_at):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise ValueError("invalid proposal time")
        if self.expires_at <= self.prepared_at:
            raise ValueError("proposal expiry must follow preparation")

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at

    def __repr__(self) -> str:
        return "HomeAssistantActionProposal()"


@dataclass(frozen=True)
class HomeAssistantConfirmation:
    """Explicit human owner confirmation for a state-changing proposal."""

    proposal_id: str
    nonce: str
    confirmed_by_actor_id: str
    confirmed_at: float

    def __post_init__(self) -> None:
        for value in (self.proposal_id, self.nonce, self.confirmed_by_actor_id):
            if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
                raise ValueError("invalid confirmation identifier")
        if not isinstance(self.confirmed_at, (int, float)) or isinstance(self.confirmed_at, bool) or not math.isfinite(self.confirmed_at):
            raise ValueError("invalid confirmed_at")

    def __repr__(self) -> str:
        return "HomeAssistantConfirmation()"


@dataclass(frozen=True)
class HomeAssistantObservation:
    """Recorded observation backed by explicit caller-supplied evidence."""

    observation_id: str
    proposal_id: str
    success: bool
    result_evidence: Optional[str]
    observed_at: float

    def __post_init__(self) -> None:
        if not _IDENTIFIER_RE.fullmatch(self.observation_id) or not _IDENTIFIER_RE.fullmatch(self.proposal_id):
            raise ValueError("invalid observation identifier")
        if self.success and not self.result_evidence:
            raise ValueError("successful observation requires explicit result_evidence")
        if self.result_evidence is not None and (
            not isinstance(self.result_evidence, str) or len(self.result_evidence) > 256
        ):
            raise ValueError("invalid result_evidence")

    def __repr__(self) -> str:
        return "HomeAssistantObservation()"


class HomeAssistantTransportInterface:
    """Injected transport interface with zero network implementation."""

    def execute_authorized_plan(self, proposal: HomeAssistantActionProposal) -> HomeAssistantObservation:
        raise NotImplementedError("transport is non-network contract interface")


@dataclass(frozen=True)
class HAEvaluationResult:
    """Evaluation result for Home Assistant actions."""

    outcome: HAActionOutcome
    reason: HAActionReason
    proposal: Optional[HomeAssistantActionProposal] = None

    def __repr__(self) -> str:
        return "HAEvaluationResult()"


class HomeAssistantContractEvaluator:
    """Pure evaluator for Home Assistant action preparation and confirmation."""

    def __init__(self, manifest: HomeAssistantCapabilityManifest, proposal_ttl_seconds: float = 300.0):
        if not isinstance(manifest, HomeAssistantCapabilityManifest):
            raise ValueError("invalid manifest")
        if not isinstance(proposal_ttl_seconds, (int, float)) or not 1.0 <= proposal_ttl_seconds <= 3600.0:
            raise ValueError("invalid proposal TTL")
        self.manifest = manifest
        self.proposal_ttl_seconds = proposal_ttl_seconds
        self.seen_nonces: set[str] = set()
        self.consumed_confirmations: set[tuple[str, str]] = set()

    def prepare_action(
        self,
        proposal_id: str,
        entity_ref: HomeAssistantEntityRef,
        service_ref: HomeAssistantServiceRef,
        service_data: Mapping[str, Any],
        actor_context: ActorContext,
        nonce: str,
        now: float,
    ) -> HAEvaluationResult:
        """Prepare a Home Assistant action proposal without calling any network or service."""
        valid_actor, _ = validate_actor_context(actor_context)
        if not valid_actor:
            return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.UNAUTHORIZED_ACTOR)

        if service_ref.domain != entity_ref.domain:
            return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.DOMAIN_NOT_ALLOWED)

        # Check replay protection. Invalid proposals do not consume nonce space.
        if nonce in self.seen_nonces:
            return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.REPLAY_DETECTED)

        # Domain / Entity / Service checks
        if entity_ref.domain not in self.manifest.allowed_domains:
            return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.DOMAIN_NOT_ALLOWED)
        if entity_ref.entity_id not in self.manifest.allowed_entities:
            return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.ENTITY_NOT_ALLOWED)
        if service_ref.service not in self.manifest.allowed_services:
            return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.SERVICE_NOT_ALLOWED)
        if service_data:
            # Field schemas are not yet part of this pure foundation. Deny
            # payload-bearing calls rather than accepting unreviewed fields.
            return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.INVALID_INPUT)

        is_sensitive = self.manifest.is_sensitive(entity_ref)
        is_state_changing = self.manifest.is_state_changing(service_ref)

        # Child / Guest / Non-owner actors receive DENY for sensitive or state-changing entities
        if actor_context.actor != Actor.OWNER and (is_sensitive or is_state_changing):
            return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.UNAUTHORIZED_ACTOR)
        if len(self.seen_nonces) >= 4096:
            return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.REPLAY_DETECTED)
        self.seen_nonces.add(nonce)

        proposal = HomeAssistantActionProposal(
            proposal_id=proposal_id,
            entity_ref=entity_ref,
            service_ref=service_ref,
            service_data=service_data,
            is_state_changing=is_state_changing,
            prepared_at=now,
            expires_at=now + self.proposal_ttl_seconds,
            nonce=nonce,
        )

        if is_sensitive or is_state_changing:
            return HAEvaluationResult(HAActionOutcome.REQUIRE_CONFIRMATION, HAActionReason.PREPARE_SUCCESS, proposal)

        return HAEvaluationResult(HAActionOutcome.ALLOW, HAActionReason.OK, proposal)

    def authorize_execution(
        self,
        proposal: HomeAssistantActionProposal,
        confirmation: Optional[HomeAssistantConfirmation],
        actor_context: ActorContext,
        now: float,
    ) -> HAEvaluationResult:
        """Recheck policy and authorize execution plan for a proposal."""
        valid_actor, _ = validate_actor_context(actor_context)
        if not valid_actor:
            return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.UNAUTHORIZED_ACTOR)

        if proposal.is_expired(now):
            return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.EXPIRED_PROPOSAL)

        if proposal.nonce not in self.seen_nonces:
            return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.STALE_PROPOSAL)
        if not self.manifest.is_allowed(proposal.entity_ref, proposal.service_ref):
            return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.STALE_PROPOSAL)

        if proposal.is_state_changing or self.manifest.is_sensitive(proposal.entity_ref):
            if confirmation is None:
                return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.STATE_CHANGING_UNCONFIRMED)
            if confirmation.proposal_id != proposal.proposal_id:
                return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.NONCE_MISMATCH)
            if confirmation.nonce != proposal.nonce:
                return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.NONCE_MISMATCH)
            if actor_context.actor != Actor.OWNER:
                return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.UNAUTHORIZED_ACTOR)
            if confirmation.confirmed_by_actor_id != actor_context.actor_id:
                return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.UNAUTHORIZED_ACTOR)
            if not proposal.prepared_at <= confirmation.confirmed_at <= now:
                return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.STALE_PROPOSAL)
            confirmation_key = (confirmation.proposal_id, confirmation.nonce)
            if confirmation_key in self.consumed_confirmations:
                return HAEvaluationResult(HAActionOutcome.DENY, HAActionReason.REPLAY_DETECTED)
            self.consumed_confirmations.add(confirmation_key)

        return HAEvaluationResult(HAActionOutcome.ALLOW, HAActionReason.OK, proposal)

    def __repr__(self) -> str:
        return "HomeAssistantContractEvaluator()"
