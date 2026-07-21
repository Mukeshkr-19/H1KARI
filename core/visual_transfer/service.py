"""Visual-transfer service: injected-clock, injected-ID, injected-handoff predicate.

Coordinates begin/receive/status/cancel/clear/expire against the bounded
buffer. Enforces exact scope correlation and cross-session non-disclosure.
No persistence, no I/O, no subprocess.
"""

from __future__ import annotations

from typing import Callable, Optional

from core.visual_transfer.buffer import VisualTransferBuffer
from core.visual_transfer.contracts import (
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


class VisualTransferService:
    """Transport-independent visual-transfer coordination.

    Dependencies are injected for deterministic testing:
      - ``clock``: returns monotonic-ish seconds (float).
      - ``transfer_id_factory``: returns a fresh transfer_id string each call.
      - ``handoff_accepted``: predicate ``(actor_scope, handoff_id) -> bool``
        proving the exact scoped handoff is currently accepted by the
        surrounding authenticated transport adapter.

    The service never imports network, disk, subprocess, OCR, browser,
    camera, screenshot, AppleScript, or provider modules.
    """

    def __init__(
        self,
        *,
        buffer: VisualTransferBuffer,
        clock: Callable[[], float],
        transfer_id_factory: Callable[[], str],
        handoff_accepted: Callable[[str, str], bool],
    ) -> None:
        if not isinstance(buffer, VisualTransferBuffer):
            raise TypeError("buffer must be a VisualTransferBuffer")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not callable(transfer_id_factory):
            raise TypeError("transfer_id_factory must be callable")
        if not callable(handoff_accepted):
            raise TypeError("handoff_accepted must be callable")
        self._buffer = buffer
        self._clock = clock
        self._transfer_id_factory = transfer_id_factory
        self._handoff_accepted = handoff_accepted

    # --- Internal helpers --------------------------------------------------

    def _ok(self, state: VisualTransferState, metadata: Optional[ValidatedImageMetadata]) -> VisualTransferResult:
        return VisualTransferResult(
            status=VisualTransferOutcomeStatus.OK,
            state=state,
            metadata=metadata,
        )

    def _err(self, state: VisualTransferState, code: VisualTransferErrorCode) -> VisualTransferResult:
        return VisualTransferResult(
            status=VisualTransferOutcomeStatus.ERROR,
            state=state,
            error=code,
        )

    @staticmethod
    def _safe_scope(actor_scope: object) -> Optional[str]:
        try:
            return validate_actor_scope(actor_scope)
        except ContractValidationError:
            return None

    # --- Public API --------------------------------------------------------

    def begin(
        self,
        actor_scope: str,
        declaration: VisualTransferDeclaration,
    ) -> VisualTransferBeginResult:
        scope = self._safe_scope(actor_scope)
        if scope is None:
            return VisualTransferBeginResult(
                status=VisualTransferOutcomeStatus.ERROR,
                state=VisualTransferState.FAILED,
                error=VisualTransferErrorCode.UNAUTHORIZED,
            )
        try:
            validate_handoff_id(declaration.handoff_id)
        except ContractValidationError:
            return VisualTransferBeginResult(
                status=VisualTransferOutcomeStatus.ERROR,
                state=VisualTransferState.FAILED,
                error=VisualTransferErrorCode.HANDOFF_NOT_ACCEPTED,
            )
        try:
            accepted = self._handoff_accepted(scope, declaration.handoff_id)
        except Exception:
            accepted = False
        if accepted is not True:
            return VisualTransferBeginResult(
                status=VisualTransferOutcomeStatus.ERROR,
                state=VisualTransferState.FAILED,
                error=VisualTransferErrorCode.HANDOFF_NOT_ACCEPTED,
            )
        try:
            transfer_id = self._transfer_id_factory()
            validate_transfer_id(transfer_id)
            state, error = self._buffer.begin(scope, transfer_id, declaration)
        except Exception:
            return VisualTransferBeginResult(
                status=VisualTransferOutcomeStatus.ERROR,
                state=VisualTransferState.FAILED,
                error=VisualTransferErrorCode.UNAVAILABLE,
            )
        if error is not None:
            return VisualTransferBeginResult(
                status=VisualTransferOutcomeStatus.ERROR,
                state=state,
                error=error,
            )
        return VisualTransferBeginResult(
            status=VisualTransferOutcomeStatus.OK,
            state=state,
            transfer_id=transfer_id,
        )

    def receive(
        self,
        actor_scope: str,
        transfer_id: str,
        frame: object,
    ) -> VisualTransferResult:
        scope = self._safe_scope(actor_scope)
        if scope is None:
            return self._err(VisualTransferState.FAILED, VisualTransferErrorCode.UNAUTHORIZED)
        try:
            validate_transfer_id(transfer_id)
        except ContractValidationError:
            return self._err(VisualTransferState.FAILED, VisualTransferErrorCode.TRANSFER_NOT_FOUND)
        state, metadata, error = self._buffer.receive(scope, transfer_id, frame)
        if error is not None:
            return self._err(state, error)
        return self._ok(state, metadata)

    def status(self, actor_scope: str, transfer_id: str) -> VisualTransferResult:
        scope = self._safe_scope(actor_scope)
        if scope is None:
            return self._err(VisualTransferState.FAILED, VisualTransferErrorCode.UNAUTHORIZED)
        try:
            validate_transfer_id(transfer_id)
        except ContractValidationError:
            return self._err(VisualTransferState.FAILED, VisualTransferErrorCode.TRANSFER_NOT_FOUND)
        state, metadata, error = self._buffer.status(scope, transfer_id)
        if error is not None and state is not VisualTransferState.COMPLETED:
            return self._err(state, error)
        if state is VisualTransferState.COMPLETED:
            return self._ok(state, metadata)
        # Non-terminal pending/receiving/validating: report state without metadata.
        return VisualTransferResult(
            status=VisualTransferOutcomeStatus.OK,
            state=state,
            metadata=None,
        )

    def cancel(self, actor_scope: str, transfer_id: str) -> VisualTransferResult:
        scope = self._safe_scope(actor_scope)
        if scope is None:
            return self._err(VisualTransferState.FAILED, VisualTransferErrorCode.UNAUTHORIZED)
        try:
            validate_transfer_id(transfer_id)
        except ContractValidationError:
            return self._err(VisualTransferState.FAILED, VisualTransferErrorCode.TRANSFER_NOT_FOUND)
        state, error = self._buffer.cancel(scope, transfer_id)
        if error is not None and state is not VisualTransferState.CANCELLED:
            return self._err(state, error)
        if state is VisualTransferState.COMPLETED:
            # Idempotent cancel on a completed transfer must return the
            # retained metadata to satisfy the result contract.
            _, metadata, _ = self._buffer.status(scope, transfer_id)
            return self._ok(state, metadata)
        return self._ok(state, None)

    def clear_session(self, actor_scope: str) -> None:
        scope = self._safe_scope(actor_scope)
        if scope is None:
            return
        self._buffer.clear_session(scope)

    def expire_due(self) -> int:
        return self._buffer.expire_due()
