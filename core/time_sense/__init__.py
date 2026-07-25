"""Deterministic, dependency-free Time Sense package.

Public surface:

- :mod:`core.time_sense.contracts` — typed immutable contracts.
- :func:`interpret_time_phrase` — conversational time interpretation.
- :func:`assess_stuck` and :class:`StuckDetector` — stuck task detection.
- :func:`build_awareness` and :class:`AwarenessBuilder` — bounded background
  work awareness snapshots.
- :func:`recommend_notification` — quiet-hours-aware notification advice.

Time Sense never mutates task or job objects, never schedules, delivers,
retries, or cancels work, and never touches the wall clock. A caller-supplied
reference datetime is the only time source.
"""

from __future__ import annotations

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
from core.time_sense.stuck_detection import (
    StuckDetector,
    StuckDetectorConfig,
    assess_stuck,
)

# Conversation_timing is the module that owns ``interpret_time_phrase``. We
# re-export it through ``stuck_detection``'s module path for discoverability,
# but the canonical home is ``conversation_timing``.
from core.time_sense.conversation_timing import interpret_time_phrase

__all__ = [
    "DEFAULT_MAX_FUTURE_HORIZON",
    "MAX_IDENTIFIER_LENGTH",
    "MAX_SNAPSHOT_ITEMS",
    "AwarenessBuilder",
    "AwarenessSnapshot",
    "BackgroundActivity",
    "NotificationAdvice",
    "NotificationRecommendation",
    "QuietHoursContext",
    "StuckAssessment",
    "StuckDetector",
    "StuckDetectorConfig",
    "StuckReason",
    "TaskProgressObservation",
    "TaskProgressState",
    "TemporalInterpretation",
    "TemporalPrecision",
    "TimeReference",
    "assess_stuck",
    "build_awareness",
    "interpret_time_phrase",
    "recommend_notification",
]
