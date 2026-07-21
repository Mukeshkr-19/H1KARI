"""Bounded in-memory buffer for one pending visual transfer.

Holds at most one active transfer per exact scoped handoff. Aggregate in-memory
bytes are capped at ``AGGREGATE_MEMORY_CAP_BYTES``. Bytearray contents are
cleared before references are dropped where practical. No persistence, no disk
writes, no network, no subprocess.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

from core.visual_transfer.contracts import (
    AGGREGATE_MEMORY_CAP_BYTES,
    ContractValidationError,
    TRANSFER_TTL_SECONDS,
    ValidatedImageMetadata,
    VisualTransferDeclaration,
    VisualTransferErrorCode,
    VisualTransferState,
    validate_actor_scope,
)
from core.visual_transfer.validator import VisualTransferValidator as _Validator


@dataclass
class _PendingTransfer:
    """Mutable in-flight record for one transfer. Never persisted."""

    declaration: VisualTransferDeclaration
    actor_scope: str
    handoff_id: str
    created_at: float
    expires_at: float
    state: VisualTransferState = VisualTransferState.PENDING
    buffer: Optional[bytearray] = None
    received_bytes: int = 0
    metadata: Optional[ValidatedImageMetadata] = None
    error: Optional[VisualTransferErrorCode] = None

    def __repr__(self) -> str:
        return f"_PendingTransfer(state={self.state.value!r})"


class VisualTransferBuffer:
    """Bounded in-memory store keyed by exact (actor_scope, transfer_id).

    The buffer enforces:
      - one active transfer per exact scoped handoff,
      - exact scope correlation (cross-session requests disclose nothing),
      - an aggregate memory cap across all in-flight buffers,
      - deterministic cleanup on complete/cancel/failure/expiry/disconnect.

    All public methods are synchronous and side-effect-bounded to in-memory
    state. No I/O is performed.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float],
        aggregate_cap_bytes: int = AGGREGATE_MEMORY_CAP_BYTES,
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        if (
            not isinstance(aggregate_cap_bytes, int)
            or isinstance(aggregate_cap_bytes, bool)
            or aggregate_cap_bytes < 1
        ):
            raise TypeError("aggregate_cap_bytes must be a positive int")
        self._clock = clock
        self._aggregate_cap = aggregate_cap_bytes
        self._transfers: Dict[Tuple[str, str], _PendingTransfer] = {}
        self._handoff_index: Dict[Tuple[str, str], str] = {}

    def _now(self) -> float:
        try:
            value = self._clock()
        except Exception:
            raise ContractValidationError("unavailable") from None
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise ContractValidationError("unavailable")
        return float(value)

    # --- Aggregate accounting ---------------------------------------------

    def _aggregate_bytes(self) -> int:
        total = 0
        for pending in self._transfers.values():
            if pending.state in {
                VisualTransferState.PENDING,
                VisualTransferState.RECEIVING,
                VisualTransferState.VALIDATING,
            }:
                total += pending.declaration.declared_byte_length
        return total

    def _clear_buffer(self, pending: _PendingTransfer) -> None:
        if pending.buffer is not None:
            # Overwrite before dropping the reference where practical.
            try:
                for i in range(len(pending.buffer)):
                    pending.buffer[i] = 0
            except (TypeError, IndexError, ValueError):
                pass
            pending.buffer = None
        pending.received_bytes = 0

    def _drop(self, key: Tuple[str, str]) -> None:
        pending = self._transfers.pop(key, None)
        if pending is None:
            return
        self._clear_buffer(pending)
        handoff_key = (pending.actor_scope, pending.handoff_id)
        if self._handoff_index.get(handoff_key) == key[1]:
            self._handoff_index.pop(handoff_key, None)

    # --- Public API --------------------------------------------------------

    def begin(
        self,
        actor_scope: str,
        transfer_id: str,
        declaration: VisualTransferDeclaration,
    ) -> Tuple[VisualTransferState, Optional[VisualTransferErrorCode]]:
        """Reserve a slot for one transfer. Returns (state, error_or_none)."""
        validate_actor_scope(actor_scope)
        from core.visual_transfer.contracts import validate_transfer_id

        validate_transfer_id(transfer_id)
        _Validator.validate_declaration(declaration)
        key = (actor_scope, transfer_id)
        handoff_key = (actor_scope, declaration.handoff_id)
        now = self._now()

        # Idempotent re-begin for the same transfer_id returns current state.
        existing = self._transfers.get(key)
        if existing is not None:
            if existing.handoff_id != declaration.handoff_id:
                return VisualTransferState.FAILED, VisualTransferErrorCode.UNAVAILABLE
            if existing.state in {
                VisualTransferState.COMPLETED,
                VisualTransferState.CANCELLED,
                VisualTransferState.FAILED,
                VisualTransferState.EXPIRED,
            }:
                return existing.state, existing.error
            return existing.state, None

        # One active transfer per exact scoped handoff.
        existing_handoff = self._handoff_index.get(handoff_key)
        if existing_handoff is not None:
            other = self._transfers.get((actor_scope, existing_handoff))
            if other is not None and other.state in {
                VisualTransferState.PENDING,
                VisualTransferState.RECEIVING,
                VisualTransferState.VALIDATING,
            }:
                return VisualTransferState.FAILED, VisualTransferErrorCode.RATE_LIMITED

        # Reject if the aggregate cap would be exhausted by the declared size.
        # We reserve the declared byte length as the upper bound for the buffer.
        if self._aggregate_bytes() + declaration.declared_byte_length > self._aggregate_cap:
            return VisualTransferState.FAILED, VisualTransferErrorCode.RATE_LIMITED

        pending = _PendingTransfer(
            declaration=declaration,
            actor_scope=actor_scope,
            handoff_id=declaration.handoff_id,
            created_at=now,
            expires_at=now + TRANSFER_TTL_SECONDS,
            state=VisualTransferState.PENDING,
        )
        self._transfers[key] = pending
        self._handoff_index[handoff_key] = transfer_id
        return VisualTransferState.PENDING, None

    def receive(
        self,
        actor_scope: str,
        transfer_id: str,
        frame: object,
    ) -> Tuple[VisualTransferState, Optional[ValidatedImageMetadata], Optional[VisualTransferErrorCode]]:
        """Accept the single frame, validate, and return terminal state.

        On success returns (COMPLETED, metadata, None). On failure returns
        (FAILED, None, error). The buffer is cleared in all terminal cases.
        """
        validate_actor_scope(actor_scope)
        from core.visual_transfer.contracts import validate_transfer_id

        validate_transfer_id(transfer_id)
        key = (actor_scope, transfer_id)
        pending = self._transfers.get(key)
        if pending is None:
            return (
                VisualTransferState.FAILED,
                None,
                VisualTransferErrorCode.TRANSFER_NOT_FOUND,
            )
        now = self._now()
        if now >= pending.expires_at:
            pending.state = VisualTransferState.EXPIRED
            pending.error = VisualTransferErrorCode.TRANSFER_EXPIRED
            self._drop(key)
            return (
                VisualTransferState.EXPIRED,
                None,
                VisualTransferErrorCode.TRANSFER_EXPIRED,
            )
        if pending.state in {
            VisualTransferState.COMPLETED,
            VisualTransferState.CANCELLED,
            VisualTransferState.FAILED,
            VisualTransferState.EXPIRED,
        }:
            # Idempotent terminal return.
            return pending.state, pending.metadata, pending.error

        pending.state = VisualTransferState.RECEIVING
        if not isinstance(frame, bytes):
            pending.state = VisualTransferState.FAILED
            pending.error = VisualTransferErrorCode.INVALID_REQUEST
            self._drop(key)
            return (
                VisualTransferState.FAILED,
                None,
                VisualTransferErrorCode.INVALID_REQUEST,
            )
        if len(frame) != pending.declaration.declared_byte_length:
            pending.state = VisualTransferState.FAILED
            pending.error = VisualTransferErrorCode.SIZE_EXCEEDED
            self._drop(key)
            return (
                VisualTransferState.FAILED,
                None,
                VisualTransferErrorCode.SIZE_EXCEEDED,
            )
        # Allocate the bounded buffer and copy bytes.
        try:
            pending.buffer = bytearray(len(frame))
            pending.buffer[:] = frame
            pending.received_bytes = len(frame)
        except MemoryError:
            pending.state = VisualTransferState.FAILED
            pending.error = VisualTransferErrorCode.SIZE_EXCEEDED
            self._drop(key)
            return (
                VisualTransferState.FAILED,
                None,
                VisualTransferErrorCode.SIZE_EXCEEDED,
            )

        pending.state = VisualTransferState.VALIDATING
        try:
            metadata = _Validator.validate_frame(pending.declaration, bytes(pending.buffer))
        except ContractValidationError as exc:
            code = _Validator._error_code_from_message(str(exc))
            pending.state = VisualTransferState.FAILED
            pending.error = code
            self._drop(key)
            return (VisualTransferState.FAILED, None, code)
        except Exception:
            pending.state = VisualTransferState.FAILED
            pending.error = VisualTransferErrorCode.MALFORMED_IMAGE
            self._drop(key)
            return (
                VisualTransferState.FAILED,
                None,
                VisualTransferErrorCode.MALFORMED_IMAGE,
            )

        pending.metadata = metadata
        pending.state = VisualTransferState.COMPLETED
        # Clear the buffer immediately; metadata is the only retained artifact.
        self._clear_buffer(pending)
        return (VisualTransferState.COMPLETED, metadata, None)

    def status(
        self,
        actor_scope: str,
        transfer_id: str,
    ) -> Tuple[VisualTransferState, Optional[ValidatedImageMetadata], Optional[VisualTransferErrorCode]]:
        """Read-only status. Cross-session requests disclose nothing."""
        validate_actor_scope(actor_scope)
        from core.visual_transfer.contracts import validate_transfer_id

        validate_transfer_id(transfer_id)
        key = (actor_scope, transfer_id)
        pending = self._transfers.get(key)
        if pending is None:
            return (
                VisualTransferState.FAILED,
                None,
                VisualTransferErrorCode.TRANSFER_NOT_FOUND,
            )
        return pending.state, pending.metadata, pending.error

    def cancel(
        self,
        actor_scope: str,
        transfer_id: str,
    ) -> Tuple[VisualTransferState, Optional[VisualTransferErrorCode]]:
        """Idempotent cancel. Clears the buffer."""
        validate_actor_scope(actor_scope)
        from core.visual_transfer.contracts import validate_transfer_id

        validate_transfer_id(transfer_id)
        key = (actor_scope, transfer_id)
        pending = self._transfers.get(key)
        if pending is None:
            return (
                VisualTransferState.FAILED,
                VisualTransferErrorCode.TRANSFER_NOT_FOUND,
            )
        if pending.state is VisualTransferState.COMPLETED:
            # Idempotent: a completed transfer stays completed.
            return VisualTransferState.COMPLETED, None
        pending.state = VisualTransferState.CANCELLED
        pending.error = None
        self._drop(key)
        return VisualTransferState.CANCELLED, None

    def clear_session(self, actor_scope: str) -> None:
        """Disconnect cleanup: drop every transfer for one actor scope."""
        validate_actor_scope(actor_scope)
        keys = [key for key in self._transfers if key[0] == actor_scope]
        for key in keys:
            self._drop(key)

    def expire_due(self) -> int:
        """Sweep expired transfers. Returns the count expired."""
        now = self._now()
        expired = 0
        for key, pending in list(self._transfers.items()):
            if pending.state in {
                VisualTransferState.COMPLETED,
                VisualTransferState.CANCELLED,
                VisualTransferState.FAILED,
                VisualTransferState.EXPIRED,
            }:
                continue
            if now >= pending.expires_at:
                pending.state = VisualTransferState.EXPIRED
                pending.error = VisualTransferErrorCode.TRANSFER_EXPIRED
                self._drop(key)
                expired += 1
        return expired

    # --- Introspection (test-only; content-free) --------------------------

    def active_count(self) -> int:
        return len(self._transfers)

    def aggregate_bytes(self) -> int:
        return self._aggregate_bytes()
