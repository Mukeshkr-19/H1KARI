"""Measured local-model routing evaluator for Phase 6.

Evaluates model candidates deterministically against scenario capability, privacy/egress,
safety, latency, cost, and measurement freshness bounds without calling model APIs,
reading env vars, or mutating core/router.py.
"""

from __future__ import annotations

import re
import math
from dataclasses import dataclass
from types import MappingProxyType
from enum import IntEnum, StrEnum
from typing import Mapping, Optional, Sequence, Tuple

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")


class ModelCapability(StrEnum):
    """Supported model capability classifications."""

    TEXT_GEN = "text_gen"
    CODE_GEN = "code_gen"
    REASONING = "reasoning"
    VISION = "vision"
    EMBEDDING = "embedding"
    AUDIO_STT = "audio_stt"
    AUDIO_TTS = "audio_tts"


class PrivacyClass(IntEnum):
    """Privacy and egress classification ordered by strictness."""

    LOCAL_ONLY = 1
    GATEWAY_OK = 2
    REMOTE_OK = 3


class ModelEvaluationRejectionReason(StrEnum):
    """Fixed rejection reason codes for model routing evaluation."""

    OK = "ok"
    MISSING_CAPABILITY = "missing_capability"
    PRIVACY_EGRESS_FORBIDDEN = "privacy_egress_forbidden"
    SAFETY_BELOW_MINIMUM = "safety_below_minimum"
    QUALITY_BELOW_MINIMUM = "quality_below_minimum"
    LATENCY_EXCEEDED = "latency_exceeded"
    COST_EXCEEDED = "cost_exceeded"
    MEASUREMENT_MISSING = "measurement_missing"
    MEASUREMENT_STALE = "measurement_stale"
    RESOURCE_EXCEEDED = "resource_exceeded"
    MEASUREMENT_MISMATCH = "measurement_mismatch"


@dataclass(frozen=True)
class ModelCandidate:
    """Declared candidate model descriptor."""

    candidate_id: str
    provider_type: str  # "local_model", "local_gateway", "remote_provider"
    model_name: str
    privacy_class: PrivacyClass
    capabilities: Tuple[ModelCapability, ...]
    provenance_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not _IDENTIFIER_RE.fullmatch(self.candidate_id):
            raise ValueError("invalid candidate_id")
        if self.provider_type not in ("local_model", "local_gateway", "remote_provider"):
            raise ValueError("invalid provider_type")
        if not isinstance(self.model_name, str) or not self.model_name.strip():
            raise ValueError("model_name is required")
        if not isinstance(self.provenance_id, str) or not self.provenance_id.strip():
            raise ValueError("provenance_id is required")
        if not isinstance(self.privacy_class, PrivacyClass):
            raise ValueError("invalid privacy_class")
        if not isinstance(self.capabilities, tuple) or not self.capabilities or any(
            not isinstance(capability, ModelCapability) for capability in self.capabilities
        ):
            raise ValueError("invalid capabilities")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("duplicate capability")
        if self.provider_type == "local_model" and self.privacy_class is not PrivacyClass.LOCAL_ONLY:
            raise ValueError("local model must be local-only")

    def __repr__(self) -> str:
        return "ModelCandidate()"


@dataclass(frozen=True)
class EvaluationScenario:
    """Declared evaluation scenario requirements."""

    scenario_id: str
    required_capabilities: Tuple[ModelCapability, ...]
    max_privacy_class: PrivacyClass
    max_latency_ms: float = 5000.0
    max_cost_usd: float = 1.0
    max_memory_mb: float = 16_384.0
    min_quality_score: float = 0.5
    min_safety_score: float = 0.8

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, str) or not _IDENTIFIER_RE.fullmatch(self.scenario_id):
            raise ValueError("invalid scenario_id")
        if not isinstance(self.required_capabilities, tuple) or not self.required_capabilities or any(
            not isinstance(capability, ModelCapability) for capability in self.required_capabilities
        ):
            raise ValueError("invalid required_capabilities")
        if not isinstance(self.max_privacy_class, PrivacyClass):
            raise ValueError("invalid max_privacy_class")
        for name, value in (
            ("max_latency_ms", self.max_latency_ms),
            ("max_cost_usd", self.max_cost_usd),
            ("max_memory_mb", self.max_memory_mb),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid {name}")
        for name, value in (("min_quality_score", self.min_quality_score), ("min_safety_score", self.min_safety_score)):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0.0 <= value <= 1.0:
                raise ValueError(f"invalid {name}")

    def __repr__(self) -> str:
        return "EvaluationScenario()"


@dataclass(frozen=True)
class ModelMeasurement:
    """Caller-supplied measurement telemetry for a model candidate."""

    candidate_id: str
    quality_score: float  # 0.0 to 1.0
    safety_score: float   # 0.0 to 1.0
    latency_ms: float
    cost_usd: float
    memory_mb: float
    reliability_score: float  # 0.0 to 1.0
    measured_at: float

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not _IDENTIFIER_RE.fullmatch(self.candidate_id):
            raise ValueError("invalid candidate_id")
        if not (0.0 <= self.quality_score <= 1.0):
            raise ValueError("quality_score must be between 0.0 and 1.0")
        if not (0.0 <= self.safety_score <= 1.0):
            raise ValueError("safety_score must be between 0.0 and 1.0")
        if not (0.0 <= self.reliability_score <= 1.0):
            raise ValueError("reliability_score must be between 0.0 and 1.0")
        for name, value in (
            ("latency_ms", self.latency_ms),
            ("cost_usd", self.cost_usd),
            ("memory_mb", self.memory_mb),
            ("measured_at", self.measured_at),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid {name}")

    def is_stale(self, max_age_seconds: float, now: float) -> bool:
        return (now - self.measured_at) > max_age_seconds

    def __repr__(self) -> str:
        return "ModelMeasurement()"


@dataclass(frozen=True)
class RoutingPolicy:
    """Policy rules governing candidate scoring and selection."""

    prefer_local: bool = True
    max_measurement_age_seconds: float = 3600.0
    min_safety_threshold: float = 0.8
    local_preference_bonus: float = 0.15

    def __post_init__(self) -> None:
        if not isinstance(self.prefer_local, bool):
            raise ValueError("invalid prefer_local")
        if not isinstance(self.max_measurement_age_seconds, (int, float)) or not 0 < self.max_measurement_age_seconds <= 31_536_000:
            raise ValueError("invalid measurement age")
        if not isinstance(self.min_safety_threshold, (int, float)) or not 0.0 <= self.min_safety_threshold <= 1.0:
            raise ValueError("invalid safety threshold")
        if not isinstance(self.local_preference_bonus, (int, float)) or not 0.0 <= self.local_preference_bonus <= 0.25:
            raise ValueError("invalid local preference bonus")

    def __repr__(self) -> str:
        return "RoutingPolicy()"


@dataclass(frozen=True)
class RoutingRecommendation:
    """Deterministic routing recommendation output."""

    scenario_id: str
    winning_candidate: Optional[ModelCandidate]
    score_breakdown: Mapping[str, float]
    candidate_outcomes: Tuple[Tuple[str, str, float], ...]  # (candidate_id, status/reason, score)
    evaluated_at: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "score_breakdown", MappingProxyType(dict(self.score_breakdown)))

    def __repr__(self) -> str:
        return "RoutingRecommendation()"


class ModelRoutingEvaluator:
    """Pure evaluator for local-model routing scenarios."""

    def __init__(self, policy: Optional[RoutingPolicy] = None):
        self.policy = policy if policy is not None else RoutingPolicy()

    def evaluate_candidates(
        self,
        scenario: EvaluationScenario,
        candidates: Sequence[ModelCandidate],
        measurements: Mapping[str, ModelMeasurement],
        now: float,
    ) -> RoutingRecommendation:
        """Evaluate and score model candidates deterministically for a scenario."""
        if not isinstance(scenario, EvaluationScenario) or not isinstance(self.policy, RoutingPolicy):
            raise ValueError("invalid evaluation inputs")
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now) or now < 0:
            raise ValueError("invalid evaluation time")
        if any(not isinstance(candidate, ModelCandidate) for candidate in candidates):
            raise ValueError("invalid candidate")
        if any(not isinstance(measurement, ModelMeasurement) for measurement in measurements.values()):
            raise ValueError("invalid measurement")
        outcomes: list[tuple[str, str, float]] = []
        eligible: list[tuple[float, ModelCandidate, ModelMeasurement, dict[str, float]]] = []

        req_caps = set(scenario.required_capabilities)

        candidate_ids = [candidate.candidate_id for candidate in candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("duplicate candidate_id")

        for candidate in sorted(candidates, key=lambda c: c.candidate_id):
            cand_id = candidate.candidate_id

            # 1. Capability check
            cand_caps = set(candidate.capabilities)
            if not req_caps.issubset(cand_caps):
                outcomes.append((cand_id, ModelEvaluationRejectionReason.MISSING_CAPABILITY.value, 0.0))
                continue

            # 2. Privacy / Egress check
            if candidate.privacy_class > scenario.max_privacy_class:
                outcomes.append((cand_id, ModelEvaluationRejectionReason.PRIVACY_EGRESS_FORBIDDEN.value, 0.0))
                continue

            # 3. Measurement presence check
            measurement = measurements.get(cand_id)
            if measurement is None:
                outcomes.append((cand_id, ModelEvaluationRejectionReason.MEASUREMENT_MISSING.value, 0.0))
                continue
            if measurement.candidate_id != cand_id:
                outcomes.append((cand_id, ModelEvaluationRejectionReason.MEASUREMENT_MISMATCH.value, 0.0))
                continue

            # 4. Measurement freshness check
            if measurement.measured_at > now or measurement.is_stale(self.policy.max_measurement_age_seconds, now):
                outcomes.append((cand_id, ModelEvaluationRejectionReason.MEASUREMENT_STALE.value, 0.0))
                continue

            # 5. Hard safety threshold check
            effective_min_safety = max(scenario.min_safety_score, self.policy.min_safety_threshold)
            if measurement.safety_score < effective_min_safety:
                outcomes.append((cand_id, ModelEvaluationRejectionReason.SAFETY_BELOW_MINIMUM.value, 0.0))
                continue

            # 6. Quality threshold check
            if measurement.quality_score < scenario.min_quality_score:
                outcomes.append((cand_id, ModelEvaluationRejectionReason.QUALITY_BELOW_MINIMUM.value, 0.0))
                continue

            # 7. Latency bound check
            if measurement.latency_ms > scenario.max_latency_ms:
                outcomes.append((cand_id, ModelEvaluationRejectionReason.LATENCY_EXCEEDED.value, 0.0))
                continue

            # 8. Cost bound check
            if measurement.cost_usd > scenario.max_cost_usd:
                outcomes.append((cand_id, ModelEvaluationRejectionReason.COST_EXCEEDED.value, 0.0))
                continue
            if measurement.memory_mb > scenario.max_memory_mb:
                outcomes.append((cand_id, ModelEvaluationRejectionReason.RESOURCE_EXCEEDED.value, 0.0))
                continue

            # Compute composite score
            # Base quality & safety (0.0 to 1.0)
            base_score = (measurement.quality_score * 0.5) + (measurement.safety_score * 0.3) + (measurement.reliability_score * 0.2)

            # Local preference bonus if enabled and candidate is local
            local_bonus = 0.0
            if self.policy.prefer_local and candidate.provider_type in ("local_model", "local_gateway"):
                local_bonus = self.policy.local_preference_bonus

            final_score = min(1.0, base_score + local_bonus)

            breakdown = {
                "base_score": round(base_score, 4),
                "local_bonus": round(local_bonus, 4),
                "quality": round(measurement.quality_score, 4),
                "safety": round(measurement.safety_score, 4),
                "reliability": round(measurement.reliability_score, 4),
                "final_score": round(final_score, 4),
            }

            outcomes.append((cand_id, ModelEvaluationRejectionReason.OK.value, round(final_score, 4)))
            eligible.append((final_score, candidate, measurement, breakdown))

        if not eligible:
            return RoutingRecommendation(
                scenario_id=scenario.scenario_id,
                winning_candidate=None,
                score_breakdown={},
                candidate_outcomes=tuple(outcomes),
                evaluated_at=now,
            )

        # Deterministic sorting: higher score first; if tie, lower candidate_id first
        eligible.sort(key=lambda item: (-item[0], item[1].candidate_id))
        winning_score, winning_candidate, winning_measurement, winning_breakdown = eligible[0]

        return RoutingRecommendation(
            scenario_id=scenario.scenario_id,
            winning_candidate=winning_candidate,
            score_breakdown=winning_breakdown,
            candidate_outcomes=tuple(outcomes),
            evaluated_at=now,
        )

    def __repr__(self) -> str:
        return "ModelRoutingEvaluator()"
