"""Stuck-task notification backoff and deduplication.

Never performs corrective actions. Tracks notification eligibility only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Dict, Optional

from core.time_sense.contracts import StuckAssessment, StuckReason
from core.time_sense.job_observations import JobObservationState, JobTimingObservation


@dataclass(frozen=True)
class StuckNotifyConfig:
    min_age_seconds: float = 60.0
    missed_heartbeat_seconds: float = 30.0
    consecutive_failure_threshold: int = 3
    base_backoff_seconds: float = 30.0
    max_backoff_seconds: float = 3600.0
    max_tracked: int = 256

    def __post_init__(self) -> None:
        for name in (
            "min_age_seconds",
            "missed_heartbeat_seconds",
            "base_backoff_seconds",
            "max_backoff_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0.0 <= float(value) <= 31_536_000.0:
                raise ValueError(f"invalid_{name}")
        for name in ("consecutive_failure_threshold", "max_tracked"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"invalid_{name}")
        if self.max_tracked > 4096 or self.consecutive_failure_threshold > 10_000:
            raise ValueError("notification_bound_exceeded")
        if self.base_backoff_seconds > self.max_backoff_seconds:
            raise ValueError("invalid_backoff_order")


@dataclass(frozen=True, repr=False)
class StuckNotifyDecision:
    should_notify: bool
    reason: str
    backoff_seconds: float
    notification_count: int

    def __repr__(self) -> str:
        return (
            f"StuckNotifyDecision(should_notify={self.should_notify}, "
            f"reason={self.reason!r})"
        )


@dataclass
class _Track:
    last_notified_at: Optional[datetime]
    notification_count: int
    resolved: bool
    cancelled: bool


class StuckNotificationTracker:
    """Exponential backoff + dedupe for stuck notifications. No side effects."""

    def __init__(self, config: Optional[StuckNotifyConfig] = None) -> None:
        self._config = config or StuckNotifyConfig()
        self._tracks: Dict[str, _Track] = {}

    def mark_resolved(self, job_id: str) -> None:
        track = self._tracks.setdefault(
            job_id, _Track(None, 0, False, False)
        )
        track.resolved = True
        track.cancelled = False

    def mark_cancelled(self, job_id: str) -> None:
        track = self._tracks.setdefault(
            job_id, _Track(None, 0, False, False)
        )
        track.cancelled = True
        track.resolved = False

    def evaluate(
        self,
        observation: JobTimingObservation,
        *,
        now: datetime,
        assessment: Optional[StuckAssessment] = None,
    ) -> StuckNotifyDecision:
        if not isinstance(observation, JobTimingObservation):
            return StuckNotifyDecision(False, "invalid_evidence", 0.0, 0)
        if not isinstance(now, datetime) or now.tzinfo is None:
            return StuckNotifyDecision(False, "invalid_now", 0.0, 0)

        if len(self._tracks) >= self._config.max_tracked and observation.job_id not in self._tracks:
            # Deterministic eviction of first key
            first = next(iter(self._tracks))
            del self._tracks[first]

        track = self._tracks.setdefault(
            observation.job_id, _Track(None, 0, False, False)
        )

        if observation.state == JobObservationState.RESOLVED or track.resolved:
            track.resolved = True
            return StuckNotifyDecision(False, "resolved", 0.0, track.notification_count)
        if observation.state == JobObservationState.CANCELLED or track.cancelled:
            track.cancelled = True
            return StuckNotifyDecision(False, "cancelled", 0.0, track.notification_count)

        if observation.age_seconds < self._config.min_age_seconds:
            return StuckNotifyDecision(False, "below_min_age", 0.0, track.notification_count)

        heartbeat_missed = (
            observation.heartbeat_age_seconds is not None
            and observation.heartbeat_age_seconds >= self._config.missed_heartbeat_seconds
        )
        failures_hit = observation.attempt_count >= self._config.consecutive_failure_threshold
        assessment_stuck = bool(assessment and assessment.stuck and assessment.reason is not StuckReason.NOT_STUCK)

        if not (heartbeat_missed or failures_hit or assessment_stuck):
            return StuckNotifyDecision(False, "not_stuck", 0.0, track.notification_count)

        backoff = min(
            self._config.max_backoff_seconds,
            self._config.base_backoff_seconds * (2 ** min(track.notification_count, 16)),
        )
        if track.last_notified_at is not None:
            elapsed = (now - track.last_notified_at).total_seconds()
            if elapsed < backoff:
                return StuckNotifyDecision(
                    False, "backoff_deduped", backoff, track.notification_count
                )

        track.last_notified_at = now
        track.notification_count += 1
        return StuckNotifyDecision(True, "notify", backoff, track.notification_count)


__all__ = [
    "StuckNotifyConfig",
    "StuckNotifyDecision",
    "StuckNotificationTracker",
]
