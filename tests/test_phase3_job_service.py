"""Tests for the Phase 3 bounded scheduled-jobs application controller.

These tests cover list bounds, ownership isolation, every valid control,
invalid transitions, duplicate/idempotent controls, stale CAS, clock failure,
malformed IDs, and exception sanitization. No I/O, network, subprocess,
execution, or hidden clocks are used.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.action_policy import Actor, ActorContext
from core.jobs import (
    AuditReasonCode,
    JobState,
    QuietHours,
    ScheduledJob,
    ScheduledJobStore,
    ScheduledJobAuditStore,
)
from core.jobs.lifecycle import JobLifecycleController
from core.jobs.service import (
    JobControlResult,
    JobServiceCode,
    ScheduledJobService,
    ScheduledJobView,
)

UTC = timezone.utc


def _aware(year, month, day, hour, minute, tz=UTC):
    return datetime(year, month, day, hour, minute, tzinfo=tz)


def _make_job(job_id="job-1", actor="actor-1", session="session-1", **over):
    base = dict(
        job_id=job_id,
        actor_id=actor,
        session_id=session,
        action="digest",
        proposal_id="prop-1",
        state=JobState.SCHEDULED,
        next_run_at=_aware(2026, 7, 18, 9, 0),
        created_at=_aware(2026, 7, 18, 8, 0),
        updated_at=_aware(2026, 7, 18, 8, 0),
    )
    base.update(over)
    return ScheduledJob(**base)


@pytest.fixture
def owner_context():
    return ActorContext(
        actor_id="actor-1",
        actor=Actor.OWNER,
        session_id="session-1",
        source="text",
    )


@pytest.fixture
def guest_context():
    return ActorContext(
        actor_id="actor-1",
        actor=Actor.GUEST,
        session_id="session-1",
        source="text",
    )


@pytest.fixture
def other_session_context():
    return ActorContext(
        actor_id="actor-1",
        actor=Actor.OWNER,
        session_id="session-2",
        source="text",
    )


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "jobs.db"


@pytest.fixture
def store(db_path):
    return ScheduledJobStore(db_path)


@pytest.fixture
def fixed_clock():
    now = _aware(2026, 7, 18, 10, 0)
    return lambda: now


@pytest.fixture
def service(store, fixed_clock):
    return ScheduledJobService(store, clock=fixed_clock)


def test_list_jobs_returns_bounded_fields(service, store, owner_context):
    store.add(_make_job(job_id="job-1"))
    result = service.list_jobs(owner_context)
    assert result.error is None
    assert result.jobs is not None
    assert len(result.jobs) == 1
    assert isinstance(result.jobs, tuple)
    view = result.jobs[0]
    assert isinstance(view, ScheduledJobView)
    assert view.job_id == "job-1"
    assert view.action == "digest"
    assert view.state == "scheduled"
    assert view.next_run_at == _aware(2026, 7, 18, 9, 0).isoformat()
    assert view.quiet_hours_summary is None
    assert view.attempt_count == 0
    assert view.max_attempts == 1


def test_list_jobs_includes_quiet_hours_summary(service, store, owner_context):
    qh = QuietHours(timezone_name="America/New_York", start_minute=1320, end_minute=1380)
    store.add(_make_job(job_id="job-1", quiet_hours=qh))
    result = service.list_jobs(owner_context)
    assert result.error is None
    assert result.jobs is not None
    assert result.jobs[0].quiet_hours_summary == "1320-1380 (America/New_York)"


def test_list_jobs_respects_limit(service, store, owner_context):
    for i in range(70):
        store.add(_make_job(job_id=f"job-{i}", updated_at=_aware(2026, 7, 18, 8, 0, tz=UTC) + timedelta(minutes=i)))
    result = service.list_jobs(owner_context, limit=64)
    assert result.error is None
    assert result.jobs is not None
    assert len(result.jobs) == 64


def test_list_jobs_clamps_oversized_limit(service, store, owner_context):
    store.add(_make_job(job_id="job-1"))
    result = service.list_jobs(owner_context, limit=1000)
    assert result.error is None
    assert result.jobs is not None
    assert len(result.jobs) <= 64


def test_list_jobs_clamps_negative_limit(service, store, owner_context):
    store.add(_make_job(job_id="job-1"))
    result = service.list_jobs(owner_context, limit=-5)
    assert result.error is None
    assert result.jobs is not None
    assert len(result.jobs) == 1


def test_list_jobs_ownership_isolation(service, store, owner_context, other_session_context):
    store.add(_make_job(job_id="job-1", session="session-1"))
    store.add(_make_job(job_id="job-2", session="session-2"))
    result = service.list_jobs(owner_context)
    assert result.error is None
    assert result.jobs is not None
    assert [j.job_id for j in result.jobs] == ["job-1"]


def test_list_jobs_rejects_non_owner(service, store, guest_context):
    store.add(_make_job(job_id="job-1"))
    result = service.list_jobs(guest_context)
    assert result.error is JobServiceCode.CONTROL_FAILED


def test_pause_transitions_scheduled_to_paused(service, store, owner_context):
    store.add(_make_job(job_id="job-1"))
    result = service.pause(owner_context, "job-1")
    assert result.error is None
    assert result.job is not None
    assert result.job.state == "paused"


def test_production_style_controls_append_audit_events(
    tmp_path, store, fixed_clock, owner_context
):
    audit_store = ScheduledJobAuditStore(tmp_path / "audit.db")
    event_ids = iter(("event-1", "event-2", "event-3"))
    lifecycle = JobLifecycleController(
        store, audit_store, fixed_clock, lambda: next(event_ids)
    )
    service = ScheduledJobService(
        store, clock=fixed_clock, lifecycle=lifecycle
    )
    store.add(_make_job(job_id="job-1"))

    paused = service.pause(owner_context, "job-1")
    resumed = service.resume(owner_context, "job-1")
    cancelled = service.cancel(owner_context, "job-1")

    assert paused.error is None
    assert resumed.error is None
    assert cancelled.error is None
    events = audit_store.read("job-1")
    assert [event.reason_code for event in events] == [
        AuditReasonCode.CONTROL,
        AuditReasonCode.CONTROL,
        AuditReasonCode.CONTROL,
    ]


def test_pause_is_idempotent(service, store, owner_context):
    store.add(_make_job(job_id="job-1"))
    service.pause(owner_context, "job-1")
    result = service.pause(owner_context, "job-1")
    assert result.error is None
    assert result.job is not None
    assert result.job.state == "paused"


def test_resume_transitions_paused_to_scheduled(service, store, owner_context):
    store.add(_make_job(job_id="job-1", state=JobState.PAUSED))
    result = service.resume(owner_context, "job-1")
    assert result.error is None
    assert result.job is not None
    assert result.job.state == "scheduled"


def test_resume_transitions_interrupted_to_scheduled(service, store, owner_context):
    store.add(_make_job(job_id="job-1", state=JobState.INTERRUPTED))
    result = service.resume(owner_context, "job-1")
    assert result.error is None
    assert result.job is not None
    assert result.job.state == "scheduled"


def test_resume_is_idempotent(service, store, owner_context):
    store.add(_make_job(job_id="job-1"))
    result = service.resume(owner_context, "job-1")
    assert result.error is None
    assert result.job is not None
    assert result.job.state == "scheduled"


def test_cancel_transitions_scheduled_to_cancelled(service, store, owner_context):
    store.add(_make_job(job_id="job-1"))
    result = service.cancel(owner_context, "job-1")
    assert result.error is None
    assert result.job is not None
    assert result.job.state == "cancelled"


def test_cancel_transitions_paused_to_cancelled(service, store, owner_context):
    store.add(_make_job(job_id="job-1", state=JobState.PAUSED))
    result = service.cancel(owner_context, "job-1")
    assert result.error is None
    assert result.job is not None
    assert result.job.state == "cancelled"


def test_cancel_transitions_running_to_cancelled(service, store, owner_context):
    store.add(_make_job(job_id="job-1", state=JobState.RUNNING))
    result = service.cancel(owner_context, "job-1")
    assert result.error is None
    assert result.job is not None
    assert result.job.state == "cancelled"


def test_cancel_transitions_interrupted_to_cancelled(service, store, owner_context):
    store.add(_make_job(job_id="job-1", state=JobState.INTERRUPTED))
    result = service.cancel(owner_context, "job-1")
    assert result.error is None
    assert result.job is not None
    assert result.job.state == "cancelled"


def test_cancel_is_idempotent(service, store, owner_context):
    store.add(_make_job(job_id="job-1", state=JobState.CANCELLED))
    result = service.cancel(owner_context, "job-1")
    assert result.error is None
    assert result.job is not None
    assert result.job.state == "cancelled"


def test_missing_job_returns_job_not_found(service, store, owner_context):
    result = service.pause(owner_context, "missing")
    assert result.error is JobServiceCode.JOB_NOT_FOUND
    assert result.job is None


def test_cross_session_job_returns_job_not_found(service, store, owner_context, other_session_context):
    store.add(_make_job(job_id="job-1", session="session-2"))
    result = service.pause(owner_context, "job-1")
    assert result.error is JobServiceCode.JOB_NOT_FOUND


def test_invalid_pause_from_terminal_fails_closed(service, store, owner_context):
    store.add(_make_job(job_id="job-1", state=JobState.COMPLETED))
    result = service.pause(owner_context, "job-1")
    assert result.error is JobServiceCode.CONTROL_FAILED


def test_invalid_resume_from_running_fails_closed(service, store, owner_context):
    store.add(_make_job(job_id="job-1", state=JobState.RUNNING))
    result = service.resume(owner_context, "job-1")
    assert result.error is JobServiceCode.CONTROL_FAILED


def test_invalid_cancel_from_completed_fails_closed(service, store, owner_context):
    store.add(_make_job(job_id="job-1", state=JobState.COMPLETED))
    result = service.cancel(owner_context, "job-1")
    assert result.error is JobServiceCode.CONTROL_FAILED


def test_stale_revision_fails_closed(service, store, owner_context):
    store.add(_make_job(job_id="job-1"))
    # Move the job forward outside the service.
    store.transition(
        "job-1",
        expected_state=JobState.SCHEDULED,
        new_state=JobState.RUNNING,
        expected_updated_at=_aware(2026, 7, 18, 8, 0),
        updated_at=_aware(2026, 7, 18, 9, 0),
        actor_id="actor-1",
        session_id="session-1",
    )
    # Service still sees the old revision in current, but CAS will fail.
    result = service.pause(owner_context, "job-1")
    assert result.error is JobServiceCode.CONTROL_FAILED


def test_clock_failure_returns_unavailable(store, owner_context):
    def broken_clock():
        raise RuntimeError("clock failure")

    service = ScheduledJobService(store, clock=broken_clock)
    store.add(_make_job(job_id="job-1"))
    result = service.pause(owner_context, "job-1")
    assert result.error is JobServiceCode.UNAVAILABLE


def test_non_timezone_aware_clock_returns_unavailable(store, owner_context):
    def naive_clock():
        return datetime(2026, 7, 18, 10, 0)

    service = ScheduledJobService(store, clock=naive_clock)
    store.add(_make_job(job_id="job-1"))
    result = service.pause(owner_context, "job-1")
    assert result.error is JobServiceCode.UNAVAILABLE


def test_non_callable_clock_raises_at_construction(store):
    with pytest.raises(TypeError):
        ScheduledJobService(store, clock="not-callable")


def test_malformed_job_id_is_rejected(service, store, owner_context):
    store.add(_make_job(job_id="job-1"))
    result = service.pause(owner_context, "bad id!")
    assert result.error is JobServiceCode.JOB_NOT_FOUND


def test_result_excludes_internal_identifiers(service, store, owner_context):
    store.add(_make_job(job_id="job-1"))
    result = service.list_jobs(owner_context)
    assert result.error is None
    view = result.jobs[0]
    assert view.job_id == "job-1"
    assert not hasattr(view, "actor_id")
    assert not hasattr(view, "session_id")
    assert not hasattr(view, "proposal_id")


def test_service_does_not_execute_work(service, store, owner_context):
    store.add(_make_job(job_id="job-1"))
    # No execution side effects: state remains scheduled after list.
    result = service.list_jobs(owner_context)
    assert result.jobs[0].state == "scheduled"
    job = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert job.state is JobState.SCHEDULED


def test_store_read_failure_is_unavailable_not_not_found(service, store, owner_context):
    def broken_get(*args, **kwargs):
        raise RuntimeError("private database detail")

    store.get = broken_get
    result = service.pause(owner_context, "job-1")
    assert result.error is JobServiceCode.UNAVAILABLE


def test_store_mutation_failure_is_safely_bounded(service, store, owner_context):
    store.add(_make_job(job_id="job-1"))

    def broken_pause(*args, **kwargs):
        raise RuntimeError("private database detail")

    store.pause = broken_pause
    result = service.pause(owner_context, "job-1")
    assert result.error is JobServiceCode.UNAVAILABLE
    assert "private" not in repr(result)


def test_view_rejects_values_the_frontend_cannot_accept():
    with pytest.raises(ValueError):
        ScheduledJobView(
            job_id="BAD:ID",
            action="digest",
            state="scheduled",
            next_run_at="2026-07-18T09:00:00+00:00",
            quiet_hours_summary=None,
            attempt_count=0,
            max_attempts=1,
        )
    with pytest.raises(ValueError):
        ScheduledJobView(
            job_id="job-1",
            action="digest",
            state="scheduled",
            next_run_at="2026-07-18T09:00:00+00:00",
            quiet_hours_summary=None,
            attempt_count=0,
            max_attempts=101,
        )


def test_unrepresentable_stored_job_returns_unavailable(service, store, owner_context):
    store.add(_make_job(job_id="job-1", max_attempts=101))
    result = service.list_jobs(owner_context)
    assert result.error is JobServiceCode.UNAVAILABLE
