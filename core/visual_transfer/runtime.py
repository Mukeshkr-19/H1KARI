"""Transport-independent visual-transfer runtime adapter.

Wraps ``VisualTransferService`` to handle JSON control messages and a
separately supplied authenticated binary frame. The runtime never starts a
network server, opens a socket, writes to disk, calls a subprocess, accesses
a camera or screenshot, performs OCR, selects a provider, or logs IDs/content.

The future server's binary-frame branch calls ``receive_binary`` with raw
``bytes`` only. Bytes never enter a JSON dictionary. Every outbound message
passes ``validate_server_message`` before being returned.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, List

from core.protocol import validate_server_message
from core.visual_transfer.contracts import (
    ContractValidationError,
    TRANSFER_TTL_SECONDS,
    VisualTransferErrorCode,
    VisualTransferOutcomeStatus,
    VisualTransferResult,
    VisualTransferState,
)
from core.visual_transfer.service import VisualTransferService
from core.visual_transfer.transport import (
    begin_result_to_ready,
    build_declaration,
    result_to_complete,
    result_to_error,
    result_to_update,
    state_to_update_status,
    unavailable_error,
    validate_control_fields,
)


class VisualTransferRuntime:
    """Transport-independent adapter for visual-transfer control + binary.

    Constructor receives:
      - ``service``: a ``VisualTransferService`` instance.
      - ``clock``: injected clock for exact ``expires_at`` computation.

    All public methods return a list of protocol dictionaries (outbound
    messages). Each dictionary is validated via ``validate_server_message``
    before being returned. If validation fails, a canonical
    ``visual_transfer_error`` with code ``unavailable`` is returned instead.
    """

    def __init__(
        self,
        *,
        service: VisualTransferService,
        clock: Callable[[], float],
    ) -> None:
        if not isinstance(service, VisualTransferService):
            raise TypeError("service must be a VisualTransferService")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._service = service
        self._clock = clock
        # Track transfers for which a visual_transfer_complete has already
        # been emitted, so a duplicate receive_binary does not emit a second
        # complete message. Keyed by (actor_scope, transfer_id).
        self._completed: set[tuple[str, str]] = set()

    # --- Internal helpers --------------------------------------------------

    _REQUEST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")

    def _now(self) -> float | None:
        try:
            value = self._clock()
        except Exception:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(float(value)):
            return None
        return float(value)

    def _safe_message(self, message: dict[str, Any]) -> List[dict[str, Any]]:
        """Validate a single outbound message. On failure, return a canonical
        unavailable error. Never returns an empty list."""
        error = validate_server_message(message)
        if error is None:
            return [message]
        # The message itself failed validation; return a safe fallback.
        request_id = self._safe_request_id(message.get("request_id"))
        fallback = unavailable_error(request_id=request_id)
        # The fallback must also validate; if it somehow fails, return it
        # anyway (it is the safest possible message).
        return [fallback]

    def _safe_messages(self, messages: List[dict[str, Any]]) -> List[dict[str, Any]]:
        """Validate each outbound message independently."""
        result: List[dict[str, Any]] = []
        for msg in messages:
            result.extend(self._safe_message(msg))
        return result

    @staticmethod
    def _safe_request_id(value: object) -> str:
        """Return the request_id or a fallback. Never raises."""
        if isinstance(value, str) and VisualTransferRuntime._REQUEST_ID_RE.fullmatch(value):
            return value
        return "invalid-request"

    @staticmethod
    def _safe_transfer_id(value: object) -> str:
        """Return the transfer_id or empty string. Never raises."""
        if isinstance(value, str):
            try:
                from core.visual_transfer.contracts import validate_transfer_id

                return validate_transfer_id(value)
            except ContractValidationError:
                pass
        return ""

    # --- Public API --------------------------------------------------------

    def begin(
        self,
        actor_scope: str,
        request_id: str,
        handoff_id: str,
        mime_type: str,
        size_bytes: int,
        width: int,
        height: int,
        frame_count: int,
    ) -> List[dict[str, Any]]:
        """Process a ``visual_transfer_begin`` control message.

        Returns ``visual_transfer_ready`` on success or ``visual_transfer_error``
        on failure. The transfer_id is always server-generated; a client-
        supplied transfer_id is never accepted.
        """
        rid = self._safe_request_id(request_id)
        # Validate scalar control fields before constructing the declaration.
        field_error = validate_control_fields(
            mime_type=mime_type,
            size_bytes=size_bytes,
            width=width,
            height=height,
            frame_count=frame_count,
        )
        if field_error is not None:
            return self._safe_message(
                {
                    "type": "visual_transfer_error",
                    "request_id": rid,
                    "code": field_error.value,
                }
            )
        try:
            declaration = build_declaration(
                handoff_id=handoff_id,
                mime_type=mime_type,
                size_bytes=size_bytes,
                width=width,
                height=height,
                frame_count=frame_count,
            )
        except ContractValidationError:
            return self._safe_message(
                {
                    "type": "visual_transfer_error",
                    "request_id": rid,
                    "code": VisualTransferErrorCode.HANDOFF_NOT_ACCEPTED.value,
                }
            )
        try:
            begun = self._service.begin(actor_scope, declaration)
        except Exception:
            return self._safe_message(
                {
                    "type": "visual_transfer_error",
                    "request_id": rid,
                    "code": VisualTransferErrorCode.UNAVAILABLE.value,
                }
            )
        now = self._now()
        if now is None:
            return self._safe_message(unavailable_error(request_id=rid))
        expires_at = now + TRANSFER_TTL_SECONDS
        message = begin_result_to_ready(begun, request_id=rid, expires_at=expires_at)
        return self._safe_message(message)

    def status(
        self,
        actor_scope: str,
        request_id: str,
        transfer_id: str,
    ) -> List[dict[str, Any]]:
        """Process a ``visual_transfer_status`` control message.

        Returns ``visual_transfer_update`` with the current state, or
        ``visual_transfer_error`` if the transfer is not found or the scope
        does not match.
        """
        rid = self._safe_request_id(request_id)
        tid = self._safe_transfer_id(transfer_id)
        if not tid:
            return self._safe_message(
                {
                    "type": "visual_transfer_error",
                    "request_id": rid,
                    "code": VisualTransferErrorCode.TRANSFER_NOT_FOUND.value,
                }
            )
        try:
            result = self._service.status(actor_scope, transfer_id)
        except Exception:
            return self._safe_message(
                {
                    "type": "visual_transfer_error",
                    "request_id": rid,
                    "code": VisualTransferErrorCode.UNAVAILABLE.value,
                }
            )
        if result.status is VisualTransferOutcomeStatus.ERROR:
            return self._safe_message(
                result_to_error(result, request_id=rid, transfer_id=tid)
            )
        # OK path: emit an update with the current state.
        update = result_to_update(
            result,
            request_id=rid,
            transfer_id=tid,
            bytes_received=0,
        )
        # If completed, also emit a complete message with the content hash.
        if result.state is VisualTransferState.COMPLETED and result.metadata is not None:
            complete = result_to_complete(
                result.metadata,
                request_id=rid,
                transfer_id=tid,
            )
            return self._safe_messages([update, complete])
        return self._safe_message(update)

    def cancel(
        self,
        actor_scope: str,
        request_id: str,
        transfer_id: str,
    ) -> List[dict[str, Any]]:
        """Process a ``visual_transfer_cancel`` control message.

        Returns ``visual_transfer_update`` with cancelled/completed state, or
        ``visual_transfer_error`` if the transfer is not found.
        """
        rid = self._safe_request_id(request_id)
        tid = self._safe_transfer_id(transfer_id)
        if not tid:
            return self._safe_message(
                {
                    "type": "visual_transfer_error",
                    "request_id": rid,
                    "code": VisualTransferErrorCode.TRANSFER_NOT_FOUND.value,
                }
            )
        try:
            result = self._service.cancel(actor_scope, transfer_id)
        except Exception:
            return self._safe_message(
                {
                    "type": "visual_transfer_error",
                    "request_id": rid,
                    "code": VisualTransferErrorCode.UNAVAILABLE.value,
                }
            )
        if result.status is VisualTransferOutcomeStatus.ERROR:
            return self._safe_message(
                result_to_error(result, request_id=rid, transfer_id=tid)
            )
        # OK path: emit an update with the terminal state.
        update = result_to_update(
            result,
            request_id=rid,
            transfer_id=tid,
            bytes_received=0,
        )
        return self._safe_message(update)

    def receive_binary(
        self,
        actor_scope: str,
        request_id: str,
        transfer_id: str,
        frame: object,
    ) -> List[dict[str, Any]]:
        """Process a separately supplied authenticated binary frame.

        The future server's binary-frame branch calls this with raw ``bytes``
        only. Rejects ``str``, ``dict``, ``list``, ``memoryview``, ``bytearray``,
        and any non-``bytes`` type. Enforces the declared size exactly. The
        existing validator enforces MIME magic, dimensions, animation, metadata/
        EXIF, and decompression limits.

        Returns:
          - On fresh success: ``visual_transfer_update`` (completed) +
            ``visual_transfer_complete`` (with content_hash).
          - On duplicate success: ``visual_transfer_update`` (completed) only.
          - On failure: ``visual_transfer_update`` (failed) +
            ``visual_transfer_error`` (with canonical code).
        """
        rid = self._safe_request_id(request_id)
        tid = self._safe_transfer_id(transfer_id)
        if not tid:
            return self._safe_message(
                {
                    "type": "visual_transfer_error",
                    "request_id": rid,
                    "code": VisualTransferErrorCode.TRANSFER_NOT_FOUND.value,
                }
            )
        try:
            result = self._service.receive(actor_scope, transfer_id, frame)
        except Exception:
            return self._safe_message(
                {
                    "type": "visual_transfer_error",
                    "request_id": rid,
                    "code": VisualTransferErrorCode.UNAVAILABLE.value,
                }
            )
        # Map the result to protocol messages.
        if result.status is VisualTransferOutcomeStatus.ERROR:
            error_msg = result_to_error(result, request_id=rid, transfer_id=tid)
            update_msg = result_to_update(
                result,
                request_id=rid,
                transfer_id=tid,
                bytes_received=0,
            )
            return self._safe_messages([update_msg, error_msg])
        # OK path: completed.
        if result.state is VisualTransferState.COMPLETED and result.metadata is not None:
            scope_key = (actor_scope, tid)
            is_fresh = scope_key not in self._completed
            bytes_recv = len(frame) if (is_fresh and isinstance(frame, bytes)) else 0
            update_msg = result_to_update(
                result,
                request_id=rid,
                transfer_id=tid,
                bytes_received=bytes_recv,
            )
            if is_fresh:
                self._completed.add(scope_key)
                complete_msg = result_to_complete(
                    result.metadata,
                    request_id=rid,
                    transfer_id=tid,
                )
                return self._safe_messages([update_msg, complete_msg])
            # Duplicate receive: emit update only, no second complete.
            return self._safe_message(update_msg)
        # Non-terminal OK (should not happen for receive, but handle safely).
        update_msg = result_to_update(
            result,
            request_id=rid,
            transfer_id=tid,
            bytes_received=0,
        )
        return self._safe_message(update_msg)

    def clear_session(self, actor_scope: str) -> None:
        """Disconnect cleanup: drop every transfer for one actor scope."""
        try:
            self._service.clear_session(actor_scope)
        except Exception:
            pass

    def expire_due(self) -> int:
        """Sweep expired transfers. Returns the count expired."""
        try:
            return self._service.expire_due()
        except Exception:
            return 0


__all__ = ["VisualTransferRuntime"]
