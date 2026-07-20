"""Phase 3 productivity-action contracts."""

from core.productivity.approval_store import SqliteApprovalStore
from core.productivity.authorization import (
    ApprovalScope,
    ProductivityApproval,
    evaluate_consume,
    issue_for_proposal,
    snapshot_digest,
    validate_issue,
)
from core.productivity.contracts import (
    ActionProposal,
    ActionTarget,
    ExecutionResult,
    ExecutionStatus,
    PreviewField,
    ProductivityAction,
    TargetKind,
)
from core.productivity.service import ProductivityCode, ProductivityService, ServiceResult
from core.productivity.runtime import ConfirmationResult, ProductivityRuntime
from core.productivity.calendar import (
    CalendarDraftPreparation,
    CalendarDraftProposalFactory,
    CalendarPreparationError,
    CalendarPreparationRegistry,
    CalendarReadPreparation,
    CalendarReadProposalFactory,
    PreparedCalendarEventDraft,
    PreparedCalendarRead,
)
from core.productivity.email_draft import (
    EmailDraftPreparation,
    EmailDraftPreparationError,
    EmailDraftPreparationRegistry,
    EmailDraftProposalFactory,
    PreparedEmailDraft,
)
from core.productivity.research import (
    PreparedResearchInput,
    ResearchPreparation,
    ResearchPreparationError,
    ResearchPreparationRegistry,
    ResearchProposalFactory,
)
from core.productivity.reminder import (
    PreparedReminderInput,
    ReminderPreparation,
    ReminderPreparationError,
    ReminderPreparationRegistry,
    ReminderProposalFactory,
)
from core.productivity.transport import (
    TransportError,
    confirmation_required,
    error_code_for,
    error_message,
    preview_entries,
    target_entries,
    update_message,
)

__all__ = [
    "ActionProposal",
    "ActionTarget",
    "ApprovalScope",
    "CalendarDraftPreparation",
    "CalendarDraftProposalFactory",
    "CalendarPreparationError",
    "CalendarPreparationRegistry",
    "CalendarReadPreparation",
    "CalendarReadProposalFactory",
    "ConfirmationResult",
    "ExecutionResult",
    "ExecutionStatus",
    "EmailDraftPreparation",
    "EmailDraftPreparationError",
    "EmailDraftPreparationRegistry",
    "EmailDraftProposalFactory",
    "PreviewField",
    "PreparedCalendarEventDraft",
    "PreparedCalendarRead",
    "PreparedEmailDraft",
    "PreparedReminderInput",
    "PreparedResearchInput",
    "ProductivityAction",
    "ProductivityApproval",
    "ProductivityCode",
    "ProductivityRuntime",
    "ReminderPreparation",
    "ReminderPreparationError",
    "ReminderPreparationRegistry",
    "ReminderProposalFactory",
    "ResearchPreparation",
    "ResearchPreparationError",
    "ResearchPreparationRegistry",
    "ResearchProposalFactory",
    "ProductivityService",
    "ServiceResult",
    "SqliteApprovalStore",
    "TargetKind",
    "TransportError",
    "confirmation_required",
    "error_code_for",
    "error_message",
    "evaluate_consume",
    "issue_for_proposal",
    "preview_entries",
    "snapshot_digest",
    "target_entries",
    "update_message",
    "validate_issue",
]
