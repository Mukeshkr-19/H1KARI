"""Bounded background-work awareness.

The awareness layer collates caller-supplied observations into a single
:class:`AwarenessSnapshot`. It performs no background thread, scheduler,
database read, polling, subprocess, automatic delivery, general AI, or
Brain v2 personal-fact access. Sensitive task payloads are excluded by
default — the caller must opt into a bounded ``payload_hint`` label.

The optional natural-language summary is produced conservatively and must
avoid claims such as "I am still working" unless the supplied observations
prove active execution. Likewise, completion claims are only made when the
caller's observations confirm completion; notifications are never claimed
to have been delivered unless the caller's state confirms delivery.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Tuple

from core.time_sense.contracts import (
    AwarenessSnapshot,
    BackgroundActivity,
    MAX_SNAPSHOT_ITEMS,
    NotificationAdvice,
    NotificationRecommendation,
    QuietHoursContext,
    StuckAssessment,
    TaskProgressObservation,
    TaskProgressState,
)
from core.time_sense.stuck_detection import (
    StuckDetector,
    StuckDetectorConfig,
    assess_stuck,
)


def _require_aware(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a timezone-aware datetime")
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


_ACTIVE_STATES: frozenset[TaskProgressState] = frozenset({
    TaskProgressState.MAKING_PROGRESS,
    TaskProgressState.RETRYING,
})
_EXTERNAL_BLOCK_STATES: frozenset[TaskProgressState] = frozenset({
    TaskProgressState.WAITING_FOR_APPROVAL,
    TaskProgressState.WAITING_FOR_USER_INPUT,
})


@dataclass(frozen=True)
class AwarenessBuilder:
    """Bounded collator of caller-supplied background work observations.

    The builder is stateless aside from its configuration. All buckets are
    capped at ``MAX_SNAPSHOT_ITEMS`` items per bucket. Sensitive payloads
    are excluded by default; pass ``include_payload_hint=True`` to carry a
    sanitized, bounded label.
    """

    max_items_per_bucket: int = MAX_SNAPSHOT_ITEMS
    include_payload_hint: bool = False
    stuck_config: StuckDetectorConfig = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_items_per_bucket, bool)
            or not isinstance(self.max_items_per_bucket, int)
            or self.max_items_per_bucket <= 0
        ):
            raise ValueError("max_items_per_bucket must be a positive integer")
        if self.max_items_per_bucket > MAX_SNAPSHOT_ITEMS:
            raise ValueError(
                "max_items_per_bucket must not exceed MAX_SNAPSHOT_ITEMS"
            )
        if not isinstance(self.include_payload_hint, bool):
            raise ValueError("include_payload_hint must be a boolean")
        if self.stuck_config is None:
            object.__setattr__(self, "stuck_config", StuckDetectorConfig())
        elif not isinstance(self.stuck_config, StuckDetectorConfig):
            raise ValueError("stuck_config must be a StuckDetectorConfig")

    def build(
        self,
        *,
        now: datetime,
        observations: Iterable[TaskProgressObservation],
        recently_completed: Iterable[BackgroundActivity] = (),
        retrying_jobs: Iterable[BackgroundActivity] = (),
        delivery_delayed: Iterable[BackgroundActivity] = (),
        quiet_hours: Optional[QuietHoursContext] = None,
        summary: Optional[str] = None,
    ) -> AwarenessSnapshot:
        """Collate caller-supplied observations into a bounded snapshot.

        The function performs no I/O and never mutates any of the inputs.
        Capped buckets preserve insertion order; the caller's authoritative
        order is respected. A task observation that is ``WAITING_FOR_APPROVAL``
        or ``WAITING_FOR_USER_INPUT`` is placed in the matching external-block
        bucket and never also in ``active_tasks``, even if the caller asserts
        the task is making progress.
        """
        _require_aware(now, "now")

        observation_items = tuple(observations)
        active: list[BackgroundActivity] = []
        approval_blocked: list[BackgroundActivity] = []
        user_input_blocked: list[BackgroundActivity] = []
        stuck_candidates: list[BackgroundActivity] = []
        quiet_suppressed: list[BackgroundActivity] = []
        detector = StuckDetector(config=self.stuck_config)

        for obs in observation_items:
            if not isinstance(obs, TaskProgressObservation):
                raise ValueError(
                    "observations must be TaskProgressObservation instances"
                )
            assessment = detector.assess(obs, now=now)
            if obs.waiting_for_approval:
                approval_blocked.append(
                    _to_activity(obs, include_payload_hint=self.include_payload_hint)
                )
                continue
            if obs.waiting_for_user_input:
                user_input_blocked.append(
                    _to_activity(obs, include_payload_hint=self.include_payload_hint)
                )
                continue
            if obs.delivery_failed and obs.quiet_hours_active:
                quiet_suppressed.append(
                    _to_activity(obs, include_payload_hint=self.include_payload_hint)
                )
                continue
            if assessment.stuck:
                stuck_candidates.append(
                    _to_activity(
                        obs,
                        assessment=assessment,
                        include_payload_hint=self.include_payload_hint,
                    )
                )
                continue
            if obs.state in _ACTIVE_STATES:
                active.append(
                    _to_activity(obs, include_payload_hint=self.include_payload_hint)
                )

        activities_recently_completed = _cap(
            list(recently_completed), self.max_items_per_bucket
        )
        activities_retrying_jobs = _cap(
            list(retrying_jobs), self.max_items_per_bucket
        )
        activities_delivery_delayed = _cap(
            list(delivery_delayed), self.max_items_per_bucket
        )

        cleaned_summary: Optional[str] = (
            summary
            if self._is_safe_summary(
                summary,
                observation_items,
                activities_recently_completed,
            )
            else None
        )

        return AwarenessSnapshot(
            reference_at=now,
            active_tasks=_cap(active, self.max_items_per_bucket),
            recently_completed=tuple(activities_recently_completed),
            approval_blocked=_cap(approval_blocked, self.max_items_per_bucket),
            user_input_blocked=_cap(user_input_blocked, self.max_items_per_bucket),
            retrying_jobs=tuple(activities_retrying_jobs),
            stuck_candidates=_cap(stuck_candidates, self.max_items_per_bucket),
            delivery_delayed=tuple(activities_delivery_delayed),
            quiet_hours_suppressed=_cap(quiet_suppressed, self.max_items_per_bucket),
            quiet_hours=quiet_hours,
            summary=cleaned_summary,
            summary_provenance=_provenance_for(
                active=active,
                completed=activities_recently_completed,
                approval=approval_blocked,
                user_input=user_input_blocked,
                stuck=stuck_candidates,
                quiet=quiet_suppressed,
            ),
        )

    @staticmethod
    def _is_safe_summary(
        summary: Optional[str],
        observations: Tuple[TaskProgressObservation, ...],
        recently_completed: list[BackgroundActivity],
    ) -> bool:
        if summary is None:
            return False
        text = summary.lower()
        if "i am still working" in text:
            evidence_active = any(
                obs.state is TaskProgressState.MAKING_PROGRESS
                for obs in observations
            )
            if not evidence_active:
                return False
        if "completed" in text and "all" in text and not recently_completed:
            return False
        return True


def build_awareness(
    *,
    now: datetime,
    observations: Iterable[TaskProgressObservation],
    recently_completed: Iterable[BackgroundActivity] = (),
    retrying_jobs: Iterable[BackgroundActivity] = (),
    delivery_delayed: Iterable[BackgroundActivity] = (),
    quiet_hours: Optional[QuietHoursContext] = None,
    summary: Optional[str] = None,
    max_items_per_bucket: int = MAX_SNAPSHOT_ITEMS,
    include_payload_hint: bool = False,
    stuck_config: Optional[StuckDetectorConfig] = None,
) -> AwarenessSnapshot:
    """Convenience wrapper around :class:`AwarenessBuilder`."""
    builder = AwarenessBuilder(
        max_items_per_bucket=max_items_per_bucket,
        include_payload_hint=include_payload_hint,
        stuck_config=stuck_config or StuckDetectorConfig(),
    )
    return builder.build(
        now=now,
        observations=observations,
        recently_completed=recently_completed,
        retrying_jobs=retrying_jobs,
        delivery_delayed=delivery_delayed,
        quiet_hours=quiet_hours,
        summary=summary,
    )


def recommend_notification(
    *,
    quiet_hours: Optional[QuietHoursContext] = None,
    delivered: bool = False,
) -> NotificationRecommendation:
    """Quiet-hours-aware notification recommendation (advice only).

    The function does not deliver, suppress, or pause a notification itself;
    it returns a :class:`NotificationRecommendation` that the caller may
    honour. ``delivered`` is True only when caller-supplied state confirms
    actual delivery, never inferred from queued or running state.
    """
    if delivered and quiet_hours is not None and quiet_hours.active:
        # Quiet hours would not allow a fresh delivery now; if the caller
        # confirms an existing delivery, we accept the authoritative state.
        return NotificationRecommendation(
            advice=NotificationAdvice.DELIVER,
            quiet_hours=quiet_hours,
            delivered=True,
        )
    if quiet_hours is not None and quiet_hours.active and not delivered:
        return NotificationRecommendation(
            advice=NotificationAdvice.DEFER,
            quiet_hours=quiet_hours,
            delivered=False,
        )
    if quiet_hours is not None and quiet_hours.suppressed and not delivered:
        return NotificationRecommendation(
            advice=NotificationAdvice.SUPPRESS,
            quiet_hours=quiet_hours,
            delivered=False,
        )
    return NotificationRecommendation(
        advice=NotificationAdvice.DELIVER,
        quiet_hours=quiet_hours,
        delivered=delivered,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_activity(
    obs: TaskProgressObservation,
    *,
    assessment: Optional[StuckAssessment] = None,
    include_payload_hint: bool = False,
) -> BackgroundActivity:
    hint = None
    if (
        include_payload_hint
        and assessment is not None
        and assessment.reason is not None
    ):
        hint = assessment.reason.value
    return BackgroundActivity(
        item_id=obs.task_id,
        kind=obs.kind,
        status=obs.state.value,
        observed_at=obs.observed_at,
        payload_hint=hint,
    )


def _cap(items: list[BackgroundActivity], limit: int) -> Tuple[BackgroundActivity, ...]:
    for item in items:
        if not isinstance(item, BackgroundActivity):
            raise ValueError("background items must be BackgroundActivity instances")
    if len(items) > limit:
        items = items[:limit]
    return tuple(items)


def _provenance_for(
    *,
    active: list[BackgroundActivity],
    completed: list[BackgroundActivity],
    approval: list[BackgroundActivity],
    user_input: list[BackgroundActivity],
    stuck: list[BackgroundActivity],
    quiet: list[BackgroundActivity],
) -> Tuple[str, ...]:
    prov: list[str] = []
    if active:
        prov.append(f"active_count={len(active)}")
    if completed:
        prov.append(f"completed_count={len(completed)}")
    if approval:
        prov.append(f"approval_blocked_count={len(approval)}")
    if user_input:
        prov.append(f"user_input_blocked_count={len(user_input)}")
    if stuck:
        prov.append(f"stuck_count={len(stuck)}")
    if quiet:
        prov.append(f"quiet_hours_suppressed_count={len(quiet)}")
    return tuple(prov)


__all__ = [
    "AwarenessBuilder",
    "build_awareness",
    "recommend_notification",
]
