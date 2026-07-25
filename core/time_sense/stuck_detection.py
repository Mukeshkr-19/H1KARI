"""Deterministic, dependency-free stuck-task detection.

A task is reported stuck only from caller-supplied evidence. The module:

- accepts a single injected reference ``now``; it never reads a wall clock;
- uses configurable thresholds via :class:`StuckDetectorConfig`;
- preserves evidence codes, never sensitive raw content;
- produces deterministic scoring and tie-breaking;
- never mutates task or job objects;
- never schedules, retries, or cancels anything;
- calls no external tools and persists no data.

False-positive avoidance is the documented priority. A single slow
observation, a single approval wait, or a quiet-hours delivery pause must
not, by itself, produce ``stuck=True``. Quiet-hours delivery delays are
isolated as ``NOT_STUCK`` (with ``quiet_hours_delivery_pause`` evidence)
when ``quiet_hours_active`` is True on the observation; conversely a genuine
delivery failure with ``quiet_hours_active=False`` raises severity.

Approval waits and user-input waits are kept distinct from technical
failures. A task may be delayed indefinitely pending either and remain
``NOT_STUCK`` — only an attached repeated failing observation, overdue
status, blocked dependency, delivery failure, or missing heartbeat can
raise severity beyond the external-block state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Tuple

from core.time_sense.contracts import (
    StuckAssessment,
    StuckReason,
    TaskProgressObservation,
    TaskProgressState,
)


# ---------------------------------------------------------------------------
# Configurable thresholds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StuckDetectorConfig:
    """Thresholds used to weigh caller-supplied stuck evidence.

    Every threshold is explicit and bounded so that two callers given the
    same ``TaskProgressObservation`` and the same ``now`` produce the same
    :class:`StuckAssessment`. No default reaches into the wall clock.
    """

    no_progress_minutes: int = 45
    repeated_failure_count: int = 3
    repeated_failure_category_count: int = 3
    retries_without_movement_count: int = 3
    overdue_grace_minutes: int = 0
    heartbeat_missing_minutes: int = 10

    # Severity weights — contribute to the final 0.0..1.0 score. Keep the
    # ordering strict so the dominant signal always wins the tiebreak.
    severity_no_progress: float = 0.4
    severity_repeated_failure: float = 0.8
    severity_repeated_category: float = 0.7
    severity_retries: float = 0.5
    severity_overdue: float = 0.6
    severity_blocked_dependency: float = 0.5
    severity_delivery_failed: float = 0.7
    severity_heartbeat_missing: float = 0.55

    # External-block severities are intentionally low and never cause stuck.
    severity_approval_wait: float = 0.1
    severity_user_input_wait: float = 0.1

    # Confidence weights — reflect evidence strength. Multi-signal
    # evidence dominates single-signal evidence.
    confidence_per_signal: float = 0.2
    confidence_max: float = 0.95
    confidence_floor: float = 0.05

    max_future_horizon: timedelta = timedelta(days=366)

    def __post_init__(self) -> None:
        if isinstance(self.no_progress_minutes, bool) or not isinstance(
            self.no_progress_minutes, int
        ):
            raise ValueError("no_progress_minutes must be an integer")
        if self.no_progress_minutes <= 0:
            raise ValueError("no_progress_minutes must be positive")
        for name in (
            "repeated_failure_count",
            "repeated_failure_category_count",
            "retries_without_movement_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        for name in ("overdue_grace_minutes", "heartbeat_missing_minutes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        names = (
            "severity_no_progress",
            "severity_repeated_failure",
            "severity_repeated_category",
            "severity_retries",
            "severity_overdue",
            "severity_blocked_dependency",
            "severity_delivery_failed",
            "severity_heartbeat_missing",
            "severity_approval_wait",
            "severity_user_input_wait",
        )
        for name in names:
            value = getattr(self, name)
            if not isinstance(value, float):
                raise ValueError(f"{name} must be a float")
            if value < 0.0 or value > 1.0:
                raise ValueError(f"{name} must be in 0.0..1.0")
        for name in ("confidence_per_signal", "confidence_max", "confidence_floor"):
            value = getattr(self, name)
            if not isinstance(value, float) or value < 0.0 or value > 1.0:
                raise ValueError(f"{name} must be in 0.0..1.0")
        if self.confidence_floor > self.confidence_max:
            raise ValueError("confidence_floor must not exceed confidence_max")
        if not isinstance(self.max_future_horizon, timedelta):
            raise ValueError("max_future_horizon must be a timedelta")


# ---------------------------------------------------------------------------
# Evidence scoring
# ---------------------------------------------------------------------------


# Codes are opaque, content-free strings that preserve the evidence category
# without exposing raw payloads or task content.
_EVIDENCE_NO_PROGRESS = "no_progress_delayed"
_EVIDENCE_REPEATED_FAILURE = "repeated_failure"
_EVIDENCE_REPEATED_CATEGORY = "repeated_failure_category"
_EVIDENCE_RETRIES = "retries_without_movement"
_EVIDENCE_OVERDUE = "overdue"
_EVIDENCE_BLOCKED_DEPENDENCY = "blocked_dependency"
_EVIDENCE_DELIVERY_FAILED = "delivery_failed"
_EVIDENCE_QUIET_HOURS_DELIVERY_PAUSE = "quiet_hours_delivery_pause"
_EVIDENCE_HEARTBEAT_MISSING = "heartbeat_missing"
_EVIDENCE_APPROVAL_WAIT = "waiting_for_approval"
_EVIDENCE_USER_INPUT_WAIT = "waiting_for_user_input"


def _require_aware(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a timezone-aware datetime")
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _evidence_for(observation: TaskProgressObservation, *, now: datetime,
                  config: StuckDetectorConfig) -> Tuple[
                      Tuple[Tuple[StuckReason, float, str], ...],
                      Tuple[str, ...],
                  ]:
    """Compute a deterministic ordered list of (reason, severity, code) signals.

    Quiet-hours delivery pauses are isolated from the technical-failure
    verdict: when ``quiet_hours_active`` is True, ``delivery_failed`` is
    silently downgraded to a non-stuck informational evidence code instead
    of contributing to the stuck score. Approval and user-input waits are
    kept distinct from technical failure and never move a task into the
    "stuck" bucket on their own.
    """
    signals: list[tuple[StuckReason, float, str]] = []
    evidence_codes: list[str] = list(observation.evidence_codes)

    # 1. Quiet-hours delivery pause should never look like technical failure.
    if observation.delivery_failed and observation.quiet_hours_active:
        evidence_codes.append(_EVIDENCE_QUIET_HOURS_DELIVERY_PAUSE)
        # Deliberately no signal added — pause during quiet hours is not stuck.
    elif observation.delivery_failed:
        signals.append(
            (StuckReason.DELIVERY_FAILED, config.severity_delivery_failed,
             _EVIDENCE_DELIVERY_FAILED)
        )

    # 2. Missing heartbeat.
    if observation.heartbeat_missing and _heartbeat_elapsed(
        observation, now=now, config=config
    ):
        signals.append(
            (StuckReason.HEARTBEAT_MISSING, config.severity_heartbeat_missing,
             _EVIDENCE_HEARTBEAT_MISSING)
        )

    # 3. Repeated failure category (same error class repeatedly).
    if (
        observation.repeated_failure_count >= config.repeated_failure_count
        and observation.repeated_failure_category
    ):
        signals.append(
            (StuckReason.REPEATED_FAILURE_CATEGORY,
             config.severity_repeated_category,
             _EVIDENCE_REPEATED_CATEGORY)
        )
    elif observation.repeated_failure_count >= config.repeated_failure_count:
        signals.append(
            (StuckReason.REPEATED_FAILURE, config.severity_repeated_failure,
             _EVIDENCE_REPEATED_FAILURE)
        )

    # 4. Retries without state movement (RETRYING state with many attempts).
    if (
        observation.state is TaskProgressState.RETRYING
        and observation.attempt_count >= config.retries_without_movement_count
    ):
        signals.append(
            (StuckReason.RETRIES_WITHOUT_MOVEMENT, config.severity_retries,
             _EVIDENCE_RETRIES)
        )

    # 5. No progress after an expected interval.
    if observation.state is TaskProgressState.NO_PROGRESS and _no_progress_elapsed(
        observation, now=now, config=config
    ):
        signals.append(
            (StuckReason.NO_PROGRESS_DELAYED, config.severity_no_progress,
             _EVIDENCE_NO_PROGRESS)
        )

    # 6. Overdue (with optional grace window).
    if observation.overdue and _overdue_concrete(
        observation, now=now, config=config
    ):
        signals.append(
            (StuckReason.OVERDUE, config.severity_overdue, _EVIDENCE_OVERDUE)
        )

    # 7. Blocked dependency.
    if observation.blocked_dependency:
        signals.append(
            (StuckReason.BLOCKED_DEPENDENCY,
             config.severity_blocked_dependency,
             _EVIDENCE_BLOCKED_DEPENDENCY)
        )

    # External-block signals are informational only. They are returned so
    # the assessment can preserve the distinction, but they do not raise
    # the stuck score by themselves.
    if observation.waiting_for_approval:
        evidence_codes.append(_EVIDENCE_APPROVAL_WAIT)
    if observation.waiting_for_user_input:
        evidence_codes.append(_EVIDENCE_USER_INPUT_WAIT)

    return tuple(signals), tuple(evidence_codes)


def _heartbeat_elapsed(
    observation: TaskProgressObservation, *, now: datetime,
    config: StuckDetectorConfig,
) -> bool:
    anchor = observation.last_progress_at or observation.observed_at
    if observation.parsed_at is not None and observation.parsed_at > anchor:
        anchor = observation.parsed_at
    delta = now - anchor
    return delta >= timedelta(minutes=config.heartbeat_missing_minutes)


def _no_progress_elapsed(
    observation: TaskProgressObservation, *, now: datetime,
    config: StuckDetectorConfig,
) -> bool:
    anchor = observation.last_progress_at or observation.observed_at
    delta = now - anchor
    return delta >= timedelta(minutes=config.no_progress_minutes)


def _overdue_concrete(
    observation: TaskProgressObservation, *, now: datetime,
    config: StuckDetectorConfig,
) -> bool:
    if observation.parsed_at is None:
        return True
    delta = now - observation.parsed_at
    return delta >= timedelta(minutes=config.overdue_grace_minutes)


def _dominant_signal(
    signals: Tuple[Tuple[StuckReason, float, str], ...],
) -> Tuple[StuckReason, float, str]:
    """Deterministic tie-break: highest severity, then stable reason order.

    Two signals with equal severity keep the original scan order so the
    result is reproducible regardless of dict hashing.
    """
    best = signals[0]
    for signal in signals[1:]:
        if signal[1] > best[1]:
            best = signal
    return best


# Stable reason ordering for deterministic tie-breaking when severities
# happen to be equal. Lower index wins.
_REASON_ORDER: tuple[StuckReason, ...] = (
    StuckReason.REPEATED_FAILURE,
    StuckReason.REPEATED_FAILURE_CATEGORY,
    StuckReason.DELIVERY_FAILED,
    StuckReason.HEARTBEAT_MISSING,
    StuckReason.OVERDUE,
    StuckReason.NO_PROGRESS_DELAYED,
    StuckReason.BLOCKED_DEPENDENCY,
    StuckReason.RETRIES_WITHOUT_MOVEMENT,
)


def _reason_sort_key(reason: StuckReason) -> int:
    try:
        return _REASON_ORDER.index(reason)
    except ValueError:
        return len(_REASON_ORDER)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StuckDetector:
    """Stateless assessor for contiguous caller-supplied evidence.

    A :class:`StuckDetector` holds only a configuration and never any task
    state. Each call to ``assess`` is independent and deterministic.
    """

    config: StuckDetectorConfig = field(default_factory=StuckDetectorConfig)

    def assess(
        self,
        observation: TaskProgressObservation,
        *,
        now: datetime,
    ) -> StuckAssessment:
        """Assess a single caller-supplied observation against ``now``.

        ``now`` is the injected reference clock and must be timezone-aware.
        The returned :class:`StuckAssessment` is immutable and carries the
        dominant reason (or ``NOT_STUCK``), severity, and confidence.
        """
        return assess_stuck(
            observation, now=now, config=self.config,
        )


def assess_stuck(
    observation: TaskProgressObservation,
    *,
    now: datetime,
    config: Optional[StuckDetectorConfig] = None,
) -> StuckAssessment:
    """Assess a single caller-supplied observation and return a verdict.

    The function is pure: same inputs produce the identical output. It never
    mutates ``observation`` or any task object, never persists data, and
    never calls an external tool.
    """
    _require_aware(now, "now")
    if not isinstance(observation, TaskProgressObservation):
        raise ValueError("observation must be a TaskProgressObservation")
    if observation.observed_at > now + config.max_future_horizon if config else False:
        raise ValueError("observation observed_at exceeds max_future_horizon")
    cfg = config or StuckDetectorConfig()
    if observation.observed_at > now + cfg.max_future_horizon:
        raise ValueError("observation observed_at exceeds max_future_horizon")

    signals, evidence_codes = _evidence_for(
        observation, now=now, config=cfg
    )

    if not signals:
        return StuckAssessment(
            task_id=observation.task_id,
            stuck=False,
            reason=StuckReason.NOT_STUCK,
            severity=0.0,
            confidence=cfg.confidence_floor,
            evidence_codes=evidence_codes,
            notes=("no_stuck_signal",),
        )

    # recognising quiet hours delivery pauses explicitly even when other
    # signals are present — the pause does not raise the score.
    pending_quiet_pause = (
        observation.delivery_failed
        and observation.quiet_hours_active
        and _EVIDENCE_QUIET_HOURS_DELIVERY_PAUSE in evidence_codes
    )

    # If external-block signals (approval/user-input) are present and there
    # is no other technical-failure signal, we keep NOT_STUCK.
    technical_signals = tuple(
        signal for signal in signals
        if signal[0] not in (
            StuckReason.WAITING_FOR_APPROVAL,
            StuckReason.WAITING_FOR_USER_INPUT,
        )
    )
    if not technical_signals:
        return StuckAssessment(
            task_id=observation.task_id,
            stuck=False,
            reason=StuckReason.NOT_STUCK,
            severity=cfg.confidence_floor,
            confidence=cfg.confidence_floor,
            evidence_codes=evidence_codes,
            notes=(
                "external_block_without_technical_failure",
                "quiet_hours_delivery_pause" if pending_quiet_pause else "",
            ),
        )

    ordered = sorted(
        technical_signals, key=lambda s: (-s[1], _reason_sort_key(s[0]))
    )
    reason, severity, _code = ordered[0]
    signal_codes = tuple(signal[2] for signal in signals)
    evidence_codes = tuple(dict.fromkeys((*evidence_codes, *signal_codes)))
    confidence = min(
        cfg.confidence_max,
        cfg.confidence_floor
        + cfg.confidence_per_signal * len(technical_signals),
    )
    notes: tuple[str, ...]
    if pending_quiet_pause:
        notes = ("dominant_signal", "quiet_hours_delivery_pause_observed")
    else:
        notes = ("dominant_signal",)

    return StuckAssessment(
        task_id=observation.task_id,
        stuck=True,
        reason=reason,
        severity=min(max(severity, 0.0), 1.0),
        confidence=confidence,
        evidence_codes=evidence_codes,
        notes=notes,
    )


__all__ = [
    "StuckAssessment",
    "StuckDetector",
    "StuckDetectorConfig",
    "StuckReason",
    "assess_stuck",
]
