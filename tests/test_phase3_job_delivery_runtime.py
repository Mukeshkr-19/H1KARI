"""Deterministic tests for the scheduled-job delivery runtime boundary."""

from __future__ import annotations

from datetime import datetime, timezone

from core.jobs.audit_store import ScheduledJobAuditStore
from core.jobs.contracts import JobState, ScheduledJob
from core.jobs.delivery import DeliveryAttemptResult, DeliveryAttemptStatus
from core.jobs.delivery_runtime import (
    DeliveryRuntimeCode,
    DeliveryRuntimeResult,
    MeaningfulChangeDeliveryRuntime,
)
from core.jobs.lifecycle import JobLifecycleController
from core.jobs.quiet_hours import QuietHours
from core.jobs.store import ScheduledJobStore


UTC = timezone.utc
NOW = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)


class Recorder:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls = []

    def __call__(self, snapshot):
        self.calls.append(snapshot)
        return self.result


def _job(**overrides) -> ScheduledJob:
    values = dict(
        job_id="job-1",
        actor_id="actor-1",
        session_id="session-1",
        action="digest",
        proposal_id="proposal-1",
        state=JobState.SCHEDULED,
        next_run_at=datetime(2026, 7, 20, 16, 0, tzinfo=UTC),
        created_at=datetime(2026, 7, 20, 14, 0, tzinfo=UTC),
        updated_at=datetime(2026, 7, 20, 14, 0, tzinfo=UTC),
    )
    values.update(overrides)
    return ScheduledJob(**values)


def _runtime(tmp_path, recorder, *, job=None):
    store = ScheduledJobStore(tmp_path / "jobs.db")
    audit = ScheduledJobAuditStore(tmp_path / "audit.db")
    counter = {"value": 0}

    def event_id():
        counter["value"] += 1
        return f"event-{counter['value']}"

    lifecycle = JobLifecycleController(store, audit, lambda: NOW, event_id)
    if job is not None:
        store.add(job)
    return (
        MeaningfulChangeDeliveryRuntime(store, lifecycle, lambda: NOW, recorder),
        store,
    )


def test_positive_acknowledgement_updates_fingerprint(tmp_path):
    recorder = Recorder(DeliveryAttemptResult(DeliveryAttemptStatus.ACKNOWLEDGED))
    runtime, store = _runtime(tmp_path, recorder, job=_job())
    result = runtime.deliver_change(
        "job-1",
        actor_id="actor-1",
        session_id="session-1",
        candidate_fingerprint="fingerprint-1",
    )
    assert result.code is DeliveryRuntimeCode.ACKNOWLEDGED
    assert len(recorder.calls) == 1
    saved = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert saved is not None
    assert saved.last_delivery_fingerprint == "fingerprint-1"


def test_quiet_hours_suppress_before_delivery(tmp_path):
    quiet = QuietHours(timezone_name="UTC", start_minute=14 * 60, end_minute=16 * 60)
    recorder = Recorder(DeliveryAttemptResult(DeliveryAttemptStatus.ACKNOWLEDGED))
    runtime, store = _runtime(tmp_path, recorder, job=_job(quiet_hours=quiet))
    result = runtime.deliver_change(
        "job-1",
        actor_id="actor-1",
        session_id="session-1",
        candidate_fingerprint="fingerprint-1",
    )
    assert result.code is DeliveryRuntimeCode.SUPPRESSED
    assert recorder.calls == []
    saved = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert saved is not None and saved.last_delivery_fingerprint is None


def test_rejected_failed_and_unknown_results_do_not_update(tmp_path):
    for index, attempt in enumerate(
        (
            DeliveryAttemptResult(DeliveryAttemptStatus.REJECTED),
            DeliveryAttemptResult(DeliveryAttemptStatus.FAILED),
            object(),
        )
    ):
        recorder = Recorder(attempt)
        runtime, store = _runtime(
            tmp_path / str(index), recorder, job=_job(job_id=f"job-{index}")
        )
        result = runtime.deliver_change(
            f"job-{index}",
            actor_id="actor-1",
            session_id="session-1",
            candidate_fingerprint=f"fingerprint-{index}",
        )
        assert result.code is DeliveryRuntimeCode.FAILED
        saved = store.get(
            f"job-{index}", actor_id="actor-1", session_id="session-1"
        )
        assert saved is not None and saved.last_delivery_fingerprint is None


def test_delivery_exception_does_not_update(tmp_path):
    def explode(_snapshot):
        raise RuntimeError("private detail")

    runtime, store = _runtime(tmp_path, explode, job=_job())
    result = runtime.deliver_change(
        "job-1",
        actor_id="actor-1",
        session_id="session-1",
        candidate_fingerprint="fingerprint-1",
    )
    assert result.code is DeliveryRuntimeCode.FAILED
    saved = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert saved is not None and saved.last_delivery_fingerprint is None
    assert "private" not in repr(result)


def test_cross_session_and_missing_are_indistinguishable(tmp_path):
    recorder = Recorder(DeliveryAttemptResult(DeliveryAttemptStatus.ACKNOWLEDGED))
    runtime, store = _runtime(tmp_path, recorder, job=_job())
    cross = runtime.deliver_change(
        "job-1",
        actor_id="actor-1",
        session_id="session-2",
        candidate_fingerprint="fingerprint-1",
    )
    missing = runtime.deliver_change(
        "missing",
        actor_id="actor-1",
        session_id="session-2",
        candidate_fingerprint="fingerprint-1",
    )
    assert cross == missing == DeliveryRuntimeResult(DeliveryRuntimeCode.NOT_FOUND)
    assert recorder.calls == []
    saved = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert saved is not None and saved.last_delivery_fingerprint is None


def test_cross_actor_is_not_delivered(tmp_path):
    recorder = Recorder(DeliveryAttemptResult(DeliveryAttemptStatus.ACKNOWLEDGED))
    runtime, store = _runtime(tmp_path, recorder, job=_job())
    result = runtime.deliver_change(
        "job-1",
        actor_id="actor-2",
        session_id="session-1",
        candidate_fingerprint="fingerprint-1",
    )
    assert result.code is DeliveryRuntimeCode.NOT_FOUND
    assert recorder.calls == []
    saved = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert saved is not None and saved.last_delivery_fingerprint is None


def test_unchanged_and_terminal_skip_delivery(tmp_path):
    recorder = Recorder(DeliveryAttemptResult(DeliveryAttemptStatus.ACKNOWLEDGED))
    runtime, _ = _runtime(
        tmp_path / "same",
        recorder,
        job=_job(last_delivery_fingerprint="fingerprint-1"),
    )
    unchanged = runtime.deliver_change(
        "job-1",
        actor_id="actor-1",
        session_id="session-1",
        candidate_fingerprint="fingerprint-1",
    )
    assert unchanged.code is DeliveryRuntimeCode.UNCHANGED
    assert recorder.calls == []

    terminal_recorder = Recorder(
        DeliveryAttemptResult(DeliveryAttemptStatus.ACKNOWLEDGED)
    )
    terminal_runtime, _ = _runtime(
        tmp_path / "terminal",
        terminal_recorder,
        job=_job(state=JobState.COMPLETED),
    )
    terminal = terminal_runtime.deliver_change(
        "job-1",
        actor_id="actor-1",
        session_id="session-1",
        candidate_fingerprint="fingerprint-2",
    )
    assert terminal.code is DeliveryRuntimeCode.TERMINAL
    assert terminal_recorder.calls == []


def test_invalid_fingerprint_and_clock_fail_closed(tmp_path):
    recorder = Recorder(DeliveryAttemptResult(DeliveryAttemptStatus.ACKNOWLEDGED))
    runtime, _ = _runtime(tmp_path, recorder, job=_job())
    invalid = runtime.deliver_change(
        "job-1",
        actor_id="actor-1",
        session_id="session-1",
        candidate_fingerprint="bad value",
    )
    assert invalid.code is DeliveryRuntimeCode.FAILED
    assert recorder.calls == []

    store = ScheduledJobStore(tmp_path / "clock-jobs.db")
    audit = ScheduledJobAuditStore(tmp_path / "clock-audit.db")
    store.add(_job())
    lifecycle = JobLifecycleController(store, audit, lambda: NOW, lambda: "event-1")
    bad_clock_runtime = MeaningfulChangeDeliveryRuntime(
        store, lifecycle, lambda: datetime(2026, 7, 20), recorder
    )
    unavailable = bad_clock_runtime.deliver_change(
        "job-1",
        actor_id="actor-1",
        session_id="session-1",
        candidate_fingerprint="fingerprint-1",
    )
    assert unavailable.code is DeliveryRuntimeCode.UNAVAILABLE
    assert recorder.calls == []


def test_result_repr_is_content_free():
    result = DeliveryRuntimeResult(DeliveryRuntimeCode.FAILED)
    text = repr(result)
    for forbidden in ("job-1", "actor-1", "session-1", "fingerprint"):
        assert forbidden not in text


def test_unknown_lifecycle_result_does_not_claim_acknowledgement(
    tmp_path, monkeypatch
):
    recorder = Recorder(DeliveryAttemptResult(DeliveryAttemptStatus.ACKNOWLEDGED))
    runtime, store = _runtime(tmp_path, recorder, job=_job())
    monkeypatch.setattr(runtime._lifecycle, "acknowledge_delivery", lambda *a, **k: object())
    result = runtime.deliver_change(
        "job-1",
        actor_id="actor-1",
        session_id="session-1",
        candidate_fingerprint="fingerprint-1",
    )
    assert result.code is DeliveryRuntimeCode.UNAVAILABLE
    saved = store.get("job-1", actor_id="actor-1", session_id="session-1")
    assert saved is not None and saved.last_delivery_fingerprint is None


def test_source_has_no_concrete_delivery_or_logging_imports():
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "core"
        / "jobs"
        / "delivery_runtime.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "import requests",
        "import urllib",
        "import subprocess",
        "import logging",
        "import socket",
        "import smtplib",
        "import os",
    ):
        assert forbidden not in source
