"""Bounded Phase 3 scheduled-jobs runtime wrapper.

The runtime wraps ``ScheduledJobService`` and converts its results into safe
canonical dictionaries. It performs no execution, timer, thread, network,
subprocess, provider, filesystem-content, or hidden-clock work.
"""

from __future__ import annotations

from typing import Any

from core.action_policy import Actor, ActorContext
from core.jobs.service import JobServiceCode, ScheduledJobService
from core.jobs.transport import TransportError, error_message, result_message


class ScheduledJobRuntime:
    """Safe runtime boundary around ``ScheduledJobService``.

    All methods catch exceptions and return a safe canonical dictionary. No
    actor_id, session_id, proposal_id, payload content, provider details, or
    exception text is included in returned messages.
    """

    def __init__(
        self,
        service: ScheduledJobService,
        *,
        owner_scope_id: str | None = None,
    ) -> None:
        if not isinstance(service, ScheduledJobService):
            raise TypeError("service must be a ScheduledJobService")
        if owner_scope_id is not None and (
            not isinstance(owner_scope_id, str)
            or not owner_scope_id
            or len(owner_scope_id) > 128
            or any(
                not (char.isascii() and (char.isalnum() or char in "._:-"))
                for char in owner_scope_id
            )
        ):
            raise ValueError("owner scope is invalid")
        self._service = service
        self._owner_scope_id = owner_scope_id

    def scheduled_actor(self, actor: ActorContext) -> ActorContext:
        """Bind local-owner job state to a stable server-owned installation scope.

        Guest identity remains request-session scoped and is denied by the service.
        The stable scope is never accepted from a client message.
        """
        if (
            self._owner_scope_id is None
            or not isinstance(actor, ActorContext)
            or actor.actor is not Actor.OWNER
        ):
            return actor
        return ActorContext(
            actor_id=actor.actor_id,
            actor=actor.actor,
            session_id=self._owner_scope_id,
            source=actor.source,
        )

    @staticmethod
    def _error(job_id: object, code: JobServiceCode) -> dict[str, Any]:
        try:
            return error_message(job_id, code)
        except (TransportError, TypeError, ValueError):
            return error_message("scheduled-jobs", code)

    def list_jobs(self, actor: ActorContext, limit: int = 64) -> dict[str, Any]:
        try:
            result = self._service.list_jobs(self.scheduled_actor(actor), limit=limit)
        except Exception:
            return self._error("scheduled-jobs", JobServiceCode.UNAVAILABLE)
        try:
            return result_message(result)
        except Exception:
            return self._error("scheduled-jobs", JobServiceCode.UNAVAILABLE)

    def pause(self, actor: ActorContext, job_id: str) -> dict[str, Any]:
        try:
            result = self._service.pause(self.scheduled_actor(actor), job_id)
        except Exception:
            return self._error(job_id, JobServiceCode.UNAVAILABLE)
        try:
            return result_message(result, job_id=job_id)
        except Exception:
            return self._error(job_id, JobServiceCode.UNAVAILABLE)

    def resume(self, actor: ActorContext, job_id: str) -> dict[str, Any]:
        try:
            result = self._service.resume(self.scheduled_actor(actor), job_id)
        except Exception:
            return self._error(job_id, JobServiceCode.UNAVAILABLE)
        try:
            return result_message(result, job_id=job_id)
        except Exception:
            return self._error(job_id, JobServiceCode.UNAVAILABLE)

    def cancel(self, actor: ActorContext, job_id: str) -> dict[str, Any]:
        try:
            result = self._service.cancel(self.scheduled_actor(actor), job_id)
        except Exception:
            return self._error(job_id, JobServiceCode.UNAVAILABLE)
        try:
            return result_message(result, job_id=job_id)
        except Exception:
            return self._error(job_id, JobServiceCode.UNAVAILABLE)
