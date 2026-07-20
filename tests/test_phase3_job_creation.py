"""Deterministic tests for the bounded Phase 3 scheduled-job creation service.

These tests cover only ``core.jobs.creation``. They use temporary databases
and injected factories; no timers, threads, subprocess, network, provider,
notification, or external execution is exercised.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.jobs.audit import AuditReasonCode
from core.jobs.audit_store import ScheduledJobAuditStore
from core.jobs.contracts import JobState
from core.jobs.creation import (
    JobCreationError,
    JobCreationRequest,
    JobCreationService,
)
from core.jobs.quiet_hours import QuietHours
from core.jobs.store import ScheduledJobStore

UTC = timezone.utc


def _aware(year, month, day, hour, minute, tz=UTC):
    return datetime(year, month, day, hour, minute, tzinfo=tz)


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
def fixed_clock():
    return lambda: _aware(2026, 7, 18, 8, 0)


@pytest.fixture
def job_id_factory():
    counter = {"n": 0}

    def _factory() -> str:
        counter["n"] += 1
        return f"job-{counter['n']}"

    return _factory


@pytest.fixture
def event_id_factory():
    counter = {"n": 0}

    def _factory() -> str:
        counter["n"] += 1
        return f"evt-{counter['n']}"

    return _factory


@pytest.fixture
def service(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    fixed_clock,
    job_id_factory,
    event_id_factory,
) -> JobCreationService:
    return JobCreationService(
        store, audit_store, fixed_clock, job_id_factory, event_id_factory
    )


def _request(**overrides) -> JobCreationRequest:
    base = dict(
        actor_id="actor-1",
        session_id="session-1",
        action="digest",
        proposal_id="prop-1",
        next_run_at=_aware(2026, 7, 18, 9, 0),
        max_attempts=1,
    )
    base.update(overrides)
    return JobCreationRequest(**base)


# ---------------------------------------------------------------------------
# Construction / validation
# ---------------------------------------------------------------------------


def test_creation_service_requires_valid_dependencies(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    fixed_clock,
    job_id_factory,
    event_id_factory,
) -> None:
    with pytest.raises(TypeError):
        JobCreationService("not-a-store", audit_store, fixed_clock, job_id_factory, event_id_factory)
    with pytest.raises(TypeError):
        JobCreationService(store, "not-an-audit", fixed_clock, job_id_factory, event_id_factory)
    with pytest.raises(TypeError):
        JobCreationService(store, audit_store, "not-callable", job_id_factory, event_id_factory)
    with pytest.raises(TypeError):
        JobCreationService(store, audit_store, fixed_clock, "not-callable", event_id_factory)
    with pytest.raises(TypeError):
        JobCreationService(store, audit_store, fixed_clock, job_id_factory, "not-callable")


def test_request_rejects_empty_actor_id() -> None:
    with pytest.raises(JobCreationError):
        _request(actor_id="")


def test_request_rejects_empty_session_id() -> None:
    with pytest.raises(JobCreationError):
        _request(session_id="")


def test_request_rejects_empty_action() -> None:
    with pytest.raises(JobCreationError):
        _request(action="")


def test_request_rejects_control_chars_in_action() -> None:
    with pytest.raises(JobCreationError):
        _request(action="bad\x01action")


def test_request_rejects_empty_proposal_id() -> None:
    with pytest.raises(JobCreationError):
        _request(proposal_id="")


def test_request_rejects_malformed_proposal_id() -> None:
    with pytest.raises(JobCreationError):
        _request(proposal_id="BAD ID!")


def test_request_rejects_naive_next_run() -> None:
    with pytest.raises(JobCreationError):
        _request(next_run_at=datetime(2026, 7, 18, 9, 0))


def test_request_rejects_invalid_max_attempts() -> None:
    with pytest.raises(JobCreationError):
        _request(max_attempts=0)
    with pytest.raises(JobCreationError):
        _request(max_attempts=101)


def test_request_rejects_invalid_fingerprint() -> None:
    with pytest.raises(JobCreationError):
        _request(meaningful_change_fingerprint="")


def test_request_accepts_quiet_hours() -> None:
    qh = QuietHours(timezone_name="UTC", start_minute=0, end_minute=60)
    req = _request(quiet_hours=qh)
    assert req.quiet_hours is qh


def test_request_repr_is_content_free() -> None:
    req = _request()
    text = repr(req)
    assert "actor-1" not in text
    assert "session-1" not in text
    assert "prop-1" not in text
    assert "digest" not in text
    assert "JobCreationRequest(" in text


# ---------------------------------------------------------------------------
# Creation flow
# ---------------------------------------------------------------------------


def test_create_persists_scheduled_job(
    service: JobCreationService,
    store: ScheduledJobStore,
) -> None:
    req = _request()
    job = service.create(req)
    assert job.state is JobState.SCHEDULED
    assert job.attempt_count == 0
    assert job.max_attempts == 1
    assert job.actor_id == "actor-1"
    assert job.session_id == "session-1"
    assert job.action == "digest"
    assert job.proposal_id == "prop-1"
    assert job.created_at == _aware(2026, 7, 18, 8, 0)
    assert job.updated_at == _aware(2026, 7, 18, 8, 0)
    fetched = store.get(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert fetched is not None
    assert fetched.job_id == "job-1"


def test_create_appends_created_audit_event(
    service: JobCreationService,
    audit_store: ScheduledJobAuditStore,
) -> None:
    req = _request()
    service.create(req)
    events = audit_store.read("job-1")
    assert len(events) == 1
    assert events[0].reason_code is AuditReasonCode.CREATED
    assert events[0].previous_state is None
    assert events[0].new_state is JobState.SCHEDULED
    assert events[0].action == "digest"


def test_create_uses_injected_job_id_factory(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    fixed_clock,
    event_id_factory,
) -> None:
    counter = {"n": 0}

    def _factory() -> str:
        counter["n"] += 1
        return f"custom-{counter['n']}"

    service = JobCreationService(
        store, audit_store, fixed_clock, _factory, event_id_factory
    )
    job = service.create(_request())
    assert job.job_id == "custom-1"


def test_create_rejects_invalid_job_id_from_factory(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    fixed_clock,
    event_id_factory,
) -> None:
    def _bad_factory() -> str:
        return "BAD ID!"

    service = JobCreationService(
        store, audit_store, fixed_clock, _bad_factory, event_id_factory
    )
    with pytest.raises(JobCreationError):
        service.create(_request())


def test_create_rejects_far_future_next_run(
    service: JobCreationService,
) -> None:
    far_future = _aware(2026, 7, 18, 8, 0) + timedelta(days=400)
    with pytest.raises(JobCreationError):
        service.create(_request(next_run_at=far_future))


def test_create_rejects_naive_clock(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    job_id_factory,
    event_id_factory,
) -> None:
    def _naive_clock() -> datetime:
        return datetime(2026, 7, 18, 8, 0)

    service = JobCreationService(
        store, audit_store, _naive_clock, job_id_factory, event_id_factory
    )
    with pytest.raises(JobCreationError):
        service.create(_request())


def test_create_rejects_clock_exception(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    job_id_factory,
    event_id_factory,
) -> None:
    def _bad_clock() -> datetime:
        raise RuntimeError("boom")

    service = JobCreationService(
        store, audit_store, _bad_clock, job_id_factory, event_id_factory
    )
    with pytest.raises(JobCreationError):
        service.create(_request())


def test_create_rejects_non_request(
    service: JobCreationService,
) -> None:
    with pytest.raises(JobCreationError):
        service.create("not-a-request")  # type: ignore[arg-type]


def test_create_with_quiet_hours_and_fingerprint(
    service: JobCreationService,
    store: ScheduledJobStore,
) -> None:
    qh = QuietHours(timezone_name="UTC", start_minute=0, end_minute=60)
    req = _request(quiet_hours=qh, meaningful_change_fingerprint="fp-1")
    job = service.create(req)
    assert job.quiet_hours is qh
    assert job.last_delivery_fingerprint is None
    assert req.meaningful_change_fingerprint == "fp-1"
    fetched = store.get(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert fetched is not None
    assert fetched.quiet_hours == qh
    assert fetched.last_delivery_fingerprint is None


def test_create_fingerprint_remains_none_until_acknowledgement(
    service: JobCreationService,
    store: ScheduledJobStore,
) -> None:
    job = service.create(_request(meaningful_change_fingerprint="fp-seed"))
    assert job.last_delivery_fingerprint is None
    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.last_delivery_fingerprint is None


def test_create_audit_failure_leaves_no_active_job(
    store: ScheduledJobStore,
    audit_db_path: Path,
    fixed_clock,
    job_id_factory,
    event_id_factory,
) -> None:
    class _FailingAudit(ScheduledJobAuditStore):
        def append(self, event):  # type: ignore[no-untyped-def]
            raise RuntimeError("audit boom")

    failing = _FailingAudit(audit_db_path)
    service = JobCreationService(
        store, failing, fixed_clock, job_id_factory, event_id_factory
    )
    with pytest.raises(JobCreationError):
        service.create(_request())
    assert store.get("job-1", actor_id="actor-1", session_id="session-1") is None
    assert store.list(actor_id="actor-1", session_id="session-1") == []


def test_create_audit_failure_deactivates_job_when_delete_compensation_misses(
    store: ScheduledJobStore,
    audit_db_path: Path,
    fixed_clock,
    job_id_factory,
    event_id_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingAudit(ScheduledJobAuditStore):
        def append(self, event):  # type: ignore[no-untyped-def]
            raise RuntimeError("private audit detail")

    monkeypatch.setattr(store, "remove_if_unmodified", lambda *args, **kwargs: False)
    service = JobCreationService(
        store,
        _FailingAudit(audit_db_path),
        fixed_clock,
        job_id_factory,
        event_id_factory,
    )

    with pytest.raises(JobCreationError) as raised:
        service.create(_request())

    assert str(raised.value) == "audit append failed"
    assert "private audit detail" not in str(raised.value)
    assert "job-1" not in str(raised.value)
    remaining = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert remaining is not None
    assert remaining.state is JobState.CANCELLED


def test_create_repr_does_not_leak_content(
    service: JobCreationService,
) -> None:
    job = service.create(_request())
    text = repr(job)
    assert "actor-1" not in text
    assert "session-1" not in text
    assert "prop-1" not in text
    assert "digest" not in text
