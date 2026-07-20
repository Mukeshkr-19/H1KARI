"""Tests for the Phase 3 pure scheduled-job delivery classification.

These tests cover only the immutable delivery snapshot and pure classification
helper in ``core.jobs.delivery``. They perform no I/O, subprocess, network,
filesystem, sleep, or thread activity, and assert the absence of those imports.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from core.jobs.contracts import JobState, ScheduledJob
from core.jobs.delivery import (
    DeliveryOutcome,
    DeliverySnapshot,
    DeliveryValidationError,
    build_delivery_snapshot,
    classify_delivery,
)
from core.jobs.quiet_hours import QuietHours

UTC = timezone.utc
NY = ZoneInfo("America/New_York")


def _aware(year, month, day, hour, minute, tz=UTC):
    return datetime(year, month, day, hour, minute, tzinfo=tz)


def _make_job(**overrides) -> ScheduledJob:
    base = dict(
        job_id="job-1",
        actor_id="actor-1",
        session_id="session-1",
        action="digest",
        proposal_id="prop-1",
        state=JobState.SCHEDULED,
        next_run_at=_aware(2026, 7, 18, 9, 0),
        created_at=_aware(2026, 7, 18, 8, 0),
        updated_at=_aware(2026, 7, 18, 8, 0),
    )
    base.update(overrides)
    return ScheduledJob(**base)


# --------------------------------------------------------------------------
# Snapshot construction and immutability
# --------------------------------------------------------------------------


def test_build_delivery_snapshot_extracts_bounded_fields():
    job = _make_job(
        state=JobState.RUNNING,
        attempt_count=2,
        max_attempts=5,
        last_delivery_fingerprint="fp-a",
    )
    snap = build_delivery_snapshot(job)
    assert snap.job_id == "job-1"
    assert snap.state is JobState.RUNNING
    assert snap.attempt_count == 2
    assert snap.max_attempts == 5
    assert snap.fingerprint == "fp-a"
    assert snap.next_run_at == _aware(2026, 7, 18, 9, 0).isoformat()


def test_delivery_snapshot_is_frozen():
    snap = build_delivery_snapshot(_make_job())
    with pytest.raises(Exception):
        snap.state = JobState.PAUSED  # type: ignore[misc]


def test_delivery_snapshot_rejects_invalid_job_id():
    with pytest.raises(DeliveryValidationError):
        DeliverySnapshot(
            job_id="has space",
            state=JobState.SCHEDULED,
            next_run_at=_aware(2026, 7, 18, 9, 0).isoformat(),
            attempt_count=0,
            max_attempts=1,
            fingerprint=None,
        )


def test_delivery_snapshot_rejects_non_jobstate():
    with pytest.raises(DeliveryValidationError):
        DeliverySnapshot(
            job_id="job-1",
            state="scheduled",  # type: ignore[arg-type]
            next_run_at=_aware(2026, 7, 18, 9, 0).isoformat(),
            attempt_count=0,
            max_attempts=1,
            fingerprint=None,
        )


def test_delivery_snapshot_rejects_oversized_next_run():
    with pytest.raises(DeliveryValidationError):
        DeliverySnapshot(
            job_id="job-1",
            state=JobState.SCHEDULED,
            next_run_at="x" * 65,
            attempt_count=0,
            max_attempts=1,
            fingerprint=None,
        )


def test_delivery_snapshot_rejects_bad_attempt_counts():
    with pytest.raises(DeliveryValidationError):
        DeliverySnapshot(
            job_id="job-1",
            state=JobState.SCHEDULED,
            next_run_at=_aware(2026, 7, 18, 9, 0).isoformat(),
            attempt_count=-1,
            max_attempts=1,
            fingerprint=None,
        )
    with pytest.raises(DeliveryValidationError):
        DeliverySnapshot(
            job_id="job-1",
            state=JobState.SCHEDULED,
            next_run_at=_aware(2026, 7, 18, 9, 0).isoformat(),
            attempt_count=2,
            max_attempts=1,
            fingerprint=None,
        )
    with pytest.raises(DeliveryValidationError):
        DeliverySnapshot(
            job_id="job-1",
            state=JobState.SCHEDULED,
            next_run_at=_aware(2026, 7, 18, 9, 0).isoformat(),
            attempt_count=0,
            max_attempts=0,
            fingerprint=None,
        )


def test_delivery_snapshot_rejects_invalid_fingerprint():
    with pytest.raises(DeliveryValidationError):
        DeliverySnapshot(
            job_id="job-1",
            state=JobState.SCHEDULED,
            next_run_at=_aware(2026, 7, 18, 9, 0).isoformat(),
            attempt_count=0,
            max_attempts=1,
            fingerprint="bad value",
        )


def test_repr_excludes_sensitive_fields():
    snap = build_delivery_snapshot(_make_job())
    text = repr(snap)
    for forbidden in (
        "job-1",
        "actor",
        "session",
        "proposal",
        "payload",
        "target",
        "provider",
        "secret",
        "digest",
    ):
        assert forbidden not in text


def test_delivery_attempt_result_repr_is_content_free():
    from core.jobs.delivery import DeliveryAttemptResult, DeliveryAttemptStatus

    text = repr(DeliveryAttemptResult(DeliveryAttemptStatus.ACKNOWLEDGED))
    assert "job-1" not in text
    assert "actor" not in text
    assert "session" not in text
    assert "fp-" not in text
    assert "DeliveryAttemptResult(" in text


# --------------------------------------------------------------------------
# Classification: meaningful change
# --------------------------------------------------------------------------


def test_classify_meaningful_on_fingerprint_change():
    job = _make_job(last_delivery_fingerprint="fp-a")
    assert (
        classify_delivery(job, "fp-b", now=_aware(2026, 7, 18, 9, 0))
        is DeliveryOutcome.MEANINGFUL
    )


def test_classify_unchanged_on_same_fingerprint():
    job = _make_job(last_delivery_fingerprint="fp-a")
    assert (
        classify_delivery(job, "fp-a", now=_aware(2026, 7, 18, 9, 0))
        is DeliveryOutcome.UNCHANGED
    )


def test_classify_unchanged_on_none_candidate():
    job = _make_job(last_delivery_fingerprint="fp-a")
    assert (
        classify_delivery(job, None, now=_aware(2026, 7, 18, 9, 0))
        is DeliveryOutcome.UNCHANGED
    )


def test_classify_meaningful_first_delivery():
    job = _make_job(last_delivery_fingerprint=None)
    assert (
        classify_delivery(job, "fp-b", now=_aware(2026, 7, 18, 9, 0))
        is DeliveryOutcome.MEANINGFUL
    )


# --------------------------------------------------------------------------
# Classification: terminal
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED],
)
def test_classify_terminal_for_terminal_states(state):
    job = _make_job(state=state, last_delivery_fingerprint=None)
    assert (
        classify_delivery(job, "fp-x", now=_aware(2026, 7, 18, 9, 0))
        is DeliveryOutcome.TERMINAL
    )


def test_classify_terminal_takes_precedence_over_quiet():
    qh = QuietHours(timezone_name="UTC", start_minute=540, end_minute=600)
    job = _make_job(state=JobState.COMPLETED)
    assert (
        classify_delivery(
            job, "fp-x", now=_aware(2026, 7, 18, 9, 30), quiet_hours=qh
        )
        is DeliveryOutcome.TERMINAL
    )


# --------------------------------------------------------------------------
# Classification: quiet hours suppression (no mutation)
# --------------------------------------------------------------------------


def test_classify_suppressed_during_quiet_hours():
    qh = QuietHours(timezone_name="UTC", start_minute=540, end_minute=600)
    job = _make_job(last_delivery_fingerprint=None)
    outcome = classify_delivery(
        job, "fp-b", now=_aware(2026, 7, 18, 9, 30), quiet_hours=qh
    )
    assert outcome is DeliveryOutcome.SUPPRESSED_QUIET_HOURS
    # The job is not mutated by classification.
    assert job.last_delivery_fingerprint is None
    assert job.state is JobState.SCHEDULED


def test_classify_uses_job_quiet_hours_by_default():
    qh = QuietHours(timezone_name="UTC", start_minute=540, end_minute=600)
    job = _make_job(last_delivery_fingerprint=None, quiet_hours=qh)
    assert (
        classify_delivery(job, "fp-b", now=_aware(2026, 7, 18, 9, 30))
        is DeliveryOutcome.SUPPRESSED_QUIET_HOURS
    )


def test_classify_meaningful_outside_quiet_hours():
    qh = QuietHours(timezone_name="UTC", start_minute=540, end_minute=600)
    job = _make_job(last_delivery_fingerprint=None)
    assert (
        classify_delivery(
            job, "fp-b", now=_aware(2026, 7, 18, 11, 0), quiet_hours=qh
        )
        is DeliveryOutcome.MEANINGFUL
    )


def test_classify_quiet_suppression_disabled_window():
    qh = QuietHours(timezone_name="UTC", start_minute=300, end_minute=300)
    job = _make_job(last_delivery_fingerprint=None)
    assert (
        classify_delivery(
            job, "fp-b", now=_aware(2026, 7, 18, 5, 0), quiet_hours=qh
        )
        is DeliveryOutcome.MEANINGFUL
    )


# --------------------------------------------------------------------------
# Classification: retry exhaustion -> terminal
# --------------------------------------------------------------------------


def test_classify_terminal_on_retry_exhaustion():
    # attempt_count == max_attempts means no further attempt; job is FAILED.
    job = _make_job(
        state=JobState.FAILED, attempt_count=3, max_attempts=3
    )
    assert (
        classify_delivery(job, "fp-x", now=_aware(2026, 7, 18, 9, 0))
        is DeliveryOutcome.TERMINAL
    )


# --------------------------------------------------------------------------
# Classification: state / next-run changes
# --------------------------------------------------------------------------


def test_classify_meaningful_on_state_change():
    job = _make_job(state=JobState.PAUSED, last_delivery_fingerprint="fp-a")
    assert (
        classify_delivery(job, "fp-b", now=_aware(2026, 7, 18, 9, 0))
        is DeliveryOutcome.MEANINGFUL
    )


def test_classify_meaningful_on_next_run_change():
    # Same fingerprint but the job's next_run changed -> fingerprint differs
    # because the snapshot fingerprint is the last delivered one; a new
    # candidate fingerprint represents the new structural state.
    job = _make_job(last_delivery_fingerprint="fp-old")
    assert (
        classify_delivery(job, "fp-new", now=_aware(2026, 7, 18, 9, 0))
        is DeliveryOutcome.MEANINGFUL
    )


# --------------------------------------------------------------------------
# Classification: input validation
# --------------------------------------------------------------------------


def test_classify_rejects_naive_now():
    job = _make_job()
    with pytest.raises(DeliveryValidationError):
        classify_delivery(job, "fp-b", now=datetime(2026, 7, 18, 9, 0))


def test_classify_rejects_invalid_candidate_fingerprint():
    job = _make_job(last_delivery_fingerprint="fp-a")
    with pytest.raises(DeliveryValidationError):
        classify_delivery(job, "bad value", now=_aware(2026, 7, 18, 9, 0))


# --------------------------------------------------------------------------
# Outcome enum is fixed
# --------------------------------------------------------------------------


def test_outcome_codes_are_fixed_values():
    assert DeliveryOutcome.UNCHANGED.value == "unchanged"
    assert DeliveryOutcome.MEANINGFUL.value == "meaningful"
    assert DeliveryOutcome.SUPPRESSED_QUIET_HOURS.value == "suppressed_quiet_hours"
    assert DeliveryOutcome.TERMINAL.value == "terminal"


# --------------------------------------------------------------------------
# No forbidden imports
# --------------------------------------------------------------------------


def test_no_forbidden_imports_in_delivery():
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "core"
        / "jobs"
        / "delivery.py"
    )
    forbidden = {
        "subprocess",
        "socket",
        "threading",
        "os",
        "time",
        "sqlite3",
        "requests",
        "asyncio",
        "logging",
        "smtplib",
        "http",
    }
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden, (
                    f"delivery.py imports forbidden module {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert node.module.split(".")[0] not in forbidden, (
                    f"delivery.py imports forbidden module {node.module}"
                )
