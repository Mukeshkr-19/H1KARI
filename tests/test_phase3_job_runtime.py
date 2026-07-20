"""Tests for the Phase 3 scheduled-jobs runtime wrapper.

These tests verify that the runtime converts service results into safe
canonical dictionaries, catches exceptions, and never leaks internal
identifiers or exception text.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.action_policy import Actor, ActorContext
from core.jobs import (
    JobState,
    ScheduledJob,
    ScheduledJobStore,
)
from core.jobs.service import ScheduledJobService
from core.jobs.runtime import ScheduledJobRuntime

UTC = timezone.utc


def _aware(year, month, day, hour, minute, tz=UTC):
    return datetime(year, month, day, hour, minute, tzinfo=tz)


@pytest.fixture
def owner_context():
    return ActorContext(
        actor_id="actor-1",
        actor=Actor.OWNER,
        session_id="session-1",
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
    return lambda: _aware(2026, 7, 18, 10, 0)


@pytest.fixture
def runtime(store, fixed_clock):
    service = ScheduledJobService(store, clock=fixed_clock)
    return ScheduledJobRuntime(service)


def _add_job(store, job_id="job-1", state=JobState.SCHEDULED):
    from core.jobs import ScheduledJob
    job = ScheduledJob(
        job_id=job_id,
        actor_id="actor-1",
        session_id="session-1",
        action="digest",
        proposal_id="prop-1",
        state=state,
        next_run_at=_aware(2026, 7, 18, 9, 0),
        created_at=_aware(2026, 7, 18, 8, 0),
        updated_at=_aware(2026, 7, 18, 8, 0),
    )
    store.add(job)


def test_list_jobs_returns_canonical_dict(runtime, store, owner_context):
    _add_job(store)
    result = runtime.list_jobs(owner_context)
    assert result["type"] == "scheduled_jobs"
    assert set(result) == {"type", "jobs"}
    assert len(result["jobs"]) == 1
    job = result["jobs"][0]
    assert job["job_id"] == "job-1"
    assert job["state"] == "scheduled"
    assert "actor_id" not in job
    assert "session_id" not in job
    assert "proposal_id" not in job


def test_pause_returns_canonical_update(runtime, store, owner_context):
    _add_job(store)
    result = runtime.pause(owner_context, "job-1")
    assert result["type"] == "scheduled_job_update"
    assert set(result) == {"type", "job"}
    assert result["job"]["state"] == "paused"


def test_resume_returns_canonical_update(runtime, store, owner_context):
    _add_job(store, state=JobState.PAUSED)
    result = runtime.resume(owner_context, "job-1")
    assert result["type"] == "scheduled_job_update"
    assert set(result) == {"type", "job"}
    assert result["job"]["state"] == "scheduled"


def test_cancel_returns_canonical_update(runtime, store, owner_context):
    _add_job(store)
    result = runtime.cancel(owner_context, "job-1")
    assert result["type"] == "scheduled_job_update"
    assert set(result) == {"type", "job"}
    assert result["job"]["state"] == "cancelled"


def test_missing_job_returns_safe_error(runtime, store, owner_context):
    result = runtime.pause(owner_context, "missing")
    assert result["type"] == "scheduled_job_error"
    assert result["job_id"] == "missing"
    assert result["code"] == "job_not_found"


def test_unauthorized_actor_returns_control_failed(runtime, store):
    guest = ActorContext(
        actor_id="actor-1",
        actor=Actor.GUEST,
        session_id="session-1",
        source="text",
    )
    _add_job(store)
    result = runtime.pause(guest, "job-1")
    assert result["type"] == "scheduled_job_error"
    assert result["job_id"] == "job-1"
    assert result["code"] == "control_failed"


def test_runtime_catches_service_exceptions(store, owner_context):
    service = ScheduledJobService(store, clock=lambda: _aware(2026, 7, 18, 10, 0))

    def _broken_list(**kwargs):
        raise RuntimeError("store failure")

    store.list = _broken_list
    runtime = ScheduledJobRuntime(service)
    result = runtime.list_jobs(owner_context)
    assert result["type"] == "scheduled_job_error"
    assert result["job_id"] == "scheduled-jobs"
    assert result["code"] == "unavailable"


def test_runtime_does_not_leak_exception_text(store, owner_context):
    service = ScheduledJobService(store, clock=lambda: _aware(2026, 7, 18, 10, 0))

    def _broken_pause(actor, job_id):
        raise RuntimeError("secret details")

    service.pause = _broken_pause
    runtime = ScheduledJobRuntime(service)
    result = runtime.pause(owner_context, "job-1")
    assert result["type"] == "scheduled_job_error"
    assert "secret" not in str(result)
    assert "details" not in str(result)


def test_stable_owner_scope_survives_connection_session_change(store, fixed_clock):
    service = ScheduledJobService(store, clock=fixed_clock)
    runtime = ScheduledJobRuntime(service, owner_scope_id="installation-1")
    job = ScheduledJob(
        job_id="job-1",
        actor_id="actor-1",
        session_id="installation-1",
        action="digest",
        proposal_id="prop-1",
        state=JobState.SCHEDULED,
        next_run_at=_aware(2026, 7, 18, 9, 0),
        created_at=_aware(2026, 7, 18, 8, 0),
        updated_at=_aware(2026, 7, 18, 8, 0),
    )
    store.add(job)
    reconnected = ActorContext(
        actor_id="actor-1",
        actor=Actor.OWNER,
        session_id="new-connection-token",
        source="websocket",
    )

    result = runtime.list_jobs(reconnected)

    assert [item["job_id"] for item in result["jobs"]] == ["job-1"]


def test_guest_is_not_rebound_to_owner_scope(store, fixed_clock):
    service = ScheduledJobService(store, clock=fixed_clock)
    runtime = ScheduledJobRuntime(service, owner_scope_id="installation-1")
    guest = ActorContext(
        actor_id="guest",
        actor=Actor.GUEST,
        session_id="guest-session",
        source="websocket",
    )

    assert runtime.scheduled_actor(guest) is guest
    assert runtime.list_jobs(guest)["code"] == "control_failed"
