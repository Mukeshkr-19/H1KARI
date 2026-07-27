"""Bounded coordinator feeding TimeSenseRuntimeBridge from read-only sources.

Never speaks, schedules, retries, cancels, or mutates tasks. Never stores
transcript text or private payloads. Collection stops at hard caps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Optional

from core.time_sense.adapters import (
    ConversationSessionObservationSource,
    ScheduledJobObservationSource,
    StreamingVoiceObservationSource,
    TaskProgressObservationSource,
)
from core.time_sense.contracts import QuietHoursContext
from core.time_sense.runtime_bridge import (
    RuntimeBridgeConfig,
    TimeSenseRuntimeBridge,
    TimingAdvisory,
)
from core.time_sense.session_policy import (
    ConversationTimingObservation,
    TimingAction,
    TimingReason,
)
from core.time_sense.stuck_detection import StuckDetector
from core.time_sense.stuck_notify import StuckNotificationTracker, StuckNotifyConfig

AwareClock = Callable[[], datetime]

_HARD_MAX_ITEMS = 256
_HARD_MAX_HISTORY = 512
_HARD_MAX_AGE_SECONDS = 86_400.0
_HARD_MAX_FUTURE_SKEW_SECONDS = 5.0
_ID_MAX = 64


def _require_positive_int(value: object, name: str, *, hard_max: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"invalid_{name}")
    if value < 1 or value > hard_max:
        raise ValueError(f"invalid_{name}")
    return value


def _require_positive_float(value: object, name: str, *, hard_max: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_{name}")
    number = float(value)
    if not math.isfinite(number) or number < 1.0 or number > hard_max:
        raise ValueError(f"invalid_{name}")
    return number


def _sanitize_id(value: object, *, name: str = "id") -> Optional[str]:
    if not isinstance(value, str) or not value or len(value) > _ID_MAX:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return None
    return value


def _bounded_take(items: Iterable[object], *, limit: int) -> tuple[list[object], bool]:
    """Iterate at most ``limit`` items; report overflow without materializing the rest."""
    out: list[object] = []
    overflow = False
    iterator = iter(items)
    for _ in range(limit):
        try:
            out.append(next(iterator))
        except StopIteration:
            return out, False
    # One more probe to detect overflow without draining unbounded sources fully.
    try:
        next(iterator)
        overflow = True
    except StopIteration:
        overflow = False
    return out, overflow


@dataclass(frozen=True)
class ObservationCoordinatorConfig:
    max_jobs: int = 32
    max_tasks: int = 32
    max_history: int = 64
    max_observation_age_seconds: float = 300.0
    stuck_cooldown_seconds: float = 60.0
    max_future_skew_seconds: float = 2.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "max_jobs", _require_positive_int(self.max_jobs, "max_jobs", hard_max=_HARD_MAX_ITEMS)
        )
        object.__setattr__(
            self, "max_tasks", _require_positive_int(self.max_tasks, "max_tasks", hard_max=_HARD_MAX_ITEMS)
        )
        object.__setattr__(
            self,
            "max_history",
            _require_positive_int(self.max_history, "max_history", hard_max=_HARD_MAX_HISTORY),
        )
        object.__setattr__(
            self,
            "max_observation_age_seconds",
            _require_positive_float(
                self.max_observation_age_seconds,
                "max_observation_age_seconds",
                hard_max=_HARD_MAX_AGE_SECONDS,
            ),
        )
        object.__setattr__(
            self,
            "stuck_cooldown_seconds",
            _require_positive_float(
                self.stuck_cooldown_seconds,
                "stuck_cooldown_seconds",
                hard_max=_HARD_MAX_AGE_SECONDS,
            ),
        )
        object.__setattr__(
            self,
            "max_future_skew_seconds",
            _require_positive_float(
                self.max_future_skew_seconds,
                "max_future_skew_seconds",
                hard_max=_HARD_MAX_FUTURE_SKEW_SECONDS,
            ),
        )


@dataclass(frozen=True, repr=False)
class CoordinatorSnapshot:
    advisories: tuple[TimingAdvisory, ...]
    stuck_task_ids: tuple[str, ...]
    suppressed: bool
    reason: str

    def __repr__(self) -> str:
        return (
            f"CoordinatorSnapshot(advisories={len(self.advisories)}, "
            f"stuck={len(self.stuck_task_ids)}, suppressed={self.suppressed}, "
            f"reason={self.reason!r})"
        )


class TimeSenseObservationCoordinator:
    """Polls injected observation sources and updates the advisory bridge."""

    def __init__(
        self,
        clock: AwareClock,
        *,
        bridge: Optional[TimeSenseRuntimeBridge] = None,
        conversation: Optional[ConversationSessionObservationSource] = None,
        jobs: Optional[ScheduledJobObservationSource] = None,
        tasks: Optional[TaskProgressObservationSource] = None,
        voice: Optional[StreamingVoiceObservationSource] = None,
        config: Optional[ObservationCoordinatorConfig] = None,
    ) -> None:
        if not callable(clock):
            raise TypeError("clock_must_be_callable")
        self._clock = clock
        self._config = config or ObservationCoordinatorConfig()
        self._bridge = bridge or TimeSenseRuntimeBridge(
            clock,
            config=RuntimeBridgeConfig(
                max_observation_age_seconds=self._config.max_observation_age_seconds
            ),
        )
        self._conversation = conversation
        self._jobs = jobs
        self._tasks = tasks
        self._voice = voice
        self._stuck_detector = StuckDetector()
        self._stuck_notify = StuckNotificationTracker(
            StuckNotifyConfig(
                min_age_seconds=30.0,
                base_backoff_seconds=self._config.stuck_cooldown_seconds,
            )
        )
        self._history: list[str] = []
        self._last_stuck_ids: tuple[str, ...] = ()
        self._cancelled = False

    @property
    def bridge(self) -> TimeSenseRuntimeBridge:
        return self._bridge

    def cancel(self) -> None:
        self._cancelled = True

    def tick(self) -> CoordinatorSnapshot:
        if self._cancelled:
            return CoordinatorSnapshot((), (), True, "cancelled")
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("clock_must_return_aware_datetime")

        quiet: Optional[QuietHoursContext] = None
        observation: Optional[ConversationTimingObservation] = None

        try:
            if self._voice is not None:
                observation = self._voice.speech_activity(now=now)
            if self._conversation is not None:
                current = self._conversation.current_conversation_timing(now=now)
                if current is not None:
                    observation = current
                quiet = self._conversation.quiet_hours(now=now)
        except Exception:
            return CoordinatorSnapshot((), (), True, "source_error")

        if observation is None:
            advisory = TimingAdvisory(
                session_id="none",
                action=TimingAction.SUPPRESS,
                reason=TimingReason.INVALID_EVIDENCE,
                observed_at=now,
            )
            return CoordinatorSnapshot((advisory,), (), True, "no_observation")

        session_id = _sanitize_id(getattr(observation, "session_id", None), name="session_id")
        if session_id is None:
            return CoordinatorSnapshot((), (), True, "invalid_session_id")

        # Classify future vs stale separately against injected now.
        delta = (observation.observed_at - now).total_seconds()
        if delta > float(self._config.max_future_skew_seconds):
            return CoordinatorSnapshot((), (), True, "future_observation")
        age = (now - observation.observed_at).total_seconds()
        if age > float(self._config.max_observation_age_seconds):
            advisory = self._bridge.ingest(
                ConversationTimingObservation(
                    session_id=session_id,
                    observed_at=observation.observed_at,
                    pause_age_seconds=observation.pause_age_seconds,
                    last_user_speech_age_seconds=observation.last_user_speech_age_seconds,
                    last_assistant_response_age_seconds=observation.last_assistant_response_age_seconds,
                    conversation_active=observation.conversation_active,
                    sleeping=True,
                    quiet_hours=True,
                    recent_dismissal=observation.recent_dismissal,
                    child_mode=observation.child_mode,
                    privacy_suppression=True,
                    user_speaking=observation.user_speaking,
                    assistant_speaking=observation.assistant_speaking,
                ),
                quiet_hours_context=quiet,
            )
            return CoordinatorSnapshot((advisory,), (), True, "stale_observation")

        # Re-bind sanitized session id without retaining private task content.
        sanitized = ConversationTimingObservation(
            session_id=session_id,
            observed_at=observation.observed_at,
            pause_age_seconds=observation.pause_age_seconds,
            last_user_speech_age_seconds=observation.last_user_speech_age_seconds,
            last_assistant_response_age_seconds=observation.last_assistant_response_age_seconds,
            conversation_active=observation.conversation_active,
            sleeping=observation.sleeping,
            quiet_hours=observation.quiet_hours,
            recent_dismissal=observation.recent_dismissal,
            child_mode=observation.child_mode,
            privacy_suppression=observation.privacy_suppression,
            user_speaking=observation.user_speaking,
            assistant_speaking=observation.assistant_speaking,
        )
        advisory = self._bridge.ingest(sanitized, quiet_hours_context=quiet)
        stuck_ids = self._evaluate_stuck(now)
        suppressed = advisory.action == TimingAction.SUPPRESS
        self._record(advisory.reason.value)
        return CoordinatorSnapshot(
            self._bridge.snapshot(),
            stuck_ids,
            suppressed,
            advisory.reason.value,
        )

    def content_free_snapshot(self) -> dict:
        snap = CoordinatorSnapshot(
            self._bridge.snapshot(),
            self._last_stuck_ids,
            any(a.action == TimingAction.SUPPRESS for a in self._bridge.snapshot()),
            self._history[-1] if self._history else "empty",
        )
        return {
            "advisory_count": len(snap.advisories),
            "stuck_count": len(snap.stuck_task_ids),
            "suppressed": snap.suppressed,
            "reason": snap.reason,
        }

    def clear(self) -> None:
        self._bridge.clear()
        self._history.clear()
        self._last_stuck_ids = ()
        self._cancelled = False

    def _evaluate_stuck(self, now: datetime) -> tuple[str, ...]:
        stuck: list[str] = []
        if self._cancelled:
            return ()
        if self._tasks is not None:
            try:
                raw = self._tasks.list_task_progress(now=now, limit=self._config.max_tasks)
            except Exception:
                raw = ()
            items, _overflow = _bounded_take(raw, limit=self._config.max_tasks)
            for obs in items:
                task_id = _sanitize_id(getattr(obs, "task_id", None), name="task_id")
                if task_id is None:
                    continue
                try:
                    assessment = self._stuck_detector.assess(obs, now=now)
                except Exception:
                    continue
                if assessment.stuck:
                    stuck.append(task_id)
        if self._jobs is not None:
            try:
                raw_jobs = self._jobs.list_job_observations(now=now, limit=self._config.max_jobs)
            except Exception:
                raw_jobs = ()
            jobs, _overflow = _bounded_take(raw_jobs, limit=self._config.max_jobs)
            for job in jobs:
                job_id = _sanitize_id(getattr(job, "job_id", None), name="job_id")
                if job_id is None:
                    continue
                try:
                    decision = self._stuck_notify.evaluate(job, now=now)
                except Exception:
                    continue
                if decision.should_notify:
                    stuck.append(job_id)
        seen: set[str] = set()
        ordered: list[str] = []
        for item in stuck:
            if item not in seen:
                seen.add(item)
                ordered.append(item)
        self._last_stuck_ids = tuple(ordered)
        return self._last_stuck_ids

    def _record(self, reason: str) -> None:
        if not isinstance(reason, str) or len(reason) > 64:
            reason = "invalid_reason"
        self._history.append(reason)
        if len(self._history) > self._config.max_history:
            self._history = self._history[-self._config.max_history :]


__all__ = [
    "CoordinatorSnapshot",
    "ObservationCoordinatorConfig",
    "TimeSenseObservationCoordinator",
]
