"""Transport-independent message mapping for visual-transfer control messages.

Maps validated control fields to ``VisualTransferDeclaration`` instances and
maps ``VisualTransferResult`` / ``VisualTransferBeginResult`` back to protocol
dictionaries that pass ``validate_server_message``. Bytes never enter a JSON
dictionary. No I/O, no network, no subprocess, no third-party deps.
"""

from __future__ import annotations

from typing import Any, Optional

from core.visual_transfer.contracts import (
    MAX_ENCODED_BYTES,
    ValidatedImageMetadata,
    VisualTransferBeginResult,
    VisualTransferDeclaration,
    VisualTransferErrorCode,
    VisualTransferOutcomeStatus,
    VisualTransferResult,
    VisualTransferState,
)
from core.visual_transfer.service import VisualTransferService

# --- Protocol message type constants -----------------------------------------

MESSAGE_READY = "visual_transfer_ready"
MESSAGE_UPDATE = "visual_transfer_update"
MESSAGE_COMPLETE = "visual_transfer_complete"
MESSAGE_ERROR = "visual_transfer_error"

# State -> update status enum value (protocol schema).
_STATE_TO_STATUS: dict[VisualTransferState, str] = {
    VisualTransferState.PENDING: "pending",
    VisualTransferState.RECEIVING: "receiving",
    VisualTransferState.VALIDATING: "validating",
    VisualTransferState.COMPLETED: "completed",
    VisualTransferState.CANCELLED: "cancelled",
    VisualTransferState.FAILED: "failed",
    # EXPIRED maps to failed for protocol purposes; the error code carries
    # the precise reason.
    VisualTransferState.EXPIRED: "failed",
}

# Error codes that are safe to include with an optional transfer_id.
# The protocol allows transfer_id on visual_transfer_error only when the
# caller already knows it (status/cancel/receive path). Begin-path errors
# never include a transfer_id because the server has not assigned one.
_TRANSFER_ID_SAFE_ERRORS = frozenset(
    {
        VisualTransferErrorCode.TRANSFER_NOT_FOUND,
        VisualTransferErrorCode.TRANSFER_EXPIRED,
        VisualTransferErrorCode.SIZE_EXCEEDED,
        VisualTransferErrorCode.INVALID_REQUEST,
        VisualTransferErrorCode.MIME_MISMATCH,
        VisualTransferErrorCode.MALFORMED_IMAGE,
        VisualTransferErrorCode.DIMENSIONS_EXCEEDED,
        VisualTransferErrorCode.DECOMPRESSION_LIMIT,
        VisualTransferErrorCode.METADATA_REJECTED,
        VisualTransferErrorCode.FRAME_COUNT_INVALID,
        VisualTransferErrorCode.MIME_UNSUPPORTED,
    }
)


# --- Control-field validation ------------------------------------------------


def validate_control_fields(
    *,
    mime_type: object,
    size_bytes: object,
    width: object,
    height: object,
    frame_count: object,
) -> Optional[VisualTransferErrorCode]:
    """Validate the scalar control fields before building a declaration.

    Returns ``None`` when all fields are valid, or a canonical error code
    when any field is out of bounds or the wrong type. The caller maps the
    code to a ``visual_transfer_error`` message.
    """
    if not isinstance(mime_type, str) or mime_type not in ("image/png", "image/jpeg"):
        return VisualTransferErrorCode.MIME_UNSUPPORTED
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int):
        return VisualTransferErrorCode.SIZE_EXCEEDED
    if size_bytes < 1 or size_bytes > MAX_ENCODED_BYTES:
        return VisualTransferErrorCode.SIZE_EXCEEDED
    if isinstance(width, bool) or not isinstance(width, int):
        return VisualTransferErrorCode.DIMENSIONS_EXCEEDED
    if width < 1 or width > 4096:
        return VisualTransferErrorCode.DIMENSIONS_EXCEEDED
    if isinstance(height, bool) or not isinstance(height, int):
        return VisualTransferErrorCode.DIMENSIONS_EXCEEDED
    if height < 1 or height > 4096:
        return VisualTransferErrorCode.DIMENSIONS_EXCEEDED
    if isinstance(frame_count, bool) or not isinstance(frame_count, int):
        return VisualTransferErrorCode.FRAME_COUNT_INVALID
    if frame_count != 1:
        return VisualTransferErrorCode.FRAME_COUNT_INVALID
    pixels = width * height
    if pixels > 16_777_216:
        return VisualTransferErrorCode.DECOMPRESSION_LIMIT
    return None


def build_declaration(
    *,
    handoff_id: str,
    mime_type: str,
    size_bytes: int,
    width: int,
    height: int,
    frame_count: int,
) -> VisualTransferDeclaration:
    """Construct a ``VisualTransferDeclaration`` from validated control fields.

    The ``transfer_id`` is left empty (server-assigned). The declaration's
    ``__post_init__`` re-validates all fields; if construction fails the
    caller catches ``ContractValidationError`` and maps it to
    ``unavailable``.
    """
    return VisualTransferDeclaration(
        transfer_id="",
        handoff_id=handoff_id,
        mime=mime_type,
        declared_byte_length=size_bytes,
        declared_width=width,
        declared_height=height,
        frame_count=frame_count,
    )


# --- Result -> protocol message mapping --------------------------------------


def _safe_transfer_id(value: Optional[str]) -> str:
    """Return the transfer_id or an empty string for protocol messages that
    require it. The caller must only include the field when non-empty."""
    return value if isinstance(value, str) and value else ""


def begin_result_to_ready(
    result: VisualTransferBeginResult,
    *,
    request_id: str,
    expires_at: float,
) -> dict[str, Any]:
    """Map a successful ``VisualTransferBeginResult`` to
    ``visual_transfer_ready``. On failure, map to ``visual_transfer_error``.
    """
    if result.status is VisualTransferOutcomeStatus.OK and result.transfer_id:
        return {
            "type": MESSAGE_READY,
            "request_id": request_id,
            "transfer_id": result.transfer_id,
            "expires_at": float(expires_at),
        }
    # Error path: begin never has a transfer_id to disclose.
    code = result.error if result.error is not None else VisualTransferErrorCode.UNAVAILABLE
    return {
        "type": MESSAGE_ERROR,
        "request_id": request_id,
        "code": code.value,
    }


def result_to_update(
    result: VisualTransferResult,
    *,
    request_id: str,
    transfer_id: str,
    bytes_received: int,
) -> dict[str, Any]:
    """Map a ``VisualTransferResult`` to ``visual_transfer_update``.

    For terminal failures the update carries the state-mapped status and
    ``bytes_received``. A separate ``visual_transfer_error`` may follow.
    """
    status = _STATE_TO_STATUS.get(result.state, "failed")
    bounded_bytes = max(0, min(bytes_received, MAX_ENCODED_BYTES))
    return {
        "type": MESSAGE_UPDATE,
        "request_id": request_id,
        "transfer_id": transfer_id,
        "status": status,
        "bytes_received": bounded_bytes,
    }


def result_to_complete(
    metadata: ValidatedImageMetadata,
    *,
    request_id: str,
    transfer_id: str,
) -> dict[str, Any]:
    """Map validated metadata to ``visual_transfer_complete``."""
    return {
        "type": MESSAGE_COMPLETE,
        "request_id": request_id,
        "transfer_id": transfer_id,
        "content_hash": metadata.sha256,
    }


def result_to_error(
    result: VisualTransferResult,
    *,
    request_id: str,
    transfer_id: Optional[str] = None,
) -> dict[str, Any]:
    """Map a failed ``VisualTransferResult`` to ``visual_transfer_error``.

    The optional ``transfer_id`` is included only when the error code is in
    the safe set and the caller already knows the transfer_id (status/cancel/
    receive path). Begin-path errors never include it.
    """
    code = result.error if result.error is not None else VisualTransferErrorCode.UNAVAILABLE
    message: dict[str, Any] = {
        "type": MESSAGE_ERROR,
        "request_id": request_id,
        "code": code.value,
    }
    if (
        transfer_id
        and code in _TRANSFER_ID_SAFE_ERRORS
    ):
        message["transfer_id"] = transfer_id
    return message


def unavailable_error(*, request_id: str) -> dict[str, Any]:
    """Construct a canonical ``visual_transfer_error`` with code
    ``unavailable``. Used when an internal exception prevents normal mapping."""
    return {
        "type": MESSAGE_ERROR,
        "request_id": request_id,
        "code": VisualTransferErrorCode.UNAVAILABLE.value,
    }


def state_to_update_status(state: VisualTransferState) -> str:
    """Public mapping from internal state to protocol update status."""
    return _STATE_TO_STATUS.get(state, "failed")


__all__ = [
    "MESSAGE_COMPLETE",
    "MESSAGE_ERROR",
    "MESSAGE_READY",
    "MESSAGE_UPDATE",
    "begin_result_to_ready",
    "build_declaration",
    "result_to_complete",
    "result_to_error",
    "result_to_update",
    "state_to_update_status",
    "unavailable_error",
    "validate_control_fields",
]
