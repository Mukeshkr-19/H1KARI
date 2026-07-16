"""Policy-gated, durable workflow for explaining one local text document."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Collection, Optional

from core.action_audit import AuditResultStatus
from core.action_policy import Actor, ActorContext, PolicyOutcome, validate_actor_context
from core.document_reader import DocumentReadError, TextDocument, read_text_document
from core.policy_service import ActionRequest, PolicyService
from core.router import AIRouter, GenerationResult
from core.tasks import TaskIntentService, TaskRecord, TaskRecordContext, TaskStatus

_PROVIDER = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_MAX_PROVIDERS = 8
_MAX_QUESTION_CHARS = 2_000
_GRANT_LIFETIME_SECONDS = 60
_READER_ERROR_CODES = frozenset(
    {
        "invalid_path",
        "missing",
        "symlink",
        "not_regular",
        "unsupported_type",
        "too_large",
        "changed",
        "permission",
        "read_failed",
        "invalid_utf8",
    }
)


@dataclass(frozen=True)
class DocumentFlowResult:
    task_id: Optional[str]
    status: str
    explanation: Optional[str] = None
    error_code: Optional[str] = None
    provider: Optional[str] = None
    attempted_providers: tuple[str, ...] = ()


class DocumentService:
    def __init__(
        self,
        tasks: TaskIntentService,
        policy: PolicyService,
        router: AIRouter,
        *,
        reader: Callable[[str], TextDocument] = read_text_document,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.tasks = tasks
        self.policy = policy
        self.router = router
        self.reader = reader
        self.clock = clock

    def prepare(
        self,
        selected_path: str,
        *,
        actor: ActorContext,
        context: TaskRecordContext,
    ) -> DocumentFlowResult:
        """Persist the selection without opening it or contacting a provider."""
        if not self._owner_context(actor, context):
            return DocumentFlowResult(None, "failed", error_code="actor_not_authorized")
        if not self._valid_selected_path(selected_path):
            return DocumentFlowResult(None, "failed", error_code="invalid_path")
        try:
            task = self.tasks.queue_document_root(selected_path, context=context)
        except (TypeError, ValueError):
            return DocumentFlowResult(None, "failed", error_code="invalid_request")
        return self._result(task)

    def confirm_and_explain(
        self,
        root_task_id: str,
        destinations: Collection[str],
        *,
        actor: ActorContext,
        context: TaskRecordContext,
    ) -> DocumentFlowResult:
        """Consume fresh read/egress grants and complete one confirmed root task."""
        if not self._owner_context(actor, context):
            return DocumentFlowResult(root_task_id, "failed", error_code="actor_not_authorized")
        providers = self._destinations(destinations)
        if providers is None:
            return DocumentFlowResult(root_task_id, "failed", error_code="invalid_destinations")
        task = self.tasks.get_task(root_task_id, context=context)
        if task is None or task.parent_task_id is not None or task.kind != "document_read":
            return DocumentFlowResult(root_task_id, "failed", error_code="task_not_found")
        if task.status in {TaskStatus.FAILED, TaskStatus.INTERRUPTED}:
            task = self.tasks.retry_task(task.task_id, context=context)
        if task is None or task.status is not TaskStatus.QUEUED or not task.selected_path:
            return DocumentFlowResult(root_task_id, "failed", error_code="invalid_task_state")
        task = self.tasks.start_task(task.task_id, context=context, checkpoint="authorizing_read")
        if task is None:
            return DocumentFlowResult(root_task_id, "failed", error_code="task_conflict")

        selected_path = task.selected_path
        try:
            grant = self.policy.grants.issue(
                actor=actor,
                action="document.read",
                resource=selected_path,
                task_id=task.task_id,
                expires_at=self.clock() + _GRANT_LIFETIME_SECONDS,
            )
            decision = self.policy.authorize(
                ActionRequest(
                    "document.read",
                    actor,
                    user_initiated=True,
                    resource=selected_path,
                    task_id=task.task_id,
                    grant_id=grant.grant_id,
                )
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return self._fail(task.task_id, context, "approval_failed")
        if decision.outcome is not PolicyOutcome.ALLOW:
            if not self._audit_result(
                decision.audit_id, AuditResultStatus.FAILED, decision.reason
            ):
                return self._interrupt(task.task_id, context, "audit_failed")
            return self._interrupt(task.task_id, context, "read_not_authorized")

        try:
            document = self.reader(selected_path)
        except DocumentReadError as exc:
            code = (
                exc.code
                if isinstance(exc.code, str) and exc.code in _READER_ERROR_CODES
                else "read_failed"
            )
            if not self._audit_result(
                decision.audit_id, AuditResultStatus.FAILED, code
            ):
                return self._fail(task.task_id, context, "audit_failed")
            return self._fail(task.task_id, context, code)
        except Exception:
            if not self._audit_result(
                decision.audit_id, AuditResultStatus.FAILED, "read_failed"
            ):
                return self._fail(task.task_id, context, "audit_failed")
            return self._fail(task.task_id, context, "read_failed")
        if not self._audit_result(decision.audit_id, AuditResultStatus.SUCCESS):
            return self._fail(task.task_id, context, "audit_failed")
        if self.tasks.update_progress(
            task.task_id, 30, context=context, checkpoint="document_read"
        ) is None:
            return self._not_running_result(
                task.task_id, self.tasks.get_task(task.task_id, context=context)
            )

        prompt = "Explain the selected document clearly and concisely."
        return self._generate(
            task.task_id,
            prompt,
            document.text,
            selected_path,
            providers,
            actor,
            context,
        )

    def follow_up(
        self,
        root_task_id: str,
        question: str,
        destinations: Collection[str],
        *,
        actor: ActorContext,
        context: TaskRecordContext,
    ) -> DocumentFlowResult:
        """Answer from the bounded prior explanation without reopening the file."""
        if self._destinations(destinations) is None:
            return DocumentFlowResult(None, "failed", error_code="invalid_destinations")
        prepared = self.prepare_follow_up(
            root_task_id, question, actor=actor, context=context
        )
        if prepared.error_code or not prepared.task_id:
            return prepared
        return self.execute_follow_up(
            prepared.task_id, destinations, actor=actor, context=context
        )

    def prepare_follow_up(
        self,
        root_task_id: str,
        question: str,
        *,
        actor: ActorContext,
        context: TaskRecordContext,
    ) -> DocumentFlowResult:
        """Create the child before any provider authorization or egress."""
        if not self._owner_context(actor, context):
            return DocumentFlowResult(None, "failed", error_code="actor_not_authorized")
        clean_question = question.strip() if isinstance(question, str) else ""
        if not clean_question or len(clean_question) > _MAX_QUESTION_CHARS:
            return DocumentFlowResult(None, "failed", error_code="invalid_question")
        root = self.tasks.get_task(root_task_id, context=context)
        if (
            root is None
            or root.kind != "document_read"
            or root.parent_task_id is not None
            or root.status is not TaskStatus.COMPLETED
            or not root.result_summary
            or not root.selected_path
        ):
            return DocumentFlowResult(None, "failed", error_code="task_not_found")
        child = self.tasks.queue_follow_up(root_task_id, clean_question, context=context)
        if child is None:
            return DocumentFlowResult(None, "failed", error_code="invalid_task_state")
        return self._result(child)

    def execute_follow_up(
        self,
        child_task_id: str,
        destinations: Collection[str],
        *,
        actor: ActorContext,
        context: TaskRecordContext,
    ) -> DocumentFlowResult:
        """Run a previously exposed child; a cancelled child never reaches egress."""
        if not self._owner_context(actor, context):
            return DocumentFlowResult(child_task_id, "failed", error_code="actor_not_authorized")
        providers = self._destinations(destinations)
        if providers is None:
            return DocumentFlowResult(child_task_id, "failed", error_code="invalid_destinations")
        child = self.tasks.get_task(child_task_id, context=context)
        if (
            child is None
            or child.kind != "document_follow_up"
            or not child.parent_task_id
            or child.status is not TaskStatus.QUEUED
        ):
            return DocumentFlowResult(child_task_id, "failed", error_code="invalid_task_state")
        root = self.tasks.get_task(child.parent_task_id, context=context)
        if (
            root is None
            or root.kind != "document_read"
            or root.parent_task_id is not None
            or root.status is not TaskStatus.COMPLETED
            or not root.result_summary
            or not root.selected_path
        ):
            return DocumentFlowResult(child_task_id, "failed", error_code="task_not_found")
        child = self.tasks.start_task(child.task_id, context=context, checkpoint="authorizing_provider")
        if child is None:
            return DocumentFlowResult(child_task_id, "failed", error_code="task_conflict")
        prompt = f"Prior explanation:\n{root.result_summary[:4000]}\n\nQuestion:\n{child.raw_text}"
        return self._generate(
            child.task_id,
            prompt,
            "",
            root.selected_path,
            providers,
            actor,
            context,
        )

    def reconnect(
        self,
        task_id: str,
        *,
        actor: ActorContext,
        context: TaskRecordContext,
    ) -> DocumentFlowResult:
        if not self._owner_context(actor, context):
            return DocumentFlowResult(task_id, "failed", error_code="actor_not_authorized")
        task = self.tasks.get_task(task_id, context=context)
        return self._result(task) if task else DocumentFlowResult(
            task_id, "failed", error_code="task_not_found"
        )

    def cancel(
        self,
        task_id: str,
        *,
        actor: ActorContext,
        context: TaskRecordContext,
    ) -> DocumentFlowResult:
        if not self._owner_context(actor, context):
            return DocumentFlowResult(task_id, "failed", error_code="actor_not_authorized")
        if self.tasks.get_task(task_id, context=context) is None:
            return DocumentFlowResult(task_id, "failed", error_code="task_not_found")
        task = self.tasks.cancel_task(task_id, context=context)
        if task is None:
            return DocumentFlowResult(task_id, "failed", error_code="invalid_task_state")
        return DocumentFlowResult(task.task_id, task.status.value)

    def _generate(
        self,
        task_id: str,
        prompt: str,
        document_context: str,
        resource: str,
        providers: tuple[str, ...],
        actor: ActorContext,
        context: TaskRecordContext,
    ) -> DocumentFlowResult:
        state = self.tasks.get_task(task_id, context=context)
        if state is None or state.status is not TaskStatus.RUNNING:
            return self._not_running_result(task_id, state)
        audit_ids: dict[str, str] = {}
        result_audits_attempted: set[str] = set()
        authorization_attempted: set[str] = set()
        audit_failed = False
        approval_failed = False

        def authorize(provider: str) -> bool:
            nonlocal approval_failed, audit_failed
            if (
                audit_failed
                or provider in authorization_attempted
                or provider not in providers
            ):
                return False
            authorization_attempted.add(provider)
            current = self.tasks.get_task(task_id, context=context)
            if current is None or current.status is not TaskStatus.RUNNING:
                return False
            try:
                grant = self.policy.grants.issue(
                    actor=actor,
                    action="provider.send_document",
                    resource=resource,
                    destination=provider,
                    task_id=task_id,
                    expires_at=self.clock() + _GRANT_LIFETIME_SECONDS,
                )
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                approval_failed = True
                return False
            decision = self.policy.authorize(
                ActionRequest(
                    "provider.send_document",
                    actor,
                    user_initiated=True,
                    resource=resource,
                    destination=provider,
                    task_id=task_id,
                    grant_id=grant.grant_id,
                )
            )
            audit_ids[provider] = decision.audit_id
            if decision.outcome is not PolicyOutcome.ALLOW:
                result_audits_attempted.add(decision.audit_id)
                if not self._audit_result(
                    decision.audit_id, AuditResultStatus.FAILED, decision.reason
                ):
                    audit_failed = True
                return False
            current = self.tasks.get_task(task_id, context=context)
            if current is None or current.status is not TaskStatus.RUNNING:
                result_audits_attempted.add(decision.audit_id)
                if not self._audit_result(
                    decision.audit_id,
                    AuditResultStatus.CANCELLED,
                    "task_cancelled",
                ):
                    audit_failed = True
                return False
            return True

        state = self.tasks.get_task(task_id, context=context)
        if state is None or state.status is not TaskStatus.RUNNING:
            return self._not_running_result(task_id, state)

        try:
            generated: GenerationResult = self.router.generate_document(
                prompt,
                allowed_providers=providers,
                before_provider_call=authorize,
                context=document_context,
                max_tokens=500,
                temperature=0.2,
            )
        except Exception:
            for audit_id in audit_ids.values():
                if audit_id in result_audits_attempted:
                    continue
                result_audits_attempted.add(audit_id)
                if not self._audit_result(
                    audit_id, AuditResultStatus.FAILED, "provider_failed"
                ):
                    audit_failed = True
            return self._interrupt(
                task_id,
                context,
                "audit_failed" if audit_failed else "provider_failed",
            )

        for provider, audit_id in audit_ids.items():
            if audit_id and audit_id not in result_audits_attempted:
                result_audits_attempted.add(audit_id)
                if not self._audit_result(
                    audit_id,
                    AuditResultStatus.SUCCESS
                    if provider == generated.provider and generated.text
                    else AuditResultStatus.FAILED,
                    None if provider == generated.provider and generated.text else "provider_failed",
                ):
                    audit_failed = True
        if audit_failed:
            return self._interrupt(
                task_id, context, "audit_failed", generated.attempted_providers
            )
        state = self.tasks.get_task(task_id, context=context)
        if state is None or state.status is not TaskStatus.RUNNING:
            return self._not_running_result(task_id, state)
        if not generated.text or not generated.provider:
            code = (
                "provider_failed"
                if generated.attempted_providers
                else "approval_failed" if approval_failed else "provider_not_authorized"
            )
            return self._interrupt(task_id, context, code, generated.attempted_providers)

        if self.tasks.update_progress(
            task_id, 80, context=context, checkpoint="provider_completed"
        ) is None:
            return self._not_running_result(
                task_id, self.tasks.get_task(task_id, context=context)
            )
        if self.tasks.begin_verification(task_id, context=context) is None:
            return DocumentFlowResult(task_id, "failed", error_code="task_conflict")
        completed = self.tasks.complete_task(
            task_id,
            context=context,
            result_summary=generated.text[:4000],
            checkpoint="explanation_ready",
        )
        if completed is None:
            return DocumentFlowResult(task_id, "failed", error_code="task_conflict")
        return DocumentFlowResult(
            completed.task_id,
            completed.status.value,
            explanation=completed.result_summary,
            provider=generated.provider,
            attempted_providers=generated.attempted_providers,
        )

    def _fail(
        self, task_id: str, context: TaskRecordContext, code: str
    ) -> DocumentFlowResult:
        task = self.tasks.fail_task(task_id, code, context=context, checkpoint=code)
        return DocumentFlowResult(
            task_id, task.status.value if task else "failed", error_code=code
        )

    def _interrupt(
        self,
        task_id: str,
        context: TaskRecordContext,
        code: str,
        attempted: tuple[str, ...] = (),
    ) -> DocumentFlowResult:
        task = self.tasks.interrupt_task(task_id, context=context, checkpoint=code)
        return DocumentFlowResult(
            task_id,
            task.status.value if task else "failed",
            error_code=code,
            attempted_providers=attempted,
        )

    @staticmethod
    def _not_running_result(
        task_id: str, task: Optional[TaskRecord]
    ) -> DocumentFlowResult:
        if task is not None and task.status is TaskStatus.CANCELLED:
            return DocumentFlowResult(task_id, "cancelled", error_code="task_cancelled")
        return DocumentFlowResult(task_id, "failed", error_code="task_conflict")

    def _audit_result(
        self,
        audit_id: str,
        status: AuditResultStatus,
        code: Optional[str] = None,
    ) -> bool:
        try:
            self.policy.audit.record_result(audit_id, status=status, code=code)
        except (KeyError, OSError, RuntimeError, TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _owner_context(actor: ActorContext, context: TaskRecordContext) -> bool:
        valid, _reason = validate_actor_context(actor)
        return bool(
            valid
            and actor.actor is Actor.OWNER
            and isinstance(context, TaskRecordContext)
            and context.actor == Actor.OWNER.value
            and context.speaker_label == actor.actor_id
            and context.session_id == actor.session_id
            and context.source == actor.source
            and not context.is_guest
        )

    @staticmethod
    def _destinations(values: Collection[str]) -> Optional[tuple[str, ...]]:
        if isinstance(values, (str, bytes)):
            return None
        try:
            providers = tuple(values)
        except TypeError:
            return None
        if not providers or len(providers) > _MAX_PROVIDERS or len(set(providers)) != len(providers):
            return None
        if any(not isinstance(value, str) or not _PROVIDER.fullmatch(value) for value in providers):
            return None
        return providers

    @staticmethod
    def _valid_selected_path(value: object) -> bool:
        if not isinstance(value, str) or not value or len(value) > 4096 or "\x00" in value:
            return False
        try:
            return Path(value).suffix.lower() == ".txt"
        except (OSError, RuntimeError, ValueError):
            return False

    @staticmethod
    def _result(task: TaskRecord) -> DocumentFlowResult:
        error_code = task.last_error
        if error_code is None and task.status in {TaskStatus.FAILED, TaskStatus.INTERRUPTED}:
            error_code = task.checkpoint
        return DocumentFlowResult(
            task.task_id,
            task.status.value,
            explanation=task.result_summary,
            error_code=error_code,
        )
