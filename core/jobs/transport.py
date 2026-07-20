"""Pure Phase 3 scheduled-jobs transport adapter.

This module converts sanitized scheduled-job views objects into canonical
dictionaries. It performs no I/O, network access, execution, logging, or
server wiring.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from core.jobs.service import JobControlResult, JobServiceCode, ScheduledJobView


class TransportError(ValueError):
    """Raised when a scheduled-job message cannot be produced safely."""


_JOB_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_FALLBACK_JOB_ID = "scheduled-jobs"


def job_view_to_dict(view: ScheduledJobView) -> dict[str, Any]:
    """Convert a ``ScheduledJobView`` into a bounded dictionary.

    The resulting dictionary contains only the fields needed by the frontend.
    No actor_id, session_id, proposal_id, payload content, or provider details
    are included.
    """
    if not isinstance(view, ScheduledJobView):
        raise TransportError("view must be a ScheduledJobView")
    message = {
        "job_id": view.job_id,
        "action": view.action,
        "state": view.state,
        "next_run_at": view.next_run_at,
        "attempt_count": view.attempt_count,
        "max_attempts": view.max_attempts,
    }
    if view.quiet_hours_summary is not None:
        message["quiet_hours_label"] = view.quiet_hours_summary
    return message


def list_message(views: Sequence[ScheduledJobView]) -> dict[str, Any]:
    """Produce a canonical scheduled_jobs message."""
    if len(views) > 64:
        raise TransportError("too many scheduled jobs")
    return {
        "type": "scheduled_jobs",
        "jobs": [job_view_to_dict(view) for view in views],
    }


def update_message(view: ScheduledJobView) -> dict[str, Any]:
    """Produce a canonical scheduled_job_update message."""
    return {
        "type": "scheduled_job_update",
        "job": job_view_to_dict(view),
    }


def error_message(job_id: object, code: JobServiceCode) -> dict[str, Any]:
    """Produce a canonical scheduled_job_error message.

    ``code`` must be one of the bounded error codes. No exception text,
    provider details, or internal identifiers are included.
    """
    if not isinstance(code, JobServiceCode):
        raise TransportError("code must be a JobServiceCode")
    if code is JobServiceCode.OK:
        raise TransportError("OK cannot be mapped to an error message")
    if not isinstance(job_id, str) or not _JOB_ID_PATTERN.fullmatch(job_id):
        raise TransportError("invalid job id")
    return {
        "type": "scheduled_job_error",
        "job_id": job_id,
        "code": code.value,
    }


def result_message(
    result: JobControlResult,
    *,
    job_id: str = _FALLBACK_JOB_ID,
) -> dict[str, Any]:
    """Convert a ``JobControlResult`` into a canonical message.

    On success, returns either a list or update message. On failure, returns
    an safe error message.
    """
    if not isinstance(result, JobControlResult):
        raise TransportError("result must be a JobControlResult")
    if result.error is not None:
        return error_message(job_id, result.error)
    if result.jobs is not None:
        return list_message(result.jobs)
    if result.job is not None:
        return update_message(result.job)
    raise TransportError("result has no job, jobs, or error")
