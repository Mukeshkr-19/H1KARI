"""Conversational timing observations and proactive-output policy.

Pure evidence evaluation with an injected reference datetime. Never schedules,
speaks, or mutates session or job state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import math
from typing import Optional

from core.time_sense.contracts import QuietHoursContext


class TimingAction(StrEnum):
    WAIT = "wait"
    RESPOND = "respond"
    CHECK_IN = "check_in"
    SUMMARIZE = "summarize"
    SUPPRESS = "suppress"


class TimingReason(StrEnum):
    OK = "ok"
    QUIET_HOURS = "quiet_hours"
    CHILD_MODE = "child_mode"
    PRIVACY_SUPPRESSION = "privacy_suppression"
    SLEEPING = "sleeping"
    ACTIVE_SPEECH = "active_speech"
    RECENT_DISMISSAL = "recent_dismissal"
    PAUSE_TOO_SHORT = "pause_too_short"
    PAUSE_CHECK_IN = "pause_check_in"
    PAUSE_SUMMARIZE = "pause_summarize"
    INVALID_EVIDENCE = "invalid_evidence"


def _require_aware(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name}_must_be_datetime")
    if value.tzinfo is None:
        raise ValueError(f"{name}_must_be_timezone_aware")
    return value


@dataclass(frozen=True, repr=False)
class ConversationTimingObservation:
    """Session timing evidence. No transcript or private payloads."""

    session_id: str
    observed_at: datetime
    pause_age_seconds: float
    last_user_speech_age_seconds: Optional[float]
    last_assistant_response_age_seconds: Optional[float]
    conversation_active: bool
    sleeping: bool
    quiet_hours: bool
    recent_dismissal: bool
    child_mode: bool
    privacy_suppression: bool
    user_speaking: bool
    assistant_speaking: bool

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not self.session_id or len(self.session_id) > 80:
            raise ValueError("invalid_session_id")
        _require_aware(self.observed_at, "observed_at")
        for name in (
            "pause_age_seconds",
            "last_user_speech_age_seconds",
            "last_assistant_response_age_seconds",
        ):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"invalid_{name}")
            number = float(value)
            if number != number or number in (float("inf"), float("-inf")) or number < 0.0:
                raise ValueError(f"invalid_{name}")
            object.__setattr__(self, name, number)
        for name in (
            "conversation_active",
            "sleeping",
            "quiet_hours",
            "recent_dismissal",
            "child_mode",
            "privacy_suppression",
            "user_speaking",
            "assistant_speaking",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"invalid_{name}")

    def __repr__(self) -> str:
        return (
            f"ConversationTimingObservation(session_id={self.session_id!r}, "
            f"sleeping={self.sleeping}, quiet_hours={self.quiet_hours})"
        )


@dataclass(frozen=True)
class TimingPolicyConfig:
    min_pause_before_respond_seconds: float = 1.5
    check_in_after_seconds: float = 45.0
    summarize_after_seconds: float = 180.0

    def __post_init__(self) -> None:
        for name in (
            "min_pause_before_respond_seconds",
            "check_in_after_seconds",
            "summarize_after_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0.0 <= float(value) <= 86_400.0:
                raise ValueError(f"invalid_{name}")
        if not self.min_pause_before_respond_seconds <= self.check_in_after_seconds <= self.summarize_after_seconds:
            raise ValueError("invalid_timing_order")


@dataclass(frozen=True, repr=False)
class TimingPolicyDecision:
    action: TimingAction
    reason: TimingReason

    def __repr__(self) -> str:
        return f"TimingPolicyDecision(action={self.action.value!r}, reason={self.reason.value!r})"


def evaluate_conversation_timing(
    observation: ConversationTimingObservation,
    *,
    config: Optional[TimingPolicyConfig] = None,
    quiet_hours_context: Optional[QuietHoursContext] = None,
) -> TimingPolicyDecision:
    """Decide WAIT/RESPOND/CHECK_IN/SUMMARIZE/SUPPRESS from supplied evidence only."""
    if not isinstance(observation, ConversationTimingObservation):
        return TimingPolicyDecision(TimingAction.SUPPRESS, TimingReason.INVALID_EVIDENCE)
    cfg = config or TimingPolicyConfig()

    quiet = observation.quiet_hours
    if quiet_hours_context is not None:
        if not isinstance(quiet_hours_context, QuietHoursContext):
            return TimingPolicyDecision(TimingAction.SUPPRESS, TimingReason.INVALID_EVIDENCE)
        quiet = quiet or bool(quiet_hours_context.active)

    if observation.privacy_suppression:
        return TimingPolicyDecision(TimingAction.SUPPRESS, TimingReason.PRIVACY_SUPPRESSION)
    if observation.child_mode:
        return TimingPolicyDecision(TimingAction.SUPPRESS, TimingReason.CHILD_MODE)
    if quiet:
        return TimingPolicyDecision(TimingAction.SUPPRESS, TimingReason.QUIET_HOURS)
    if observation.sleeping:
        return TimingPolicyDecision(TimingAction.SUPPRESS, TimingReason.SLEEPING)
    if observation.user_speaking or observation.assistant_speaking:
        return TimingPolicyDecision(TimingAction.SUPPRESS, TimingReason.ACTIVE_SPEECH)
    if observation.recent_dismissal:
        return TimingPolicyDecision(TimingAction.SUPPRESS, TimingReason.RECENT_DISMISSAL)

    pause = observation.pause_age_seconds
    if pause >= cfg.summarize_after_seconds:
        return TimingPolicyDecision(TimingAction.SUMMARIZE, TimingReason.PAUSE_SUMMARIZE)
    if pause >= cfg.check_in_after_seconds:
        return TimingPolicyDecision(TimingAction.CHECK_IN, TimingReason.PAUSE_CHECK_IN)
    if observation.conversation_active and pause >= cfg.min_pause_before_respond_seconds:
        return TimingPolicyDecision(TimingAction.RESPOND, TimingReason.OK)
    if pause < cfg.min_pause_before_respond_seconds:
        return TimingPolicyDecision(TimingAction.WAIT, TimingReason.PAUSE_TOO_SHORT)
    return TimingPolicyDecision(TimingAction.WAIT, TimingReason.OK)


__all__ = [
    "ConversationTimingObservation",
    "TimingAction",
    "TimingPolicyConfig",
    "TimingPolicyDecision",
    "TimingReason",
    "evaluate_conversation_timing",
]
