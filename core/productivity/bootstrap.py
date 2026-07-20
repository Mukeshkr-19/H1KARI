"""Private, lazy composition for the Phase 3 productivity runtime."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from core.productivity.approval_store import SqliteApprovalStore
from core.productivity.adapters.macos_actions import (
    CalendarDraftMacAdapter,
    EmailDraftMacAdapter,
    ReminderCreateMacAdapter,
)
from core.productivity.adapters.calendar_read import CalendarReadMacAdapter
from core.productivity.adapters.research import BrowserResearchAdapter
from core.productivity.calendar import (
    CalendarDraftProposalFactory,
    CalendarPreparationRegistry,
    CalendarReadProposalFactory,
)
from core.productivity.email_draft import (
    EmailDraftPreparationRegistry,
    EmailDraftProposalFactory,
)
from core.productivity.research import (
    ResearchPreparationRegistry,
    ResearchProposalFactory,
)
from core.productivity.reminder import (
    ReminderPreparationRegistry,
    ReminderProposalFactory,
)
from core.productivity.runtime import ProductivityRuntime
from core.productivity.service import ProductivityService
from core.productivity.contracts import ProductivityAction
from core.productivity.execution import ActionAdapter, ProductivityExecutionCoordinator
from core.productivity.tool_permissions import PermissionManifest
from core.productivity.tool_wrapper import ToolPolicyWrapper, ToolRegistration
from core.runtime_paths import hikari_home


PRODUCTIVITY_DB_NAME = "productivity-approvals.db"


def _production_approval_id() -> str:
    """Return a cryptographically random canonical opaque identifier."""
    return f"approval-{secrets.token_hex(16)}"


def _production_proposal_id() -> str:
    """Return a cryptographically random canonical proposal identifier."""
    return f"proposal-{secrets.token_hex(16)}"


def create_calendar_preparation(
    *,
    clock: Callable[[], float] | None = None,
    proposal_id_factory: Callable[[], str] | None = None,
    registry_limit: int = 64,
) -> tuple[
    CalendarReadProposalFactory,
    CalendarDraftProposalFactory,
    CalendarPreparationRegistry,
]:
    """Construct the side-effect-free calendar preparation boundary."""
    clock_fn = clock or time.time
    proposal_fn = proposal_id_factory or _production_proposal_id
    return (
        CalendarReadProposalFactory(clock_fn, proposal_fn),
        CalendarDraftProposalFactory(clock_fn, proposal_fn),
        CalendarPreparationRegistry(limit=registry_limit),
    )


def create_research_preparation(
    *,
    clock: Callable[[], float] | None = None,
    proposal_id_factory: Callable[[], str] | None = None,
    registry_limit: int = 64,
) -> tuple[ResearchProposalFactory, ResearchPreparationRegistry]:
    """Construct the side-effect-free browser-research preparation boundary."""
    clock_fn = clock or time.time
    proposal_fn = proposal_id_factory or _production_proposal_id
    return (
        ResearchProposalFactory(clock_fn, proposal_fn),
        ResearchPreparationRegistry(limit=registry_limit),
    )


def create_email_draft_preparation(
    *,
    clock: Callable[[], float] | None = None,
    proposal_id_factory: Callable[[], str] | None = None,
    registry_limit: int = 64,
) -> tuple[EmailDraftProposalFactory, EmailDraftPreparationRegistry]:
    """Construct the side-effect-free email-draft preparation boundary."""
    return (
        EmailDraftProposalFactory(
            clock or time.time,
            proposal_id_factory or _production_proposal_id,
        ),
        EmailDraftPreparationRegistry(limit=registry_limit),
    )


def create_reminder_preparation(
    *,
    clock: Callable[[], float] | None = None,
    proposal_id_factory: Callable[[], str] | None = None,
    registry_limit: int = 64,
) -> tuple[ReminderProposalFactory, ReminderPreparationRegistry]:
    """Construct the side-effect-free reminder preparation boundary."""
    clock_fn = clock or time.time
    proposal_fn = proposal_id_factory or _production_proposal_id
    return (
        ReminderProposalFactory(clock_fn, proposal_fn),
        ReminderPreparationRegistry(limit=registry_limit),
    )


def productivity_db_path() -> Path:
    """Resolve the approval database beneath private HIKARI runtime state."""
    return (hikari_home() / "policy" / PRODUCTIVITY_DB_NAME).resolve()


def create_productivity_runtime(
    *,
    db_path: str | Path | None = None,
    clock: Callable[[], float] | None = None,
    approval_id_factory: Callable[[], str] | None = None,
) -> ProductivityRuntime:
    """Construct the productivity store, service, and controller.

    Construction is explicit and creates only the approval database. Importing
    this module has no filesystem side effects.
    """
    resolved_db = (
        productivity_db_path()
        if db_path is None
        else Path(db_path).expanduser().resolve()
    )
    store = SqliteApprovalStore(str(resolved_db))
    service = ProductivityService(store)
    return ProductivityRuntime(
        service,
        clock or time.time,
        approval_id_factory or _production_approval_id,
    )


def create_productivity_execution_coordinator(
    runtime: ProductivityRuntime,
    *,
    adapters: Mapping[ProductivityAction, ActionAdapter] | None = None,
) -> ProductivityExecutionCoordinator:
    """Construct the server-only coordinator for implemented action adapters.

    Construction performs no external action. Every registered adapter has a
    bounded transport result or terminal status contract.
    """
    adapter_map: Mapping[ProductivityAction, ActionAdapter]
    if adapters is None:
        adapter_map = {
            ProductivityAction.BROWSER_RESEARCH: BrowserResearchAdapter(),
            ProductivityAction.EMAIL_DRAFT: EmailDraftMacAdapter(),
            ProductivityAction.CALENDAR_READ: CalendarReadMacAdapter(),
            ProductivityAction.CALENDAR_DRAFT: CalendarDraftMacAdapter(),
            ProductivityAction.REMINDER_CREATE: ReminderCreateMacAdapter(),
        }
    else:
        adapter_map = adapters
    return ProductivityExecutionCoordinator(runtime, adapter_map)


def create_tool_policy_wrapper(
    *,
    manifest: PermissionManifest | None = None,
    registrations: tuple[ToolRegistration, ...] = (),
) -> ToolPolicyWrapper:
    """Construct the third-party boundary; production defaults to deny-all."""
    if manifest is None and registrations == ():
        return ToolPolicyWrapper.disabled()
    return ToolPolicyWrapper(manifest, registrations)
