"""Measured routing optional adapter.

Composes the pure model-routing evaluator from
``core.phase6_ecosystem.model_evaluation`` with injected benchmark observation
ingestion and canary/hysteresis/rollback proposal contracts.  Default
construction leaves the adapter disabled.  It never edits ``core/router.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
from typing import Any, Mapping, Optional, Sequence, Tuple

from core.phase6_ecosystem.model_evaluation import (
    EvaluationScenario,
    ModelCandidate,
    ModelMeasurement,
    ModelRoutingEvaluator,
    RoutingPolicy,
    RoutingRecommendation,
)

from core.phase6_adapters.contracts import AdapterException, AdapterOutcome, AdapterReason, AdapterState


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


class MeasuredRoutingAdapterOutcome(StrEnum):
    """Fixed outcomes for the measured routing adapter."""

    RECOMMEND = "recommend"
    DENY = "deny"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class MeasuredRoutingAdapterConfig:
    """Explicit configuration enabling the measured routing adapter."""

    canary_score_threshold: float = 0.05
    hysteresis_window_size: int = 5
    max_candidate_age_seconds: float = 3600.0
    require_canary_confirmation: bool = True

    def __post_init__(self) -> None:
        for name, value in (
            ("canary_score_threshold", self.canary_score_threshold),
            ("max_candidate_age_seconds", self.max_candidate_age_seconds),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"invalid {name}")
        if not isinstance(self.hysteresis_window_size, int) or self.hysteresis_window_size <= 0:
            raise ValueError("invalid hysteresis_window_size")
        if self.hysteresis_window_size > 1024 or self.max_candidate_age_seconds > 31_536_000 or self.canary_score_threshold > 1.0:
            raise ValueError("routing bound exceeded")
        if not isinstance(self.require_canary_confirmation, bool):
            raise ValueError("invalid require_canary_confirmation")

    def __repr__(self) -> str:
        return "MeasuredRoutingAdapterConfig()"


@dataclass(frozen=True)
class CanaryProposal:
    """Canary evaluation proposal before full routing commitment."""

    scenario_id: str
    candidate_id: Optional[str]
    canary_score: float
    requires_confirmation: bool

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

    def __repr__(self) -> str:
        return "RoutingAdapterResult()"


class BenchmarkObservationInterface:
    """Injected benchmark observation source (no real implementation)."""

    def observe(self, candidate_id: str) -> Optional[ModelMeasurement]:
        raise NotImplementedError("benchmark observation source is injected")


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
    ) -> None:
        self._config = config
        self._policy = policy if policy is not None else RoutingPolicy()
        self._evaluator = ModelRoutingEvaluator(self._policy)
        self._observation_source = observation_source
        self._clock = clock
        self._history: dict[str, list[Tuple[str, float]]] = {}
        self._last_winner: dict[str, str] = {}

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
            )
        assert self._config is not None
        try:
            now = self._now()
            merged_measurements = dict(measurements)
            if self._observation_source is not None:
                for candidate in candidates:
                    if candidate.candidate_id not in merged_measurements:
                        observed = self._observation_source.observe(candidate.candidate_id)
                        if observed is not None:
                            merged_measurements[candidate.candidate_id] = observed
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
            )
        if recommendation.winning_candidate is None:
            return RoutingAdapterResult(
                MeasuredRoutingAdapterOutcome.DENY,
                MeasuredRoutingAdapterReason.NO_ELIGIBLE_CANDIDATE,
                recommendation,
                None,
                None,
            )
        winner_id = recommendation.winning_candidate.candidate_id
        score = recommendation.score_breakdown.get("final_score", 0.0)
        # Hysteresis: if the winner flaps from the previous winner, require confirmation.
        previous_winner = self._last_winner.get(scenario.scenario_id)
        flap_detected = previous_winner is not None and previous_winner != winner_id
        hist = self._history.get(scenario.scenario_id, [])
        stable_previous = (
            hist
            and previous_winner is not None
            and all(h[0] == previous_winner for h in hist[-self._config.hysteresis_window_size :])
        )
        canary = CanaryProposal(
            scenario_id=scenario.scenario_id,
            candidate_id=winner_id,
            canary_score=score,
            requires_confirmation=self._config.require_canary_confirmation or flap_detected,
        )
        rollback_id: Optional[str] = previous_winner if stable_previous else None
        self._last_winner[scenario.scenario_id] = winner_id
        history = self._history.setdefault(scenario.scenario_id, [])
        history.append((winner_id, score))
        if len(history) > self._config.hysteresis_window_size:
            history.pop(0)
        return RoutingAdapterResult(
            MeasuredRoutingAdapterOutcome.RECOMMEND,
            MeasuredRoutingAdapterReason.OK,
            recommendation,
            canary,
            rollback_id,
        )

    def __repr__(self) -> str:
        return "MeasuredRoutingAdapter()"
