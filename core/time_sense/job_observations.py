"""Task/background-job timing observations (IDs and status only)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Optional, Tuple

from core.time_sense.contracts import _validate_identifier


class JobObservationState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RESOLVED = "resolved"


@dataclass(frozen=True, repr=False)
class JobTimingObservation:
    """Bounded job timing evidence. No raw private payloads."""

    job_id: str
    state: JobObservationState
    observed_at: datetime
    age_seconds: float
    heartbeat_age_seconds: Optional[float]
    attempt_count: int
    completion_estimate_min_seconds: Optional[float] = None
    completion_estimate_max_seconds: Optional[float] = None
    cancellation_evidence: Optional[str] = None
    resolution_evidence: Optional[str] = None

    def __post_init__(self) -> None:
        _validate_identifier("job_id", self.job_id)
        if not isinstance(self.state, JobObservationState):
            raise ValueError("invalid_state")
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None:
            raise ValueError("observed_at_must_be_timezone_aware")
        for name in (
            "age_seconds",
            "heartbeat_age_seconds",
            "completion_estimate_min_seconds",
            "completion_estimate_max_seconds",
        ):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"invalid_{name}")
            number = float(value)
            if not math.isfinite(number) or number < 0.0:
                raise ValueError(f"invalid_{name}")
            object.__setattr__(self, name, number)
        lo = self.completion_estimate_min_seconds
        hi = self.completion_estimate_max_seconds
        if (lo is None) ^ (hi is None):
            raise ValueError("invalid_completion_estimate_range")
        if lo is not None and hi is not None and hi < lo:
            raise ValueError("invalid_completion_estimate_range")
        if isinstance(self.attempt_count, bool) or not isinstance(self.attempt_count, int):
            raise ValueError("invalid_attempt_count")
        if self.attempt_count < 0 or self.attempt_count > 10_000:
            raise ValueError("invalid_attempt_count")
        for name in ("cancellation_evidence", "resolution_evidence"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, str) or not value or len(value) > 64:
                raise ValueError(f"invalid_{name}")
            if not all(ch.isalnum() or ch in "_-." for ch in value):
                raise ValueError(f"invalid_{name}")

    @property
    def is_terminal(self) -> bool:
        return self.state in (
            JobObservationState.SUCCEEDED,
            JobObservationState.FAILED,
            JobObservationState.CANCELLED,
            JobObservationState.RESOLVED,
        )

    def __repr__(self) -> str:
        return f"JobTimingObservation(job_id={self.job_id!r}, state={self.state.value!r})"


__all__ = ["JobObservationState", "JobTimingObservation"]
