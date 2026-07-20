"""Pure scheduled-job domain contracts for Phase 3.

This package defines immutable value objects and pure validation helpers only.
It contains no runner, database, timer, thread, notification, or adapter logic.
"""

from core.jobs.contracts import (
    IdentifierError,
    JobState,
    RetryBudgetExhausted,
    ScheduledJob,
    TransitionError,
    can_transition,
    delivery_is_meaningful_change,
    execution_is_eligible,
    retry_budget_remains,
    transition_table,
    validate_fingerprint,
)
from core.jobs.quiet_hours import (
    QuietHours,
    QuietHoursError,
    is_quiet,
)
from core.jobs.runtime import ScheduledJobRuntime
from core.jobs.service import (
    JobControlResult,
    JobServiceCode,
    ScheduledJobService,
    ScheduledJobView,
)
from core.jobs.store import ScheduledJobStore
from core.jobs.transport import (
    TransportError,
    error_message as scheduled_job_error_message,
    job_view_to_dict,
    list_message as scheduled_job_list_message,
    result_message as scheduled_job_result_message,
    update_message as scheduled_job_update_message,
)
from core.jobs.bootstrap import (
    SCHEDULED_JOBS_AUDIT_DB_NAME,
    SCHEDULED_JOBS_DB_NAME,
    SCHEDULED_ACTIONS_DB_NAME,
    SCHEDULED_OWNER_SCOPE_NAME,
    ScheduledJobSubsystem,
    create_scheduled_job_subsystem,
    create_scheduled_job_runtime,
    scheduled_jobs_audit_db_path,
    scheduled_jobs_db_path,
    scheduled_owner_scope_path,
)
from core.jobs.audit import (
    AuditEvent,
    AuditReasonCode,
    AuditStoreError,
    AuditTransitionError,
    AuditValidationError,
    validate_transition,
)
from core.jobs.audit_store import ScheduledJobAuditStore

__all__ = [
    "JobState",
    "ScheduledJob",
    "ScheduledJobStore",
    "TransitionError",
    "IdentifierError",
    "RetryBudgetExhausted",
    "can_transition",
    "delivery_is_meaningful_change",
    "execution_is_eligible",
    "retry_budget_remains",
    "transition_table",
    "validate_fingerprint",
    "QuietHours",
    "QuietHoursError",
    "is_quiet",
    "JobServiceCode",
    "JobControlResult",
    "ScheduledJobView",
    "ScheduledJobService",
    "ScheduledJobRuntime",
    "TransportError",
    "job_view_to_dict",
    "scheduled_job_error_message",
    "scheduled_job_list_message",
    "scheduled_job_update_message",
    "scheduled_job_result_message",
    "SCHEDULED_JOBS_DB_NAME",
    "SCHEDULED_ACTIONS_DB_NAME",
    "SCHEDULED_JOBS_AUDIT_DB_NAME",
    "SCHEDULED_OWNER_SCOPE_NAME",
    "create_scheduled_job_runtime",
    "create_scheduled_job_subsystem",
    "ScheduledJobSubsystem",
    "scheduled_jobs_audit_db_path",
    "scheduled_jobs_db_path",
    "scheduled_owner_scope_path",
    "AuditEvent",
    "AuditReasonCode",
    "AuditStoreError",
    "AuditTransitionError",
    "AuditValidationError",
    "validate_transition",
    "ScheduledJobAuditStore",
]
