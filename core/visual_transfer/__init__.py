"""Phase 4 visual-transfer validation core.

Pure, bounded, in-memory validation for a single already-captured binary image
frame delivered by a future authenticated transport adapter. This package never
captures images, opens a network endpoint, writes to disk, calls a subprocess,
selects a provider, or performs OCR. It uses only the Python standard library.

Public surface:
    - VisualTransferState, VisualTransferDeclaration, ValidatedImageMetadata,
      VisualTransferResult, VisualTransferErrorCode, VisualTransferOutcomeStatus
    - validate_transfer_id, validate_handoff_id, validate_actor_scope
    - VisualTransferValidator, VisualTransferBuffer, VisualTransferService
"""

from __future__ import annotations

from core.visual_transfer.contracts import (
    AGGREGATE_MEMORY_CAP_BYTES,
    DECOMPRESSION_PIXEL_LIMIT,
    HANDOFF_ID_PATTERN,
    MAX_DIMENSION,
    MAX_ENCODED_BYTES,
    MIN_DIMENSION,
    TRANSFER_ID_PATTERN,
    TRANSFER_TTL_SECONDS,
    ContractValidationError,
    ValidatedImageMetadata,
    VisualTransferBeginResult,
    VisualTransferDeclaration,
    VisualTransferErrorCode,
    VisualTransferOutcomeStatus,
    VisualTransferResult,
    VisualTransferState,
    validate_actor_scope,
    validate_handoff_id,
    validate_transfer_id,
)
from core.visual_transfer.buffer import VisualTransferBuffer
from core.visual_transfer.runtime import VisualTransferRuntime
from core.visual_transfer.service import VisualTransferService
from core.visual_transfer.transport import (
    MESSAGE_COMPLETE,
    MESSAGE_ERROR,
    MESSAGE_READY,
    MESSAGE_UPDATE,
    begin_result_to_ready,
    build_declaration,
    result_to_complete,
    result_to_error,
    result_to_update,
    state_to_update_status,
    unavailable_error,
    validate_control_fields,
)
from core.visual_transfer.validator import VisualTransferValidator

__all__ = [
    "AGGREGATE_MEMORY_CAP_BYTES",
    "DECOMPRESSION_PIXEL_LIMIT",
    "HANDOFF_ID_PATTERN",
    "MAX_DIMENSION",
    "MAX_ENCODED_BYTES",
    "MESSAGE_COMPLETE",
    "MESSAGE_ERROR",
    "MESSAGE_READY",
    "MESSAGE_UPDATE",
    "MIN_DIMENSION",
    "TRANSFER_ID_PATTERN",
    "TRANSFER_TTL_SECONDS",
    "ContractValidationError",
    "ValidatedImageMetadata",
    "VisualTransferBuffer",
    "VisualTransferBeginResult",
    "VisualTransferDeclaration",
    "VisualTransferErrorCode",
    "VisualTransferOutcomeStatus",
    "VisualTransferResult",
    "VisualTransferRuntime",
    "VisualTransferService",
    "VisualTransferState",
    "VisualTransferValidator",
    "begin_result_to_ready",
    "build_declaration",
    "result_to_complete",
    "result_to_error",
    "result_to_update",
    "state_to_update_status",
    "unavailable_error",
    "validate_actor_scope",
    "validate_control_fields",
    "validate_handoff_id",
    "validate_transfer_id",
]
