"""Bounded Phase 4 task-handoff runtime controller.

This module sits between the transport adapter and the handoff service. It
converts immutable handoff service results into canonical v1 WebSocket server
messages and performs no external I/O, execution, logging, or network access.
No authority, approval, grant, or execution ticket is transferred or exposed.
"""

from __future__ import annotations

import re

from core.action_policy import ActorContext
from core.handoff.contracts import HandoffErrorCode, HandoffState
from core.handoff.service import HandoffService


_HANDOFF_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")


class HandoffRuntime:
    """Coordinate handoff lifecycle without executing external work."""

    def __init__(self, service: HandoffService) -> None:
        if not isinstance(service, HandoffService):
            raise TypeError("service must be a HandoffService")
        self._service = service

    def prepare(
        self,
        actor: ActorContext,
        request_id: str,
        task_id: str,
        summary: str,
    ) -> dict:
        """Prepare a handoff and return the canonical ``handoff_offer`` message."""
        result = self._service.prepare(actor, task_id, summary, request_id)
        if not result.success:
            return self._error(request_id, result.error_code, result.handoff_id)

        # The stored record is authoritative for content that leaves the
        # server. The caller-supplied arguments are only used to locate it.
        record = self._service.store.get_scoped(
            result.handoff_id,
            actor_id=actor.actor_id,
            session_id=actor.session_id,
        )
        if record is None:
            return self._error(
                request_id,
                HandoffErrorCode.UNAVAILABLE,
                result.handoff_id,
            )

        return {
            "type": "handoff_offer",
            "request_id": request_id,
            "handoff_id": record.handoff_id,
            "task_id": record.task_id,
            "summary": record.summary,
            "expires_at": record.expires_at,
        }

    def accept(
        self,
        actor: ActorContext,
        request_id: str,
        handoff_id: str,
        acknowledged: bool,
    ) -> dict:
        """Accept a handoff and return the canonical ``handoff_update`` message."""
        if not self._valid_handoff_id(handoff_id):
            return self._error(request_id, HandoffErrorCode.HANDOFF_NOT_FOUND)
        result = self._service.accept(actor, handoff_id, acknowledged)
        if not result.success:
            return self._error(request_id, result.error_code, handoff_id)
        return self._update(request_id, handoff_id, result.state)

    def reject(
        self,
        actor: ActorContext,
        request_id: str,
        handoff_id: str,
    ) -> dict:
        """Reject a handoff and return the canonical ``handoff_update`` message."""
        if not self._valid_handoff_id(handoff_id):
            return self._error(request_id, HandoffErrorCode.HANDOFF_NOT_FOUND)
        result = self._service.reject(actor, handoff_id)
        if not result.success:
            return self._error(request_id, result.error_code, handoff_id)
        return self._update(request_id, handoff_id, result.state)

    def cancel(
        self,
        actor: ActorContext,
        request_id: str,
        handoff_id: str,
    ) -> dict:
        """Cancel a handoff and return the canonical ``handoff_update`` message."""
        if not self._valid_handoff_id(handoff_id):
            return self._error(request_id, HandoffErrorCode.HANDOFF_NOT_FOUND)
        result = self._service.cancel(actor, handoff_id)
        if not result.success:
            return self._error(request_id, result.error_code, handoff_id)
        return self._update(request_id, handoff_id, result.state)

    def status(
        self,
        actor: ActorContext,
        request_id: str,
        handoff_id: str,
    ) -> dict:
        """Return the canonical ``handoff_update`` message for the current state."""
        if not self._valid_handoff_id(handoff_id):
            return self._error(request_id, HandoffErrorCode.HANDOFF_NOT_FOUND)
        result = self._service.status(actor, handoff_id)
        if not result.success:
            return self._error(request_id, result.error_code, handoff_id)
        return self._update(request_id, handoff_id, result.state)

    def expire_due(self) -> int:
        """Expire all past-TTL offered handoffs and return the count."""
        return self._service.expire_due()

    def _update(self, request_id: str, handoff_id: str, state: HandoffState) -> dict:
        return {
            "type": "handoff_update",
            "request_id": request_id,
            "handoff_id": handoff_id,
            "status": state.value,
        }

    def _valid_handoff_id(self, value: object) -> bool:
        # Defensive guard: the service layer raises ValueError when asked to
        # build a HandoffResult with a malformed handoff_id. Validating here
        # lets us return a safe canonical error instead of leaking an
        # exception to the transport.
        return isinstance(value, str) and bool(_HANDOFF_ID_RE.fullmatch(value))

    def _error(
        self,
        request_id: str,
        error_code: HandoffErrorCode,
        handoff_id: str | None = None,
    ) -> dict:
        message: dict = {
            "type": "handoff_error",
            "request_id": request_id,
            "code": error_code.value,
        }
        if handoff_id is not None:
            message["handoff_id"] = handoff_id
        return message
