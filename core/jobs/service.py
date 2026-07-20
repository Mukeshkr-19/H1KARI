"""Bounded Phase 3 scheduled-jobs application controller.

This module provides an actor/session-scoped controller over the existing
``ScheduledJobStore``. It performs no execution, timer, thread, network,
subprocess, provider, filesystem-content, or hidden-clock work.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Callable, Optional

from core.action_policy import Actor, ActorContext
from core.jobs.contracts import JobState, ScheduledJob
from core.jobs.lifecycle import (
    JobLifecycleController,
    LifecycleOutcomeCode,
)
from core.jobs.store import ScheduledJobStore


class JobServiceCode(StrEnum):
    """Stable bounded result codes for the scheduled-jobs controller."""

    OK = "ok"
    CONTROL_FAILED = "control_failed"
    JOB_NOT_FOUND = "job_not_found"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ScheduledJobView:
    """Sanitized frontend representation of a scheduled job.

    Contains only the bounded fields needed by the frontend. No actor_id,
    session_id, proposal_id, payload content, or provider details are exposed.
    """

    job_id: str
    action: str
    state: str
    next_run_at: str
    quiet_hours_summary: Optional[str]
    attempt_count: int
    max_attempts: int

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,79}", self.job_id):
            raise ValueError("invalid job view id")
        if (
            not isinstance(self.action, str)
            or not self.action
            or len(self.action) > 128
            or any(ord(char) < 32 or ord(char) == 127 for char in self.action)
        ):
            raise ValueError("invalid job view action")
        if self.state not in {state.value for state in JobState}:
            raise ValueError("invalid job view state")
        if (
            not isinstance(self.next_run_at, str)
            or not self.next_run_at
            or len(self.next_run_at) > 64
        ):
            raise ValueError("invalid job view next run")
        if self.quiet_hours_summary is not None and (
            not isinstance(self.quiet_hours_summary, str)
            or len(self.quiet_hours_summary) > 160
            or any(
                ord(char) < 32 or ord(char) == 127
                for char in self.quiet_hours_summary
            )
        ):
            raise ValueError("invalid job view quiet hours")
        if (
            isinstance(self.attempt_count, bool)
            or not isinstance(self.attempt_count, int)
            or isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or self.attempt_count < 0
            or self.max_attempts < 1
            or self.attempt_count > self.max_attempts
            or self.max_attempts > 100
        ):
            raise ValueError("invalid job view attempts")


@dataclass(frozen=True)
class JobControlResult:
    """Safe return boundary for scheduled-job control operations.

    On success, ``job`` holds a single sanitized view and ``jobs`` holds a list
    of sanitized views. On failure, ``error`` holds one of the bounded codes.
    """

    job: Optional[ScheduledJobView] = None
    jobs: Optional[tuple[ScheduledJobView, ...]] = None
    error: Optional[JobServiceCode] = None

    def __post_init__(self) -> None:
        populated = sum(
            value is not None for value in (self.job, self.jobs, self.error)
        )
        if populated != 1 or self.error is JobServiceCode.OK:
            raise ValueError("result must contain exactly one outcome")
        if self.jobs is not None and not isinstance(self.jobs, tuple):
            raise ValueError("jobs must be an immutable tuple")


class ScheduledJobService:
    """Bounded actor/session-scoped controller over ``ScheduledJobStore``.

    The controller supports listing, pausing, resuming, and cancelling jobs.
    It does not execute scheduled work and never exposes internal identifiers
    or exception text.
    """

    _MAX_LIST_LIMIT = 64
    _DEFAULT_LIST_LIMIT = 64

    def __init__(
        self,
        store: ScheduledJobStore,
        clock: Callable[[], datetime],
        *,
        lifecycle: JobLifecycleController | None = None,
    ) -> None:
        if not isinstance(store, ScheduledJobStore):
            raise TypeError("store must be a ScheduledJobStore")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if lifecycle is not None and not isinstance(
            lifecycle, JobLifecycleController
        ):
            raise TypeError("lifecycle must be a JobLifecycleController")
        self._store = store
        self._clock = clock
        self._lifecycle = lifecycle

    def _now(self) -> Optional[datetime]:
        """Return a timezone-aware datetime from the injected clock, or None."""
        try:
            value = self._clock()
        except Exception:
            return None
        if isinstance(value, bool) or not isinstance(value, datetime):
            return None
        if value.tzinfo is None:
            return None
        try:
            numeric = value.timestamp()
        except Exception:
            return None
        if not math.isfinite(numeric):
            return None
        return value

    @staticmethod
    def _require_owner(actor: ActorContext) -> Optional[JobControlResult]:
        if not isinstance(actor, ActorContext):
            return JobControlResult(error=JobServiceCode.CONTROL_FAILED)
        if actor.actor is not Actor.OWNER:
            return JobControlResult(error=JobServiceCode.CONTROL_FAILED)
        return None

    @staticmethod
    def _quiet_hours_summary(job: ScheduledJob) -> Optional[str]:
        if job.quiet_hours is None:
            return None
        qh = job.quiet_hours
        return f"{qh.start_minute}-{qh.end_minute} ({qh.timezone_name})"

    @classmethod
    def _view(cls, job: ScheduledJob) -> ScheduledJobView:
        return ScheduledJobView(
            job_id=job.job_id,
            action=job.action,
            state=job.state.value,
            next_run_at=job.next_run_at.isoformat(),
            quiet_hours_summary=cls._quiet_hours_summary(job),
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
        )

    def list_jobs(
        self,
        actor: ActorContext,
        limit: int = _DEFAULT_LIST_LIMIT,
    ) -> JobControlResult:
        """Return at most ``limit`` jobs for the exact actor/session scope.

        ``limit`` is clamped to ``1..64``. The result payload is a list of
        sanitized ``ScheduledJobView`` objects.
        """
        owner_error = self._require_owner(actor)
        if owner_error is not None:
            return owner_error

        if isinstance(limit, bool) or not isinstance(limit, int):
            limit = self._DEFAULT_LIST_LIMIT
        if limit < 1:
            limit = 1
        if limit > self._MAX_LIST_LIMIT:
            limit = self._MAX_LIST_LIMIT

        try:
            jobs = self._store.list(
                actor_id=actor.actor_id,
                session_id=actor.session_id,
                limit=limit,
            )
            views = tuple(self._view(job) for job in jobs)
        except Exception:
            return JobControlResult(error=JobServiceCode.UNAVAILABLE)
        return JobControlResult(jobs=views)

    def _load_for_control(
        self, actor: ActorContext, job_id: str
    ) -> tuple[Optional[ScheduledJob], Optional[JobServiceCode]]:
        try:
            job = self._store.get(
                job_id, actor_id=actor.actor_id, session_id=actor.session_id
            )
        except Exception:
            return None, JobServiceCode.UNAVAILABLE
        if job is None:
            return None, JobServiceCode.JOB_NOT_FOUND
        return job, None

    def _update_with_cas(
        self,
        actor: ActorContext,
        job_id: str,
        current: ScheduledJob,
        new_state: JobState,
    ) -> JobControlResult:
        if self._lifecycle is not None:
            try:
                if new_state is JobState.PAUSED:
                    outcome = self._lifecycle.pause(
                        job_id,
                        actor_id=actor.actor_id,
                        session_id=actor.session_id,
                    )
                elif new_state is JobState.SCHEDULED:
                    outcome = self._lifecycle.resume(
                        job_id,
                        actor_id=actor.actor_id,
                        session_id=actor.session_id,
                    )
                elif new_state is JobState.CANCELLED:
                    outcome = self._lifecycle.cancel(
                        job_id,
                        actor_id=actor.actor_id,
                        session_id=actor.session_id,
                    )
                else:
                    return JobControlResult(error=JobServiceCode.CONTROL_FAILED)
            except Exception:
                return JobControlResult(error=JobServiceCode.UNAVAILABLE)
            if outcome.code is LifecycleOutcomeCode.NOT_FOUND:
                return JobControlResult(error=JobServiceCode.JOB_NOT_FOUND)
            if outcome.code is LifecycleOutcomeCode.UNAVAILABLE:
                return JobControlResult(error=JobServiceCode.UNAVAILABLE)
            if outcome.code is not LifecycleOutcomeCode.OK:
                return JobControlResult(error=JobServiceCode.CONTROL_FAILED)
            try:
                updated = self._store.get(
                    job_id,
                    actor_id=actor.actor_id,
                    session_id=actor.session_id,
                )
                if updated is None:
                    return JobControlResult(error=JobServiceCode.UNAVAILABLE)
                return JobControlResult(job=self._view(updated))
            except Exception:
                return JobControlResult(error=JobServiceCode.UNAVAILABLE)

        now = self._now()
        if now is None:
            return JobControlResult(error=JobServiceCode.UNAVAILABLE)

        try:
            if new_state is JobState.PAUSED:
                updated = self._store.pause(
                    job_id,
                    expected_updated_at=current.updated_at,
                    updated_at=now,
                    actor_id=actor.actor_id,
                    session_id=actor.session_id,
                )
            elif new_state is JobState.SCHEDULED:
                if current.state is JobState.PAUSED:
                    updated = self._store.resume(
                        job_id,
                        expected_updated_at=current.updated_at,
                        updated_at=now,
                        actor_id=actor.actor_id,
                        session_id=actor.session_id,
                    )
                elif current.state is JobState.INTERRUPTED:
                    updated = self._store.transition(
                        job_id,
                        expected_state=JobState.INTERRUPTED,
                        new_state=JobState.SCHEDULED,
                        expected_updated_at=current.updated_at,
                        updated_at=now,
                        actor_id=actor.actor_id,
                        session_id=actor.session_id,
                    )
                else:
                    return JobControlResult(error=JobServiceCode.CONTROL_FAILED)
            elif new_state is JobState.CANCELLED:
                updated = self._store.cancel(
                    job_id,
                    expected_updated_at=current.updated_at,
                    updated_at=now,
                    actor_id=actor.actor_id,
                    session_id=actor.session_id,
                )
            else:
                return JobControlResult(error=JobServiceCode.CONTROL_FAILED)
        except Exception:
            return JobControlResult(error=JobServiceCode.UNAVAILABLE)

        if updated is None:
            return JobControlResult(error=JobServiceCode.CONTROL_FAILED)
        try:
            return JobControlResult(job=self._view(updated))
        except Exception:
            return JobControlResult(error=JobServiceCode.UNAVAILABLE)

    def pause(self, actor: ActorContext, job_id: str) -> JobControlResult:
        """Transition a job from ``scheduled`` to ``paused``.

        Idempotent when already paused. Fails closed for missing, cross-scope,
        or stale jobs.
        """
        owner_error = self._require_owner(actor)
        if owner_error is not None:
            return owner_error

        current, load_error = self._load_for_control(actor, job_id)
        if load_error is not None:
            return JobControlResult(error=load_error)
        assert current is not None
        if current.state is JobState.PAUSED:
            return JobControlResult(job=self._view(current))
        if current.state is not JobState.SCHEDULED:
            return JobControlResult(error=JobServiceCode.CONTROL_FAILED)

        return self._update_with_cas(actor, job_id, current, JobState.PAUSED)

    def resume(self, actor: ActorContext, job_id: str) -> JobControlResult:
        """Transition a job from ``paused`` or ``interrupted`` to ``scheduled``.

        Idempotent when already scheduled. Fails closed for missing,
        cross-scope, or stale jobs.
        """
        owner_error = self._require_owner(actor)
        if owner_error is not None:
            return owner_error

        current, load_error = self._load_for_control(actor, job_id)
        if load_error is not None:
            return JobControlResult(error=load_error)
        assert current is not None
        if current.state is JobState.SCHEDULED:
            return JobControlResult(job=self._view(current))
        if current.state not in (JobState.PAUSED, JobState.INTERRUPTED):
            return JobControlResult(error=JobServiceCode.CONTROL_FAILED)

        return self._update_with_cas(actor, job_id, current, JobState.SCHEDULED)

    def cancel(self, actor: ActorContext, job_id: str) -> JobControlResult:
        """Transition a job from any active state to ``cancelled``.

        Idempotent when already cancelled. Fails closed for missing or
        cross-scope jobs.
        """
        owner_error = self._require_owner(actor)
        if owner_error is not None:
            return owner_error

        current, load_error = self._load_for_control(actor, job_id)
        if load_error is not None:
            return JobControlResult(error=load_error)
        assert current is not None
        if current.state is JobState.CANCELLED:
            return JobControlResult(job=self._view(current))
        if current.state in (JobState.COMPLETED, JobState.FAILED):
            return JobControlResult(error=JobServiceCode.CONTROL_FAILED)

        return self._update_with_cas(actor, job_id, current, JobState.CANCELLED)
