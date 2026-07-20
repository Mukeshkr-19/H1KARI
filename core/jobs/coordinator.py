"""Isolated coordinator for durable scheduled read actions.

Only browser research and calendar reads are accepted.  The coordinator
retains an exact validated input before creating an executable job, and later
executes only the retained input bound to an exact claimed job.  It does not
schedule write actions, deliver results, start workers, or expose stored
content through errors or representations.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Protocol

from core.jobs.action_store import (
    ScheduledActionStore,
    StoredActionEnvelope,
)
from core.jobs.contracts import JobState, ScheduledJob
from core.jobs.creation import JobCreationRequest, JobCreationService
from core.jobs.quiet_hours import QuietHours
from core.jobs.store import ScheduledJobStore
from core.productivity.action_inputs import (
    BrowserResearchAdapterInput,
    CalendarReadAdapterInput,
)
from core.productivity.action_results import BrowserSearchResult, CalendarReadResult
from core.productivity.adapters.research import BrowserResearchAdapterResult
from core.productivity.contracts import ProductivityAction
from core.productivity.execution import AdapterResult, AdapterResultStatus


_CANONICAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_OWNER_SCOPE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_FINGERPRINT_RE = re.compile(r"^sha256\.[0-9a-f]{64}$")
_MAX_RETENTION = timedelta(days=366)
_EXECUTION_GRACE = timedelta(days=1)
_ALLOWED_ACTIONS = frozenset(
    (ProductivityAction.BROWSER_RESEARCH, ProductivityAction.CALENDAR_READ)
)
_INPUT_TYPES = {
    ProductivityAction.BROWSER_RESEARCH: BrowserResearchAdapterInput,
    ProductivityAction.CALENDAR_READ: CalendarReadAdapterInput,
}


class ScheduledReadScheduleCode(StrEnum):
    SCHEDULED = "scheduled"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"
    COMPENSATION_INCOMPLETE = "compensation_incomplete"


class ScheduledReadExecutionCode(StrEnum):
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"


@dataclass(frozen=True, repr=False)
class StableOwnerScope:
    """Server-derived stable ownership scope for durable scheduled state."""

    owner_id: str
    scope_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.owner_id, str) or not _OWNER_SCOPE_RE.fullmatch(
            self.owner_id
        ):
            raise ValueError("scheduled owner scope is invalid")
        if not isinstance(self.scope_id, str) or not _OWNER_SCOPE_RE.fullmatch(
            self.scope_id
        ):
            raise ValueError("scheduled owner scope is invalid")

    def __repr__(self) -> str:
        return "StableOwnerScope()"


@dataclass(frozen=True, repr=False)
class ScheduledReadScheduleResult:
    code: ScheduledReadScheduleCode
    job: ScheduledJob | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, ScheduledReadScheduleCode):
            raise ValueError("scheduled read result is invalid")
        if (self.code is ScheduledReadScheduleCode.SCHEDULED) != isinstance(
            self.job, ScheduledJob
        ):
            raise ValueError("scheduled read result is invalid")

    def __repr__(self) -> str:
        return f"ScheduledReadScheduleResult(code={self.code.value!r})"


ReadResult = BrowserSearchResult | CalendarReadResult


@dataclass(frozen=True, repr=False)
class ScheduledReadExecutionOutcome:
    code: ScheduledReadExecutionCode
    fingerprint: str | None = None
    result: ReadResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, ScheduledReadExecutionCode):
            raise ValueError("scheduled read outcome is invalid")
        succeeded = self.code is ScheduledReadExecutionCode.SUCCEEDED
        if succeeded:
            if not isinstance(self.fingerprint, str) or not _FINGERPRINT_RE.fullmatch(
                self.fingerprint
            ):
                raise ValueError("scheduled read outcome is invalid")
            if not isinstance(self.result, (BrowserSearchResult, CalendarReadResult)):
                raise ValueError("scheduled read outcome is invalid")
        elif self.fingerprint is not None or self.result is not None:
            raise ValueError("scheduled read outcome is invalid")

    def __repr__(self) -> str:
        return f"ScheduledReadExecutionOutcome(code={self.code.value!r})"


class ReadAdapter(Protocol):
    def __call__(self, input_value: object) -> object: ...


CreatorFactory = Callable[[Callable[[], str]], JobCreationService]


class ScheduledReadCoordinator:
    """Schedule and execute only exact retained read-action inputs."""

    def __init__(
        self,
        *,
        creation_service_factory: CreatorFactory,
        job_store: ScheduledJobStore,
        action_store: ScheduledActionStore,
        clock: Callable[[], datetime],
        job_id_factory: Callable[[], str],
        adapters: Mapping[ProductivityAction, ReadAdapter],
    ) -> None:
        if not callable(creation_service_factory):
            raise TypeError("creation_service_factory must be callable")
        if not isinstance(job_store, ScheduledJobStore):
            raise TypeError("job_store must be a ScheduledJobStore")
        if not isinstance(action_store, ScheduledActionStore):
            raise TypeError("action_store must be a ScheduledActionStore")
        if not callable(clock) or not callable(job_id_factory):
            raise TypeError("coordinator factories must be callable")
        if not isinstance(adapters, Mapping) or set(adapters) != _ALLOWED_ACTIONS:
            raise ValueError("scheduled read adapters are invalid")
        if any(not callable(adapter) for adapter in adapters.values()):
            raise ValueError("scheduled read adapters are invalid")
        if len({id(adapter) for adapter in adapters.values()}) != len(adapters):
            raise ValueError("scheduled read adapters are invalid")

        self._creation_service_factory = creation_service_factory
        self._job_store = job_store
        self._action_store = action_store
        self._clock = clock
        self._job_id_factory = job_id_factory
        self._adapters = dict(adapters)

    def __repr__(self) -> str:
        return "ScheduledReadCoordinator()"

    def _now(self) -> datetime | None:
        try:
            value = self._clock()
            numeric = value.timestamp()
        except Exception:
            return None
        if (
            isinstance(value, bool)
            or not isinstance(value, datetime)
            or value.tzinfo is None
            or not math.isfinite(numeric)
        ):
            return None
        return value

    def _job_id(self) -> str | None:
        try:
            value = self._job_id_factory()
        except Exception:
            return None
        if not isinstance(value, str) or not _CANONICAL_ID_RE.fullmatch(value):
            return None
        return value

    @staticmethod
    def _valid_input(action: object, adapter_input: object) -> bool:
        if not isinstance(action, ProductivityAction) or action not in _ALLOWED_ACTIONS:
            return False
        if type(adapter_input) is not _INPUT_TYPES[action]:
            return False
        try:
            adapter_input.validate()
        except Exception:
            return False
        return adapter_input.action is action

    def _remove_created_job(self, job_id: str, owner: StableOwnerScope) -> bool:
        try:
            job = self._job_store.get(
                job_id, actor_id=owner.owner_id, session_id=owner.scope_id
            )
            if job is None:
                return True
            return self._job_store.remove_if_unmodified(
                job_id,
                actor_id=owner.owner_id,
                session_id=owner.scope_id,
                expected_updated_at=job.updated_at,
                expected_state=JobState.SCHEDULED,
            )
        except Exception:
            return False

    def _remove_envelope(self, job_id: str, owner: StableOwnerScope) -> bool:
        try:
            return self._action_store.delete(
                job_id,
                actor_id=owner.owner_id,
                session_id=owner.scope_id,
                expected_revision=1,
            )
        except Exception:
            return False

    def schedule(
        self,
        *,
        owner: StableOwnerScope,
        proposal_id: str,
        action: ProductivityAction,
        adapter_input: object,
        next_run_at: datetime,
        max_attempts: int = 1,
        quiet_hours: QuietHours | None = None,
    ) -> ScheduledReadScheduleResult:
        """Retain one input before creating its exact scheduled job."""
        if not isinstance(owner, StableOwnerScope):
            return ScheduledReadScheduleResult(ScheduledReadScheduleCode.INVALID)
        if not isinstance(proposal_id, str) or not _CANONICAL_ID_RE.fullmatch(
            proposal_id
        ):
            return ScheduledReadScheduleResult(ScheduledReadScheduleCode.INVALID)
        if not self._valid_input(action, adapter_input):
            return ScheduledReadScheduleResult(ScheduledReadScheduleCode.INVALID)

        now = self._now()
        if now is None:
            return ScheduledReadScheduleResult(ScheduledReadScheduleCode.UNAVAILABLE)
        if not isinstance(next_run_at, datetime) or next_run_at.tzinfo is None:
            return ScheduledReadScheduleResult(ScheduledReadScheduleCode.INVALID)
        try:
            expires_at = next_run_at + _EXECUTION_GRACE
            invalid_window = next_run_at <= now or expires_at - now > _MAX_RETENTION
            request = JobCreationRequest(
                actor_id=owner.owner_id,
                session_id=owner.scope_id,
                action=action.value,
                proposal_id=proposal_id,
                next_run_at=next_run_at,
                max_attempts=max_attempts,
                quiet_hours=quiet_hours,
            )
        except Exception:
            return ScheduledReadScheduleResult(ScheduledReadScheduleCode.INVALID)
        if invalid_window:
            return ScheduledReadScheduleResult(ScheduledReadScheduleCode.INVALID)
        job_id = self._job_id()
        if job_id is None:
            return ScheduledReadScheduleResult(ScheduledReadScheduleCode.UNAVAILABLE)

        try:
            envelope = StoredActionEnvelope(
                job_id=job_id,
                proposal_id=proposal_id,
                actor_id=owner.owner_id,
                session_id=owner.scope_id,
                adapter_input=adapter_input,
                created_at=now,
                expires_at=expires_at,
            )
            self._action_store.put(envelope)
        except Exception:
            return ScheduledReadScheduleResult(ScheduledReadScheduleCode.UNAVAILABLE)

        reservation_used = False

        def reserved_job_id() -> str:
            nonlocal reservation_used
            if reservation_used:
                raise ValueError("reserved job id is single use")
            reservation_used = True
            return job_id

        try:
            creator = self._creation_service_factory(reserved_job_id)
            if not isinstance(creator, JobCreationService):
                raise TypeError
            job = creator.create(request)
            if (
                not reservation_used
                or job.job_id != job_id
                or job.actor_id != owner.owner_id
                or job.session_id != owner.scope_id
                or job.proposal_id != proposal_id
                or job.action != action.value
                or job.state is not JobState.SCHEDULED
            ):
                raise ValueError
        except Exception:
            job_removed = self._remove_created_job(job_id, owner)
            envelope_removed = self._remove_envelope(job_id, owner)
            code = (
                ScheduledReadScheduleCode.UNAVAILABLE
                if job_removed and envelope_removed
                else ScheduledReadScheduleCode.COMPENSATION_INCOMPLETE
            )
            return ScheduledReadScheduleResult(code)

        return ScheduledReadScheduleResult(ScheduledReadScheduleCode.SCHEDULED, job)

    def execute_claimed(self, job: object) -> ScheduledReadExecutionOutcome:
        """Execute the exact retained input for one exact claimed read job."""
        if (
            not isinstance(job, ScheduledJob)
            or job.state is not JobState.RUNNING
            or job.action not in {action.value for action in _ALLOWED_ACTIONS}
        ):
            return ScheduledReadExecutionOutcome(
                ScheduledReadExecutionCode.PERMANENT_FAILURE
            )

        action = ProductivityAction(job.action)
        now = self._now()
        if now is None:
            return ScheduledReadExecutionOutcome(
                ScheduledReadExecutionCode.RETRYABLE_FAILURE
            )
        try:
            envelope = self._action_store.get(
                job.job_id, actor_id=job.actor_id, session_id=job.session_id
            )
        except Exception:
            return ScheduledReadExecutionOutcome(
                ScheduledReadExecutionCode.RETRYABLE_FAILURE
            )
        if (
            envelope is None
            or envelope.proposal_id != job.proposal_id
            or envelope.action is not action
            or type(envelope.adapter_input) is not _INPUT_TYPES[action]
            or envelope.expires_at <= now
        ):
            return ScheduledReadExecutionOutcome(
                ScheduledReadExecutionCode.PERMANENT_FAILURE
            )

        try:
            raw_result = self._adapters[action](envelope.adapter_input)
        except Exception:
            return ScheduledReadExecutionOutcome(
                ScheduledReadExecutionCode.RETRYABLE_FAILURE
            )
        result = self._extract_result(action, raw_result)
        if result is None:
            return ScheduledReadExecutionOutcome(
                ScheduledReadExecutionCode.RETRYABLE_FAILURE
            )
        try:
            fingerprint = _result_fingerprint(result)
        except Exception:
            return ScheduledReadExecutionOutcome(
                ScheduledReadExecutionCode.PERMANENT_FAILURE
            )
        return ScheduledReadExecutionOutcome(
            ScheduledReadExecutionCode.SUCCEEDED,
            fingerprint=fingerprint,
            result=result,
        )

    @staticmethod
    def _extract_result(
        action: ProductivityAction, raw_result: object
    ) -> ReadResult | None:
        if action is ProductivityAction.BROWSER_RESEARCH:
            if (
                type(raw_result) is BrowserResearchAdapterResult
                and raw_result.status is AdapterResultStatus.SUCCESS
                and isinstance(raw_result.result, BrowserSearchResult)
            ):
                return raw_result.result
            return None
        if action is ProductivityAction.CALENDAR_READ:
            if isinstance(raw_result, CalendarReadResult):
                return raw_result
            if isinstance(raw_result, AdapterResult):
                return None
        return None


def _canonical_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _result_fingerprint(result: ReadResult) -> str:
    if isinstance(result, BrowserSearchResult):
        payload = {
            "kind": "browser.research",
            "query": result.query,
            "items": [
                {
                    "title": item.title,
                    "url": item.url,
                    "domain": item.domain,
                    "snippet": item.snippet,
                }
                for item in result.items
            ],
        }
    elif isinstance(result, CalendarReadResult):
        payload = {
            "kind": "calendar.read",
            "calendar_label": result.calendar_label,
            "events": [
                {
                    "title": event.title,
                    "start": _canonical_datetime(event.start),
                    "end": _canonical_datetime(event.end),
                    "calendar_label": event.calendar_label,
                    "location": event.location,
                }
                for event in result.events
            ],
        }
    else:
        raise ValueError("scheduled read result is invalid")
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256.{hashlib.sha256(canonical).hexdigest()}"
