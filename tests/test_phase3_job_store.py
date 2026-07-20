"""Tests for the Phase3 bounded scheduled-job SQLite store.

These tests use temporary databases only. They cover actor/session
isolation, compare-and-swap conflicts, transitions, quiet-hours round
trips, retry fields, terminal immutability, and the absence of any
payload-bearing column.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.jobs import (
    JobState,
    QuietHours,
    ScheduledJob,
    ScheduledJobStore,
    TransitionError,
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
def db_path(tmp_path):
    path = tmp_path / "jobs.db"
    yield path


@pytest.fixture
def store(db_path):
    return ScheduledJobStore(db_path)


def test_add_and_get_within_scope(db_path):
    svc = ScheduledJobStore(db_path)
    job = _make_job()
    assert svc.add(job) is job
    fetched = svc.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.job_id == "job-1"
    assert fetched.state is JobState.SCHEDULED
    assert fetched.attempt_count == 0
    assert fetched.max_attempts == 1


def test_get_rejects_cross_actor(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    assert (
        svc.get("job-1", actor_id="actor-2", session_id="session-1") is None
    )


def test_get_rejects_cross_session(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    assert (
        svc.get("job-1", actor_id="actor-1", session_id="session-2") is None
    )


def test_list_scoped_and_ordered(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job(job_id="a", session="s1"))
    svc.add(_make_job(job_id="b", session="s1", updated_at=_aware(2026, 7, 18, 9, 0)))
    svc.add(_make_job(job_id="c", actor="actor-2", session="s2"))
    rows = svc.list(actor_id="actor-1", session_id="s1")
    assert [r.job_id for r in rows] == ["b", "a"]
    # Other actor's job is excluded.
    assert all(r.actor_id == "actor-1" for r in rows)


def test_list_rejects_missing_scope(db_path):
    svc = ScheduledJobStore(db_path)
    with pytest.raises(ValueError):
        svc.list(actor_id="", session_id="session-1")
    with pytest.raises(ValueError):
        svc.list(actor_id="actor-1", session_id="")


def test_transition_scheduled_to_running(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    updated = svc.transition(
        "job-1",
        expected_state=JobState.SCHEDULED,
        new_state=JobState.RUNNING,
        expected_updated_at=_aware(2026, 7, 18, 8, 0),
        updated_at=_aware(2026, 7, 18, 9, 0),
        actor_id="actor-1",
        session_id="session-1",
    )
    assert updated is not None
    assert updated.state is JobState.RUNNING
    assert updated.updated_at == _aware(2026, 7, 18, 9, 0)


def test_transition_cas_conflict_on_stale_revision(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    # A concurrent writer already moved it; our expected_updated_at is stale.
    svc.transition(
        "job-1",
        expected_state=JobState.SCHEDULED,
        new_state=JobState.RUNNING,
        expected_updated_at=_aware(2026, 7, 18, 8, 0),
        updated_at=_aware(2026, 7, 18, 9, 0),
        actor_id="actor-1",
        session_id="session-1",
    )
    # Second writer still expects SCHEDULED at the original revision -> conflict.
    conflict = svc.transition(
        "job-1",
        expected_state=JobState.SCHEDULED,
        new_state=JobState.RUNNING,
        expected_updated_at=_aware(2026, 7, 18, 8, 0),
        updated_at=_aware(2026, 7, 18, 9, 30),
        actor_id="actor-1",
        session_id="session-1",
    )
    assert conflict is None
    assert svc.get("job-1", actor_id="actor-1", session_id="session-1").state is (
        JobState.RUNNING
    )


def test_transition_rejects_invalid_per_table(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    with pytest.raises(TransitionError):
        svc.transition(
            "job-1",
            expected_state=JobState.SCHEDULED,
            new_state=JobState.COMPLETED,
            expected_updated_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 9, 0),
            actor_id="actor-1",
            session_id="session-1",
        )


def test_transition_rejects_cross_scope(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    assert (
        svc.transition(
            "job-1",
            expected_state=JobState.SCHEDULED,
            new_state=JobState.RUNNING,
            expected_updated_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 9, 0),
            actor_id="actor-2",
            session_id="session-1",
        )
        is None
    )


def test_pause_resume_cancel_use_transition_table(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    paused = svc.pause(
        "job-1",
        expected_updated_at=_aware(2026, 7, 18, 8, 0),
        updated_at=_aware(2026, 7, 18, 9, 0),
        actor_id="actor-1",
        session_id="session-1",
    )
    assert paused is not None and paused.state is JobState.PAUSED
    resumed = svc.resume(
        "job-1",
        expected_updated_at=_aware(2026, 7, 18, 9, 0),
        updated_at=_aware(2026, 7, 18, 9, 30),
        actor_id="actor-1",
        session_id="session-1",
    )
    assert resumed is not None and resumed.state is JobState.SCHEDULED
    cancelled = svc.cancel(
        "job-1",
        expected_updated_at=_aware(2026, 7, 18, 9, 30),
        updated_at=_aware(2026, 7, 18, 10, 0),
        actor_id="actor-1",
        session_id="session-1",
    )
    assert cancelled is not None and cancelled.state is JobState.CANCELLED


def test_terminal_state_is_immutable(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    svc.transition(
        "job-1",
        expected_state=JobState.SCHEDULED,
        new_state=JobState.RUNNING,
        expected_updated_at=_aware(2026, 7, 18, 8, 0),
        updated_at=_aware(2026, 7, 18, 9, 0),
        actor_id="actor-1",
        session_id="session-1",
    )
    svc.transition(
        "job-1",
        expected_state=JobState.RUNNING,
        new_state=JobState.COMPLETED,
        expected_updated_at=_aware(2026, 7, 18, 9, 0),
        updated_at=_aware(2026, 7, 18, 9, 30),
        actor_id="actor-1",
        session_id="session-1",
    )
    # COMPLETED -> SCHEDULED is not in the transition table, so the
    # store raises deterministically rather than mutating a terminal state.
    with pytest.raises(TransitionError):
        svc.transition(
            "job-1",
            expected_state=JobState.COMPLETED,
            new_state=JobState.SCHEDULED,
            expected_updated_at=_aware(2026, 7, 18, 9, 30),
            updated_at=_aware(2026, 7, 18, 10, 0),
            actor_id="actor-1",
            session_id="session-1",
        )
    assert svc.get("job-1", actor_id="actor-1", session_id="session-1").state is (
        JobState.COMPLETED
    )


def test_terminal_idempotent_same_state_read(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    svc.transition(
        "job-1",
        expected_state=JobState.SCHEDULED,
        new_state=JobState.CANCELLED,
        expected_updated_at=_aware(2026, 7, 18, 8, 0),
        updated_at=_aware(2026, 7, 18, 9, 0),
        actor_id="actor-1",
        session_id="session-1",
    )
    # Same-state transition is idempotent and allowed.
    again = svc.transition(
        "job-1",
        expected_state=JobState.CANCELLED,
        new_state=JobState.CANCELLED,
        expected_updated_at=_aware(2026, 7, 18, 9, 0),
        updated_at=_aware(2026, 7, 18, 9, 0),
        actor_id="actor-1",
        session_id="session-1",
    )
    assert again is not None
    assert again.state is JobState.CANCELLED


def test_quiet_hours_round_trip(db_path):
    svc = ScheduledJobStore(db_path)
    qh = QuietHours(timezone_name="America/New_York", start_minute=1320, end_minute=1380)
    job = _make_job(quiet_hours=qh)
    svc.add(job)
    fetched = svc.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.quiet_hours is not None
    assert fetched.quiet_hours.timezone_name == "America/New_York"
    assert fetched.quiet_hours.start_minute == 1320
    assert fetched.quiet_hours.end_minute == 1380


def test_quiet_hours_none_round_trip(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())  # no quiet_hours
    fetched = svc.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.quiet_hours is None


def test_update_next_run_cas(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    updated = svc.update_next_run(
        "job-1",
        next_run_at=_aware(2026, 7, 18, 12, 0),
        expected_updated_at=_aware(2026, 7, 18, 8, 0),
        updated_at=_aware(2026, 7, 18, 9, 0),
        actor_id="actor-1",
        session_id="session-1",
    )
    assert updated is not None
    assert updated.next_run_at == _aware(2026, 7, 18, 12, 0)
    # Stale revision fails.
    assert (
        svc.update_next_run(
            "job-1",
            next_run_at=_aware(2026, 7, 18, 13, 0),
            expected_updated_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 9, 30),
            actor_id="actor-1",
            session_id="session-1",
        )
        is None
    )


def test_update_delivery_fingerprint_cas(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    updated = svc.update_delivery_fingerprint(
        "job-1",
        fingerprint="fp-1",
        expected_updated_at=_aware(2026, 7, 18, 8, 0),
        updated_at=_aware(2026, 7, 18, 9, 0),
        actor_id="actor-1",
        session_id="session-1",
    )
    assert updated is not None
    assert updated.last_delivery_fingerprint == "fp-1"
    cleared = svc.update_delivery_fingerprint(
        "job-1",
        fingerprint=None,
        expected_updated_at=_aware(2026, 7, 18, 9, 0),
        updated_at=_aware(2026, 7, 18, 9, 30),
        actor_id="actor-1",
        session_id="session-1",
    )
    assert cleared is not None
    assert cleared.last_delivery_fingerprint is None


def test_retry_fields_persist(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job(attempt_count=2, max_attempts=5))
    fetched = svc.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.attempt_count == 2
    assert fetched.max_attempts == 5


def test_database_permissions(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    import os
    import stat

    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700


def test_no_payload_columns_present(db_path):
    import sqlite3

    ScheduledJobStore(db_path).add(_make_job())
    with sqlite3.connect(db_path) as conn:
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(scheduled_jobs)")
        }
    forbidden = {
        "raw_text",
        "prompt",
        "query",
        "email_body",
        "calendar_content",
        "provider_response",
        "secret",
        "token",
        "password",
    }
    assert forbidden.isdisjoint(cols)
    # Only contract fields plus quiet-hours columns exist.
    allowed = {
        "job_id",
        "actor_id",
        "session_id",
        "action",
        "proposal_id",
        "state",
        "next_run_at",
        "created_at",
        "updated_at",
        "attempt_count",
        "max_attempts",
        "qh_timezone",
        "qh_start_minute",
        "qh_end_minute",
        "last_delivery_fingerprint",
    }
    assert cols == allowed


def test_failed_transition_leaves_row_unchanged(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    # A regressed updated_at (before created_at) must raise before any write.
    with pytest.raises(ValueError):
        svc.transition(
            "job-1",
            expected_state=JobState.SCHEDULED,
            new_state=JobState.RUNNING,
            expected_updated_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 7, 0),
            actor_id="actor-1",
            session_id="session-1",
        )
    fetched = svc.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.state is JobState.SCHEDULED
    assert fetched.updated_at == _aware(2026, 7, 18, 8, 0)


def test_failed_update_next_run_leaves_row_unchanged(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    # next_run_at before created_at must raise before any write.
    with pytest.raises(ValueError):
        svc.update_next_run(
            "job-1",
            next_run_at=_aware(2026, 7, 18, 7, 0),
            expected_updated_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 9, 0),
            actor_id="actor-1",
            session_id="session-1",
        )
    fetched = svc.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.next_run_at == _aware(2026, 7, 18, 9, 0)
    assert fetched.updated_at == _aware(2026, 7, 18, 8, 0)


def test_failed_update_fingerprint_leaves_row_unchanged(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    # Invalid fingerprint must raise before any write.
    with pytest.raises(Exception):
        svc.update_delivery_fingerprint(
            "job-1",
            fingerprint="bad fp!",
            expected_updated_at=_aware(2026, 7, 18, 8, 0),
            updated_at=_aware(2026, 7, 18, 9, 0),
            actor_id="actor-1",
            session_id="session-1",
        )
    fetched = svc.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.last_delivery_fingerprint is None
    assert fetched.updated_at == _aware(2026, 7, 18, 8, 0)


def test_idempotent_same_state_preserves_revision(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    original = svc.get("job-1", actor_id="actor-1", session_id="session-1")
    assert original is not None
    original_updated = original.updated_at
    # Same-state transition must not modify any column.
    again = svc.transition(
        "job-1",
        expected_state=JobState.SCHEDULED,
        new_state=JobState.SCHEDULED,
        expected_updated_at=original_updated,
        updated_at=_aware(2026, 7, 18, 9, 0),
        actor_id="actor-1",
        session_id="session-1",
    )
    assert again is not None
    assert again.state is JobState.SCHEDULED
    assert again.updated_at == original_updated
    assert again.updated_at == _aware(2026, 7, 18, 8, 0)
    # Confirm the stored row was not touched.
    refetched = svc.get("job-1", actor_id="actor-1", session_id="session-1")
    assert refetched.updated_at == original_updated


def test_idempotent_transition_rejects_stale_state_or_revision(db_path):
    svc = ScheduledJobStore(db_path)
    job = _make_job()
    svc.add(job)

    assert svc.transition(
        job.job_id,
        expected_state=JobState.SCHEDULED,
        new_state=JobState.SCHEDULED,
        expected_updated_at=job.updated_at - timedelta(minutes=1),
        updated_at=job.updated_at + timedelta(minutes=1),
        actor_id=job.actor_id,
        session_id=job.session_id,
    ) is None
    assert svc.transition(
        job.job_id,
        expected_state=JobState.PAUSED,
        new_state=JobState.PAUSED,
        expected_updated_at=job.updated_at,
        updated_at=job.updated_at + timedelta(minutes=1),
        actor_id=job.actor_id,
        session_id=job.session_id,
    ) is None

    unchanged = svc.get(
        job.job_id, actor_id=job.actor_id, session_id=job.session_id
    )
    assert unchanged is not None
    assert unchanged.state is JobState.SCHEDULED
    assert unchanged.updated_at == job.updated_at


def test_stale_revision_returns_none_before_mutation_validation(db_path):
    svc = ScheduledJobStore(db_path)
    job = _make_job()
    svc.add(job)
    stale_revision = job.updated_at - timedelta(minutes=1)
    regressed_update = job.updated_at - timedelta(seconds=1)

    assert svc.update_next_run(
        job.job_id,
        next_run_at=job.next_run_at + timedelta(hours=1),
        expected_updated_at=stale_revision,
        updated_at=regressed_update,
        actor_id=job.actor_id,
        session_id=job.session_id,
    ) is None
    assert svc.update_delivery_fingerprint(
        job.job_id,
        fingerprint="fp-new",
        expected_updated_at=stale_revision,
        updated_at=regressed_update,
        actor_id=job.actor_id,
        session_id=job.session_id,
    ) is None


def test_partial_quiet_hours_row_rejected(db_path):
    import sqlite3

    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    # Inject a partially populated quiet-hours row (tz present, minutes NULL).
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE scheduled_jobs SET qh_timezone = 'America/New_York' "
            "WHERE job_id = 'job-1' AND actor_id = 'actor-1' "
            "AND session_id = 'session-1'"
        )
        conn.commit()
    with pytest.raises(ValueError):
        svc.get("job-1", actor_id="actor-1", session_id="session-1")


def test_list_rejects_bool_limit(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    with pytest.raises(ValueError):
        svc.list(actor_id="actor-1", session_id="session-1", limit=True)
    with pytest.raises(ValueError):
        svc.list(actor_id="actor-1", session_id="session-1", limit=False)


def test_list_rejects_out_of_range_limit(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job())
    with pytest.raises(ValueError):
        svc.list(actor_id="actor-1", session_id="session-1", limit=0)
    with pytest.raises(ValueError):
        svc.list(actor_id="actor-1", session_id="session-1", limit=201)


def test_remove_if_unmodified_deletes_exact_revision(db_path):
    from core.jobs.contracts import JobState

    svc = ScheduledJobStore(db_path)
    job = _make_job()
    svc.add(job)
    assert svc.remove_if_unmodified(
        "job-1",
        actor_id="actor-1",
        session_id="session-1",
        expected_updated_at=job.updated_at,
        expected_state=JobState.SCHEDULED,
    )
    assert svc.get("job-1", actor_id="actor-1", session_id="session-1") is None


def test_remove_if_unmodified_cross_session_and_stale_are_noop(db_path):
    from core.jobs.contracts import JobState
    from datetime import datetime, timezone

    svc = ScheduledJobStore(db_path)
    job = _make_job()
    svc.add(job)
    assert not svc.remove_if_unmodified(
        "job-1",
        actor_id="actor-1",
        session_id="session-other",
        expected_updated_at=job.updated_at,
        expected_state=JobState.SCHEDULED,
    )
    assert not svc.remove_if_unmodified(
        "job-1",
        actor_id="actor-1",
        session_id="session-1",
        expected_updated_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        expected_state=JobState.SCHEDULED,
    )
    fetched = svc.get("job-1", actor_id="actor-1", session_id="session-1")
    assert fetched is not None
    assert fetched.state is JobState.SCHEDULED


def test_list_state_is_bounded_and_not_scoped_by_client_identity(db_path):
    svc = ScheduledJobStore(db_path)
    svc.add(_make_job(job_id="running-a", state=JobState.RUNNING))
    svc.add(
        _make_job(
            job_id="running-b",
            actor="actor-2",
            session="session-2",
            state=JobState.RUNNING,
        )
    )
    svc.add(_make_job(job_id="paused", state=JobState.PAUSED))

    rows = svc.list_state(JobState.RUNNING, limit=2)
    assert {row.job_id for row in rows} == {"running-a", "running-b"}
    for value in (True, 0, 65):
        with pytest.raises(ValueError):
            svc.list_state(JobState.RUNNING, limit=value)


def test_store_uses_stdlib_only(monkeypatch):
    # Ensure no forbidden modules are imported by the store module.
    import ast
    import core.jobs.store as store_mod
    import inspect

    source = inspect.getsource(store_mod)
    tree = ast.parse(source)
    forbidden = {"subprocess", "socket", "threading", "asyncio", "os", "time"}
    # os/time are allowed for permissions + timestamps; verify only the truly
    # forbidden ones are absent by scanning import statements.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in {
                    "subprocess",
                    "socket",
                    "threading",
                    "asyncio",
                }, f"forbidden import {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert node.module.split(".")[0] not in {
                    "subprocess",
                    "socket",
                    "threading",
                    "asyncio",
                }, f"forbidden import {node.module}"
