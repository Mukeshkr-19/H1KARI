"""Tests for the Phase3 pure scheduled-job and quiet-hours contracts.

These tests cover only the immutable value objects and pure helpers in
``core.jobs``. They perform no I/O, subprocess, network, filesystem, sleep,
or thread activity, and assert the absence of those imports.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from core.jobs import (
    IdentifierError,
    JobState,
    QuietHours,
    QuietHoursError,
    RetryBudgetExhausted,
    ScheduledJob,
    TransitionError,
    can_transition,
    delivery_is_meaningful_change,
    execution_is_eligible,
    is_quiet,
    retry_budget_remains,
    transition_table,
)

UTC = timezone.utc
EAT = ZoneInfo("Africa/Nairobi")  # UTC+3, no DST
NY = ZoneInfo("America/New_York")  # DST observer


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
# Transition table
# --------------------------------------------------------------------------

def test_transition_table_covers_all_states():
    assert set(transition_table) == set(JobState)


@pytest.mark.parametrize(
    "current,target",
    [
        (JobState.SCHEDULED, JobState.PAUSED),
        (JobState.SCHEDULED, JobState.RUNNING),
        (JobState.SCHEDULED, JobState.CANCELLED),
        (JobState.PAUSED, JobState.SCHEDULED),
        (JobState.PAUSED, JobState.CANCELLED),
        (JobState.RUNNING, JobState.INTERRUPTED),
        (JobState.RUNNING, JobState.COMPLETED),
        (JobState.RUNNING, JobState.FAILED),
        (JobState.RUNNING, JobState.CANCELLED),
        (JobState.INTERRUPTED, JobState.SCHEDULED),
        (JobState.INTERRUPTED, JobState.FAILED),
        (JobState.INTERRUPTED, JobState.CANCELLED),
    ],
)
def test_valid_transitions(current, target):
    assert can_transition(current, target) is True


@pytest.mark.parametrize(
    "current,target",
    [
        (JobState.SCHEDULED, JobState.COMPLETED),
        (JobState.SCHEDULED, JobState.FAILED),
        (JobState.SCHEDULED, JobState.INTERRUPTED),
        (JobState.PAUSED, JobState.RUNNING),
        (JobState.PAUSED, JobState.INTERRUPTED),
        (JobState.PAUSED, JobState.COMPLETED),
        (JobState.PAUSED, JobState.FAILED),
        (JobState.RUNNING, JobState.PAUSED),
        (JobState.RUNNING, JobState.SCHEDULED),
        (JobState.INTERRUPTED, JobState.PAUSED),
        (JobState.INTERRUPTED, JobState.RUNNING),
        (JobState.INTERRUPTED, JobState.COMPLETED),
        (JobState.COMPLETED, JobState.SCHEDULED),
        (JobState.COMPLETED, JobState.FAILED),
        (JobState.FAILED, JobState.SCHEDULED),
        (JobState.FAILED, JobState.RUNNING),
        (JobState.CANCELLED, JobState.SCHEDULED),
        (JobState.CANCELLED, JobState.RUNNING),
    ],
)
def test_invalid_transitions(current, target):
    assert can_transition(current, target) is False


def test_repeated_transition_is_idempotent():
    for state in JobState:
        assert can_transition(state, state) is True


def test_terminal_states_cannot_leave():
    for terminal in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
        for other in JobState:
            if other is terminal:
                continue
            assert can_transition(terminal, other) is False


def test_can_transition_rejects_non_enum():
    with pytest.raises(TransitionError):
        can_transition("scheduled", JobState.SCHEDULED)  # type: ignore[arg-type]
    with pytest.raises(TransitionError):
        can_transition(JobState.SCHEDULED, "running")  # type: ignore[arg-type]


def test_with_state_enforces_transition_table():
    job = _make_job()
    paused = job.with_state(JobState.PAUSED, updated_at=_aware(2026, 7, 18, 9, 1))
    assert paused.state is JobState.PAUSED
    assert paused.updated_at == _aware(2026, 7, 18, 9, 1)
    with pytest.raises(TransitionError):
        paused.with_state(JobState.COMPLETED, updated_at=_aware(2026, 7, 18, 9, 2))


def test_with_state_idempotent_repeat():
    job = _make_job(state=JobState.RUNNING)
    again = job.with_state(JobState.RUNNING, updated_at=_aware(2026, 7, 18, 9, 5))
    assert again.state is JobState.RUNNING


# --------------------------------------------------------------------------
# ScheduledJob immutability and validation
# --------------------------------------------------------------------------

def test_scheduled_job_is_frozen():
    job = _make_job()
    with pytest.raises(Exception):
        job.state = JobState.PAUSED  # type: ignore[misc]


def test_scheduled_job_rejects_naive_timestamps():
    with pytest.raises(ValueError):
        _make_job(next_run_at=datetime(2026, 7, 18, 9, 0))


def test_scheduled_job_rejects_bad_state_type():
    with pytest.raises(TransitionError):
        _make_job(state="scheduled")  # type: ignore[arg-type]


def test_scheduled_job_rejects_attempt_overflow():
    with pytest.raises(ValueError):
        _make_job(attempt_count=3, max_attempts=2)


def test_scheduled_job_rejects_negative_attempt():
    with pytest.raises(ValueError):
        _make_job(attempt_count=-1)


def test_scheduled_job_rejects_zero_max_attempts():
    with pytest.raises(ValueError):
        _make_job(max_attempts=0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"attempt_count": True},
        {"attempt_count": 1.5},
        {"max_attempts": True},
        {"max_attempts": 1.5},
    ],
)
def test_scheduled_job_rejects_non_integer_attempt_counts(overrides):
    with pytest.raises(ValueError):
        _make_job(**overrides)


def test_identifier_errors_do_not_echo_rejected_content():
    rejected = "private user payload"
    with pytest.raises(IdentifierError) as exc_info:
        _make_job(action=rejected)
    assert rejected not in str(exc_info.value)


def test_fingerprint_errors_do_not_echo_rejected_content():
    rejected = "private delivery content"
    with pytest.raises(IdentifierError) as exc_info:
        _make_job(last_delivery_fingerprint=rejected)
    assert rejected not in str(exc_info.value)


def test_scheduled_job_excludes_user_payload_fields():
    job = _make_job()
    repr_text = repr(job)
    for forbidden in ("raw_text", "body", "email", "query", "calendar"):
        assert forbidden not in repr_text
    # No payload-bearing attributes exist on the object.
    assert not any(
        name in ("raw_text", "email_body", "query", "calendar_content")
        for name in job.__dataclass_fields__
    )


# --------------------------------------------------------------------------
# Eligibility, retry budget, meaningful change
# --------------------------------------------------------------------------

def test_execution_eligible_when_scheduled_and_due():
    job = _make_job(next_run_at=_aware(2026, 7, 18, 9, 0))
    assert execution_is_eligible(job, _aware(2026, 7, 18, 9, 0)) is True
    assert execution_is_eligible(job, _aware(2026, 7, 18, 10, 0)) is True


def test_execution_not_eligible_before_next_run():
    job = _make_job(next_run_at=_aware(2026, 7, 18, 10, 0))
    assert execution_is_eligible(job, _aware(2026, 7, 18, 9, 0)) is False


def test_execution_not_eligible_when_not_scheduled():
    for state in (JobState.PAUSED, JobState.RUNNING, JobState.COMPLETED):
        job = _make_job(state=state)
        assert execution_is_eligible(job, _aware(2026, 7, 18, 9, 0)) is False


def test_execution_not_eligible_during_quiet():
    qh = QuietHours(timezone_name="UTC", start_minute=540, end_minute=600)
    job = _make_job(next_run_at=_aware(2026, 7, 18, 9, 0), quiet_hours=qh)
    assert execution_is_eligible(job, _aware(2026, 7, 18, 9, 30)) is False
    assert execution_is_eligible(job, _aware(2026, 7, 18, 11, 0)) is True


def test_execution_eligible_rejects_naive_now():
    job = _make_job()
    with pytest.raises(ValueError):
        execution_is_eligible(job, datetime(2026, 7, 18, 9, 0))


def test_retry_budget_remains():
    assert retry_budget_remains(_make_job(attempt_count=0, max_attempts=3)) is True
    assert retry_budget_remains(_make_job(attempt_count=2, max_attempts=3)) is True
    assert retry_budget_remains(_make_job(attempt_count=3, max_attempts=3)) is False


def test_with_attempt_increments():
    job = _make_job(attempt_count=1, max_attempts=3)
    bumped = job.with_attempt(updated_at=_aware(2026, 7, 18, 9, 10))
    assert bumped.attempt_count == 2


def test_meaningful_change_on_fingerprint_inequality():
    job = _make_job(last_delivery_fingerprint="fp-a")
    assert delivery_is_meaningful_change(job, "fp-b") is True
    assert delivery_is_meaningful_change(job, "fp-a") is False
    # None is never a meaningful delivery.
    assert delivery_is_meaningful_change(job, None) is False


# --------------------------------------------------------------------------
# Identifier validation
# --------------------------------------------------------------------------

def test_identifiers_accept_conservative_syntax():
    job = _make_job(
        job_id="job_1.2-3:a",
        actor_id="actor-1",
        session_id="sess_2026-x",
        action="digest.v2",
        proposal_id="prop-1",
    )
    assert job.job_id == "job_1.2-3:a"


@pytest.mark.parametrize(
    "field,value",
    [
        ("job_id", ""),
        ("job_id", "   "),
        ("actor_id", "has space"),
        ("session_id", "bad\nchar"),
        ("action", "bad\tchar"),
        ("proposal_id", "bad;char"),
        ("job_id", "a" * 129),
        ("actor_id", "slash/invalid"),
        ("session_id", "comma,invalid"),
        ("action", "paren(invalid)"),
        ("proposal_id", "quote'invalid"),
    ],
)
def test_identifier_rejects_invalid(field, value):
    with pytest.raises(IdentifierError):
        _make_job(**{field: value})


def test_identifier_rejects_non_string():
    with pytest.raises(IdentifierError):
        _make_job(job_id=None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "proposal_id",
    ["Proposal-1", "proposal:1", " proposal-1", "proposal-1 ", "a" * 81],
)
def test_proposal_id_matches_productivity_contract(proposal_id):
    with pytest.raises(IdentifierError):
        _make_job(proposal_id=proposal_id)


def test_proposal_id_accepts_productivity_boundary():
    assert _make_job(proposal_id="a" * 80).proposal_id == "a" * 80


# --------------------------------------------------------------------------
# Representation privacy
# --------------------------------------------------------------------------

def test_repr_exposes_only_safe_fields():
    job = _make_job(attempt_count=2, max_attempts=5)
    text = repr(job)
    assert "job-1" in text
    assert "scheduled" in text
    assert "attempt_count=2" in text
    assert "max_attempts=5" in text
    # Sensitive / payload-like fields must never appear.
    for forbidden in (
        "actor-1",
        "session-1",
        "digest",
        "prop-1",
        "quiet",
        "fingerprint",
        "actor_id",
        "session_id",
        "action",
        "proposal_id",
    ):
        assert forbidden not in text


# --------------------------------------------------------------------------
# Fingerprint validation
# --------------------------------------------------------------------------

def test_stored_fingerprint_rejects_invalid():
    with pytest.raises(IdentifierError):
        _make_job(last_delivery_fingerprint="")
    with pytest.raises(IdentifierError):
        _make_job(last_delivery_fingerprint="has space")
    with pytest.raises(IdentifierError):
        _make_job(last_delivery_fingerprint="a" * 257)


def test_with_delivery_fingerprint_rejects_invalid():
    job = _make_job()
    with pytest.raises(IdentifierError):
        job.with_delivery_fingerprint("bad value", updated_at=_aware(2026, 7, 18, 9, 1))


def test_with_delivery_fingerprint_accepts_none_and_valid():
    job = _make_job(last_delivery_fingerprint="fp-a")
    cleared = job.with_delivery_fingerprint(None, updated_at=_aware(2026, 7, 18, 9, 1))
    assert cleared.last_delivery_fingerprint is None
    set_fp = cleared.with_delivery_fingerprint(
        "fp-b", updated_at=_aware(2026, 7, 18, 9, 2)
    )
    assert set_fp.last_delivery_fingerprint == "fp-b"


def test_meaningful_change_rejects_invalid_candidate():
    job = _make_job(last_delivery_fingerprint="fp-a")
    with pytest.raises(IdentifierError):
        delivery_is_meaningful_change(job, "bad value")


# --------------------------------------------------------------------------
# Temporal consistency
# --------------------------------------------------------------------------

def test_updated_at_must_not_precede_created_at():
    with pytest.raises(ValueError):
        _make_job(
            created_at=_aware(2026, 7, 18, 9, 0),
            updated_at=_aware(2026, 7, 18, 8, 0),
        )


def test_next_run_at_must_not_precede_created_at():
    with pytest.raises(ValueError):
        _make_job(
            created_at=_aware(2026, 7, 18, 9, 0),
            next_run_at=_aware(2026, 7, 18, 8, 0),
        )


def test_copy_helper_rejects_regressed_updated_at():
    job = _make_job(updated_at=_aware(2026, 7, 18, 9, 0))
    later = _aware(2026, 7, 18, 9, 30)
    earlier = _aware(2026, 7, 18, 8, 30)
    assert job.with_state(JobState.PAUSED, updated_at=later).updated_at == later
    with pytest.raises(ValueError):
        job.with_state(JobState.PAUSED, updated_at=earlier)
    with pytest.raises(ValueError):
        job.with_next_run(_aware(2026, 7, 18, 10, 0), updated_at=earlier)
    with pytest.raises(ValueError):
        job.with_attempt(updated_at=earlier)
    with pytest.raises(ValueError):
        job.with_delivery_fingerprint("fp-x", updated_at=earlier)


def test_with_next_run_must_not_precede_created_at():
    job = _make_job(
        created_at=_aware(2026, 7, 18, 9, 0),
        updated_at=_aware(2026, 7, 18, 9, 0),
    )
    with pytest.raises(ValueError):
        job.with_next_run(
            _aware(2026, 7, 18, 8, 0), updated_at=_aware(2026, 7, 18, 9, 1)
        )


# --------------------------------------------------------------------------
# Retry budget exhaustion
# --------------------------------------------------------------------------

def test_with_attempt_raises_when_exhausted():
    job = _make_job(attempt_count=3, max_attempts=3)
    with pytest.raises(RetryBudgetExhausted):
        job.with_attempt(updated_at=_aware(2026, 7, 18, 9, 10))


def test_with_attempt_raises_when_exactly_at_limit():
    # attempt_count == max_attempts means no further attempt is permitted.
    job = _make_job(attempt_count=2, max_attempts=2)
    with pytest.raises(RetryBudgetExhausted):
        job.with_attempt(updated_at=_aware(2026, 7, 18, 9, 10))


def test_with_attempt_succeeds_when_budget_remains():
    job = _make_job(attempt_count=1, max_attempts=3)
    bumped = job.with_attempt(updated_at=_aware(2026, 7, 18, 9, 10))
    assert bumped.attempt_count == 2


# --------------------------------------------------------------------------
# QuietHours construction and validation
# --------------------------------------------------------------------------

def test_quiet_hours_daytime_window():
    qh = QuietHours(timezone_name="UTC", start_minute=540, end_minute=600)
    assert qh.enabled is True


def test_quiet_hours_equal_boundary_disabled():
    qh = QuietHours(timezone_name="UTC", start_minute=600, end_minute=600)
    assert qh.enabled is False
    assert is_quiet(_aware(2026, 7, 18, 10, 0), qh) is False


def test_quiet_hours_rejects_unknown_timezone():
    with pytest.raises(QuietHoursError):
        QuietHours(timezone_name="Not/A_Zone", start_minute=0, end_minute=10)


def test_quiet_hours_rejects_non_string_timezone():
    with pytest.raises(QuietHoursError):
        QuietHours(timezone_name=123, start_minute=0, end_minute=10)  # type: ignore[arg-type]


def test_quiet_hours_rejects_low_minute():
    with pytest.raises(QuietHoursError):
        QuietHours(timezone_name="UTC", start_minute=-1, end_minute=10)


def test_quiet_hours_rejects_high_minute():
    with pytest.raises(QuietHoursError):
        QuietHours(timezone_name="UTC", start_minute=0, end_minute=1440)


def test_quiet_hours_rejects_bool_minute():
    with pytest.raises(QuietHoursError):
        QuietHours(timezone_name="UTC", start_minute=True, end_minute=10)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# is_quiet behavior
# --------------------------------------------------------------------------

def test_is_quiet_daytime_window():
    qh = QuietHours(timezone_name="UTC", start_minute=540, end_minute=600)
    assert is_quiet(_aware(2026, 7, 18, 9, 0), qh) is True   # 09:00 start
    assert is_quiet(_aware(2026, 7, 18, 9, 59), qh) is True  # before end
    assert is_quiet(_aware(2026, 7, 18, 10, 0), qh) is False  # end exclusive
    assert is_quiet(_aware(2026, 7, 18, 8, 59), qh) is False


def test_is_quiet_exact_start_boundary():
    qh = QuietHours(timezone_name="UTC", start_minute=540, end_minute=600)
    assert is_quiet(_aware(2026, 7, 18, 9, 0), qh) is True


def test_is_quiet_exact_end_boundary_exclusive():
    qh = QuietHours(timezone_name="UTC", start_minute=540, end_minute=600)
    assert is_quiet(_aware(2026, 7, 18, 10, 0), qh) is False


def test_is_quiet_cross_midnight_window():
    # 23:00 -> 01:00 next day.
    qh = QuietHours(timezone_name="UTC", start_minute=1380, end_minute=60)
    assert is_quiet(_aware(2026, 7, 18, 23, 0), qh) is True
    assert is_quiet(_aware(2026, 7, 18, 23, 59), qh) is True
    assert is_quiet(_aware(2026, 7, 19, 0, 0), qh) is True
    assert is_quiet(_aware(2026, 7, 19, 0, 59), qh) is True
    assert is_quiet(_aware(2026, 7, 19, 1, 0), qh) is False  # end exclusive
    assert is_quiet(_aware(2026, 7, 18, 22, 0), qh) is False


def test_is_quiet_timezone_conversion():
    # Window 22:00-23:00 in New York (EDT = UTC-4 in summer).
    qh = QuietHours(timezone_name="America/New_York", start_minute=1320, end_minute=1380)
    # 02:00 UTC == 22:00 EDT -> start inclusive, inside window.
    assert is_quiet(_aware(2026, 7, 19, 2, 0), qh) is True
    # 02:30 UTC == 22:30 EDT -> inside window.
    assert is_quiet(_aware(2026, 7, 19, 2, 30), qh) is True
    # 03:00 UTC == 23:00 EDT -> end exclusive, outside window.
    assert is_quiet(_aware(2026, 7, 19, 3, 0), qh) is False
    # 04:00 UTC == 00:00 EDT next day -> after end.
    assert is_quiet(_aware(2026, 7, 19, 4, 0), qh) is False


def test_is_quiet_dst_aware():
    # New York window 01:30-02:30 local. DST springs forward at 02:00 local
    # on 2026-03-08, so the 02:00-03:00 local hour is skipped. Conversion is
    # performed by zoneinfo against the correct offset for each instant.
    qh = QuietHours(timezone_name="America/New_York", start_minute=90, end_minute=150)
    # 2026-03-08 06:00 UTC == 01:00 EST -> before start, outside.
    assert is_quiet(datetime(2026, 3, 8, 6, 0, tzinfo=UTC), qh) is False
    # 2026-03-08 06:30 UTC == 01:30 EST -> start inclusive, inside.
    assert is_quiet(datetime(2026, 3, 8, 6, 30, tzinfo=UTC), qh) is True
    # 2026-03-08 07:00 UTC == 03:00 EDT (post spring-forward) -> after end.
    assert is_quiet(datetime(2026, 3, 8, 7, 0, tzinfo=UTC), qh) is False


def test_is_quiet_rejects_naive_now():
    qh = QuietHours(timezone_name="UTC", start_minute=540, end_minute=600)
    with pytest.raises(QuietHoursError):
        is_quiet(datetime(2026, 7, 18, 9, 0), qh)


def test_is_quiet_rejects_wrong_type():
    with pytest.raises(QuietHoursError):
        is_quiet(_aware(2026, 7, 18, 9, 0), "not-quiet-hours")  # type: ignore[arg-type]


def test_is_quiet_disabled_window_never_quiet():
    qh = QuietHours(timezone_name="UTC", start_minute=300, end_minute=300)
    assert is_quiet(_aware(2026, 7, 18, 5, 0), qh) is False


# --------------------------------------------------------------------------
# No forbidden imports / no recurrence or overdue fields
# --------------------------------------------------------------------------

def test_no_forbidden_imports_in_contracts():
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "core" / "jobs"
    forbidden = {
        "subprocess",
        "socket",
        "threading",
        "os",
        "time",
        "requests",
        "asyncio",
    }
    # This purity contract applies to the value-object modules. The separate
    # SQLite store intentionally uses OS permission APIs and has its own
    # dependency-boundary tests.
    for path in (root / "contracts.py", root / "quiet_hours.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in forbidden, (
                        f"{path.name} imports forbidden module {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert node.module.split(".")[0] not in forbidden, (
                        f"{path.name} imports forbidden module {node.module}"
                    )


def test_no_recurrence_or_overdue_fields():
    field_names = set(ScheduledJob.__dataclass_fields__)
    for forbidden in (
        "recurrence",
        "recurrence_rule",
        "overdue",
        "due_at",
        "due_date",
    ):
        assert forbidden not in field_names
