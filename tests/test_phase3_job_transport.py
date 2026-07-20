"""Tests for the Phase 3 scheduled-jobs transport adapter.

These tests verify that transport helpers convert views objects and results
into canonical dictionaries without leaking internal identifiers or exception
text.
"""

from __future__ import annotations

import pytest

from core.jobs.service import JobControlResult, JobServiceCode, ScheduledJobView
from core.jobs.transport import (
    TransportError,
    error_message,
    job_view_to_dict,
    list_message,
    result_message,
    update_message,
)


def _sample_view(job_id="job-1", state="scheduled"):
    return ScheduledJobView(
        job_id=job_id,
        action="digest",
        state=state,
        next_run_at="2026-07-18T09:00:00+00:00",
        quiet_hours_summary=None,
        attempt_count=0,
        max_attempts=1,
    )


def test_job_view_to_dict_excludes_internal_fields():
    view = _sample_view()
    d = job_view_to_dict(view)
    assert d == {
        "job_id": "job-1",
        "action": "digest",
        "state": "scheduled",
        "next_run_at": "2026-07-18T09:00:00+00:00",
        "attempt_count": 0,
        "max_attempts": 1,
    }
    assert "actor_id" not in d
    assert "session_id" not in d
    assert "proposal_id" not in d


def test_list_message_contains_jobs():
    views = [_sample_view("job-1"), _sample_view("job-2")]
    msg = list_message(views)
    assert msg["type"] == "scheduled_jobs"
    assert set(msg) == {"type", "jobs"}
    assert len(msg["jobs"]) == 2


def test_update_message_contains_job():
    view = _sample_view()
    msg = update_message(view)
    assert msg["type"] == "scheduled_job_update"
    assert set(msg) == {"type", "job"}
    assert msg["job"]["job_id"] == "job-1"


def test_error_message_contains_bounded_code():
    msg = error_message("job-1", JobServiceCode.JOB_NOT_FOUND)
    assert msg["type"] == "scheduled_job_error"
    assert msg["job_id"] == "job-1"
    assert msg["code"] == "job_not_found"


def test_result_message_maps_success():
    result = JobControlResult(job=_sample_view())
    msg = result_message(result)
    assert msg["type"] == "scheduled_job_update"
    assert set(msg) == {"type", "job"}


def test_result_message_maps_list_success():
    result = JobControlResult(jobs=(_sample_view(),))
    msg = result_message(result)
    assert msg["type"] == "scheduled_jobs"
    assert set(msg) == {"type", "jobs"}


def test_result_message_maps_error():
    result = JobControlResult(error=JobServiceCode.CONTROL_FAILED)
    msg = result_message(result)
    assert msg["type"] == "scheduled_job_error"
    assert msg["job_id"] == "scheduled-jobs"
    assert msg["code"] == "control_failed"


def test_error_message_rejects_ok_code():
    with pytest.raises(TransportError):
        error_message("job-1", JobServiceCode.OK)


def test_result_message_rejects_empty_result():
    with pytest.raises(TransportError):
        result_message("not-a-result")


def test_job_view_to_dict_rejects_non_view():
    with pytest.raises(TransportError):
        job_view_to_dict("not-a-view")


def test_quiet_hours_uses_frontend_wire_key():
    view = ScheduledJobView(
        job_id="job-1",
        action="digest",
        state="scheduled",
        next_run_at="2026-07-18T09:00:00+00:00",
        quiet_hours_summary="1320-1380 (America/New_York)",
        attempt_count=0,
        max_attempts=1,
    )
    assert job_view_to_dict(view)["quiet_hours_label"] == view.quiet_hours_summary


def test_result_requires_exactly_one_immutable_outcome():
    with pytest.raises(ValueError):
        JobControlResult()
    with pytest.raises(ValueError):
        JobControlResult(job=_sample_view(), error=JobServiceCode.UNAVAILABLE)
    with pytest.raises(ValueError):
        JobControlResult(jobs=[_sample_view()])
