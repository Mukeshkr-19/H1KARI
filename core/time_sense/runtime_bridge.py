"""Narrow Time Sense runtime bridge for caller-supplied observations.

Advisory only: WAIT / RESPOND / CHECK_IN / SUMMARIZE / SUPPRESS.
Never schedules, retries, cancels, delivers, or speaks. Never stores transcript
text. Injected aware datetime only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Callable, Dict, Optional

from core.time_sense.session_policy import (
    ConversationTimingObservation,
    TimingAction,
    TimingPolicyConfig,
    TimingPolicyDecision,
    TimingReason,
    evaluate_conversation_timing,
)
from core.time_sense.contracts import QuietHoursContext

AwareClock = Callable[[], datetime]


@dataclass(frozen=True)
class RuntimeBridgeConfig:
    max_tracked_sessions: int = 32
    max_observation_age_seconds: float = 3600.0
    timing: TimingPolicyConfig = TimingPolicyConfig()

    def __post_init__(self) -> None:
        if isinstance(self.max_tracked_sessions, bool) or not isinstance(self.max_tracked_sessions, int):
            raise ValueError("invalid_max_tracked_sessions")
        if self.max_tracked_sessions < 1 or self.max_tracked_sessions > 256:
            raise ValueError("invalid_max_tracked_sessions")
        if (
            isinstance(self.max_observation_age_seconds, bool)
            or not isinstance(self.max_observation_age_seconds, (int, float))
            or not math.isfinite(float(self.max_observation_age_seconds))
            or float(self.max_observation_age_seconds) < 1.0
            or float(self.max_observation_age_seconds) > 2_592_000.0
        ):
            raise ValueError("invalid_max_observation_age_seconds")


@dataclass(frozen=True, repr=False)
class TimingAdvisory:
    session_id: str
    action: TimingAction
    reason: TimingReason
    observed_at: datetime

    def __repr__(self) -> str:
        return "TimingAdvisory()"


class TimeSenseRuntimeBridge:
    """Consumes timing observations and retains content-free advisories only."""

    def __init__(
        self,
        clock: AwareClock,
        *,
        config: Optional[RuntimeBridgeConfig] = None,
    ) -> None:
        if not callable(clock):
            raise TypeError("clock_must_be_callable")
        self._clock = clock
        self._config = config or RuntimeBridgeConfig()
        self._latest: Dict[str, TimingAdvisory] = {}

    def ingest(
        self,
        observation: ConversationTimingObservation,
        *,
        quiet_hours_context: Optional[QuietHoursContext] = None,
    ) -> TimingAdvisory:
        if not isinstance(observation, ConversationTimingObservation):
            raise TypeError("observation_must_be_ConversationTimingObservation")
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("clock_must_return_aware_datetime")
        age = abs((now - observation.observed_at).total_seconds())
        if age > float(self._config.max_observation_age_seconds):
            advisory = TimingAdvisory(
                session_id=observation.session_id,
                action=TimingAction.SUPPRESS,
                reason=TimingReason.INVALID_EVIDENCE,
                observed_at=observation.observed_at,
            )
            self._store(advisory)
            return advisory
        decision = evaluate_conversation_timing(
            observation,
            config=self._config.timing,
            quiet_hours_context=quiet_hours_context,
        )
        advisory = TimingAdvisory(
            session_id=observation.session_id,
            action=decision.action,
            reason=decision.reason,
            observed_at=observation.observed_at,
        )
        self._store(advisory)
        return advisory

    def latest(self, session_id: str) -> Optional[TimingAdvisory]:
        return self._latest.get(session_id)

    def snapshot(self) -> tuple[TimingAdvisory, ...]:
        return tuple(self._latest[key] for key in sorted(self._latest))

    def clear(self) -> None:
        self._latest.clear()

    def _store(self, advisory: TimingAdvisory) -> None:
        if (
            advisory.session_id not in self._latest
            and len(self._latest) >= self._config.max_tracked_sessions
        ):
            # Deterministic eviction: drop lexicographically first session id.
            first = sorted(self._latest)[0]
            del self._latest[first]
        self._latest[advisory.session_id] = advisory


__all__ = [
    "AwareClock",
    "RuntimeBridgeConfig",
    "TimeSenseRuntimeBridge",
    "TimingAdvisory",
]
