"""Bounded runtime boundary for scheduled-job change delivery.

The runtime classifies a structural job change, invokes an injected delivery
callable only when the change is meaningful and outside quiet hours, and records
the fingerprint only after an exact positive acknowledgement.  It contains no
notification backend, network, subprocess, timer, logging, or user content.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from core.jobs.contracts import validate_fingerprint
from core.jobs.delivery import (
    DeliveryAttemptResult,
    DeliveryAttemptStatus,
    DeliveryOutcome,
    DeliverySnapshot,
    build_delivery_snapshot,
    classify_delivery,
)
from core.jobs.lifecycle import (
    JobLifecycleController,
    LifecycleOutcomeCode,
    LifecycleResult,
)
from core.jobs.store import ScheduledJobStore


class DeliveryRuntimeCode(StrEnum):
    """Fixed, content-free runtime outcomes."""

    ACKNOWLEDGED = "acknowledged"
    SUPPRESSED = "suppressed"
    UNCHANGED = "unchanged"
    TERMINAL = "terminal"
    NOT_FOUND = "not_found"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class DeliveryRuntimeResult:
    """A bounded outcome that carries no job or identity data."""

    code: DeliveryRuntimeCode

    def __post_init__(self) -> None:
        if not isinstance(self.code, DeliveryRuntimeCode):
            raise ValueError("invalid delivery runtime code")

    def __repr__(self) -> str:
        return f"DeliveryRuntimeResult(code={self.code.value!r})"


class StructuralDelivery(Protocol):
    """Injected delivery callable receiving structural data only."""

    def __call__(self, snapshot: DeliverySnapshot) -> DeliveryAttemptResult: ...


class MeaningfulChangeDeliveryRuntime:
    """Deliver actor-scoped structural changes and acknowledge them exactly once."""

    def __init__(
        self,
        store: ScheduledJobStore,
        lifecycle: JobLifecycleController,
        clock: Callable[[], datetime],
        deliver: StructuralDelivery,
    ) -> None:
        if not isinstance(store, ScheduledJobStore):
            raise TypeError("store must be a ScheduledJobStore")
        if not isinstance(lifecycle, JobLifecycleController):
            raise TypeError("lifecycle must be a JobLifecycleController")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not callable(deliver):
            raise TypeError("deliver must be callable")
        self._store = store
        self._lifecycle = lifecycle
        self._clock = clock
        self._deliver = deliver

    def _now(self) -> datetime | None:
        try:
            value = self._clock()
        except Exception:
            return None
        if not isinstance(value, datetime) or value.tzinfo is None:
            return None
        return value

    def deliver_change(
        self,
        job_id: str,
        *,
        actor_id: str,
        session_id: str,
        candidate_fingerprint: object,
    ) -> DeliveryRuntimeResult:
        """Attempt one bounded delivery for an exact actor/session/job binding."""
        try:
            fingerprint = validate_fingerprint(candidate_fingerprint)
        except Exception:
            return DeliveryRuntimeResult(DeliveryRuntimeCode.FAILED)

        try:
            job = self._store.get(
                job_id,
                actor_id=actor_id,
                session_id=session_id,
            )
        except Exception:
            return DeliveryRuntimeResult(DeliveryRuntimeCode.UNAVAILABLE)
        if job is None:
            return DeliveryRuntimeResult(DeliveryRuntimeCode.NOT_FOUND)

        now = self._now()
        if now is None:
            return DeliveryRuntimeResult(DeliveryRuntimeCode.UNAVAILABLE)
        try:
            outcome = classify_delivery(job, fingerprint, now=now)
        except Exception:
            return DeliveryRuntimeResult(DeliveryRuntimeCode.FAILED)

        if outcome is DeliveryOutcome.SUPPRESSED_QUIET_HOURS:
            return DeliveryRuntimeResult(DeliveryRuntimeCode.SUPPRESSED)
        if outcome is DeliveryOutcome.UNCHANGED:
            return DeliveryRuntimeResult(DeliveryRuntimeCode.UNCHANGED)
        if outcome is DeliveryOutcome.TERMINAL:
            return DeliveryRuntimeResult(DeliveryRuntimeCode.TERMINAL)
        if outcome is not DeliveryOutcome.MEANINGFUL:
            return DeliveryRuntimeResult(DeliveryRuntimeCode.FAILED)

        try:
            snapshot = build_delivery_snapshot(job)
            attempt = self._deliver(snapshot)
        except Exception:
            return DeliveryRuntimeResult(DeliveryRuntimeCode.FAILED)
        if type(attempt) is not DeliveryAttemptResult:
            return DeliveryRuntimeResult(DeliveryRuntimeCode.FAILED)
        if attempt.status is not DeliveryAttemptStatus.ACKNOWLEDGED:
            return DeliveryRuntimeResult(DeliveryRuntimeCode.FAILED)

        try:
            acknowledged = self._lifecycle.acknowledge_delivery(
                job_id,
                actor_id=actor_id,
                session_id=session_id,
                candidate_fingerprint=fingerprint,
                delivery_result=attempt,
            )
        except Exception:
            return DeliveryRuntimeResult(DeliveryRuntimeCode.UNAVAILABLE)
        if type(acknowledged) is not LifecycleResult:
            return DeliveryRuntimeResult(DeliveryRuntimeCode.UNAVAILABLE)
        if acknowledged.code is LifecycleOutcomeCode.OK:
            return DeliveryRuntimeResult(DeliveryRuntimeCode.ACKNOWLEDGED)
        if acknowledged.code is LifecycleOutcomeCode.NOT_FOUND:
            return DeliveryRuntimeResult(DeliveryRuntimeCode.NOT_FOUND)
        if acknowledged.code is LifecycleOutcomeCode.UNAVAILABLE:
            return DeliveryRuntimeResult(DeliveryRuntimeCode.UNAVAILABLE)
        return DeliveryRuntimeResult(DeliveryRuntimeCode.FAILED)
