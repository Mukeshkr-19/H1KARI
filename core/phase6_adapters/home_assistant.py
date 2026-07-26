"""Home Assistant optional adapter with fail-closed transport safety.

This module composes the pure Home Assistant evaluator from
``core.phase6_ecosystem.home_assistant`` with transport-level validation.  All
networking, audit, clock, and ID factory behavior is injected.  Default
construction leaves the adapter disabled.
"""

from __future__ import annotations

import re
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, FrozenSet, Mapping, Optional, Tuple
from urllib.parse import urlparse

from core.action_policy import ActorContext
from core.phase6_ecosystem.home_assistant import (
    HAActionOutcome,
    HAActionReason,
    HAEvaluationResult,
    HomeAssistantActionProposal,
    HomeAssistantCapabilityManifest,
    HomeAssistantConfirmation,
    HomeAssistantContractEvaluator,
    HomeAssistantEntityRef,
    HomeAssistantObservation,
    HomeAssistantServiceRef,
    HomeAssistantTransportInterface,
)

from core.phase6_adapters.contracts import AdapterException, AdapterOutcome, AdapterReason, AdapterState


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
      - allow_local_network: if True, permit local-network RFC1918 addresses
      - request_timeout_seconds: hard timeout per transport call
      - max_response_bytes: hard response byte cap
      - max_retries: bounded retry count
      - proposal_ttl_seconds: confirmation proposal lifetime
    """

    base_url: str
    manifest: HomeAssistantCapabilityManifest
    allowed_schemes: FrozenSet[str] = frozenset({"https", "wss"})
    allow_loopback_http: bool = False
    allow_local_network: bool = False
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
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("userinfo not allowed in base_url")
        if parsed.fragment:
            raise ValueError("fragment not allowed in base_url")
        if parsed.query:
            raise ValueError("query not allowed in base_url")
        if parsed.path and parsed.path != "/" and not parsed.path.startswith("/"):
            raise ValueError("invalid path in base_url")
        if parsed.path and parsed.path not in ("", "/") and "/../" in parsed.path:
            raise ValueError("path traversal not allowed in base_url")
        if not isinstance(self.manifest, HomeAssistantCapabilityManifest):
            raise ValueError("manifest is required")
        if not isinstance(self.allowed_schemes, frozenset) or not self.allowed_schemes:
            raise ValueError("allowed_schemes must be a non-empty frozenset")
        for scheme in self.allowed_schemes:
            if not isinstance(scheme, str) or scheme not in {"http", "https", "ws", "wss"}:
                raise ValueError("disallowed scheme")
        if not isinstance(self.allow_loopback_http, bool) or not isinstance(self.allow_local_network, bool):
            raise ValueError("loopback/local_network flags must be boolean")
        if not isinstance(self.request_timeout_seconds, (int, float)) or not math.isfinite(self.request_timeout_seconds) or not 1.0 <= self.request_timeout_seconds <= 300.0:
            raise ValueError("invalid request_timeout_seconds")
        if not isinstance(self.max_response_bytes, int) or not 1 <= self.max_response_bytes <= 100_000_000:
            raise ValueError("invalid max_response_bytes")
        if not isinstance(self.max_retries, int) or not 0 <= self.max_retries <= 16:
            raise ValueError("invalid max_retries")
        if not isinstance(self.proposal_ttl_seconds, (int, float)) or not math.isfinite(self.proposal_ttl_seconds) or not 1.0 <= self.proposal_ttl_seconds <= 3600.0:
            raise ValueError("invalid proposal_ttl_seconds")

    def __repr__(self) -> str:
        return "HomeAssistantAdapterConfig()"


class HomeAssistantAdapterResult:
    """Result of a Home Assistant adapter operation."""

    def __init__(
        self,
        outcome: HomeAssistantAdapterOutcome,
        reason: HomeAssistantAdapterReason,
        proposal: Optional[HomeAssistantActionProposal] = None,
        observation: Optional[HomeAssistantObservation] = None,
    ) -> None:
        self.outcome = outcome
        self.reason = reason
        self.proposal = proposal
        self.observation = observation

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
        transport: Optional[HomeAssistantTransportInterface] = None,
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
        now = self._clock() if callable(self._clock) else float(self._clock)
        if not isinstance(now, (int, float)):
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        return float(now)

    def _next_id(self) -> str:
        if self._id_factory is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        return str(self._id_factory() if callable(self._id_factory) else self._id_factory)

    def _record_audit(self, proposal: HomeAssistantActionProposal, outcome: HomeAssistantAdapterOutcome, reason: HomeAssistantAdapterReason) -> bool:
        """Record authorization before execution; failure denies the side effect."""
        if self._auditor is None or not callable(getattr(self._auditor, "record", None)):
            return False
        try:
            self._auditor.record(
                proposal_id=proposal.proposal_id,
                outcome=outcome.value,
                reason=reason.value,
            )
        except Exception:
            return False
        return True

    def _validate_url(self, url: str) -> Tuple[bool, HomeAssistantAdapterReason]:
        if not isinstance(url, str) or not url:
            return False, HomeAssistantAdapterReason.INVALID_URL
        if "*" in url or "@" in url:
            return False, HomeAssistantAdapterReason.WILDCARD_IN_URL if "*" in url else HomeAssistantAdapterReason.USERINFO_IN_URL
        parsed = urlparse(url)
        if parsed.scheme not in self._config.allowed_schemes:  # type: ignore[union-attr]
            # Loopback exception for http
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
        # Host mismatch with configured base endpoint
        base = urlparse(self._config.base_url)  # type: ignore[union-attr]
        if host.lower() != (base.hostname or "").lower() or port != base.port:
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
        if not self._record_audit(proposal, HomeAssistantAdapterOutcome.ALLOW, HomeAssistantAdapterReason.OK):
            return HomeAssistantAdapterResult(HomeAssistantAdapterOutcome.DENY, HomeAssistantAdapterReason.AUDIT_FAILURE)
        # Never retry a possibly state-changing operation without an idempotency
        # contract. The injected transport gets exactly one invocation.
        started = self._now()
        try:
            observation = self._transport.execute_authorized_plan(proposal)
        except Exception:
            return HomeAssistantAdapterResult(HomeAssistantAdapterOutcome.DENY, HomeAssistantAdapterReason.TRANSPORT_FAILURE)
        if observation is None:
            return HomeAssistantAdapterResult(HomeAssistantAdapterOutcome.DENY, HomeAssistantAdapterReason.TRANSPORT_FAILURE)
        if self._now() - started > self._config.request_timeout_seconds:
            return HomeAssistantAdapterResult(HomeAssistantAdapterOutcome.DENY, HomeAssistantAdapterReason.TIMEOUT_EXCEEDED)
        evidence = getattr(observation, "result_evidence", None)
        if not isinstance(evidence, str) or len(evidence.encode("utf-8")) > self._config.max_response_bytes:
            return HomeAssistantAdapterResult(HomeAssistantAdapterOutcome.DENY, HomeAssistantAdapterReason.RESPONSE_TOO_LARGE)
        return HomeAssistantAdapterResult(HomeAssistantAdapterOutcome.ALLOW, HomeAssistantAdapterReason.OK, proposal=proposal, observation=observation)

    def __repr__(self) -> str:
        return "HomeAssistantAdapter()"
