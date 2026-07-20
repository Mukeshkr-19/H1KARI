"""Bounded Phase 3 scheduled-job runner.

Claims due jobs atomically via the store's CAS ``claim_due`` API, executes
each via an injected callable, and transitions the job through the canonical
state flow. Retry is bounded by ``max_attempts`` with deterministic bounded
backoff. Pause and cancel prevent later execution because the store only
claims ``SCHEDULED`` rows.

No timers, threads, subprocess, network, provider, notification, or external
execution is present. No actor/session/proposal identifiers, payloads, targets,
provider data, or exception text are surfaced through errors or repr.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from zoneinfo import ZoneInfo

from core.jobs.audit import AuditEvent, AuditReasonCode
from core.jobs.audit_store import ScheduledJobAuditStore
from core.jobs.contracts import JobState, ScheduledJob
from core.jobs.quiet_hours import QuietHours, is_quiet
from core.jobs.store import ScheduledJobStore

_DEFAULT_BASE_BACKOFF_SECONDS = 5
_DEFAULT_MAX_BACKOFF_SECONDS = 300
_MAX_CLAIM_LIMIT = 64
_MAX_QUIET_GAP_SCAN_MINUTES = 2 * 24 * 60


class JobRunnerError(ValueError):
    """Fixed, safe error for the runner. No content or identifiers reflected."""


class ExecutionStatus(StrEnum):
    """Fixed structural execution outcome."""

    SUCCESS = "success"
    FAILED = "failed"


@dataclass(frozen=True)
class ExecutionResult:
    """Immutable, content-free result of an injected job execution."""

    status: ExecutionStatus

    def __post_init__(self) -> None:
        if not isinstance(self.status, ExecutionStatus):
            raise JobRunnerError("execution status is invalid")

    def __repr__(self) -> str:
        return f"ExecutionResult(status={self.status.value!r})"


@dataclass(frozen=True)
class JobRunOutcome:
    """Immutable summary of one runner pass for a single job.

    Contains only structural fields. No job/actor/session/proposal identifiers,
    payload, target, provider data, or exception text is present.
    """

    previous_state: JobState
    new_state: JobState
    attempt_count: int
    retried: bool
    suppressed: bool

    def __repr__(self) -> str:
        return (
            f"JobRunOutcome(new_state={self.new_state.value!r}, "
            f"attempt_count={self.attempt_count}, retried={self.retried}, "
            f"suppressed={self.suppressed})"
        )


def _bounded_backoff_seconds(
    attempt_count: int,
    *,
    base_seconds: int,
    max_seconds: int,
) -> int:
    """Return a deterministic bounded backoff in seconds.

    Doubles per attempt starting from ``base_seconds``, capped at
    ``max_seconds``. ``attempt_count`` is the count *before* the retry.
    """
    if attempt_count < 0:
        attempt_count = 0
    shift = min(attempt_count, 30)
    value = base_seconds * (1 << shift)
    if value > max_seconds or value < 0:
        return max_seconds
    return value


def _valid_wall_instants(naive: datetime, tz: ZoneInfo) -> tuple[datetime, ...]:
    """Return the real instants represented by one local wall-clock minute."""
    instants: dict[datetime, datetime] = {}
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=tz, fold=fold)
        utc_candidate = candidate.astimezone(timezone.utc)
        normalized = utc_candidate.astimezone(tz)
        if normalized.replace(tzinfo=None) != naive:
            continue
        instants[utc_candidate] = normalized
    return tuple(instants[key] for key in sorted(instants))


def _next_eligible_after_quiet(now: datetime, quiet_hours: QuietHours) -> datetime:
    """Return the next real quiet-window end instant in the configured zone.

    Ambiguous wall times retain both folds and choose the first future instant.
    Non-existent wall times advance to the first real local minute after the
    gap. The search is bounded to cover even a skipped civil day.
    """
    tz = ZoneInfo(quiet_hours.timezone_name)
    local = now.astimezone(tz)
    now_utc = now.astimezone(timezone.utc)
    end_minute = quiet_hours.end_minute
    base = local.replace(
        hour=end_minute // 60,
        minute=end_minute % 60,
        second=0,
        microsecond=0,
        tzinfo=None,
    )

    for day_offset in range(3):
        wall = base + timedelta(days=day_offset)
        candidates = _valid_wall_instants(wall, tz)
        if not candidates:
            for minute_offset in range(1, _MAX_QUIET_GAP_SCAN_MINUTES + 1):
                candidates = _valid_wall_instants(
                    wall + timedelta(minutes=minute_offset), tz
                )
                if candidates:
                    break
        for candidate in candidates:
            if candidate.astimezone(timezone.utc) > now_utc:
                return candidate
    raise JobRunnerError("quiet-hours schedule failed")


class ScheduledJobRunner:
    """Claim due jobs, execute them, and transition through the lifecycle."""

    def __init__(
        self,
        store: ScheduledJobStore,
        audit_store: ScheduledJobAuditStore,
        clock: Callable[[], datetime],
        execute_callable: Callable[[ScheduledJob], object],
        event_id_factory: Callable[[], str],
        *,
        base_backoff_seconds: int = _DEFAULT_BASE_BACKOFF_SECONDS,
        max_backoff_seconds: int = _DEFAULT_MAX_BACKOFF_SECONDS,
        next_quiet_eligible_factory: Callable[[datetime, QuietHours], datetime]
        | None = None,
    ) -> None:
        if not isinstance(store, ScheduledJobStore):
            raise TypeError("store must be a ScheduledJobStore")
        if not isinstance(audit_store, ScheduledJobAuditStore):
            raise TypeError("audit_store must be a ScheduledJobAuditStore")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not callable(execute_callable):
            raise TypeError("execute_callable must be callable")
        if not callable(event_id_factory):
            raise TypeError("event_id_factory must be callable")
        if (
            next_quiet_eligible_factory is not None
            and not callable(next_quiet_eligible_factory)
        ):
            raise TypeError("next_quiet_eligible_factory must be callable")
        if (
            isinstance(base_backoff_seconds, bool)
            or not isinstance(base_backoff_seconds, int)
            or base_backoff_seconds < 1
            or base_backoff_seconds > max_backoff_seconds
        ):
            raise ValueError("base_backoff_seconds is out of range")
        if (
            isinstance(max_backoff_seconds, bool)
            or not isinstance(max_backoff_seconds, int)
            or max_backoff_seconds < 1
            or max_backoff_seconds > 24 * 3600
        ):
            raise ValueError("max_backoff_seconds is out of range")
        self._store = store
        self._audit_store = audit_store
        self._clock = clock
        self._execute_callable = execute_callable
        self._event_id_factory = event_id_factory
        self._base_backoff_seconds = base_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds
        self._next_quiet_eligible_factory = next_quiet_eligible_factory

    def _now(self) -> datetime:
        try:
            value = self._clock()
        except Exception:
            raise JobRunnerError("clock unavailable") from None
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise JobRunnerError("clock must return a timezone-aware datetime")
        return value

    def _generate_event_id(self) -> str:
        try:
            value = self._event_id_factory()
        except Exception:
            raise JobRunnerError("event id factory failed") from None
        if not isinstance(value, str) or not value:
            raise JobRunnerError("event id factory produced an invalid id")
        return value

    def _append_event(
        self,
        *,
        job: ScheduledJob,
        previous_state: JobState,
        new_state: JobState,
        reason: AuditReasonCode,
        occurred_at: datetime,
    ) -> None:
        try:
            event = AuditEvent(
                event_id=self._generate_event_id(),
                job_id=job.job_id,
                action=job.action,
                previous_state=previous_state,
                new_state=new_state,
                occurred_at=occurred_at,
                reason_code=reason,
            )
            self._audit_store.append(event)
        except Exception:
            raise JobRunnerError("audit append failed") from None

    def _transition(
        self,
        job: ScheduledJob,
        *,
        expected_state: JobState,
        new_state: JobState,
        now: datetime,
    ) -> ScheduledJob | None:
        try:
            return self._store.transition(
                job.job_id,
                expected_state=expected_state,
                new_state=new_state,
                expected_updated_at=job.updated_at,
                updated_at=now,
                actor_id=job.actor_id,
                session_id=job.session_id,
            )
        except Exception:
            raise JobRunnerError("state transition failed") from None

    def _update_next_run(
        self,
        job: ScheduledJob,
        *,
        next_run_at: datetime,
        now: datetime,
    ) -> ScheduledJob | None:
        try:
            return self._store.update_next_run(
                job.job_id,
                next_run_at=next_run_at,
                expected_updated_at=job.updated_at,
                updated_at=now,
                actor_id=job.actor_id,
                session_id=job.session_id,
            )
        except Exception:
            raise JobRunnerError("next-run update failed") from None

    def _bump_attempt(
        self,
        job: ScheduledJob,
        *,
        now: datetime,
    ) -> ScheduledJob | None:
        try:
            return self._store.update_attempt(
                job.job_id,
                expected_updated_at=job.updated_at,
                updated_at=now,
                actor_id=job.actor_id,
                session_id=job.session_id,
            )
        except Exception:
            raise JobRunnerError("attempt update failed") from None

    def _compensate_claimed(self, job: ScheduledJob, *, now: datetime) -> None:
        """Return an exact claimed RUNNING revision to SCHEDULED via CAS.

        Concurrent or subsequently modified jobs are left untouched.
        """
        try:
            interrupted = self._store.transition(
                job.job_id,
                expected_state=JobState.RUNNING,
                new_state=JobState.INTERRUPTED,
                expected_updated_at=job.updated_at,
                updated_at=now,
                actor_id=job.actor_id,
                session_id=job.session_id,
            )
        except Exception:
            return
        if interrupted is None:
            return
        try:
            self._store.transition(
                interrupted.job_id,
                expected_state=JobState.INTERRUPTED,
                new_state=JobState.SCHEDULED,
                expected_updated_at=interrupted.updated_at,
                updated_at=now,
                actor_id=interrupted.actor_id,
                session_id=interrupted.session_id,
            )
        except Exception:
            return

    def _resolve_execution(self, job: ScheduledJob) -> bool:
        """Return True only for an explicit successful ``ExecutionResult``."""
        try:
            result = self._execute_callable(job)
        except Exception:
            return False
        if isinstance(result, ExecutionResult) and result.status is ExecutionStatus.SUCCESS:
            return True
        return False

    def _refresh_running(self, job: ScheduledJob) -> ScheduledJob | None:
        """Reload the exact claimed job after an execution-side CAS update.

        Delivery acknowledgement may update the fingerprint and revision while
        preserving RUNNING. Terminalization must use that current revision.
        """
        try:
            current = self._store.get(
                job.job_id,
                actor_id=job.actor_id,
                session_id=job.session_id,
            )
        except Exception:
            return None
        if current is None or current.state is not JobState.RUNNING:
            return None
        return current

    def _quiet_reschedule(
        self,
        job: ScheduledJob,
        *,
        now: datetime,
    ) -> JobRunOutcome:
        """Suppress execution during quiet hours and restore a schedulable job."""
        interrupted = self._transition(
            job,
            expected_state=JobState.RUNNING,
            new_state=JobState.INTERRUPTED,
            now=now,
        )
        if interrupted is None:
            return JobRunOutcome(
                previous_state=JobState.RUNNING,
                new_state=JobState.RUNNING,
                attempt_count=job.attempt_count,
                retried=False,
                suppressed=True,
            )
        self._append_event(
            job=interrupted,
            previous_state=JobState.RUNNING,
            new_state=JobState.INTERRUPTED,
            reason=AuditReasonCode.QUIET_SUPPRESSED,
            occurred_at=now,
        )

        quiet_hours = job.quiet_hours
        assert quiet_hours is not None
        try:
            if self._next_quiet_eligible_factory is not None:
                next_run_at = self._next_quiet_eligible_factory(now, quiet_hours)
            else:
                next_run_at = _next_eligible_after_quiet(now, quiet_hours)
        except Exception:
            raise JobRunnerError("quiet-hours schedule failed") from None
        if not isinstance(next_run_at, datetime) or next_run_at.tzinfo is None:
            raise JobRunnerError("quiet-hours schedule failed")

        bumped = self._update_next_run(
            interrupted,
            next_run_at=next_run_at,
            now=now,
        )
        if bumped is None:
            return JobRunOutcome(
                previous_state=JobState.RUNNING,
                new_state=JobState.INTERRUPTED,
                attempt_count=job.attempt_count,
                retried=False,
                suppressed=True,
            )
        rescheduled = self._transition(
            bumped,
            expected_state=JobState.INTERRUPTED,
            new_state=JobState.SCHEDULED,
            now=now,
        )
        if rescheduled is None:
            return JobRunOutcome(
                previous_state=JobState.RUNNING,
                new_state=JobState.INTERRUPTED,
                attempt_count=job.attempt_count,
                retried=False,
                suppressed=True,
            )
        self._append_event(
            job=rescheduled,
            previous_state=JobState.INTERRUPTED,
            new_state=JobState.SCHEDULED,
            reason=AuditReasonCode.STATE_TRANSITION,
            occurred_at=now,
        )
        return JobRunOutcome(
            previous_state=JobState.RUNNING,
            new_state=JobState.SCHEDULED,
            attempt_count=job.attempt_count,
            retried=False,
            suppressed=True,
        )

    def _process_claimed(
        self,
        job: ScheduledJob,
        *,
        now: datetime,
    ) -> JobRunOutcome:
        """Execute one claimed job and drive its lifecycle transitions."""
        if job.quiet_hours is not None and is_quiet(now, job.quiet_hours):
            return self._quiet_reschedule(job, now=now)

        succeeded = self._resolve_execution(job)

        if succeeded:
            current = self._refresh_running(job)
            if current is None:
                return JobRunOutcome(
                    previous_state=JobState.RUNNING,
                    new_state=JobState.RUNNING,
                    attempt_count=job.attempt_count,
                    retried=False,
                    suppressed=False,
                )
            terminal_now = self._now()
            if terminal_now < current.updated_at:
                terminal_now = current.updated_at
            completed = self._transition(
                current,
                expected_state=JobState.RUNNING,
                new_state=JobState.COMPLETED,
                now=terminal_now,
            )
            if completed is not None:
                self._append_event(
                    job=completed,
                    previous_state=JobState.RUNNING,
                    new_state=JobState.COMPLETED,
                    reason=AuditReasonCode.STATE_TRANSITION,
                    occurred_at=terminal_now,
                )
            return JobRunOutcome(
                previous_state=JobState.RUNNING,
                new_state=JobState.COMPLETED,
                attempt_count=current.attempt_count,
                retried=False,
                suppressed=False,
            )

        # Failure path: retry via INTERRUPTED -> SCHEDULED if budget remains,
        # otherwise terminal FAILED. ``max_attempts`` is the total number of
        # attempts allowed; the current attempt is ``attempt_count`` (0-indexed),
        # so the budget is exhausted when ``attempt_count >= max_attempts - 1``.
        if job.attempt_count < job.max_attempts - 1:
            interrupted = self._transition(
                job,
                expected_state=JobState.RUNNING,
                new_state=JobState.INTERRUPTED,
                now=now,
            )
            if interrupted is None:
                return JobRunOutcome(
                    previous_state=JobState.RUNNING,
                    new_state=JobState.RUNNING,
                    attempt_count=job.attempt_count,
                    retried=False,
                    suppressed=False,
                )
            backoff = _bounded_backoff_seconds(
                job.attempt_count,
                base_seconds=self._base_backoff_seconds,
                max_seconds=self._max_backoff_seconds,
            )
            next_run_at = now + timedelta(seconds=backoff)
            bumped_next = self._update_next_run(
                interrupted,
                next_run_at=next_run_at,
                now=now,
            )
            if bumped_next is None:
                return JobRunOutcome(
                    previous_state=JobState.RUNNING,
                    new_state=JobState.INTERRUPTED,
                    attempt_count=job.attempt_count,
                    retried=False,
                    suppressed=False,
                )
            bumped_attempt = self._bump_attempt(bumped_next, now=now)
            if bumped_attempt is None:
                return JobRunOutcome(
                    previous_state=JobState.RUNNING,
                    new_state=JobState.INTERRUPTED,
                    attempt_count=job.attempt_count,
                    retried=False,
                    suppressed=False,
                )
            rescheduled = self._transition(
                bumped_attempt,
                expected_state=JobState.INTERRUPTED,
                new_state=JobState.SCHEDULED,
                now=now,
            )
            if rescheduled is not None:
                self._append_event(
                    job=rescheduled,
                    previous_state=JobState.INTERRUPTED,
                    new_state=JobState.SCHEDULED,
                    reason=AuditReasonCode.STATE_TRANSITION,
                    occurred_at=now,
                )
            return JobRunOutcome(
                previous_state=JobState.RUNNING,
                new_state=JobState.SCHEDULED,
                attempt_count=bumped_attempt.attempt_count,
                retried=True,
                suppressed=False,
            )

        # Retry budget exhausted: terminal FAILED.
        failed = self._transition(
            job,
            expected_state=JobState.RUNNING,
            new_state=JobState.FAILED,
            now=now,
        )
        if failed is not None:
            self._append_event(
                job=failed,
                previous_state=JobState.RUNNING,
                new_state=JobState.FAILED,
                reason=AuditReasonCode.RETRY_EXHAUSTED,
                occurred_at=now,
            )
        return JobRunOutcome(
            previous_state=JobState.RUNNING,
            new_state=JobState.FAILED,
            attempt_count=job.attempt_count,
            retried=False,
            suppressed=False,
        )

    def run_once(self, *, limit: int = 1) -> tuple[JobRunOutcome, ...]:
        """Claim up to ``limit`` due jobs and process each.

        Returns one ``JobRunOutcome`` per claimed job, in claim order. Raises
        ``JobRunnerError`` on clock, factory, persistence, or audit failure.
        """
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise JobRunnerError("limit must be an integer")
        if limit < 1 or limit > _MAX_CLAIM_LIMIT:
            raise JobRunnerError(f"limit must be in 1..{_MAX_CLAIM_LIMIT}")

        now = self._now()
        try:
            claimed = self._store.claim_due(now=now, updated_at=now, limit=limit)
        except Exception:
            raise JobRunnerError("claim failed") from None

        outcomes: list[JobRunOutcome] = []
        for index, job in enumerate(claimed):
            try:
                self._append_event(
                    job=job,
                    previous_state=JobState.SCHEDULED,
                    new_state=JobState.RUNNING,
                    reason=AuditReasonCode.STATE_TRANSITION,
                    occurred_at=now,
                )
            except JobRunnerError:
                self._compensate_claimed(job, now=now)
                for remaining in claimed[index + 1 :]:
                    self._compensate_claimed(remaining, now=now)
                raise
            outcomes.append(self._process_claimed(job, now=now))
        return tuple(outcomes)

    def recover_startup(self, *, limit: int = 64) -> int:
        """Recover crash-left RUNNING/INTERRUPTED reads to a schedulable state.

        Every mutation is audited. If the final audit append fails, the job is
        moved to PAUSED so no unaudited active row can execute.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 64:
            raise JobRunnerError("limit must be in 1..64")
        now = self._now()
        try:
            running = self._store.list_state(JobState.RUNNING, limit=limit)
        except Exception:
            raise JobRunnerError("startup recovery failed") from None
        interrupted_jobs: list[ScheduledJob] = []
        for job in running:
            interrupted = self._transition(
                job,
                expected_state=JobState.RUNNING,
                new_state=JobState.INTERRUPTED,
                now=now,
            )
            if interrupted is None:
                continue
            self._append_event(
                job=interrupted,
                previous_state=JobState.RUNNING,
                new_state=JobState.INTERRUPTED,
                reason=AuditReasonCode.STATE_TRANSITION,
                occurred_at=now,
            )
            interrupted_jobs.append(interrupted)
        try:
            existing = self._store.list_state(
                JobState.INTERRUPTED,
                limit=max(1, limit - len(interrupted_jobs)),
            )
        except Exception:
            raise JobRunnerError("startup recovery failed") from None
        by_key = {
            (job.job_id, job.actor_id, job.session_id): job
            for job in (*interrupted_jobs, *existing)
        }
        recovered = 0
        for job in tuple(by_key.values())[:limit]:
            scheduled = self._transition(
                job,
                expected_state=JobState.INTERRUPTED,
                new_state=JobState.SCHEDULED,
                now=now,
            )
            if scheduled is None:
                continue
            try:
                self._append_event(
                    job=scheduled,
                    previous_state=JobState.INTERRUPTED,
                    new_state=JobState.SCHEDULED,
                    reason=AuditReasonCode.STATE_TRANSITION,
                    occurred_at=now,
                )
            except JobRunnerError:
                try:
                    self._store.transition(
                        scheduled.job_id,
                        expected_state=JobState.SCHEDULED,
                        new_state=JobState.PAUSED,
                        expected_updated_at=scheduled.updated_at,
                        updated_at=now,
                        actor_id=scheduled.actor_id,
                        session_id=scheduled.session_id,
                    )
                except Exception:
                    pass
                raise
            recovered += 1
        return recovered
