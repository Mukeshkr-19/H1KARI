"""Deterministic, dependency-free Time Sense package.

Public surface:

- :mod:`core.time_sense.contracts` — typed immutable contracts.
- :func:`interpret_time_phrase` — conversational time interpretation.
- :func:`assess_stuck` and :class:`StuckDetector` — stuck task detection.
- :func:`build_awareness` and :class:`AwarenessBuilder` — bounded background
  work awareness snapshots.
- :func:`recommend_notification` — quiet-hours-aware notification advice.
- :mod:`core.time_sense.session_policy` — conversation timing policy.
- :mod:`core.time_sense.job_observations` — job timing observations.
- :mod:`core.time_sense.stuck_notify` — stuck notification backoff/dedupe.
- :mod:`core.time_sense.adapters` — Mira integration protocols (no I/O).

Time Sense never mutates task or job objects, never schedules, delivers,
retries, or cancels work, and never touches the wall clock. A caller-supplied
reference datetime is the only time source.
"""

from __future__ import annotations

from core.time_sense.adapters import (
    ConversationSessionObservationSource,
    ScheduledJobObservationSource,
    StreamingVoiceObservationSource,
    TaskProgressObservationSource,
)
from core.time_sense.background_awareness import (
    AwarenessBuilder,
    build_awareness,
    recommend_notification,
)
from core.time_sense.contracts import (
    DEFAULT_MAX_FUTURE_HORIZON,
    MAX_IDENTIFIER_LENGTH,
    MAX_SNAPSHOT_ITEMS,
    AwarenessSnapshot,
    BackgroundActivity,
    NotificationAdvice,
    NotificationRecommendation,
    QuietHoursContext,
    StuckAssessment,
    StuckReason,
    TaskProgressObservation,
    TaskProgressState,
    TemporalInterpretation,
    TemporalPrecision,
    TimeReference,
)
from core.time_sense.conversation_timing import interpret_time_phrase
from core.time_sense.job_observations import JobObservationState, JobTimingObservation
from core.time_sense.session_policy import (
    ConversationTimingObservation,
    TimingAction,
    TimingPolicyConfig,
    TimingPolicyDecision,
    TimingReason,
    evaluate_conversation_timing,
)
from core.time_sense.stuck_detection import (
    StuckDetector,
    StuckDetectorConfig,
    assess_stuck,
)
from core.time_sense.stuck_notify import (
    StuckNotifyConfig,
    StuckNotifyDecision,
    StuckNotificationTracker,
)

__all__ = [
    "DEFAULT_MAX_FUTURE_HORIZON",
    "MAX_IDENTIFIER_LENGTH",
    "MAX_SNAPSHOT_ITEMS",
    "AwarenessBuilder",
    "AwarenessSnapshot",
    "BackgroundActivity",
    "ConversationSessionObservationSource",
    "ConversationTimingObservation",
    "JobObservationState",
    "JobTimingObservation",
    "NotificationAdvice",
    "NotificationRecommendation",
    "QuietHoursContext",
    "ScheduledJobObservationSource",
    "StreamingVoiceObservationSource",
    "StuckAssessment",
    "StuckDetector",
    "StuckDetectorConfig",
    "StuckNotifyConfig",
    "StuckNotifyDecision",
    "StuckNotificationTracker",
    "StuckReason",
    "TaskProgressObservation",
    "TaskProgressObservationSource",
    "TaskProgressState",
    "TemporalInterpretation",
    "TemporalPrecision",
    "TimeReference",
    "TimingAction",
    "TimingPolicyConfig",
    "TimingPolicyDecision",
    "TimingReason",
    "assess_stuck",
    "build_awareness",
    "evaluate_conversation_timing",
    "interpret_time_phrase",
    "recommend_notification",
]
