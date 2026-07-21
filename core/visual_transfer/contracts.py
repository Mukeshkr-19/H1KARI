"""Frozen, content-safe visual-transfer contracts (no I/O, no third-party deps).

All dataclasses are frozen. Repr never reveals transfer/handoff IDs, hashes,
bytes, dimensions tied to a particular user event, timestamps, or exception
text. Errors carry only a stable code string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

# --- Canonical limits (frozen, exported) -------------------------------------

MIN_ENCODED_BYTES = 1
MAX_ENCODED_BYTES = 1_048_576  # 1 MiB encoded byte budget.
MIN_DIMENSION = 1
MAX_DIMENSION = 4_096
DECOMPRESSION_PIXEL_LIMIT = 16_777_216  # 4096 * 4096.
EXACT_FRAME_COUNT = 1
TRANSFER_TTL_SECONDS = 60

# Hard aggregate in-memory cap. 8 MiB is the documented ceiling; the validator
# rejects frames above 1 MiB encoded, so this cap bounds the sum of concurrent
# in-flight buffers plus per-transfer overhead with comfortable slack.
AGGREGATE_MEMORY_CAP_BYTES = 8 * 1_048_576

TRANSFER_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
HANDOFF_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_ACTOR_SCOPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")

_MIME_ALLOWLIST: Tuple[str, ...] = ("image/png", "image/jpeg")


class ContractValidationError(ValueError):
    """Raised when a contract value fails validation.

    The message never includes the offending value, only a stable reason code.
    """


class VisualTransferState(str, Enum):
    PENDING = "pending"
    RECEIVING = "receiving"
    VALIDATING = "validating"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    EXPIRED = "expired"


class VisualTransferErrorCode(str, Enum):
    UNAVAILABLE = "unavailable"
    INVALID_REQUEST = "invalid_request"
    UNAUTHORIZED = "unauthorized"
    TRANSFER_NOT_FOUND = "transfer_not_found"
    TRANSFER_EXPIRED = "transfer_expired"
    HANDOFF_NOT_ACCEPTED = "handoff_not_accepted"
    MIME_UNSUPPORTED = "mime_unsupported"
    MIME_MISMATCH = "mime_mismatch"
    SIZE_EXCEEDED = "size_exceeded"
    DIMENSIONS_EXCEEDED = "dimensions_exceeded"
    FRAME_COUNT_INVALID = "frame_count_invalid"
    DECOMPRESSION_LIMIT = "decompression_limit"
    METADATA_REJECTED = "metadata_rejected"
    MALFORMED_IMAGE = "malformed_image"
    RATE_LIMITED = "rate_limited"


class VisualTransferOutcomeStatus(str, Enum):
    OK = "ok"
    ERROR = "error"


# --- Validators ------------------------------------------------------------


def validate_transfer_id(value: object) -> str:
    if not isinstance(value, str) or not TRANSFER_ID_PATTERN.fullmatch(value):
        raise ContractValidationError("transfer_id is invalid")
    return value


def validate_handoff_id(value: object) -> str:
    if not isinstance(value, str) or not HANDOFF_ID_PATTERN.fullmatch(value):
        raise ContractValidationError("handoff_id is invalid")
    return value


def validate_actor_scope(value: object) -> str:
    """Validate the server-derived actor scope identifier.

    The actor scope is the exact correlation key binding a transfer to one
    authenticated local session. It must be a stable identifier; it is never
    disclosed cross-session.
    """
    if not isinstance(value, str) or not _ACTOR_SCOPE_PATTERN.fullmatch(value):
        raise ContractValidationError("actor_scope is invalid")
    return value


def validate_mime(value: object) -> str:
    if not isinstance(value, str) or value not in _MIME_ALLOWLIST:
        raise ContractValidationError("mime_unsupported")
    return value


def _validate_positive_int(value: object, *, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractValidationError(f"{name} is invalid")
    if value < MIN_DIMENSION or value > maximum:
        raise ContractValidationError(f"{name} is invalid")
    return value


# --- Frozen dataclasses ----------------------------------------------------


@dataclass(frozen=True)
class VisualTransferDeclaration:
    """Pre-buffer declaration of a single frame to be received.

    Validated before any byte buffer is allocated. ``frame_count`` must be
    exactly 1. ``declared_byte_length`` is the exact number of bytes the
    caller commits to deliver; the receiver rejects any other length.
    """

    transfer_id: str = ""
    handoff_id: str = ""
    mime: str = ""
    declared_byte_length: int = 0
    declared_width: int = 0
    declared_height: int = 0
    frame_count: int = EXACT_FRAME_COUNT

    def __post_init__(self) -> None:
        # transfer_id is server-assigned via VisualTransferService.begin(); an
        # empty string means "not yet assigned" and is accepted here. The buffer
        # and service validate the assigned identifier via validate_transfer_id.
        if self.transfer_id != "":
            validate_transfer_id(self.transfer_id)
        validate_handoff_id(self.handoff_id)
        validate_mime(self.mime)
        if isinstance(self.declared_byte_length, bool) or not isinstance(
            self.declared_byte_length, int
        ):
            raise ContractValidationError("declared_byte_length is invalid")
        if (
            self.declared_byte_length < MIN_ENCODED_BYTES
            or self.declared_byte_length > MAX_ENCODED_BYTES
        ):
            raise ContractValidationError("size_exceeded")
        _validate_positive_int(self.declared_width, name="declared_width", maximum=MAX_DIMENSION)
        _validate_positive_int(self.declared_height, name="declared_height", maximum=MAX_DIMENSION)
        if isinstance(self.frame_count, bool) or not isinstance(self.frame_count, int):
            raise ContractValidationError("frame_count_invalid")
        if self.frame_count != EXACT_FRAME_COUNT:
            raise ContractValidationError("frame_count_invalid")
        pixels = self.declared_width * self.declared_height
        if pixels > DECOMPRESSION_PIXEL_LIMIT:
            raise ContractValidationError("decompression_limit")

    def __repr__(self) -> str:
        return "VisualTransferDeclaration(declared)"


@dataclass(frozen=True)
class VisualTransferBeginResult:
    """Result of reserving a server-generated transfer identifier."""

    status: VisualTransferOutcomeStatus
    state: VisualTransferState
    transfer_id: Optional[str] = None
    error: Optional[VisualTransferErrorCode] = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, VisualTransferOutcomeStatus):
            raise ContractValidationError("status is invalid")
        if not isinstance(self.state, VisualTransferState):
            raise ContractValidationError("state is invalid")
        if self.status is VisualTransferOutcomeStatus.OK:
            validate_transfer_id(self.transfer_id)
            if self.error is not None:
                raise ContractValidationError("ambiguous_result")
        else:
            if self.transfer_id is not None or not isinstance(
                self.error, VisualTransferErrorCode
            ):
                raise ContractValidationError("invalid_begin_result")

    def __repr__(self) -> str:
        return (
            "VisualTransferBeginResult(status="
            f"{self.status.value!r}, state={self.state.value!r})"
        )


@dataclass(frozen=True)
class ValidatedImageMetadata:
    """Metadata produced after successful validation.

    ``sha256`` is the receipt hash in the form ``sha256.<64 lowercase hex>``.
    Dimensions are the actual parsed dimensions, which must equal the
    declaration. No bytes are retained on this object.
    """

    mime: str
    width: int
    height: int
    sha256: str

    def __post_init__(self) -> None:
        if self.mime not in _MIME_ALLOWLIST:
            raise ContractValidationError("mime_unsupported")
        for name in ("width", "height"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ContractValidationError(f"{name} is invalid")
            if value < MIN_DIMENSION or value > MAX_DIMENSION:
                raise ContractValidationError("dimensions_exceeded")
        if not isinstance(self.sha256, str) or not self.sha256.startswith("sha256."):
            raise ContractValidationError("sha256 is invalid")
        hex_part = self.sha256[len("sha256."):]
        if len(hex_part) != 64 or not re.fullmatch(r"[0-9a-f]{64}", hex_part):
            raise ContractValidationError("sha256 is invalid")
        if self.width * self.height > DECOMPRESSION_PIXEL_LIMIT:
            raise ContractValidationError("decompression_limit")

    def __repr__(self) -> str:
        return "ValidatedImageMetadata(validated)"


@dataclass(frozen=True)
class VisualTransferResult:
    """Terminal outcome for a transfer operation.

    On success, ``metadata`` is set and ``error`` is None. On failure,
    ``error`` is a stable code and ``metadata`` is None. Neither field ever
    carries bytes, IDs, hashes (except the receipt on metadata), or
    timestamps.
    """

    status: VisualTransferOutcomeStatus
    state: VisualTransferState
    metadata: Optional[ValidatedImageMetadata] = None
    error: Optional[VisualTransferErrorCode] = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, VisualTransferOutcomeStatus):
            raise ContractValidationError("status is invalid")
        if not isinstance(self.state, VisualTransferState):
            raise ContractValidationError("state is invalid")
        if self.metadata is not None and self.error is not None:
            raise ContractValidationError("ambiguous_result")
        if self.status is VisualTransferOutcomeStatus.OK and self.error is not None:
            raise ContractValidationError("ambiguous_result")
        if self.status is VisualTransferOutcomeStatus.ERROR and self.error is None:
            raise ContractValidationError("missing_error")
        if (
            self.status is VisualTransferOutcomeStatus.OK
            and self.state is VisualTransferState.COMPLETED
            and self.metadata is None
        ):
            raise ContractValidationError("missing_metadata")
        if self.metadata is not None and self.state is not VisualTransferState.COMPLETED:
            raise ContractValidationError("state_mismatch")

    def __repr__(self) -> str:
        if self.status is VisualTransferOutcomeStatus.OK:
            return (
                "VisualTransferResult(status='ok', "
                f"state={self.state.value!r})"
            )
        return f"VisualTransferResult(status='error', state={self.state.value!r})"


# The buffer, service, and validator modules import from this contracts module.
# To avoid a circular import, they are imported by the package __init__ rather
# than here. This keeps contracts a pure, dependency-free leaf.

__all__ = [
    "AGGREGATE_MEMORY_CAP_BYTES",
    "ContractValidationError",
    "DECOMPRESSION_PIXEL_LIMIT",
    "EXACT_FRAME_COUNT",
    "HANDOFF_ID_PATTERN",
    "MAX_DIMENSION",
    "MAX_ENCODED_BYTES",
    "MIN_DIMENSION",
    "TRANSFER_ID_PATTERN",
    "TRANSFER_TTL_SECONDS",
    "ValidatedImageMetadata",
    "VisualTransferBeginResult",
    "VisualTransferDeclaration",
    "VisualTransferErrorCode",
    "VisualTransferOutcomeStatus",
    "VisualTransferResult",
    "VisualTransferState",
    "validate_actor_scope",
    "validate_handoff_id",
    "validate_mime",
    "validate_transfer_id",
]
