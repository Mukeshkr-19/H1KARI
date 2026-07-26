"""Home Assistant optional adapter with fail-closed transport safety.

This module composes the pure Home Assistant evaluator from
``core.phase6_ecosystem.home_assistant`` with a hardened transport-level
contract.  All networking, audit, clock, and ID factory behavior is injected.
Default construction leaves the adapter disabled.
"""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, FrozenSet, Mapping, Optional, Tuple
from urllib.parse import urlparse

from core.action_policy import ActorContext
from core.phase6_ecosystem.home_assistant import (
    HAActionOutcome,
    HAActionReason,
    HomeAssistantActionProposal,
    HomeAssistantCapabilityManifest,
    HomeAssistantConfirmation,
    HomeAssistantContractEvaluator,
    HomeAssistantEntityRef,
    HomeAssistantObservation,
    HomeAssistantServiceRef,
)

from core.phase6_adapters.contracts import AdapterException, AdapterReason, AdapterState


class HomeAssistantAdapterReason(StrEnum):
    """Fixed reason codes for the Home Assistant adapter."""

    OK = "ok"
    DISABLED = "disabled"
    INVALID_CONFIGURATION = "invalid_configuration"
    INVALID_URL = "invalid_url"
    SCHEME_NOT_ALLOWED = "scheme_not_allowed"
    HOST_NOT_ALLOWED = "host_not_allowed"
    WILDCARD_IN_URL = "wildcard_in_url"
    USERINFO_IN_URL = "userinfo_in_url"
    FRAGMENT_IN_URL = "fragment_in_url"
    MALFORMED_PORT = "malformed_port"
    REDIRECT_TARGET_REJECTED = "redirect_target_rejected"
    DNS_HOST_MISMATCH = "dns_host_mismatch"
    UNAUTHORIZED_ACTOR = "unauthorized_actor"
    POLICY_DENIED = "policy_denied"
    PREPARE_REQUIRED = "prepare_required"
    CONFIRMATION_MISMATCH = "confirmation_mismatch"
    REPLAY_DETECTED = "replay_detected"
    EXPIRED_PROPOSAL = "expired_proposal"
    TRANSPORT_FAILURE = "transport_failure"
    TIMEOUT_EXCEEDED = "timeout_exceeded"
    RESPONSE_TOO_LARGE = "response_too_large"
    RETRY_EXHAUSTED = "retry_exhausted"
    MISSING_DEPENDENCY = "missing_dependency"
    AUDIT_FAILURE = "audit_failure"


class HomeAssistantAdapterOutcome(StrEnum):
    """Fixed outcomes for the Home Assistant adapter."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class HomeAssistantAdapterConfig:
    """Explicit configuration enabling the Home Assistant adapter.

    Fields:
      - base_url: exact trusted base endpoint, e.g. "https://hass.local:8123"
      - manifest: exact allowed entity/service manifest
      - allowed_schemes: schemes permitted (default: HTTPS/WSS only)
      - allow_loopback_http: if True, permit http:// on loopback addresses
      - request_timeout_seconds: hard timeout per transport call
      - max_response_bytes: hard response byte cap
      - max_retries: bounded retry count
      - proposal_ttl_seconds: confirmation proposal lifetime
    """

    base_url: str
    manifest: HomeAssistantCapabilityManifest
    allowed_schemes: FrozenSet[str] = frozenset({"https", "wss"})
    allow_loopback_http: bool = False
    request_timeout_seconds: float = 30.0
    max_response_bytes: int = 1_048_576
    max_retries: int = 3
    proposal_ttl_seconds: float = 300.0

    def __post_init__(self) -> None:
        if not isinstance(self.base_url, str) or not self.base_url:
            raise ValueError("base_url is required")
        if "*" in self.base_url:
            raise ValueError("wildcards not allowed in base_url")
        parsed = urlparse(self.base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("base_url must have a scheme and host")
        # Accessing .port raises ValueError for malformed ports.
        try:
            parsed.port
        except ValueError:
            raise ValueError("malformed port in base_url")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("userinfo not allowed in base_url")
        if parsed.fragment:
            raise ValueError("fragment not allowed in base_url")
        if parsed.query:
            raise ValueError("query not allowed in base_url")
        if parsed.path and parsed.path not in ("", "/") and "/../" in parsed.path:
            raise ValueError("path traversal not allowed in base_url")
        if not isinstance(self.manifest, HomeAssistantCapabilityManifest):
            raise ValueError("manifest is required")
        if not isinstance(self.allowed_schemes, frozenset) or not self.allowed_schemes:
            raise ValueError("allowed_schemes must be a non-empty frozenset")
        for scheme in self.allowed_schemes:
            if not isinstance(scheme, str) or scheme not in {"http", "https", "ws", "wss"}:
                raise ValueError("disallowed scheme")
        if not isinstance(self.allow_loopback_http, bool):
            raise ValueError("loopback flag must be boolean")
        _reject_bool_nan(self.request_timeout_seconds, "request_timeout_seconds")
        if not 1.0 <= self.request_timeout_seconds <= 300.0:
            raise ValueError("invalid request_timeout_seconds")
        _reject_bool(self.max_response_bytes, "max_response_bytes")
        if not 1 <= self.max_response_bytes <= 100_000_000:
            raise ValueError("invalid max_response_bytes")
        _reject_bool(self.max_retries, "max_retries")
        if not 0 <= self.max_retries <= 16:
            raise ValueError("invalid max_retries")
        _reject_bool_nan(self.proposal_ttl_seconds, "proposal_ttl_seconds")
        if not 1.0 <= self.proposal_ttl_seconds <= 3600.0:
            raise ValueError("invalid proposal_ttl_seconds")

    def __repr__(self) -> str:
        return "HomeAssistantAdapterConfig()"


@dataclass(frozen=True)
class HomeAssistantTransportRequest:
    """Hardened execution envelope for a single Home Assistant transport call."""

    base_url: str
    entity_ref: HomeAssistantEntityRef
    service_ref: HomeAssistantServiceRef
    service_data: Mapping[str, Any]
    proposal_id: str
    nonce: str
    idempotency_key: str
    deadline: float
    max_response_bytes: int
    redirect_policy: str = "deny"

    def __repr__(self) -> str:
        return "HomeAssistantTransportRequest()"


@dataclass(frozen=True)
class HomeAssistantTransportEvidence:
    """Hardened, content-safe evidence returned by a Home Assistant transport."""

    observation_id: str
    proposal_id: str
    final_url: str
    resolved_host: str
    response_byte_count: int
    elapsed_seconds: float
    success_category: Optional[str]
    failure_category: Optional[str]
    idempotency_contract_proven: bool
    observed_at: float

    def __post_init__(self) -> None:
        identifier = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
        if not isinstance(self.observation_id, str) or not identifier.fullmatch(
            self.observation_id
        ):
            raise ValueError("invalid observation_id")
        if not isinstance(self.proposal_id, str) or not identifier.fullmatch(
            self.proposal_id
        ):
            raise ValueError("invalid proposal_id")
        if not isinstance(self.final_url, str) or len(self.final_url) > 2048:
            raise ValueError("invalid final_url")
        if not isinstance(self.resolved_host, str) or not 1 <= len(self.resolved_host) <= 253:
            raise ValueError("invalid resolved_host")
        _reject_bool(self.response_byte_count, "response_byte_count")
        if type(self.response_byte_count) is not int or self.response_byte_count < 0:
            raise ValueError("invalid response_byte_count")
        _reject_bool_nan(self.elapsed_seconds, "elapsed_seconds")
        if self.elapsed_seconds < 0:
            raise ValueError("invalid elapsed_seconds")
        _reject_bool_nan(self.observed_at, "observed_at")
        if not isinstance(self.idempotency_contract_proven, bool):
            raise ValueError("invalid idempotency_contract_proven")
        success_values = {None, "ok", "accepted", "no_content"}
        failure_values = {
            None,
            "transport_error",
            "timeout",
            "rejected",
            "invalid_response",
            "unavailable",
        }
        if self.success_category not in success_values or self.failure_category not in failure_values:
            raise ValueError("invalid transport category")
        if (self.success_category is None) == (self.failure_category is None):
            raise ValueError("exactly one transport category is required")

    def __repr__(self) -> str:
        return "HomeAssistantTransportEvidence()"


class HomeAssistantTransportContract(ABC):
    """Injected production-grade transport contract (no implementation)."""

    @abstractmethod
    def execute_request(
        self,
        request: HomeAssistantTransportRequest,
    ) -> HomeAssistantTransportEvidence:
        """Execute exactly one authorized request and return bounded evidence."""
        ...


class HomeAssistantAdapterResult:
    """Result of a Home Assistant adapter operation."""

    def __init__(
        self,
        outcome: HomeAssistantAdapterOutcome,
        reason: HomeAssistantAdapterReason,
        proposal: Optional[HomeAssistantActionProposal] = None,
        observation: Optional[HomeAssistantObservation] = None,
        evidence: Optional[HomeAssistantTransportEvidence] = None,
    ) -> None:
        self.outcome = outcome
        self.reason = reason
        self.proposal = proposal
        self.observation = observation
        self.evidence = evidence

    def __repr__(self) -> str:
        return "HomeAssistantAdapterResult()"


class HomeAssistantAdapter:
    """Disabled-by-default Home Assistant adapter.

    All side effects are injected: clock, id_factory, audit sink, transport.  No
    network call is made unless the adapter is explicitly enabled and a valid
    proposal has passed confirmation.
    """

    def __init__(
        self,
        *,
        config: Optional[HomeAssistantAdapterConfig] = None,
        clock: Optional[object] = None,
        id_factory: Optional[object] = None,
        auditor: Optional[object] = None,
        transport: Optional[HomeAssistantTransportContract] = None,
    ) -> None:
        self._config = config
        self._clock = clock
        self._id_factory = id_factory
        self._auditor = auditor
        self._transport = transport
        self._evaluator: Optional[HomeAssistantContractEvaluator] = None
        if config is not None:
            self._evaluator = HomeAssistantContractEvaluator(
                manifest=config.manifest,
                proposal_ttl_seconds=config.proposal_ttl_seconds,
            )

    @property
    def state(self) -> AdapterState:
        return AdapterState.ENABLED if self._config is not None else AdapterState.DISABLED

    def _now(self) -> float:
        if self._clock is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        now = self._clock() if callable(self._clock) else self._clock
        if (
            not isinstance(now, (int, float))
            or isinstance(now, bool)
            or not math.isfinite(float(now))
            or float(now) < 0.0
        ):
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        return float(now)

    def _next_id(self) -> str:
        if self._id_factory is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        value = str(self._id_factory() if callable(self._id_factory) else self._id_factory)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
            raise AdapterException(AdapterReason.INVALID_CONFIGURATION)
        return value

    def _record_audit(
        self,
        proposal: HomeAssistantActionProposal,
        outcome: HomeAssistantAdapterOutcome,
        reason: HomeAssistantAdapterReason,
        **extra: Any,
    ) -> bool:
        """Record authorization or terminal audit; failure denies the side effect."""
        if self._auditor is None or not callable(getattr(self._auditor, "record", None)):
            return False
        try:
            self._auditor.record(
                proposal_id=proposal.proposal_id,
                outcome=outcome.value,
                reason=reason.value,
                **extra,
            )
        except Exception:
            return False
        return True

    def _validate_url(self, url: str) -> Tuple[bool, HomeAssistantAdapterReason]:
        if not isinstance(url, str) or not url:
            return False, HomeAssistantAdapterReason.INVALID_URL
        if "*" in url or "@" in url:
            return False, HomeAssistantAdapterReason.WILDCARD_IN_URL if "*" in url else HomeAssistantAdapterReason.USERINFO_IN_URL
        try:
            parsed = urlparse(url)
        except Exception:
            return False, HomeAssistantAdapterReason.INVALID_URL
        if parsed.scheme not in self._config.allowed_schemes:  # type: ignore[union-attr]
            # Loopback exception for http only when explicitly configured
            if parsed.scheme == "http" and self._config.allow_loopback_http:
                host = parsed.hostname or ""
                if host not in ("127.0.0.1", "localhost", "::1"):
                    return False, HomeAssistantAdapterReason.HOST_NOT_ALLOWED
            else:
                return False, HomeAssistantAdapterReason.SCHEME_NOT_ALLOWED
        if parsed.username is not None or parsed.password is not None:
            return False, HomeAssistantAdapterReason.USERINFO_IN_URL
        if parsed.fragment:
            return False, HomeAssistantAdapterReason.FRAGMENT_IN_URL
        host = parsed.hostname or ""
        if not host:
            return False, HomeAssistantAdapterReason.INVALID_URL
        try:
            port = parsed.port
        except ValueError:
            return False, HomeAssistantAdapterReason.MALFORMED_PORT
        if port is not None and (port <= 0 or port > 65535):
            return False, HomeAssistantAdapterReason.MALFORMED_PORT
        # Host/scheme/port must exactly match configured base endpoint.
        base = urlparse(self._config.base_url)  # type: ignore[union-attr]
        if parsed.scheme.lower() != (base.scheme or "").lower():
            return False, HomeAssistantAdapterReason.SCHEME_NOT_ALLOWED
        if host.lower() != (base.hostname or "").lower():
            return False, HomeAssistantAdapterReason.HOST_NOT_ALLOWED
        if port != base.port:
            return False, HomeAssistantAdapterReason.HOST_NOT_ALLOWED
        return True, HomeAssistantAdapterReason.OK

    def prepare(
        self,
        proposal_id: str,
        entity_ref: HomeAssistantEntityRef,
        service_ref: HomeAssistantServiceRef,
        service_data: Mapping[str, Any],
        actor_context: ActorContext,
        nonce: str,
    ) -> HomeAssistantAdapterResult:
        """Prepare a Home Assistant action proposal."""
        if self.state is AdapterState.DISABLED:
            return HomeAssistantAdapterResult(
                HomeAssistantAdapterOutcome.UNAVAILABLE,
                HomeAssistantAdapterReason.DISABLED,
            )
        assert self._config is not None and self._evaluator is not None
        ok, reason = self._validate_url(self._config.base_url)
        if not ok:
            return HomeAssistantAdapterResult(HomeAssistantAdapterOutcome.DENY, reason)
        try:
            result = self._evaluator.prepare_action(
                proposal_id=proposal_id,
                entity_ref=entity_ref,
                service_ref=service_ref,
                service_data=service_data,
                actor_context=actor_context,
                nonce=nonce,
                now=self._now(),
            )
        except Exception:
            return HomeAssistantAdapterResult(
                HomeAssistantAdapterOutcome.DENY,
                HomeAssistantAdapterReason.POLICY_DENIED,
            )
        adapter_outcome = {
            HAActionOutcome.ALLOW: HomeAssistantAdapterOutcome.ALLOW,
            HAActionOutcome.REQUIRE_CONFIRMATION: HomeAssistantAdapterOutcome.REQUIRE_CONFIRMATION,
            HAActionOutcome.DENY: HomeAssistantAdapterOutcome.DENY,
        }[result.outcome]
        adapter_reason = {
            HAActionReason.OK: HomeAssistantAdapterReason.OK,
            HAActionReason.PREPARE_SUCCESS: HomeAssistantAdapterReason.OK,
            HAActionReason.UNAUTHORIZED_ACTOR: HomeAssistantAdapterReason.UNAUTHORIZED_ACTOR,
            HAActionReason.ENTITY_NOT_ALLOWED: HomeAssistantAdapterReason.POLICY_DENIED,
            HAActionReason.DOMAIN_NOT_ALLOWED: HomeAssistantAdapterReason.POLICY_DENIED,
            HAActionReason.SERVICE_NOT_ALLOWED: HomeAssistantAdapterReason.POLICY_DENIED,
            HAActionReason.SENSITIVE_ENTITY_DENIED: HomeAssistantAdapterReason.POLICY_DENIED,
            HAActionReason.STATE_CHANGING_UNCONFIRMED: HomeAssistantAdapterReason.PREPARE_REQUIRED,
            HAActionReason.REPLAY_DETECTED: HomeAssistantAdapterReason.REPLAY_DETECTED,
            HAActionReason.INVALID_INPUT: HomeAssistantAdapterReason.POLICY_DENIED,
        }.get(result.reason, HomeAssistantAdapterReason.POLICY_DENIED)
        return HomeAssistantAdapterResult(adapter_outcome, adapter_reason, proposal=result.proposal)

    def confirm_and_execute(
        self,
        proposal: HomeAssistantActionProposal,
        confirmation: HomeAssistantConfirmation,
        actor_context: ActorContext,
    ) -> HomeAssistantAdapterResult:
        """Confirm a prepared proposal and, if allowed, call the injected transport once."""
        if self.state is AdapterState.DISABLED:
            return HomeAssistantAdapterResult(
                HomeAssistantAdapterOutcome.UNAVAILABLE,
                HomeAssistantAdapterReason.DISABLED,
            )
        assert self._config is not None and self._evaluator is not None
        if self._transport is None:
            return HomeAssistantAdapterResult(
                HomeAssistantAdapterOutcome.UNAVAILABLE,
                HomeAssistantAdapterReason.MISSING_DEPENDENCY,
            )
        ok, reason = self._validate_url(self._config.base_url)
        if not ok:
            return HomeAssistantAdapterResult(HomeAssistantAdapterOutcome.DENY, reason)
        try:
            result = self._evaluator.authorize_execution(
                proposal=proposal,
                confirmation=confirmation,
                actor_context=actor_context,
                now=self._now(),
            )
        except Exception:
            return HomeAssistantAdapterResult(
                HomeAssistantAdapterOutcome.DENY,
                HomeAssistantAdapterReason.POLICY_DENIED,
            )
        if result.outcome is HAActionOutcome.DENY:
            return HomeAssistantAdapterResult(
                HomeAssistantAdapterOutcome.DENY,
                HomeAssistantAdapterReason.POLICY_DENIED,
            )
        if result.outcome is HAActionOutcome.REQUIRE_CONFIRMATION:
            return HomeAssistantAdapterResult(
                HomeAssistantAdapterOutcome.REQUIRE_CONFIRMATION,
                HomeAssistantAdapterReason.CONFIRMATION_MISMATCH,
            )
        # Authorization audit must succeed before any transport invocation.
        if not self._record_audit(proposal, HomeAssistantAdapterOutcome.ALLOW, HomeAssistantAdapterReason.OK):
            return HomeAssistantAdapterResult(HomeAssistantAdapterOutcome.DENY, HomeAssistantAdapterReason.AUDIT_FAILURE)

        # The transport deadline is bound to when the proposal was prepared so
        # that a delayed confirmation cannot extend the effective lifetime.
        now = self._now()
        deadline = proposal.prepared_at + self._config.request_timeout_seconds
        request = HomeAssistantTransportRequest(
            base_url=self._config.base_url,
            entity_ref=proposal.entity_ref,
            service_ref=proposal.service_ref,
            service_data=proposal.service_data,
            proposal_id=proposal.proposal_id,
            nonce=proposal.nonce,
            idempotency_key=self._next_id(),
            deadline=deadline,
            max_response_bytes=self._config.max_response_bytes,
            redirect_policy="deny",
        )

        started = self._now()
        # Do not invoke transport if the deadline has already expired.
        if started >= request.deadline:
            return HomeAssistantAdapterResult(HomeAssistantAdapterOutcome.DENY, HomeAssistantAdapterReason.TIMEOUT_EXCEEDED)

        try:
            evidence = self._transport.execute_request(request)
        except Exception:
            self._record_audit(
                proposal,
                HomeAssistantAdapterOutcome.DENY,
                HomeAssistantAdapterReason.TRANSPORT_FAILURE,
                observation_id=None,
                response_byte_count=0,
                elapsed_seconds=self._now() - started,
            )
            return HomeAssistantAdapterResult(HomeAssistantAdapterOutcome.DENY, HomeAssistantAdapterReason.TRANSPORT_FAILURE)


        after = self._now()
        if not isinstance(evidence, HomeAssistantTransportEvidence):
            self._record_audit(
                proposal,
                HomeAssistantAdapterOutcome.DENY,
                HomeAssistantAdapterReason.TRANSPORT_FAILURE,
                observation_id=None,
                response_byte_count=0,
                elapsed_seconds=after - started,
            )
            return HomeAssistantAdapterResult(HomeAssistantAdapterOutcome.DENY, HomeAssistantAdapterReason.TRANSPORT_FAILURE)

        # Time and size bounds
        elapsed = after - started
        if elapsed > self._config.request_timeout_seconds or after > request.deadline:
            self._record_audit(
                proposal,
                HomeAssistantAdapterOutcome.DENY,
                HomeAssistantAdapterReason.TIMEOUT_EXCEEDED,
                observation_id=evidence.observation_id,
                response_byte_count=evidence.response_byte_count,
                elapsed_seconds=elapsed,
            )
            return HomeAssistantAdapterResult(HomeAssistantAdapterOutcome.DENY, HomeAssistantAdapterReason.TIMEOUT_EXCEEDED)
        if evidence.response_byte_count > self._config.max_response_bytes:
            self._record_audit(
                proposal,
                HomeAssistantAdapterOutcome.DENY,
                HomeAssistantAdapterReason.RESPONSE_TOO_LARGE,
                observation_id=evidence.observation_id,
                response_byte_count=evidence.response_byte_count,
                elapsed_seconds=elapsed,
            )
            return HomeAssistantAdapterResult(HomeAssistantAdapterOutcome.DENY, HomeAssistantAdapterReason.RESPONSE_TOO_LARGE)

        # Redirect / final-host mismatch
        try:
            parsed_final = urlparse(evidence.final_url)
            base = urlparse(self._config.base_url)
            final_host = parsed_final.hostname or ""
            final_port = parsed_final.port
        except (TypeError, ValueError):
            self._record_audit(
                proposal,
                HomeAssistantAdapterOutcome.DENY,
                HomeAssistantAdapterReason.REDIRECT_TARGET_REJECTED,
                observation_id=evidence.observation_id,
                response_byte_count=evidence.response_byte_count,
                elapsed_seconds=elapsed,
            )
            return HomeAssistantAdapterResult(
                HomeAssistantAdapterOutcome.DENY,
                HomeAssistantAdapterReason.REDIRECT_TARGET_REJECTED,
            )
        if evidence.proposal_id != proposal.proposal_id:
            reason = HomeAssistantAdapterReason.CONFIRMATION_MISMATCH
        elif (parsed_final.scheme or "").lower() != (base.scheme or "").lower():
            reason = HomeAssistantAdapterReason.REDIRECT_TARGET_REJECTED
        elif final_host.lower() != (base.hostname or "").lower() or final_port != base.port:
            reason = HomeAssistantAdapterReason.REDIRECT_TARGET_REJECTED
        elif evidence.resolved_host.lower() != (base.hostname or "").lower():
            reason = HomeAssistantAdapterReason.DNS_HOST_MISMATCH
        else:
            reason = None

        if reason is not None:
            self._record_audit(
                proposal,
                HomeAssistantAdapterOutcome.DENY,
                reason,
                observation_id=evidence.observation_id,
                response_byte_count=evidence.response_byte_count,
                elapsed_seconds=elapsed,
            )
            return HomeAssistantAdapterResult(HomeAssistantAdapterOutcome.DENY, reason)

        # State-changing calls receive one invocation unless the transport proves
        # an idempotent retry contract.  The transport got exactly one call above;
        # an idempotency proof in the evidence merely records that the transport
        # considers the operation safely retryable, which we do not act on.
        if proposal.is_state_changing and not evidence.idempotency_contract_proven:
            # one-shot state-changing operation: record that no retry was performed.
            pass

        # Terminal audit with fixed safe data only.  Never echo raw transport
        # category strings; map them to a fixed safe summary.
        if evidence.failure_category is None:
            outcome = HomeAssistantAdapterOutcome.ALLOW
            terminal_reason = HomeAssistantAdapterReason.OK
            terminal_category = "ok"
        else:
            outcome = HomeAssistantAdapterOutcome.DENY
            terminal_reason = HomeAssistantAdapterReason.TRANSPORT_FAILURE
            terminal_category = "error"
        audit_ok = self._record_audit(
            proposal,
            outcome,
            terminal_reason,
            observation_id=evidence.observation_id,
            response_byte_count=evidence.response_byte_count,
            elapsed_seconds=elapsed,
            terminal_category=terminal_category,
        )
        if not audit_ok:
            return HomeAssistantAdapterResult(
                HomeAssistantAdapterOutcome.DENY,
                HomeAssistantAdapterReason.AUDIT_FAILURE,
                evidence=evidence,
            )

        if outcome is HomeAssistantAdapterOutcome.DENY:
            return HomeAssistantAdapterResult(HomeAssistantAdapterOutcome.DENY, HomeAssistantAdapterReason.TRANSPORT_FAILURE, evidence=evidence)

        # Build a backward-compatible observation for callers that need it.
        observation = HomeAssistantObservation(
            observation_id=evidence.observation_id,
            proposal_id=proposal.proposal_id,
            success=evidence.failure_category is None,
            result_evidence=evidence.success_category or evidence.failure_category,
            observed_at=evidence.observed_at,
        )
        return HomeAssistantAdapterResult(
            HomeAssistantAdapterOutcome.ALLOW,
            HomeAssistantAdapterReason.OK,
            proposal=proposal,
            observation=observation,
            evidence=evidence,
        )

    def __repr__(self) -> str:
        return "HomeAssistantAdapter()"


def _reject_bool(value: object, name: str) -> None:
    if isinstance(value, bool):
        raise ValueError(f"{name} must not be a boolean")


def _reject_bool_nan(value: object, name: str) -> None:
    _reject_bool(value, name)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"invalid {name}")
