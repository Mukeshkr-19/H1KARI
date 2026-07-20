"""Shared WebSocket contract tests for bounded scheduled-job messages."""

from __future__ import annotations

import copy

import pytest

from core.protocol import validate_client_message, validate_server_message


def _job(**overrides: object) -> dict[str, object]:
    job: dict[str, object] = {
        "job_id": "job-1",
        "action": "email.draft",
        "state": "scheduled",
        "next_run_at": "2026-07-19T12:30:00+00:00",
        "attempt_count": 0,
        "max_attempts": 3,
    }
    job.update(overrides)
    return job


@pytest.mark.parametrize(
    "message",
    [
        {"type": "scheduled_jobs_list"},
        {"type": "scheduled_job_pause", "job_id": "job-1"},
        {"type": "scheduled_job_resume", "job_id": "job_1"},
        {"type": "scheduled_job_cancel", "job_id": "job.1"},
    ],
)
def test_client_job_messages_accept_only_canonical_shapes(message):
    assert validate_client_message(message) is None


@pytest.mark.parametrize(
    "message",
    [
        {"type": "scheduled_jobs_list", "session_id": "session-1"},
        {"type": "scheduled_job_pause"},
        {"type": "scheduled_job_resume", "job_id": "Job-1"},
        {"type": "scheduled_job_cancel", "job_id": "job:1"},
        {"type": "scheduled_job_cancel", "job_id": "x" * 81},
        {"type": "scheduled_job_cancel", "job_id": "job-1", "payload": {}},
    ],
)
def test_client_job_messages_reject_missing_private_or_invalid_fields(message):
    assert validate_client_message(message) is not None


def test_server_job_messages_accept_bounded_canonical_shapes():
    listed = _job(quiet_hours_label="1320-1380 (America/New_York)")
    assert validate_server_message({"type": "scheduled_jobs", "jobs": []}) is None
    assert (
        validate_server_message({"type": "scheduled_jobs", "jobs": [listed]})
        is None
    )
    assert (
        validate_server_message({"type": "scheduled_job_update", "job": _job()})
        is None
    )
    assert (
        validate_server_message(
            {
                "type": "scheduled_job_error",
                "job_id": "job-1",
                "code": "control_failed",
            }
        )
        is None
    )


def test_server_job_list_allows_exact_maximum():
    jobs = [_job(job_id=f"job-{index}") for index in range(64)]
    assert validate_server_message({"type": "scheduled_jobs", "jobs": jobs}) is None
    jobs.append(_job(job_id="job-64"))
    assert validate_server_message({"type": "scheduled_jobs", "jobs": jobs}) == (
        "Array too long: jobs"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("job_id", "Job-1"),
        ("job_id", "job:1"),
        ("job_id", "x" * 81),
        ("action", "bad action"),
        ("action", "x" * 129),
        ("state", "unknown"),
        ("next_run_at", "tomorrow"),
        ("next_run_at", "2026-07-19T12:30:00"),
        ("quiet_hours_label", "quiet\nnow"),
        ("quiet_hours_label", "x" * 161),
        ("attempt_count", True),
        ("attempt_count", -1),
        ("attempt_count", 101),
        ("max_attempts", 0),
        ("max_attempts", 101),
        ("max_attempts", float("inf")),
    ],
)
def test_server_job_fields_reject_invalid_or_unbounded_values(field, value):
    message = {"type": "scheduled_job_update", "job": _job(**{field: value})}
    assert validate_server_message(message) is not None


def test_server_job_rejects_inconsistent_attempts():
    message = {
        "type": "scheduled_job_update",
        "job": _job(attempt_count=3, max_attempts=2),
    }
    assert validate_server_message(message) == (
        "Invalid field value: job.attempt_count"
    )


@pytest.mark.parametrize(
    "private_field",
    [
        "actor_id",
        "session_id",
        "proposal_id",
        "payload",
        "provider",
        "provider_details",
    ],
)
def test_server_job_rejects_private_or_unknown_job_fields(private_field):
    job = _job()
    job[private_field] = "private"
    message = {"type": "scheduled_job_update", "job": job}
    assert validate_server_message(message) == f"Unknown field: job.{private_field}"


def test_server_job_message_rejects_unknown_top_level_fields():
    for field in ("actor_id", "session_id", "proposal_id", "payload", "provider"):
        message = {"type": "scheduled_jobs", "jobs": [], field: "private"}
        assert validate_server_message(message) == f"Unknown field: {field}"


@pytest.mark.parametrize(
    "code",
    ["control_failed", "job_not_found", "unavailable"],
)
def test_server_job_error_accepts_only_safe_codes(code):
    message = {"type": "scheduled_job_error", "job_id": "job-1", "code": code}
    assert validate_server_message(message) is None


@pytest.mark.parametrize(
    "extra,value",
    [
        ("message", "raw provider exception"),
        ("detail", "stack trace"),
        ("provider", "remote-provider"),
        ("payload", {"secret": True}),
        ("actor_id", "owner"),
        ("session_id", "session-1"),
        ("proposal_id", "proposal-1"),
    ],
)
def test_server_job_error_rejects_raw_or_private_details(extra, value):
    message = {
        "type": "scheduled_job_error",
        "job_id": "job-1",
        "code": "unavailable",
        extra: value,
    }
    assert validate_server_message(message) == f"Unknown field: {extra}"


def test_server_job_error_rejects_unsafe_code_and_bad_identifier():
    assert (
        validate_server_message(
            {"type": "scheduled_job_error", "job_id": "job-1", "code": "ok"}
        )
        == "Invalid field value: code"
    )
    assert (
        validate_server_message(
            {
                "type": "scheduled_job_error",
                "job_id": "bad id",
                "code": "unavailable",
            }
        )
        == "Invalid field value: job_id"
    )


def test_validation_does_not_mutate_server_payload():
    message = {"type": "scheduled_jobs", "jobs": [_job()]}
    snapshot = copy.deepcopy(message)
    assert validate_server_message(message) is None
    assert message == snapshot
