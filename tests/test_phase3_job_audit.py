"""Tests for the Phase 3 pure scheduled-job audit contracts.

These tests cover only the immutable audit value object and pure transition
validation in ``core.jobs.audit``. They perform no I/O, subprocess, network,
filesystem, sleep, or thread activity, and assert the absence of those imports.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.jobs.audit import (
    AuditEvent,
    AuditReasonCode,
    AuditTransitionError,
    AuditValidationError,
    validate_transition,
)
from core.jobs.contracts import JobState, TERMINAL_JOB_STATES

UTC = timezone.utc


def _aware(year, month, day, hour, minute, tz=UTC):
    return datetime(year, month, day, hour, minute, tzinfo=tz)


def _make_event(**overrides) -> AuditEvent:
    base = dict(
        event_id="evt-1",
        job_id="job-1",
        action="digest",
        previous_state=JobState.SCHEDULED,
        new_state=JobState.RUNNING,
        occurred_at=_aware(2026, 7, 18, 9, 0),
        reason_code=AuditReasonCode.STATE_TRANSITION,
    )
    base.update(overrides)
    return AuditEvent(**base)


# --------------------------------------------------------------------------
# Construction and immutability
# --------------------------------------------------------------------------


def test_audit_event_is_frozen():
    event = _make_event()
    with pytest.raises(Exception):
        event.job_id = "job-2"  # type: ignore[misc]


def test_audit_event_requires_timezone_aware_time():
    with pytest.raises(AuditValidationError):
        _make_event(occurred_at=datetime(2026, 7, 18, 9, 0))


def test_audit_event_rejects_out_of_range_time():
    with pytest.raises(AuditValidationError):
        _make_event(occurred_at=_aware(1950, 1, 1, 0, 0))
    with pytest.raises(AuditValidationError):
        _make_event(occurred_at=_aware(2150, 1, 1, 0, 0))


def test_audit_event_rejects_empty_event_id():
    with pytest.raises(AuditValidationError):
        _make_event(event_id="")


def test_audit_event_rejects_oversized_event_id():
    with pytest.raises(AuditValidationError):
        _make_event(event_id="e" * 129)


def test_audit_event_rejects_invalid_job_id_chars():
    with pytest.raises(AuditValidationError):
        _make_event(job_id="has space")


def test_audit_event_rejects_invalid_action_chars():
    with pytest.raises(AuditValidationError):
        _make_event(action="bad;char")


def test_audit_event_rejects_non_enum_reason():
    with pytest.raises(AuditValidationError):
        _make_event(reason_code="created")  # type: ignore[arg-type]


def test_audit_event_accepts_none_previous_for_creation():
    event = _make_event(
        previous_state=None, reason_code=AuditReasonCode.CREATED
    )
    assert event.previous_state is None
    assert event.reason_code is AuditReasonCode.CREATED


def test_audit_event_rejects_non_jobstate_state():
    with pytest.raises(AuditValidationError):
        _make_event(new_state="running")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Privacy-safe representation
# --------------------------------------------------------------------------


def test_repr_excludes_sensitive_fields():
    event = _make_event()
    text = repr(event)
    assert "evt-1" in text
    assert "job-1" in text
    assert "state_transition" in text
    # No payload-like or identifier-like leakage beyond the bounded fields.
    for forbidden in (
        "actor",
        "session",
        "proposal",
        "payload",
        "target",
        "provider",
        "exception",
        "approval",
        "digest",
    ):
        assert forbidden not in text


# --------------------------------------------------------------------------
# Transition validation
# --------------------------------------------------------------------------


def test_validate_transition_allows_permitted():
    assert (
        validate_transition(JobState.SCHEDULED, JobState.RUNNING) is True
    )
    assert validate_transition(None, JobState.SCHEDULED) is True


def test_validate_transition_rejects_impossible():
    with pytest.raises(AuditTransitionError):
        validate_transition(JobState.SCHEDULED, JobState.COMPLETED)
    with pytest.raises(AuditTransitionError):
        validate_transition(JobState.COMPLETED, JobState.SCHEDULED)
    with pytest.raises(AuditTransitionError):
        validate_transition(JobState.PAUSED, JobState.RUNNING)


def test_audit_event_rejects_impossible_transition():
    with pytest.raises(AuditTransitionError):
        _make_event(
            previous_state=JobState.SCHEDULED,
            new_state=JobState.COMPLETED,
        )


def test_validate_transition_rejects_malformed_endpoints():
    with pytest.raises(AuditValidationError):
        validate_transition(JobState.SCHEDULED, None)  # type: ignore[arg-type]
    with pytest.raises(AuditValidationError):
        validate_transition("scheduled", JobState.RUNNING)  # type: ignore[arg-type]


def test_validate_transition_repeated_is_permitted():
    for state in JobState:
        assert validate_transition(state, state) is True


def test_validate_transition_terminal_cannot_leave():
    for terminal in TERMINAL_JOB_STATES:
        for other in JobState:
            if other is terminal:
                continue
            with pytest.raises(AuditTransitionError):
                validate_transition(terminal, other)


# --------------------------------------------------------------------------
# Reason code enum is fixed
# --------------------------------------------------------------------------


def test_reason_codes_are_fixed_values():
    assert AuditReasonCode.CREATED.value == "created"
    assert AuditReasonCode.STATE_TRANSITION.value == "state_transition"
    assert AuditReasonCode.DELIVERED.value == "delivered"
    assert AuditReasonCode.RETRY_EXHAUSTED.value == "retry_exhausted"
    assert AuditReasonCode.TERMINAL.value == "terminal"
    assert AuditReasonCode.CONTROL.value == "control"
    assert AuditReasonCode.QUIET_SUPPRESSED.value == "quiet_suppressed"


# --------------------------------------------------------------------------
# No forbidden imports
# --------------------------------------------------------------------------


def test_no_forbidden_imports_in_audit():
    import ast
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "core" / "jobs" / "audit.py"
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
                    f"audit.py imports forbidden module {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert node.module.split(".")[0] not in forbidden, (
                    f"audit.py imports forbidden module {node.module}"
                )
