"""Deterministic tests for the bounded Phase 3 scheduled-job lifecycle controller.

These tests cover only ``core.jobs.lifecycle``. They use temporary databases
and injected clocks/factories; no timers, threads, subprocess, network,
provider, notification, or external execution is exercised.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.jobs.audit import AuditReasonCode
from core.jobs.audit_store import ScheduledJobAuditStore
from core.jobs.contracts import JobState, ScheduledJob
from core.jobs.lifecycle import (
    JobLifecycleController,
    JobLifecycleError,
    LifecycleOutcomeCode,
    LifecycleResult,
)
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
    return lambda: _aware(2026, 7, 18, 9, 0)


@pytest.fixture
def event_id_factory():
    counter = {"n": 0}

    def _factory() -> str:
        counter["n"] += 1
        return f"evt-{counter['n']}"

    return _factory


@pytest.fixture
def controller(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    fixed_clock,
    event_id_factory,
) -> JobLifecycleController:
    return JobLifecycleController(
        store, audit_store, fixed_clock, event_id_factory
    )


def _make_job(
    *,
    job_id: str = "job-1",
    actor_id: str = "actor-1",
    session_id: str = "session-1",
    state: JobState = JobState.SCHEDULED,
    updated_at: datetime | None = None,
) -> ScheduledJob:
    return ScheduledJob(
        job_id=job_id,
        actor_id=actor_id,
        session_id=session_id,
        action="digest",
        proposal_id="prop-1",
        state=state,
        next_run_at=_aware(2026, 7, 18, 10, 0),
        created_at=_aware(2026, 7, 18, 8, 0),
        updated_at=updated_at or _aware(2026, 7, 18, 8, 0),
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_controller_requires_valid_dependencies(
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
    fixed_clock,
    event_id_factory,
) -> None:
    with pytest.raises(TypeError):
        JobLifecycleController("not-a-store", audit_store, fixed_clock, event_id_factory)
    with pytest.raises(TypeError):
        JobLifecycleController(store, "not-an-audit", fixed_clock, event_id_factory)
    with pytest.raises(TypeError):
        JobLifecycleController(store, audit_store, "not-callable", event_id_factory)
    with pytest.raises(TypeError):
        JobLifecycleController(store, audit_store, fixed_clock, "not-callable")


# ---------------------------------------------------------------------------
# Pause
# ---------------------------------------------------------------------------


def test_pause_transitions_scheduled_to_paused(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
) -> None:
    store.add(_make_job())
    result = controller.pause(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert result.code is LifecycleOutcomeCode.OK
    assert result.new_state is JobState.PAUSED
    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.state is JobState.PAUSED
    events = audit_store.read("job-1")
    assert any(
        e.previous_state is JobState.SCHEDULED
        and e.new_state is JobState.PAUSED
        and e.reason_code is AuditReasonCode.CONTROL
        for e in events
    )


def test_pause_is_idempotent_when_already_paused(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
) -> None:
    store.add(_make_job(state=JobState.PAUSED))
    result = controller.pause(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert result.code is LifecycleOutcomeCode.OK
    assert result.new_state is JobState.PAUSED


def test_pause_rejects_invalid_state(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
) -> None:
    store.add(_make_job(state=JobState.COMPLETED))
    result = controller.pause(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert result.code is LifecycleOutcomeCode.INVALID_STATE


def test_pause_reveals_no_cross_session_job(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
) -> None:
    store.add(_make_job(session_id="session-2"))
    result = controller.pause(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert result.code is LifecycleOutcomeCode.NOT_FOUND


def test_pause_reveals_no_missing_job(
    controller: JobLifecycleController,
) -> None:
    result = controller.pause(
        "missing", actor_id="actor-1", session_id="session-1"
    )
    assert result.code is LifecycleOutcomeCode.NOT_FOUND


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def test_resume_transitions_paused_to_scheduled(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
) -> None:
    store.add(_make_job(state=JobState.PAUSED))
    result = controller.resume(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert result.code is LifecycleOutcomeCode.OK
    assert result.new_state is JobState.SCHEDULED
    events = audit_store.read("job-1")
    assert any(
        e.previous_state is JobState.PAUSED
        and e.new_state is JobState.SCHEDULED
        for e in events
    )


def test_resume_transitions_interrupted_to_scheduled(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
) -> None:
    store.add(_make_job(state=JobState.INTERRUPTED))
    result = controller.resume(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert result.code is LifecycleOutcomeCode.OK
    assert result.new_state is JobState.SCHEDULED


def test_resume_does_not_duplicate_completed(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
) -> None:
    store.add(_make_job(state=JobState.COMPLETED))
    result = controller.resume(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert result.code is LifecycleOutcomeCode.INVALID_STATE


def test_resume_is_idempotent_when_already_scheduled(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
) -> None:
    store.add(_make_job(state=JobState.SCHEDULED))
    result = controller.resume(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert result.code is LifecycleOutcomeCode.OK
    assert result.new_state is JobState.SCHEDULED


def test_resume_reveals_no_cross_session_job(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
) -> None:
    store.add(_make_job(state=JobState.PAUSED, session_id="session-2"))
    result = controller.resume(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert result.code is LifecycleOutcomeCode.NOT_FOUND


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


def test_cancel_transitions_scheduled_to_cancelled(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
) -> None:
    store.add(_make_job())
    result = controller.cancel(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert result.code is LifecycleOutcomeCode.OK
    assert result.new_state is JobState.CANCELLED
    events = audit_store.read("job-1")
    assert any(
        e.previous_state is JobState.SCHEDULED
        and e.new_state is JobState.CANCELLED
        for e in events
    )


def test_cancel_transitions_running_to_cancelled(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
) -> None:
    store.add(_make_job(state=JobState.RUNNING))
    result = controller.cancel(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert result.code is LifecycleOutcomeCode.OK
    assert result.new_state is JobState.CANCELLED


def test_cancel_is_idempotent_when_already_cancelled(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
) -> None:
    store.add(_make_job(state=JobState.CANCELLED))
    result = controller.cancel(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert result.code is LifecycleOutcomeCode.OK
    assert result.new_state is JobState.CANCELLED


def test_cancel_rejects_completed(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
) -> None:
    store.add(_make_job(state=JobState.COMPLETED))
    result = controller.cancel(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert result.code is LifecycleOutcomeCode.INVALID_STATE


def test_cancel_rejects_failed(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
) -> None:
    store.add(_make_job(state=JobState.FAILED))
    result = controller.cancel(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert result.code is LifecycleOutcomeCode.INVALID_STATE


def test_cancel_reveals_no_cross_session_job(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
) -> None:
    store.add(_make_job(session_id="session-2"))
    result = controller.cancel(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert result.code is LifecycleOutcomeCode.NOT_FOUND


def test_cancel_prevents_later_execution(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
) -> None:
    """A cancelled job must not be claimable by the runner."""
    store.add(_make_job())
    result = controller.cancel(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert result.code is LifecycleOutcomeCode.OK

    # The store's claim_due only picks SCHEDULED rows, so a cancelled job
    # is structurally excluded from later execution.
    claimed = store.claim_due(
        now=_aware(2026, 7, 18, 10, 0),
        updated_at=_aware(2026, 7, 18, 10, 0),
        limit=10,
    )
    assert claimed == ()
    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.state is JobState.CANCELLED


def test_pause_prevents_later_execution(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
) -> None:
    store.add(_make_job())
    result = controller.pause(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    assert result.code is LifecycleOutcomeCode.OK
    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.state is JobState.PAUSED


# ---------------------------------------------------------------------------
# Repr / privacy
# ---------------------------------------------------------------------------


def test_result_repr_is_content_free() -> None:
    result = LifecycleResult(
        code=LifecycleOutcomeCode.OK, new_state=JobState.PAUSED
    )
    text = repr(result)
    assert "actor-1" not in text
    assert "session-1" not in text
    assert "LifecycleResult(" in text


def test_operations_do_not_leak_identifiers(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
) -> None:
    store.add(_make_job(session_id="session-2"))
    result = controller.pause(
        "job-1", actor_id="actor-1", session_id="session-1"
    )
    text = repr(result)
    assert "job-1" not in text
    assert "actor-1" not in text
    assert "session-2" not in text


def test_acknowledge_delivery_updates_exactly_once(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
    audit_store: ScheduledJobAuditStore,
) -> None:
    from core.jobs.delivery import DeliveryAttemptResult, DeliveryAttemptStatus

    store.add(_make_job())
    ack = DeliveryAttemptResult(DeliveryAttemptStatus.ACKNOWLEDGED)
    first = controller.acknowledge_delivery(
        "job-1",
        actor_id="actor-1",
        session_id="session-1",
        candidate_fingerprint="fp-1",
        delivery_result=ack,
    )
    assert first.code is LifecycleOutcomeCode.OK
    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.last_delivery_fingerprint == "fp-1"

    second = controller.acknowledge_delivery(
        "job-1",
        actor_id="actor-1",
        session_id="session-1",
        candidate_fingerprint="fp-1",
        delivery_result=ack,
    )
    assert second.code is LifecycleOutcomeCode.INVALID_STATE
    fetched2 = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched2 is not None
    assert fetched2.last_delivery_fingerprint == "fp-1"
    assert fetched2.updated_at == fetched.updated_at
    delivered = [
        event
        for event in audit_store.read("job-1")
        if event.reason_code is AuditReasonCode.DELIVERED
    ]
    assert len(delivered) == 1


def test_acknowledge_delivery_audit_failure_reverts_fingerprint(
    store: ScheduledJobStore,
    audit_db_path: Path,
    fixed_clock,
    event_id_factory,
) -> None:
    from core.jobs.delivery import DeliveryAttemptResult, DeliveryAttemptStatus

    class _FailingAudit(ScheduledJobAuditStore):
        def append(self, event):  # type: ignore[no-untyped-def]
            raise RuntimeError("private audit detail")

    store.add(_make_job())
    controller = JobLifecycleController(
        store, _FailingAudit(audit_db_path), fixed_clock, event_id_factory
    )
    result = controller.acknowledge_delivery(
        "job-1",
        actor_id="actor-1",
        session_id="session-1",
        candidate_fingerprint="fp-1",
        delivery_result=DeliveryAttemptResult(DeliveryAttemptStatus.ACKNOWLEDGED),
    )

    assert result.code is LifecycleOutcomeCode.UNAVAILABLE
    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.last_delivery_fingerprint is None


def test_resume_audit_failure_compensates_to_paused(
    store: ScheduledJobStore,
    audit_db_path: Path,
    fixed_clock,
    event_id_factory,
) -> None:
    class _FailingAudit(ScheduledJobAuditStore):
        def append(self, event):  # type: ignore[no-untyped-def]
            raise RuntimeError("private audit detail")

    store.add(_make_job(state=JobState.PAUSED))
    controller = JobLifecycleController(
        store, _FailingAudit(audit_db_path), fixed_clock, event_id_factory
    )
    result = controller.resume(
        "job-1", actor_id="actor-1", session_id="session-1"
    )

    assert result == LifecycleResult(
        code=LifecycleOutcomeCode.UNAVAILABLE, new_state=JobState.PAUSED
    )
    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.state is JobState.PAUSED


def test_resume_audit_failure_cancels_if_pause_compensation_misses(
    store: ScheduledJobStore,
    audit_db_path: Path,
    fixed_clock,
    event_id_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingAudit(ScheduledJobAuditStore):
        def append(self, event):  # type: ignore[no-untyped-def]
            raise RuntimeError("private audit detail")

    store.add(_make_job(state=JobState.PAUSED))
    original_transition = store.transition
    calls = 0

    def miss_first_compensation(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 2:
            return None
        return original_transition(*args, **kwargs)

    monkeypatch.setattr(store, "transition", miss_first_compensation)
    controller = JobLifecycleController(
        store, _FailingAudit(audit_db_path), fixed_clock, event_id_factory
    )
    result = controller.resume(
        "job-1", actor_id="actor-1", session_id="session-1"
    )

    assert result == LifecycleResult(
        code=LifecycleOutcomeCode.UNAVAILABLE, new_state=JobState.CANCELLED
    )
    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.state is JobState.CANCELLED


def test_acknowledge_delivery_rejects_failed_and_suppressed(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
) -> None:
    from core.jobs.delivery import DeliveryAttemptResult, DeliveryAttemptStatus
    from core.jobs.quiet_hours import QuietHours

    store.add(
        ScheduledJob(
            job_id="job-1",
            actor_id="actor-1",
            session_id="session-1",
            action="digest",
            proposal_id="prop-1",
            state=JobState.SCHEDULED,
            next_run_at=_aware(2026, 7, 18, 10, 0),
            created_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 8, 0),
            quiet_hours=QuietHours(
                timezone_name="UTC", start_minute=0, end_minute=1439
            ),
        )
    )
    failed = controller.acknowledge_delivery(
        "job-1",
        actor_id="actor-1",
        session_id="session-1",
        candidate_fingerprint="fp-1",
        delivery_result=DeliveryAttemptResult(DeliveryAttemptStatus.FAILED),
    )
    assert failed.code is LifecycleOutcomeCode.INVALID_STATE
    suppressed = controller.acknowledge_delivery(
        "job-1",
        actor_id="actor-1",
        session_id="session-1",
        candidate_fingerprint="fp-1",
        delivery_result=DeliveryAttemptResult(DeliveryAttemptStatus.ACKNOWLEDGED),
    )
    assert suppressed.code is LifecycleOutcomeCode.INVALID_STATE
    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.last_delivery_fingerprint is None


def test_acknowledge_delivery_cross_session_is_noop(
    controller: JobLifecycleController,
    store: ScheduledJobStore,
) -> None:
    from core.jobs.delivery import DeliveryAttemptResult, DeliveryAttemptStatus

    store.add(_make_job())
    result = controller.acknowledge_delivery(
        "job-1",
        actor_id="actor-1",
        session_id="session-other",
        candidate_fingerprint="fp-1",
        delivery_result=DeliveryAttemptResult(DeliveryAttemptStatus.ACKNOWLEDGED),
    )
    assert result.code is LifecycleOutcomeCode.NOT_FOUND
    fetched = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.last_delivery_fingerprint is None
