"""Measured routing optional adapter.

Composes the pure model-routing evaluator from
``core.phase6_ecosystem.model_evaluation`` with injected benchmark observation
ingestion and canary/hysteresis/rollback proposal contracts.  Default
construction leaves the adapter disabled.  It never edits ``core/router.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from abc import ABC, abstractmethod
from typing import Mapping, Optional, Sequence

from core.phase6_ecosystem.model_evaluation import (
    EvaluationScenario,
    ModelCandidate,
    ModelMeasurement,
    ModelRoutingEvaluator,
    RoutingPolicy,
    RoutingRecommendation,
)

from core.phase6_adapters.contracts import AdapterException, AdapterReason, AdapterState


class MeasuredRoutingAdapterReason(StrEnum):
    """Fixed reason codes for the measured routing adapter."""

    OK = "ok"
    DISABLED = "disabled"
    INVALID_CONFIGURATION = "invalid_configuration"
    MISSING_DEPENDENCY = "missing_dependency"
    NO_ELIGIBLE_CANDIDATE = "no_eligible_candidate"
    PRIVACY_EGRESS_FORBIDDEN = "privacy_egress_forbidden"
    MEASUREMENT_STALE = "measurement_stale"
    MEASUREMENT_MISMATCH = "measurement_mismatch"
    SAFETY_BELOW_MINIMUM = "safety_below_minimum"
    FLAP_PREVENTED = "flap_prevented"
    CANARY_FAILED = "canary_failed"
    ROLLBACK_REQUIRED = "rollback_required"
    CANARY_EXPIRED = "canary_expired"
    CANARY_CANCELLED = "canary_cancelled"


class MeasuredRoutingAdapterOutcome(StrEnum):
    """Fixed outcomes for the measured routing adapter."""

    RECOMMEND = "recommend"
    DENY = "deny"
    UNAVAILABLE = "unavailable"


class CanaryState(StrEnum):
    """Canary lifecycle states."""

    EVALUATED = "evaluated"
    CANARY_PROPOSED = "canary_proposed"
    CONFIRMED = "confirmed"
    CANARY_PASSED = "canary_passed"
    RECOMMEND_READY = "recommend_ready"
    CANARY_FAILED = "canary_failed"
    ROLLBACK_REQUIRED = "rollback_required"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class MeasuredRoutingAdapterConfig:
    """Explicit configuration enabling the measured routing adapter."""

    canary_score_threshold: float = 0.05
    hysteresis_window_size: int = 5
    max_candidate_age_seconds: float = 3600.0
    require_canary_confirmation: bool = True
    canary_expiry_seconds: float = 300.0
    max_pending_canaries: int = 16
    max_history_per_scenario: int = 64

    def __post_init__(self) -> None:
        for name, value in (
            ("canary_score_threshold", self.canary_score_threshold),
            ("max_candidate_age_seconds", self.max_candidate_age_seconds),
            ("canary_expiry_seconds", self.canary_expiry_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"invalid {name}")
        for name, value in (
            ("hysteresis_window_size", self.hysteresis_window_size),
            ("max_pending_canaries", self.max_pending_canaries),
            ("max_history_per_scenario", self.max_history_per_scenario),
        ):
            _reject_bool(value, name)
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"invalid {name}")
        if self.hysteresis_window_size > 1024 or self.max_candidate_age_seconds > 31_536_000 or self.canary_score_threshold > 1.0:
            raise ValueError("routing bound exceeded")
        if self.canary_expiry_seconds > 86_400:
            raise ValueError("invalid canary_expiry_seconds")
        if not isinstance(self.require_canary_confirmation, bool):
            raise ValueError("invalid require_canary_confirmation")

    def __repr__(self) -> str:
        return "MeasuredRoutingAdapterConfig()"


@dataclass(frozen=True)
class CanaryProposal:
    """Canary evaluation proposal before full routing commitment."""

    proposal_id: str
    scenario_id: str
    candidate_id: Optional[str]
    canary_score: float
    requires_confirmation: bool
    expires_at: float

    def __repr__(self) -> str:
        return "CanaryProposal()"


@dataclass(frozen=True)
class RoutingAdapterResult:
    """Advisory routing result with optional canary and rollback proposals."""

    outcome: MeasuredRoutingAdapterOutcome
    reason: MeasuredRoutingAdapterReason
    recommendation: Optional[RoutingRecommendation]
    canary: Optional[CanaryProposal]
    rollback_candidate_id: Optional[str]
    canary_state: CanaryState

    def __repr__(self) -> str:
        return "RoutingAdapterResult()"


class BenchmarkObservationInterface(ABC):
    """Injected benchmark observation source (no real implementation)."""

    @abstractmethod
    def observe(self, candidate_id: str) -> Optional[ModelMeasurement]:
        ...


class MeasuredRoutingAdapter:
    """Disabled-by-default measured routing adapter.

    Benchmark ingestion is injected.  Recommendations are advisory only.  The
    adapter never mutates ``core/router.py``.
    """

    def __init__(
        self,
        *,
        config: Optional[MeasuredRoutingAdapterConfig] = None,
        policy: Optional[RoutingPolicy] = None,
        observation_source: Optional[BenchmarkObservationInterface] = None,
        clock: Optional[object] = None,
        id_factory: Optional[object] = None,
    ) -> None:
        self._config = config
        self._policy = policy if policy is not None else RoutingPolicy()
        self._evaluator = ModelRoutingEvaluator(self._policy)
        self._observation_source = observation_source
        self._clock = clock
        self._id_factory = id_factory
        self._history: dict[str, list[tuple[str, float]]] = {}
        self._last_winner: dict[str, str] = {}
        self._pending_canaries: dict[str, CanaryProposal] = {}
        self._id_counter = 0

    @property
    def state(self) -> AdapterState:
        return AdapterState.ENABLED if self._config is not None else AdapterState.DISABLED

    def _now(self) -> float:
        if self._clock is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        now = self._clock() if callable(self._clock) else float(self._clock)
        if not isinstance(now, (int, float)) or isinstance(now, bool):
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        return float(now)

    def _next_id(self) -> str:
        if self._id_factory is None:
            self._id_counter += 1
            return f"id{self._id_counter}"
        return str(self._id_factory() if callable(self._id_factory) else self._id_factory)

    def _bind_observations(self, candidates: Sequence[ModelCandidate], measurements: Mapping[str, ModelMeasurement]) -> Mapping[str, ModelMeasurement]:
        if self._observation_source is None:
            return measurements
        merged = dict(measurements)
        for candidate in candidates:
            if candidate.candidate_id not in merged:
                observed = self._observation_source.observe(candidate.candidate_id)
                if observed is not None:
                    merged[candidate.candidate_id] = observed
        return merged

    def evaluate(
        self,
        scenario: EvaluationScenario,
        candidates: Sequence[ModelCandidate],
        measurements: Mapping[str, ModelMeasurement],
    ) -> RoutingAdapterResult:
        """Return an advisory routing recommendation with canary/hysteresis."""
        if self.state is AdapterState.DISABLED:
            return RoutingAdapterResult(
                MeasuredRoutingAdapterOutcome.UNAVAILABLE,
                MeasuredRoutingAdapterReason.DISABLED,
                None,
                None,
                None,
                CanaryState.EVALUATED,
            )
        assert self._config is not None
        try:
            now = self._now()
            merged_measurements = self._bind_observations(candidates, measurements)
            fresh_measurements = {
                key: value for key, value in merged_measurements.items()
                if value.measured_at <= now and now - value.measured_at <= self._config.max_candidate_age_seconds
            }
            recommendation = self._evaluator.evaluate_candidates(
                scenario=scenario,
                candidates=candidates,
                measurements=fresh_measurements,
                now=now,
            )
        except Exception:
            return RoutingAdapterResult(
                MeasuredRoutingAdapterOutcome.DENY,
                MeasuredRoutingAdapterReason.INVALID_CONFIGURATION,
                None,
                None,
                None,
                CanaryState.EVALUATED,
            )
        if recommendation.winning_candidate is None:
            return RoutingAdapterResult(
                MeasuredRoutingAdapterOutcome.DENY,
                MeasuredRoutingAdapterReason.NO_ELIGIBLE_CANDIDATE,
                recommendation,
                None,
                None,
                CanaryState.EVALUATED,
            )

        winner_id = recommendation.winning_candidate.candidate_id
        score = recommendation.score_breakdown.get("final_score", 0.0)
        previous_winner = self._last_winner.get(scenario.scenario_id)
        flap_detected = previous_winner is not None and previous_winner != winner_id

        proposal_id = self._next_id()
        canary = CanaryProposal(
            proposal_id=proposal_id,
            scenario_id=scenario.scenario_id,
            candidate_id=winner_id,
            canary_score=score,
            requires_confirmation=self._config.require_canary_confirmation or flap_detected,
            expires_at=now + self._config.canary_expiry_seconds,
        )

        # Evaluation alone does not update committed winner history.
        # It proposes a canary; confirmation is required to change incumbent.
        if len(self._pending_canaries) >= self._config.max_pending_canaries and proposal_id not in self._pending_canaries:
            # Evict oldest pending canary deterministically.
            oldest = min(self._pending_canaries.keys())
            del self._pending_canaries[oldest]
        self._pending_canaries[proposal_id] = canary

        rollback_id: Optional[str] = None
        hist = self._history.get(scenario.scenario_id, [])
        if hist and previous_winner is not None and all(h[0] == previous_winner for h in hist[-self._config.hysteresis_window_size :]):
            rollback_id = previous_winner

        return RoutingAdapterResult(
            MeasuredRoutingAdapterOutcome.RECOMMEND,
            MeasuredRoutingAdapterReason.OK,
            recommendation,
            canary,
            rollback_id,
            CanaryState.CANARY_PROPOSED,
        )

    def confirm_canary(self, proposal_id: str) -> RoutingAdapterResult:
        """Confirm a pending canary; only then may it become the incumbent."""
        if self.state is AdapterState.DISABLED:
            return RoutingAdapterResult(
                MeasuredRoutingAdapterOutcome.UNAVAILABLE,
                MeasuredRoutingAdapterReason.DISABLED,
                None,
                None,
                None,
                CanaryState.EVALUATED,
            )
        assert self._config is not None
        canary = self._pending_canaries.get(proposal_id)
        if canary is None:
            return RoutingAdapterResult(
                MeasuredRoutingAdapterOutcome.DENY,
                MeasuredRoutingAdapterReason.MEASUREMENT_MISMATCH,
                None,
                None,
                None,
                CanaryState.EVALUATED,
            )
        now = self._now()
        if now > canary.expires_at:
            self._pending_canaries.pop(proposal_id, None)
            return RoutingAdapterResult(
                MeasuredRoutingAdapterOutcome.DENY,
                MeasuredRoutingAdapterReason.CANARY_EXPIRED,
                None,
                canary,
                None,
                CanaryState.EXPIRED,
            )

        # Confirmed canary updates history and incumbent.
        history = self._history.setdefault(canary.scenario_id, [])
        history.append((canary.candidate_id, canary.canary_score))
        if len(history) > self._config.max_history_per_scenario:
            history.pop(0)
        self._last_winner[canary.scenario_id] = canary.candidate_id

        return RoutingAdapterResult(
            MeasuredRoutingAdapterOutcome.RECOMMEND,
            MeasuredRoutingAdapterReason.OK,
            None,
            canary,
            None,
            CanaryState.CONFIRMED,
        )

    def record_canary_pass(self, proposal_id: str) -> RoutingAdapterResult:
        """Record that a confirmed canary passed."""
        if proposal_id not in self._pending_canaries:
            return RoutingAdapterResult(
                MeasuredRoutingAdapterOutcome.DENY,
                MeasuredRoutingAdapterReason.MEASUREMENT_MISMATCH,
                None,
                None,
                None,
                CanaryState.EVALUATED,
            )
        canary = self._pending_canaries.pop(proposal_id)
        return RoutingAdapterResult(
            MeasuredRoutingAdapterOutcome.RECOMMEND,
            MeasuredRoutingAdapterReason.OK,
            None,
            canary,
            None,
            CanaryState.CANARY_PASSED,
        )

    def record_canary_failure(self, proposal_id: str) -> RoutingAdapterResult:
        """Record canary failure; previous confirmed route is restored."""
        if proposal_id not in self._pending_canaries:
            return RoutingAdapterResult(
                MeasuredRoutingAdapterOutcome.DENY,
                MeasuredRoutingAdapterReason.MEASUREMENT_MISMATCH,
                None,
                None,
                None,
                CanaryState.EVALUATED,
            )
        canary = self._pending_canaries.pop(proposal_id)
        rollback_id = self._last_winner.get(canary.scenario_id)
        return RoutingAdapterResult(
            MeasuredRoutingAdapterOutcome.RECOMMEND,
            MeasuredRoutingAdapterReason.CANARY_FAILED,
            None,
            canary,
            rollback_id,
            CanaryState.CANARY_FAILED,
        )

    def cancel_canary(self, proposal_id: str) -> RoutingAdapterResult:
        """Cancel a pending canary."""
        if proposal_id not in self._pending_canaries:
            return RoutingAdapterResult(
                MeasuredRoutingAdapterOutcome.DENY,
                MeasuredRoutingAdapterReason.MEASUREMENT_MISMATCH,
                None,
                None,
                None,
                CanaryState.EVALUATED,
            )
        canary = self._pending_canaries.pop(proposal_id)
        return RoutingAdapterResult(
            MeasuredRoutingAdapterOutcome.DENY,
            MeasuredRoutingAdapterReason.CANARY_CANCELLED,
            None,
            canary,
            None,
            CanaryState.CANCELLED,
        )

    def __repr__(self) -> str:
        return "MeasuredRoutingAdapter()"


def _reject_bool(value: object, name: str) -> None:
    if isinstance(value, bool):
        raise ValueError(f"{name} must not be a boolean")
