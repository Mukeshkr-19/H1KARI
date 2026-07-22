"""Bounded Phase 4 task-handoff transport adapter.

This module dispatches already protocol-validated client messages to the
handoff runtime, validates every outbound message against the v1 server
protocol, and never parses identity or authority from client fields. It
performs no I/O, execution, logging, or network access.
"""

from __future__ import annotations

from core.action_policy import ActorContext
from core.handoff.contracts import HandoffErrorCode
from core.handoff.runtime import HandoffRuntime
from core.protocol import validate_server_message


class HandoffTransportAdapter:
    """Transport adapter for bounded task handoffs."""

    def __init__(self, runtime: HandoffRuntime) -> None:
        if not isinstance(runtime, HandoffRuntime):
            raise TypeError("runtime must be a HandoffRuntime")
        self._runtime = runtime

    def dispatch(self, actor: ActorContext, message: dict) -> dict:
        """Dispatch a protocol-validated client message to the runtime.

        The ``ActorContext`` is supplied by the server transport and must not
        be derived from client fields. The returned message is always a valid
        v1 server message; validation failures are replaced with a safe
        ``handoff_error`` response.
        """
        request_id: str = "invalid-request"
        try:
            request_id = message.get("request_id", "invalid-request")
            if not isinstance(request_id, str):
                request_id = "invalid-request"
            msg_type = message.get("type")
            if msg_type == "handoff_prepare":
                result = self._runtime.prepare(
                    actor,
                    request_id,
                    message["task_id"],
                    message["summary"],
                )
            elif msg_type == "handoff_accept":
                result = self._runtime.accept(
                    actor,
                    request_id,
                    message["handoff_id"],
                    message["acknowledged"],
                )
            elif msg_type == "handoff_reject":
                result = self._runtime.reject(
                    actor, request_id, message["handoff_id"]
                )
            elif msg_type == "handoff_cancel":
                result = self._runtime.cancel(
                    actor, request_id, message["handoff_id"]
                )
            elif msg_type == "handoff_status":
                result = self._runtime.status(
                    actor, request_id, message["handoff_id"]
                )
            else:
                result = self._error(request_id, HandoffErrorCode.UNAVAILABLE)
        except Exception:
            result = self._error(request_id, HandoffErrorCode.UNAVAILABLE)

        return self._validate_or_fallback(result, request_id)

    def expire_due(self) -> int:
        """Expire handoffs through the same bounded runtime boundary."""
        try:
            return self._runtime.expire_due()
        except Exception:
            return 0

    def _validate_or_fallback(self, message: dict, request_id: str) -> dict:
        if isinstance(message, dict) and validate_server_message(message) is None:
            return message
        fallback = {
            "type": "handoff_error",
            "request_id": request_id,
            "code": HandoffErrorCode.UNAVAILABLE.value,
        }
        if validate_server_message(fallback) is None:
            return fallback
        # This should never happen; return the smallest safe message.
        return {"type": "handoff_error", "request_id": "invalid-request", "code": "unavailable"}

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
