"""Deterministic tests for the isolated scheduled-read coordinator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.jobs.action_store import ScheduledActionStore, StoredActionEnvelope
from core.jobs.audit_store import ScheduledJobAuditStore
from core.jobs.contracts import JobState, ScheduledJob
from core.jobs.coordinator import (
    ScheduledReadCoordinator,
    ScheduledReadExecutionCode,
    ScheduledReadScheduleCode,
    StableOwnerScope,
)
from core.jobs.creation import JobCreationService
from core.jobs.store import ScheduledJobStore
from core.productivity.action_inputs import (
    BrowserResearchAdapterInput,
    CalendarDraftAdapterInput,
    CalendarReadAdapterInput,
    EmailDraftAdapterInput,
)
from core.productivity.action_results import (
    BrowserSearchResult,
    BrowserSearchResultItem,
    CalendarEventItem,
    CalendarReadResult,
)
from core.productivity.adapters.research import BrowserResearchAdapterResult
from core.productivity.contracts import ProductivityAction
from core.productivity.execution import AdapterResult, AdapterResultStatus


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
OWNER = StableOwnerScope("local-owner", "installation-1")


class _ResearchAdapter:
    def __init__(self, result: object | None = None) -> None:
        self.calls: list[object] = []
        self.result = result or BrowserResearchAdapterResult(
            AdapterResultStatus.SUCCESS,
            result=BrowserSearchResult(
                "release notes",
                (
                    BrowserSearchResultItem(
                        "Release", "https://example.com/release", "example.com"
                    ),
                ),
            ),
        )

    def __call__(self, input_value: object) -> object:
        self.calls.append(input_value)
        return self.result


class _CalendarAdapter:
    def __init__(self, result: object | None = None) -> None:
        self.calls: list[object] = []
        self.result = result or CalendarReadResult(
            (
                CalendarEventItem(
                    "Planning",
                    datetime(2026, 7, 21, 9, tzinfo=timezone.utc),
                    datetime(2026, 7, 21, 10, tzinfo=timezone.utc),
                    "Work",
                ),
            ),
            calendar_label="Work",
        )

    def __call__(self, input_value: object) -> object:
        self.calls.append(input_value)
        return self.result


def _components(tmp_path: Path):
    job_store = ScheduledJobStore(tmp_path / "jobs.db")
    action_store = ScheduledActionStore(tmp_path / "private" / "actions.db")
    audit_store = ScheduledJobAuditStore(tmp_path / "audit.db")
    event_number = {"value": 0}

    def creator_factory(reserved_job_id):
        def event_id():
            event_number["value"] += 1
            return f"event-{event_number['value']}"

        return JobCreationService(
            job_store, audit_store, lambda: NOW, reserved_job_id, event_id
        )

    research = _ResearchAdapter()
    calendar = _CalendarAdapter()
    ids = {"calls": 0}

    def job_id():
        ids["calls"] += 1
        return "job-1"

    coordinator = ScheduledReadCoordinator(
        creation_service_factory=creator_factory,
        job_store=job_store,
        action_store=action_store,
        clock=lambda: NOW,
        job_id_factory=job_id,
        adapters={
            ProductivityAction.BROWSER_RESEARCH: research,
            ProductivityAction.CALENDAR_READ: calendar,
        },
    )
    return coordinator, job_store, action_store, research, calendar, ids


def _research_input() -> BrowserResearchAdapterInput:
    return BrowserResearchAdapterInput("release notes", ("example.com",), 5)


def _calendar_input() -> CalendarReadAdapterInput:
    return CalendarReadAdapterInput(
        "2026-07-21T09:00:00Z", "2026-07-21T10:00:00Z", "Work"
    )


@pytest.mark.parametrize(
    ("action", "adapter_input"),
    (
        (ProductivityAction.BROWSER_RESEARCH, _research_input()),
        (ProductivityAction.CALENDAR_READ, _calendar_input()),
    ),
)
def test_schedule_retains_exact_input_before_create_and_reserves_id_once(
    tmp_path: Path, action: ProductivityAction, adapter_input: object
) -> None:
    coordinator, job_store, action_store, _, _, ids = _components(tmp_path)
    original_factory = coordinator._creation_service_factory
    observed = {"retained_first": False}

    def checking_factory(reserved_job_id):
        stored = action_store.get(
            "job-1", actor_id=OWNER.owner_id, session_id=OWNER.scope_id
        )
        observed["retained_first"] = stored is not None
        return original_factory(reserved_job_id)

    coordinator._creation_service_factory = checking_factory
    result = coordinator.schedule(
        owner=OWNER,
        proposal_id="proposal-1",
        action=action,
        adapter_input=adapter_input,
        next_run_at=NOW + timedelta(hours=1),
        max_attempts=3,
    )

    assert result.code is ScheduledReadScheduleCode.SCHEDULED
    assert result.job is not None and result.job.job_id == "job-1"
    assert observed["retained_first"] is True
    assert ids["calls"] == 1
    stored = action_store.get(
        "job-1", actor_id=OWNER.owner_id, session_id=OWNER.scope_id
    )
    assert stored is not None and stored.adapter_input == adapter_input
    persisted = job_store.get(
        "job-1", actor_id=OWNER.owner_id, session_id=OWNER.scope_id
    )
    assert persisted is not None and persisted.action == action.value


@pytest.mark.parametrize(
    ("action", "adapter_input"),
    (
        (
            ProductivityAction.EMAIL_DRAFT,
            EmailDraftAdapterInput("a@example.com", "Subject", "Body"),
        ),
        (
            ProductivityAction.CALENDAR_DRAFT,
            CalendarDraftAdapterInput(
                "Event",
                "2026-07-21T09:00:00Z",
                "2026-07-21T10:00:00Z",
                "Work",
                None,
                None,
            ),
        ),
        (ProductivityAction.BROWSER_RESEARCH, _calendar_input()),
    ),
)
def test_schedule_rejects_writes_and_wrong_subtypes_before_reserving_id(
    tmp_path: Path, action: ProductivityAction, adapter_input: object
) -> None:
    coordinator, job_store, action_store, _, _, ids = _components(tmp_path)
    result = coordinator.schedule(
        owner=OWNER,
        proposal_id="proposal-1",
        action=action,
        adapter_input=adapter_input,
        next_run_at=NOW + timedelta(hours=1),
    )
    assert result.code is ScheduledReadScheduleCode.INVALID
    assert ids["calls"] == 0
    assert job_store.list(actor_id=OWNER.owner_id, session_id=OWNER.scope_id) == []
    assert action_store.count(actor_id=OWNER.owner_id, session_id=OWNER.scope_id) == 0


def test_create_failure_cas_deletes_retained_envelope(tmp_path: Path) -> None:
    coordinator, job_store, action_store, _, _, _ = _components(tmp_path)
    coordinator._creation_service_factory = lambda reserved: (_ for _ in ()).throw(
        RuntimeError("private failure")
    )
    result = coordinator.schedule(
        owner=OWNER,
        proposal_id="proposal-1",
        action=ProductivityAction.BROWSER_RESEARCH,
        adapter_input=_research_input(),
        next_run_at=NOW + timedelta(hours=1),
    )
    assert result.code is ScheduledReadScheduleCode.UNAVAILABLE
    assert action_store.get(
        "job-1", actor_id=OWNER.owner_id, session_id=OWNER.scope_id
    ) is None
    assert job_store.get(
        "job-1", actor_id=OWNER.owner_id, session_id=OWNER.scope_id
    ) is None
    assert "private" not in repr(result)


def test_schedule_expiry_is_bounded_and_covers_execution(tmp_path: Path) -> None:
    coordinator, _, action_store, _, _, _ = _components(tmp_path)
    accepted = coordinator.schedule(
        owner=OWNER,
        proposal_id="proposal-1",
        action=ProductivityAction.BROWSER_RESEARCH,
        adapter_input=_research_input(),
        next_run_at=NOW + timedelta(days=365),
    )
    assert accepted.code is ScheduledReadScheduleCode.SCHEDULED
    stored = action_store.get(
        "job-1", actor_id=OWNER.owner_id, session_id=OWNER.scope_id
    )
    assert stored is not None
    assert stored.expires_at == NOW + timedelta(days=366)


@pytest.mark.parametrize(
    ("next_run_at", "max_attempts"),
    (
        (NOW - timedelta(microseconds=1), 1),
        (datetime.max.replace(tzinfo=timezone.utc), 1),
        (NOW + timedelta(hours=1), 0),
        (NOW + timedelta(hours=1), True),
    ),
)
def test_invalid_schedule_bounds_fail_before_job_id_or_persistence(
    tmp_path: Path, next_run_at: datetime, max_attempts: object
) -> None:
    coordinator, job_store, action_store, _, _, ids = _components(tmp_path)
    result = coordinator.schedule(
        owner=OWNER,
        proposal_id="proposal-1",
        action=ProductivityAction.BROWSER_RESEARCH,
        adapter_input=_research_input(),
        next_run_at=next_run_at,
        max_attempts=max_attempts,
    )
    assert result.code is ScheduledReadScheduleCode.INVALID
    assert ids["calls"] == 0
    assert job_store.list(actor_id=OWNER.owner_id, session_id=OWNER.scope_id) == []
    assert action_store.count(actor_id=OWNER.owner_id, session_id=OWNER.scope_id) == 0


def _claim(job_store: ScheduledJobStore) -> ScheduledJob:
    claimed = job_store.claim_due(
        now=NOW + timedelta(hours=2),
        updated_at=NOW + timedelta(hours=2),
        limit=1,
    )
    assert len(claimed) == 1
    return claimed[0]


def test_execute_claimed_research_uses_exact_input_and_stable_fingerprint(
    tmp_path: Path,
) -> None:
    coordinator, job_store, _, research, _, _ = _components(tmp_path)
    coordinator.schedule(
        owner=OWNER,
        proposal_id="proposal-1",
        action=ProductivityAction.BROWSER_RESEARCH,
        adapter_input=_research_input(),
        next_run_at=NOW + timedelta(hours=1),
    )
    job = _claim(job_store)
    first = coordinator.execute_claimed(job)
    second = coordinator.execute_claimed(job)
    assert first.code is ScheduledReadExecutionCode.SUCCEEDED
    assert first.result == research.result.result
    assert first.fingerprint == second.fingerprint
    assert first.fingerprint is not None and first.fingerprint.startswith("sha256.")
    assert research.calls == [_research_input(), _research_input()]
    assert "release" not in repr(first).lower()


def test_execute_claimed_calendar_normalizes_equal_instants_in_fingerprint(
    tmp_path: Path,
) -> None:
    coordinator, job_store, _, _, calendar, _ = _components(tmp_path)
    coordinator.schedule(
        owner=OWNER,
        proposal_id="proposal-1",
        action=ProductivityAction.CALENDAR_READ,
        adapter_input=_calendar_input(),
        next_run_at=NOW + timedelta(hours=1),
    )
    job = _claim(job_store)
    utc_outcome = coordinator.execute_claimed(job)
    calendar.result = CalendarReadResult(
        (
            CalendarEventItem(
                "Planning",
                datetime(2026, 7, 21, 5, tzinfo=timezone(timedelta(hours=-4))),
                datetime(2026, 7, 21, 6, tzinfo=timezone(timedelta(hours=-4))),
                "Work",
            ),
        ),
        calendar_label="Work",
    )
    offset_outcome = coordinator.execute_claimed(job)
    assert utc_outcome.fingerprint == offset_outcome.fingerprint


def test_missing_cross_scope_or_mismatched_envelope_never_invokes_adapter(
    tmp_path: Path,
) -> None:
    coordinator, job_store, action_store, research, _, _ = _components(tmp_path)
    job = ScheduledJob(
        job_id="job-1",
        actor_id=OWNER.owner_id,
        session_id=OWNER.scope_id,
        action=ProductivityAction.BROWSER_RESEARCH.value,
        proposal_id="proposal-1",
        state=JobState.RUNNING,
        next_run_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    assert coordinator.execute_claimed(job).code is ScheduledReadExecutionCode.PERMANENT_FAILURE
    action_store.put(
        StoredActionEnvelope(
            job_id="job-1",
            proposal_id="different-proposal",
            actor_id=OWNER.owner_id,
            session_id=OWNER.scope_id,
            adapter_input=_research_input(),
            created_at=NOW,
            expires_at=NOW + timedelta(days=1),
        )
    )
    assert coordinator.execute_claimed(job).code is ScheduledReadExecutionCode.PERMANENT_FAILURE
    assert research.calls == []
    assert job_store.list(actor_id=OWNER.owner_id, session_id=OWNER.scope_id) == []


def test_expired_input_and_nonclaimed_job_never_execute(tmp_path: Path) -> None:
    coordinator, _, action_store, research, _, _ = _components(tmp_path)
    running = ScheduledJob(
        job_id="job-1",
        actor_id=OWNER.owner_id,
        session_id=OWNER.scope_id,
        action=ProductivityAction.BROWSER_RESEARCH.value,
        proposal_id="proposal-1",
        state=JobState.RUNNING,
        next_run_at=NOW,
        created_at=NOW - timedelta(days=2),
        updated_at=NOW,
    )
    action_store.put(
        StoredActionEnvelope(
            job_id="job-1",
            proposal_id="proposal-1",
            actor_id=OWNER.owner_id,
            session_id=OWNER.scope_id,
            adapter_input=_research_input(),
            created_at=NOW - timedelta(days=2),
            expires_at=NOW,
        )
    )
    assert coordinator.execute_claimed(running).code is ScheduledReadExecutionCode.PERMANENT_FAILURE
    scheduled = running.with_state(JobState.INTERRUPTED, updated_at=NOW).with_state(
        JobState.SCHEDULED, updated_at=NOW
    )
    assert coordinator.execute_claimed(scheduled).code is ScheduledReadExecutionCode.PERMANENT_FAILURE
    assert research.calls == []


def test_adapter_failure_is_retryable_and_repr_is_content_free(tmp_path: Path) -> None:
    coordinator, job_store, _, research, _, _ = _components(tmp_path)
    research.result = AdapterResult(AdapterResultStatus.FAILED, "private-provider")
    coordinator.schedule(
        owner=OWNER,
        proposal_id="proposal-1",
        action=ProductivityAction.BROWSER_RESEARCH,
        adapter_input=_research_input(),
        next_run_at=NOW + timedelta(hours=1),
    )
    outcome = coordinator.execute_claimed(_claim(job_store))
    assert outcome.code is ScheduledReadExecutionCode.RETRYABLE_FAILURE
    assert "private" not in repr(outcome)
    assert "provider" not in repr(outcome)


def test_constructor_rejects_missing_or_write_adapter(tmp_path: Path) -> None:
    coordinator, job_store, action_store, research, calendar, _ = _components(tmp_path)
    kwargs = dict(
        creation_service_factory=coordinator._creation_service_factory,
        job_store=job_store,
        action_store=action_store,
        clock=lambda: NOW,
        job_id_factory=lambda: "job-2",
    )
    with pytest.raises(ValueError):
        ScheduledReadCoordinator(
            **kwargs,
            adapters={ProductivityAction.BROWSER_RESEARCH: research},
        )
    with pytest.raises(ValueError):
        ScheduledReadCoordinator(
            **kwargs,
            adapters={
                ProductivityAction.BROWSER_RESEARCH: research,
                ProductivityAction.CALENDAR_READ: calendar,
                ProductivityAction.EMAIL_DRAFT: lambda value: value,
            },
        )


def test_source_has_no_scheduler_or_external_side_effects() -> None:
    source = (
        Path(__file__).parents[1] / "core" / "jobs" / "coordinator.py"
    ).read_text(encoding="utf-8")
    for marker in (
        "import subprocess",
        "import requests",
        "import socket",
        "osascript",
        "threading",
        "logging.",
        "\nprint(",
    ):
        assert marker not in source
