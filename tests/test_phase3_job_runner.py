"""Deterministic tests for the bounded Phase 3 scheduled-job runner.

These tests cover only ``core.jobs.runner``. They use temporary databases and
injected clocks/execution callables; no timers, threads, subprocess, network,
provider, notification, or external execution is exercised.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.jobs.audit import AuditReasonCode
from core.jobs.audit_store import ScheduledJobAuditStore
from core.jobs.contracts import JobState, ScheduledJob
from core.jobs.quiet_hours import QuietHours
from core.jobs.runner import (
    ExecutionResult,
    ExecutionStatus,
    JobRunOutcome,
    JobRunnerError,
    ScheduledJobRunner,
    _bounded_backoff_seconds,
    _next_eligible_after_quiet,
)
from core.jobs.store import ScheduledJobStore

UTC = timezone.utc


def _aware(year, month, day, hour, minute, tz=UTC):
    return datetime(year, month, day, hour, minute, tzinfo=tz)


def _success(_job: ScheduledJob) -> ExecutionResult:
    return ExecutionResult(ExecutionStatus.SUCCESS)


def _failed(_job: ScheduledJob) -> ExecutionResult:
    return ExecutionResult(ExecutionStatus.FAILED)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "jobs.db"


@pytest.fixture
def audit_db_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.db"


@pytest.fixture
def store(db_path: Path) -> ScheduledJobStore:
    return ScheduledJobStore(db_path)


@pytest.fixture
def audit_store(audit_db_path: Path) -> ScheduledJobAuditStore:
    return ScheduledJobAuditStore(audit_db_path)


@pytest.fixture
def event_id_factory():
    counter = {"n": 0}

    def _factory() -> str:
        counter["n"] += 1
        return f"evt-{counter['n']}"

    return _factory


def _make_job(
    *,
    job_id: str = "job-1",
    actor_id: str = "actor-1",
    session_id: str = "session-1",
    action: str = "digest",
    proposal_id: str = "prop-1",
    state: JobState = JobState.SCHEDULED,
    next_run_at: datetime | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    attempt_count: int = 0,
    max_attempts: int = 1,
    quiet_hours: QuietHours | None = None,
) -> ScheduledJob:
    return ScheduledJob(
        job_id=job_id,
        actor_id=actor_id,
        session_id=session_id,
        action=action,
        proposal_id=proposal_id,
        state=state,
        next_run_at=next_run_at or _aware(2026, 7, 18, 9, 0),
        created_at=created_at or _aware(2026, 7, 18, 8, 0),
        updated_at=updated_at or _aware(2026, 7, 18, 8, 0),
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        quiet_hours=quiet_hours,
    )


def test_runner_requires_valid_dependencies(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    event_id_factory,
) -> None:
    def _clock() -> datetime:
        return _aware(2026, 7, 18, 8, 0)

    with pytest.raises(TypeError):
        ScheduledJobRunner("not-a-store", audit_store, _clock, _success, event_id_factory)
    with pytest.raises(TypeError):
        ScheduledJobRunner(store, "not-an-audit", _clock, _success, event_id_factory)
    with pytest.raises(TypeError):
        ScheduledJobRunner(store, audit_store, "not-callable", _success, event_id_factory)
    with pytest.raises(TypeError):
        ScheduledJobRunner(store, audit_store, _clock, "not-callable", event_id_factory)
    with pytest.raises(TypeError):
        ScheduledJobRunner(store, audit_store, _clock, _success, "not-callable")


def test_runner_rejects_invalid_backoff_bounds(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    event_id_factory,
) -> None:
    def _clock() -> datetime:
        return _aware(2026, 7, 18, 8, 0)

    with pytest.raises(ValueError):
        ScheduledJobRunner(
            store, audit_store, _clock, _success, event_id_factory,
            base_backoff_seconds=0,
        )
    with pytest.raises(ValueError):
        ScheduledJobRunner(
            store, audit_store, _clock, _success, event_id_factory,
            max_backoff_seconds=0,
        )


def test_bounded_backoff_doubles_and_caps() -> None:
    assert _bounded_backoff_seconds(0, base_seconds=5, max_seconds=300) == 5
    assert _bounded_backoff_seconds(1, base_seconds=5, max_seconds=300) == 10
    assert _bounded_backoff_seconds(2, base_seconds=5, max_seconds=300) == 20
    assert _bounded_backoff_seconds(3, base_seconds=5, max_seconds=300) == 40
    assert _bounded_backoff_seconds(10, base_seconds=5, max_seconds=300) == 300
    assert _bounded_backoff_seconds(100, base_seconds=5, max_seconds=300) == 300


def test_run_once_claims_due_job_and_completes(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    event_id_factory,
) -> None:
    now = _aware(2026, 7, 18, 9, 0)
    store.add(
        _make_job(
            next_run_at=_aware(2026, 7, 18, 8, 30),
            created_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 8, 0),
        )
    )

    executed: list[ScheduledJob] = []

    def _exec(job: ScheduledJob) -> ExecutionResult:
        executed.append(job)
        return ExecutionResult(ExecutionStatus.SUCCESS)

    runner = ScheduledJobRunner(
        store, audit_store, lambda: now, _exec, event_id_factory
    )
    outcomes = runner.run_once(limit=1)
    assert len(outcomes) == 1
    assert outcomes[0].new_state is JobState.COMPLETED
    assert outcomes[0].suppressed is False
    assert outcomes[0].retried is False
    assert len(executed) == 1
    assert executed[0].job_id == "job-1"

    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.state is JobState.COMPLETED


def test_success_terminalizes_current_revision_after_delivery_fingerprint_cas(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    event_id_factory,
) -> None:
    claimed_at = _aware(2026, 7, 18, 9, 0)
    delivered_at = claimed_at + timedelta(seconds=30)
    completed_at = claimed_at + timedelta(minutes=1)
    store.add(_make_job(next_run_at=_aware(2026, 7, 18, 8, 30)))
    clock_values = iter((claimed_at, completed_at))

    def _exec(job: ScheduledJob) -> ExecutionResult:
        updated = store.update_delivery_fingerprint(
            job.job_id,
            fingerprint="sha256.delivery",
            expected_updated_at=job.updated_at,
            updated_at=delivered_at,
            actor_id=job.actor_id,
            session_id=job.session_id,
        )
        assert updated is not None
        return ExecutionResult(ExecutionStatus.SUCCESS)

    runner = ScheduledJobRunner(
        store,
        audit_store,
        lambda: next(clock_values),
        _exec,
        event_id_factory,
    )

    outcome = runner.run_once(limit=1)[0]
    persisted = store.get("job-1", actor_id="actor-1", session_id="session-1")

    assert outcome.new_state is JobState.COMPLETED
    assert persisted is not None
    assert persisted.state is JobState.COMPLETED
    assert persisted.last_delivery_fingerprint == "sha256.delivery"
    assert persisted.updated_at == completed_at


def test_run_once_appends_audit_events(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    event_id_factory,
) -> None:
    now = _aware(2026, 7, 18, 9, 0)
    store.add(
        _make_job(
            next_run_at=_aware(2026, 7, 18, 8, 30),
            created_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 8, 0),
        )
    )

    runner = ScheduledJobRunner(
        store,
        audit_store,
        lambda: now,
        _success,
        event_id_factory,
    )
    runner.run_once(limit=1)
    events = audit_store.read("job-1")
    assert len(events) == 2
    assert events[0].previous_state is JobState.SCHEDULED
    assert events[0].new_state is JobState.RUNNING
    assert events[1].previous_state is JobState.RUNNING
    assert events[1].new_state is JobState.COMPLETED


def test_run_once_skips_future_jobs(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    event_id_factory,
) -> None:
    now = _aware(2026, 7, 18, 9, 0)
    store.add(
        _make_job(
            next_run_at=_aware(2026, 7, 18, 10, 0),
            created_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 8, 0),
        )
    )

    runner = ScheduledJobRunner(
        store,
        audit_store,
        lambda: now,
        _success,
        event_id_factory,
    )
    outcomes = runner.run_once(limit=1)
    assert outcomes == ()


def test_run_once_adapter_exception_marks_failed(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    event_id_factory,
) -> None:
    now = _aware(2026, 7, 18, 9, 0)
    store.add(
        _make_job(
            next_run_at=_aware(2026, 7, 18, 8, 30),
            created_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 8, 0),
            max_attempts=1,
        )
    )

    def _exec(job: ScheduledJob) -> ExecutionResult:
        raise RuntimeError("boom")

    runner = ScheduledJobRunner(
        store, audit_store, lambda: now, _exec, event_id_factory
    )
    outcomes = runner.run_once(limit=1)
    assert len(outcomes) == 1
    assert outcomes[0].new_state is JobState.FAILED
    assert outcomes[0].retried is False

    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.state is JobState.FAILED

    events = audit_store.read("job-1")
    assert any(
        e.new_state is JobState.FAILED
        and e.reason_code is AuditReasonCode.RETRY_EXHAUSTED
        for e in events
    )


def test_run_once_unexpected_execution_return_fails_closed(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    event_id_factory,
) -> None:
    now = _aware(2026, 7, 18, 9, 0)
    store.add(
        _make_job(
            next_run_at=_aware(2026, 7, 18, 8, 30),
            created_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 8, 0),
            max_attempts=1,
        )
    )

    runner = ScheduledJobRunner(
        store, audit_store, lambda: now, lambda job: None, event_id_factory
    )
    outcomes = runner.run_once(limit=1)
    assert outcomes[0].new_state is JobState.FAILED
    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.state is JobState.FAILED


def test_run_once_explicit_failed_execution_result(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    event_id_factory,
) -> None:
    now = _aware(2026, 7, 18, 9, 0)
    store.add(
        _make_job(
            next_run_at=_aware(2026, 7, 18, 8, 30),
            created_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 8, 0),
            max_attempts=1,
        )
    )
    runner = ScheduledJobRunner(
        store, audit_store, lambda: now, _failed, event_id_factory
    )
    outcomes = runner.run_once(limit=1)
    assert outcomes[0].new_state is JobState.FAILED


def test_run_once_retries_within_max_attempts(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    event_id_factory,
) -> None:
    now = _aware(2026, 7, 18, 9, 0)
    store.add(
        _make_job(
            next_run_at=_aware(2026, 7, 18, 8, 30),
            created_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 8, 0),
            max_attempts=3,
        )
    )

    def _exec(job: ScheduledJob) -> ExecutionResult:
        raise RuntimeError("boom")

    runner = ScheduledJobRunner(
        store, audit_store, lambda: now, _exec, event_id_factory
    )
    outcomes = runner.run_once(limit=1)
    assert len(outcomes) == 1
    assert outcomes[0].new_state is JobState.SCHEDULED
    assert outcomes[0].retried is True
    assert outcomes[0].attempt_count == 1

    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.state is JobState.SCHEDULED
    assert fetched.attempt_count == 1
    assert fetched.next_run_at > now

    transitions = [
        (event.previous_state, event.new_state)
        for event in audit_store.read("job-1")
    ]
    assert transitions == [
        (JobState.SCHEDULED, JobState.RUNNING),
        (JobState.RUNNING, JobState.INTERRUPTED),
        (JobState.INTERRUPTED, JobState.SCHEDULED),
    ]


def test_retry_interrupted_audit_failure_leaves_job_inactive(
    store: ScheduledJobStore,
    audit_db_path: Path,
    event_id_factory,
) -> None:
    now = _aware(2026, 7, 18, 9, 0)
    store.add(
        _make_job(
            next_run_at=_aware(2026, 7, 18, 8, 30),
            created_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 8, 0),
            max_attempts=3,
        )
    )

    class _FailSecondAudit(ScheduledJobAuditStore):
        calls = 0

        def append(self, event):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("audit boom")
            return super().append(event)

    audit_store = _FailSecondAudit(audit_db_path)
    runner = ScheduledJobRunner(
        store, audit_store, lambda: now, _failed, event_id_factory
    )

    with pytest.raises(JobRunnerError, match="audit append failed"):
        runner.run_once(limit=1)

    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.state is JobState.INTERRUPTED
    assert [event.new_state for event in audit_store.read("job-1")] == [
        JobState.RUNNING
    ]


def test_retry_reschedule_audit_failure_pauses_exact_active_revision(
    store: ScheduledJobStore,
    audit_db_path: Path,
    event_id_factory,
) -> None:
    now = _aware(2026, 7, 18, 9, 0)
    store.add(
        _make_job(
            next_run_at=_aware(2026, 7, 18, 8, 30),
            created_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 8, 0),
            max_attempts=3,
        )
    )

    class _FailThirdAudit(ScheduledJobAuditStore):
        calls = 0

        def append(self, event):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 3:
                raise RuntimeError("audit boom")
            return super().append(event)

    audit_store = _FailThirdAudit(audit_db_path)
    runner = ScheduledJobRunner(
        store, audit_store, lambda: now, _failed, event_id_factory
    )

    with pytest.raises(JobRunnerError, match="audit append failed"):
        runner.run_once(limit=1)

    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.state is JobState.PAUSED
    assert fetched.attempt_count == 1
    assert [event.new_state for event in audit_store.read("job-1")] == [
        JobState.RUNNING,
        JobState.INTERRUPTED,
    ]


def test_retry_audit_failure_compensates_remaining_claimed_jobs(
    store: ScheduledJobStore,
    audit_db_path: Path,
    event_id_factory,
) -> None:
    now = _aware(2026, 7, 18, 9, 0)
    for job_id, next_run_at in (
        ("job-1", _aware(2026, 7, 18, 8, 29)),
        ("job-2", _aware(2026, 7, 18, 8, 30)),
    ):
        store.add(
            _make_job(
                job_id=job_id,
                next_run_at=next_run_at,
                created_at=_aware(2026, 7, 18, 8, 0),
                updated_at=_aware(2026, 7, 18, 8, 0),
                max_attempts=3,
            )
        )

    class _FailSecondAudit(ScheduledJobAuditStore):
        calls = 0

        def append(self, event):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("audit boom")
            return super().append(event)

    runner = ScheduledJobRunner(
        store,
        _FailSecondAudit(audit_db_path),
        lambda: now,
        _failed,
        event_id_factory,
    )

    with pytest.raises(JobRunnerError, match="audit append failed"):
        runner.run_once(limit=2)

    first = store.get("job-1", actor_id="actor-1", session_id="session-1")
    second = store.get("job-2", actor_id="actor-1", session_id="session-1")
    assert first is not None
    assert second is not None
    assert first.state is JobState.INTERRUPTED
    assert second.state is JobState.SCHEDULED


def test_retry_audit_compensation_does_not_mutate_newer_revision(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    event_id_factory,
) -> None:
    now = _aware(2026, 7, 18, 9, 0)
    original = _make_job(
        state=JobState.SCHEDULED,
        next_run_at=_aware(2026, 7, 18, 9, 30),
        created_at=_aware(2026, 7, 18, 8, 0),
        updated_at=now,
        max_attempts=3,
    )
    store.add(original)
    revised = store.update_next_run(
        "job-1",
        next_run_at=_aware(2026, 7, 18, 10, 0),
        expected_updated_at=original.updated_at,
        updated_at=_aware(2026, 7, 18, 9, 1),
        actor_id="actor-1",
        session_id="session-1",
    )
    assert revised is not None
    runner = ScheduledJobRunner(
        store, audit_store, lambda: now, _failed, event_id_factory
    )

    runner._pause_unaudited_schedule(original, now=now)

    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched == revised


def test_run_once_quiet_hours_reschedules_after_window(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    event_id_factory,
) -> None:
    now = _aware(2026, 7, 18, 9, 0)
    qh = QuietHours(timezone_name="UTC", start_minute=8 * 60, end_minute=10 * 60)
    store.add(
        _make_job(
            next_run_at=_aware(2026, 7, 18, 8, 30),
            created_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 8, 0),
            quiet_hours=qh,
        )
    )

    executed: list[ScheduledJob] = []

    def _exec(job: ScheduledJob) -> ExecutionResult:
        executed.append(job)
        return ExecutionResult(ExecutionStatus.SUCCESS)

    runner = ScheduledJobRunner(
        store, audit_store, lambda: now, _exec, event_id_factory
    )
    outcomes = runner.run_once(limit=1)
    assert len(outcomes) == 1
    assert outcomes[0].suppressed is True
    assert outcomes[0].new_state is JobState.SCHEDULED
    assert executed == []

    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.state is JobState.SCHEDULED
    assert fetched.next_run_at == _aware(2026, 7, 18, 10, 0)

    events = audit_store.read("job-1")
    assert any(
        e.reason_code is AuditReasonCode.QUIET_SUPPRESSED for e in events
    )

    later = _aware(2026, 7, 18, 10, 0)
    runner_later = ScheduledJobRunner(
        store, audit_store, lambda: later, _exec, event_id_factory
    )
    later_outcomes = runner_later.run_once(limit=1)
    assert len(later_outcomes) == 1
    assert later_outcomes[0].suppressed is False
    assert later_outcomes[0].new_state is JobState.COMPLETED
    assert len(executed) == 1


def test_quiet_hours_spring_gap_uses_first_real_local_minute() -> None:
    eastern = ZoneInfo("America/New_York")
    now = datetime(2026, 3, 8, 1, 30, tzinfo=eastern)
    quiet = QuietHours(
        timezone_name="America/New_York",
        start_minute=60,
        end_minute=150,
    )

    result = _next_eligible_after_quiet(now, quiet)

    assert result == datetime(2026, 3, 8, 3, 0, tzinfo=eastern)
    assert result.astimezone(UTC) == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)


def test_quiet_hours_fall_fold_chooses_future_occurrence() -> None:
    eastern = ZoneInfo("America/New_York")
    now = datetime(2026, 11, 1, 1, 0, tzinfo=eastern, fold=1)
    quiet = QuietHours(
        timezone_name="America/New_York",
        start_minute=0,
        end_minute=90,
    )

    result = _next_eligible_after_quiet(now, quiet)

    assert result == datetime(2026, 11, 1, 1, 30, tzinfo=eastern, fold=1)
    assert result.fold == 1
    assert result.astimezone(UTC) == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)


def test_run_once_concurrent_claim_executes_once(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    event_id_factory,
) -> None:
    """Two runners racing on the same due job must execute it exactly once."""
    now = _aware(2026, 7, 18, 9, 0)
    store.add(
        _make_job(
            next_run_at=_aware(2026, 7, 18, 8, 30),
            created_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 8, 0),
        )
    )

    executed: list[str] = []

    def _exec(job: ScheduledJob) -> ExecutionResult:
        executed.append(job.job_id)
        return ExecutionResult(ExecutionStatus.SUCCESS)

    runner_a = ScheduledJobRunner(
        store, audit_store, lambda: now, _exec, event_id_factory
    )
    runner_b = ScheduledJobRunner(
        store, audit_store, lambda: now, _exec, event_id_factory
    )
    outcomes_a = runner_a.run_once(limit=1)
    outcomes_b = runner_b.run_once(limit=1)
    total_executed = len(executed)
    total_outcomes = len(outcomes_a) + len(outcomes_b)
    assert total_executed == 1
    assert total_outcomes == 1


def test_claim_audit_failure_leaves_no_running_job(
    store: ScheduledJobStore,
    audit_db_path: Path,
    event_id_factory,
) -> None:
    now = _aware(2026, 7, 18, 9, 0)
    store.add(
        _make_job(
            next_run_at=_aware(2026, 7, 18, 8, 30),
            created_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 8, 0),
        )
    )

    class _FailingAudit(ScheduledJobAuditStore):
        def append(self, event):  # type: ignore[no-untyped-def]
            raise RuntimeError("audit boom")

    runner = ScheduledJobRunner(
        store, _FailingAudit(audit_db_path), lambda: now, _success, event_id_factory
    )
    with pytest.raises(JobRunnerError):
        runner.run_once(limit=1)

    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.state is JobState.SCHEDULED


def test_run_once_rejects_invalid_limit(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    event_id_factory,
) -> None:
    runner = ScheduledJobRunner(
        store,
        audit_store,
        lambda: _aware(2026, 7, 18, 9, 0),
        _success,
        event_id_factory,
    )
    with pytest.raises(JobRunnerError):
        runner.run_once(limit=0)
    with pytest.raises(JobRunnerError):
        runner.run_once(limit=65)


def test_run_once_rejects_naive_clock(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    event_id_factory,
) -> None:
    runner = ScheduledJobRunner(
        store,
        audit_store,
        lambda: datetime(2026, 7, 18, 9, 0),
        _success,
        event_id_factory,
    )
    with pytest.raises(JobRunnerError):
        runner.run_once(limit=1)


def test_run_once_rejects_clock_exception(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    event_id_factory,
) -> None:
    def _bad_clock() -> datetime:
        raise RuntimeError("boom")

    runner = ScheduledJobRunner(
        store, audit_store, _bad_clock, _success, event_id_factory
    )
    with pytest.raises(JobRunnerError):
        runner.run_once(limit=1)


def test_outcome_repr_is_content_free() -> None:
    outcome = JobRunOutcome(
        previous_state=JobState.RUNNING,
        new_state=JobState.COMPLETED,
        attempt_count=0,
        retried=False,
        suppressed=False,
    )
    text = repr(outcome)
    assert "job-1" not in text
    assert "actor-1" not in text
    assert "session-1" not in text
    assert "JobRunOutcome(" in text


def test_execution_result_repr_is_content_free() -> None:
    text = repr(ExecutionResult(ExecutionStatus.SUCCESS))
    assert "job-1" not in text
    assert "actor" not in text
    assert "session" not in text
    assert "boom" not in text
    assert "ExecutionResult(" in text


def test_recover_startup_reschedules_running_and_interrupted_jobs(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    event_id_factory,
) -> None:
    now = _aware(2026, 7, 18, 9, 0)
    store.add(_make_job(job_id="running-job", state=JobState.RUNNING))
    store.add(_make_job(job_id="interrupted-job", state=JobState.INTERRUPTED))
    runner = ScheduledJobRunner(
        store, audit_store, lambda: now, _success, event_id_factory
    )

    assert runner.recover_startup() == 2
    for job_id in ("running-job", "interrupted-job"):
        restored = store.get(
            job_id, actor_id="actor-1", session_id="session-1"
        )
        assert restored is not None
        assert restored.state is JobState.SCHEDULED
    running_events = audit_store.read("running-job")
    assert [(event.previous_state, event.new_state) for event in running_events] == [
        (JobState.RUNNING, JobState.INTERRUPTED),
        (JobState.INTERRUPTED, JobState.SCHEDULED),
    ]


def test_recover_startup_rejects_invalid_limits(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    event_id_factory,
) -> None:
    runner = ScheduledJobRunner(
        store,
        audit_store,
        lambda: _aware(2026, 7, 18, 9, 0),
        _success,
        event_id_factory,
    )
    for value in (True, 0, 65):
        with pytest.raises(JobRunnerError):
            runner.recover_startup(limit=value)
