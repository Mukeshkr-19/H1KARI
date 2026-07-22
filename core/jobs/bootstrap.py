"""Private, lazy composition for the Phase 3 scheduled-job runtime."""

from __future__ import annotations

import secrets
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.jobs.audit_store import ScheduledJobAuditStore
from core.jobs.action_store import ScheduledActionStore
from core.jobs.coordinator import ScheduledReadCoordinator
from core.jobs.contracts import ScheduledJob
from core.jobs.creation import JobCreationService
from core.jobs.lifecycle import JobLifecycleController
from core.jobs.runner import ScheduledJobRunner
from core.jobs.runtime import ScheduledJobRuntime
from core.jobs.service import ScheduledJobService
from core.jobs.store import ScheduledJobStore
from core.runtime_paths import hikari_home
from core.productivity.adapters.calendar_read import CalendarReadMacAdapter
from core.productivity.adapters.research import BrowserResearchAdapter
from core.productivity.contracts import ProductivityAction


SCHEDULED_JOBS_DB_NAME = "scheduled-jobs.db"
SCHEDULED_JOBS_AUDIT_DB_NAME = "scheduled-jobs-audit.db"
SCHEDULED_OWNER_SCOPE_NAME = "scheduled-owner-scope"
SCHEDULED_ACTIONS_DB_NAME = "scheduled-actions.db"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def scheduled_jobs_db_path() -> Path:
    """Resolve the job database beneath private HIKARI runtime state."""
    return (hikari_home() / "policy" / SCHEDULED_JOBS_DB_NAME).resolve()


def scheduled_jobs_audit_db_path() -> Path:
    """Resolve the job audit database beneath private HIKARI runtime state."""
    return (hikari_home() / "policy" / SCHEDULED_JOBS_AUDIT_DB_NAME).resolve()


def scheduled_owner_scope_path() -> Path:
    """Resolve the private installation-scoped scheduled owner identifier."""
    return (hikari_home() / "policy" / SCHEDULED_OWNER_SCOPE_NAME).resolve()


def _load_or_create_owner_scope(path: Path) -> str:
    """Return a private stable scope generated only by the server bootstrap."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    try:
        value = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        value = f"installation-{secrets.token_hex(16)}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(path, flags, 0o600)
        try:
            os.write(descriptor, (value + "\n").encode("ascii"))
        finally:
            os.close(descriptor)
    except Exception:
        raise RuntimeError("scheduled owner scope unavailable") from None
    if (
        not value
        or len(value) > 128
        or any(
            not (char.isascii() and (char.isalnum() or char in "._:-"))
            for char in value
        )
    ):
        raise RuntimeError("scheduled owner scope unavailable")
    try:
        os.chmod(path, 0o600)
    except OSError:
        raise RuntimeError("scheduled owner scope unavailable") from None
    return value


def _event_id() -> str:
    return f"event-{secrets.token_hex(16)}"


def _job_id() -> str:
    return f"job-{secrets.token_hex(16)}"


@dataclass(frozen=True)
class ScheduledJobSubsystem:
    """Lazy server-only composition for controls, creation, and execution."""

    runtime: ScheduledJobRuntime
    coordinator: ScheduledReadCoordinator
    store: ScheduledJobStore
    action_store: ScheduledActionStore
    audit_store: ScheduledJobAuditStore
    lifecycle: JobLifecycleController
    clock: Callable[[], datetime]
    event_id_factory: Callable[[], str]

    def create_runner(
        self, execute_callable: Callable[[object], object]
    ) -> ScheduledJobRunner:
        def delete_terminal_action(job: object) -> None:
            if not isinstance(job, ScheduledJob):
                raise TypeError("job must be a ScheduledJob")
            envelope = self.action_store.get(
                job.job_id,
                actor_id=job.actor_id,
                session_id=job.session_id,
            )
            if envelope is not None:
                deleted = self.action_store.delete(
                    job.job_id,
                    actor_id=job.actor_id,
                    session_id=job.session_id,
                    expected_revision=envelope.revision,
                )
                if not deleted:
                    raise RuntimeError("terminal action cleanup failed")

        return ScheduledJobRunner(
            self.store,
            self.audit_store,
            self.clock,
            execute_callable,
            self.event_id_factory,
            terminal_callback=delete_terminal_action,
        )


def create_scheduled_job_runtime(
    *,
    db_path: str | Path | None = None,
    audit_db_path: str | Path | None = None,
    clock: Callable[[], datetime] | None = None,
    event_id_factory: Callable[[], str] | None = None,
    owner_scope_id: str | None = None,
    owner_scope_path: str | Path | None = None,
) -> ScheduledJobRuntime:
    """Construct the store, service, and runtime only when explicitly called."""
    resolved_db = (
        scheduled_jobs_db_path()
        if db_path is None
        else Path(db_path).expanduser().resolve()
    )
    resolved_audit_db = (
        (
            scheduled_jobs_audit_db_path()
            if db_path is None
            else resolved_db.with_name(SCHEDULED_JOBS_AUDIT_DB_NAME)
        )
        if audit_db_path is None
        else Path(audit_db_path).expanduser().resolve()
    )
    clock_fn = clock or _utc_now
    resolved_owner_scope = owner_scope_id
    if resolved_owner_scope is None:
        scope_path = (
            scheduled_owner_scope_path()
            if owner_scope_path is None and db_path is None
            else (
                resolved_db.with_name(SCHEDULED_OWNER_SCOPE_NAME)
                if owner_scope_path is None
                else Path(owner_scope_path).expanduser().resolve()
            )
        )
        resolved_owner_scope = _load_or_create_owner_scope(scope_path)
    store = ScheduledJobStore(resolved_db)
    audit_store = ScheduledJobAuditStore(resolved_audit_db)
    lifecycle = JobLifecycleController(
        store,
        audit_store,
        clock_fn,
        event_id_factory or _event_id,
    )
    service = ScheduledJobService(store, clock=clock_fn, lifecycle=lifecycle)
    return ScheduledJobRuntime(service, owner_scope_id=resolved_owner_scope)


def create_scheduled_job_subsystem(
    *,
    db_path: str | Path | None = None,
    audit_db_path: str | Path | None = None,
    action_db_path: str | Path | None = None,
    owner_scope_id: str | None = None,
    owner_scope_path: str | Path | None = None,
    clock: Callable[[], datetime] | None = None,
    job_id_factory: Callable[[], str] | None = None,
    event_id_factory: Callable[[], str] | None = None,
    adapters: dict[ProductivityAction, Callable[[object], object]] | None = None,
) -> ScheduledJobSubsystem:
    """Construct the complete scheduled-read subsystem only for server startup."""
    resolved_db = (
        scheduled_jobs_db_path()
        if db_path is None
        else Path(db_path).expanduser().resolve()
    )
    resolved_audit = (
        scheduled_jobs_audit_db_path()
        if audit_db_path is None and db_path is None
        else (
            resolved_db.with_name(SCHEDULED_JOBS_AUDIT_DB_NAME)
            if audit_db_path is None
            else Path(audit_db_path).expanduser().resolve()
        )
    )
    resolved_actions = (
        (hikari_home() / "policy" / SCHEDULED_ACTIONS_DB_NAME).resolve()
        if action_db_path is None and db_path is None
        else (
            resolved_db.with_name(SCHEDULED_ACTIONS_DB_NAME)
            if action_db_path is None
            else Path(action_db_path).expanduser().resolve()
        )
    )
    resolved_scope = owner_scope_id
    if resolved_scope is None:
        scope_path = (
            scheduled_owner_scope_path()
            if owner_scope_path is None and db_path is None
            else (
                resolved_db.with_name(SCHEDULED_OWNER_SCOPE_NAME)
                if owner_scope_path is None
                else Path(owner_scope_path).expanduser().resolve()
            )
        )
        resolved_scope = _load_or_create_owner_scope(scope_path)

    clock_fn = clock or _utc_now
    event_factory = event_id_factory or _event_id
    store = ScheduledJobStore(resolved_db)
    audit_store = ScheduledJobAuditStore(resolved_audit)
    action_store = ScheduledActionStore(resolved_actions)
    lifecycle = JobLifecycleController(store, audit_store, clock_fn, event_factory)
    service = ScheduledJobService(store, clock=clock_fn, lifecycle=lifecycle)
    runtime = ScheduledJobRuntime(service, owner_scope_id=resolved_scope)

    def creator_factory(reserved_job_id: Callable[[], str]) -> JobCreationService:
        return JobCreationService(
            store,
            audit_store,
            clock_fn,
            reserved_job_id,
            event_factory,
        )

    read_adapters = adapters or {
        ProductivityAction.BROWSER_RESEARCH: BrowserResearchAdapter(),
        ProductivityAction.CALENDAR_READ: CalendarReadMacAdapter(),
    }
    coordinator = ScheduledReadCoordinator(
        creation_service_factory=creator_factory,
        job_store=store,
        action_store=action_store,
        clock=clock_fn,
        job_id_factory=job_id_factory or _job_id,
        adapters=read_adapters,
    )
    return ScheduledJobSubsystem(
        runtime=runtime,
        coordinator=coordinator,
        store=store,
        action_store=action_store,
        audit_store=audit_store,
        lifecycle=lifecycle,
        clock=clock_fn,
        event_id_factory=event_factory,
    )
