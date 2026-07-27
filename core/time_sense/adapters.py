"""Adapter protocols for Mira-owned Time Sense integration.

Describe how scheduled jobs, conversation sessions, and streaming voice will
supply observations. No database or runtime I/O in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol, Sequence, runtime_checkable

from core.time_sense.job_observations import JobTimingObservation
from core.time_sense.session_policy import ConversationTimingObservation
from core.time_sense.contracts import TaskProgressObservation, QuietHoursContext


@runtime_checkable
class ScheduledJobObservationSource(Protocol):
    """Mira-owned scheduled-job adapter supplies job timing observations."""

    def list_job_observations(
        self, *, now: datetime, limit: int = 32
    ) -> Sequence[JobTimingObservation]:
        ...


@runtime_checkable
class ConversationSessionObservationSource(Protocol):
    """Mira-owned conversation session adapter supplies timing observations."""

    def current_conversation_timing(
        self, *, now: datetime
    ) -> Optional[ConversationTimingObservation]:
        ...

    def quiet_hours(self, *, now: datetime) -> QuietHoursContext:
        ...


@runtime_checkable
class StreamingVoiceObservationSource(Protocol):
    """Streaming-voice adapter projects turn/speech ages into timing evidence."""

    def speech_activity(
        self, *, now: datetime
    ) -> ConversationTimingObservation:
        ...


@runtime_checkable
class TaskProgressObservationSource(Protocol):
    """Existing stuck-detection observation supplier (task IDs only)."""

    def list_task_progress(
        self, *, now: datetime, limit: int = 32
    ) -> Sequence[TaskProgressObservation]:
        ...


__all__ = [
    "ConversationSessionObservationSource",
    "ScheduledJobObservationSource",
    "StaticConversationSessionSource",
    "StaticScheduledJobSource",
    "StaticStreamingVoiceSource",
    "StaticTaskProgressSource",
    "StreamingVoiceObservationSource",
    "TaskProgressObservationSource",
]


@dataclass(frozen=True)
class StaticConversationSessionSource:
    """Injected conversation timing supplier for tests and Mira wiring."""

    observation: Optional[ConversationTimingObservation]
    quiet: QuietHoursContext

    def current_conversation_timing(self, *, now: datetime):
        return self.observation

    def quiet_hours(self, *, now: datetime) -> QuietHoursContext:
        return self.quiet


@dataclass(frozen=True)
class StaticScheduledJobSource:
    observations: tuple[JobTimingObservation, ...]

    def list_job_observations(self, *, now: datetime, limit: int = 32):
        return self.observations[: max(0, min(limit, 32))]


@dataclass(frozen=True)
class StaticTaskProgressSource:
    observations: tuple[TaskProgressObservation, ...]

    def list_task_progress(self, *, now: datetime, limit: int = 32):
        return self.observations[: max(0, min(limit, 32))]


@dataclass(frozen=True)
class StaticStreamingVoiceSource:
    observation: ConversationTimingObservation

    def speech_activity(self, *, now: datetime) -> ConversationTimingObservation:
        return self.observation
