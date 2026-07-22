"""Pure, transport-independent Phase 4 vision analysis service.

No OCR, inference, networking, filesystem access, subprocess execution,
camera access, screenshot capture, provider selection, upload, or external
execution occurs here. The service only manages analysis lifecycle records.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from typing import Optional

from core.action_policy import ActorContext, validate_actor_context
from core.vision.contracts import (
    ANALYSIS_TTL_SECONDS,
    MAX_OBSERVATIONS,
    ContractValidationError,
    VisionAnalysisRecord,
    VisionAnalysisRequest,
    VisionAnalysisState,
    VisionCapability,
    VisionObservation,
    VisionOutcomeCode,
    VisionServiceOutcome,
    validate_analysis_id,
    validate_handoff_id,
    validate_transfer_id,
)


class VisionAnalysisService:
    """In-memory analysis lifecycle controller with actor/session isolation."""

    def __init__(
        self,
        *,
        clock: Callable[[], float],
        analysis_id_factory: Callable[[], str],
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not callable(analysis_id_factory):
            raise TypeError("analysis_id_factory must be callable")
        self._clock = clock
        self._analysis_id_factory = analysis_id_factory
        self._lock = threading.Lock()
        self._by_id: dict[str, VisionAnalysisRecord] = {}
        self._by_request: dict[tuple[str, str, str], str] = {}

    def __repr__(self) -> str:
        return "VisionAnalysisService()"

    def __str__(self) -> str:
        return self.__repr__()

    def _now(self) -> float:
        try:
            value = self._clock()
        except Exception:
            raise ContractValidationError("clock unavailable") from None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ContractValidationError("clock unavailable")
        try:
            numeric = float(value)
        except (OverflowError, TypeError, ValueError):
            raise ContractValidationError("clock unavailable") from None
        if not math.isfinite(numeric):
            raise ContractValidationError("clock unavailable")
        return numeric

    def _new_analysis_id(self) -> str:
        try:
            value = self._analysis_id_factory()
        except Exception:
            raise ContractValidationError("analysis id factory failed") from None
        return validate_analysis_id(value)

    @staticmethod
    def _validate_actor(actor: object) -> Optional[ActorContext]:
        valid, _ = validate_actor_context(actor)
        if not valid:
            return None
        return actor  # type: ignore[return-value]

    def _outcome_for_record(self, record: VisionAnalysisRecord) -> VisionServiceOutcome:
        code_map = {
            VisionAnalysisState.AWAITING_IMAGE: (
                VisionOutcomeCode.READY
                if record.transfer_id is not None
                else VisionOutcomeCode.AWAITING_IMAGE
            ),
            VisionAnalysisState.ANALYZING: VisionOutcomeCode.ANALYZING,
            VisionAnalysisState.COMPLETED: VisionOutcomeCode.COMPLETED,
            VisionAnalysisState.CANCELLED: VisionOutcomeCode.CANCELLED,
            VisionAnalysisState.EXPIRED: VisionOutcomeCode.EXPIRED,
            VisionAnalysisState.FAILED: VisionOutcomeCode.UNAVAILABLE,
        }
        return VisionServiceOutcome(
            code=code_map[record.state],
            analysis_id=record.analysis_id,
            request_id=record.request_id,
            state=record.state,
            observation_count=len(record.observations),
        )

    def _expire_if_due_locked(
        self, record: VisionAnalysisRecord, now: float
    ) -> VisionAnalysisRecord:
        if record.state.is_terminal:
            return record
        if now < record.expires_at:
            return record
        expired = replace(
            record,
            state=VisionAnalysisState.EXPIRED,
            updated_at=now,
            observations=(),
        )
        self._by_id[record.analysis_id] = expired
        return expired

    def _get_scoped_locked(
        self,
        actor: ActorContext,
        analysis_id: str,
        now: float,
    ) -> Optional[VisionAnalysisRecord]:
        try:
            analysis_id = validate_analysis_id(analysis_id)
        except ContractValidationError:
            return None
        record = self._by_id.get(analysis_id)
        if record is None:
            return None
        if record.actor_id != actor.actor_id or record.session_id != actor.session_id:
            return None
        return self._expire_if_due_locked(record, now)

    def prepare(
        self,
        actor: ActorContext,
        request_id: str,
        handoff_id: str,
        capability: VisionCapability,
    ) -> VisionServiceOutcome:
        """Create or reuse an awaiting-image analysis for one request."""
        scoped = self._validate_actor(actor)
        if scoped is None:
            return VisionServiceOutcome(code=VisionOutcomeCode.INVALID_REQUEST)

        try:
            request = VisionAnalysisRequest(
                request_id=request_id,
                handoff_id=handoff_id,
                capability=capability,
            )
        except ContractValidationError:
            return VisionServiceOutcome(code=VisionOutcomeCode.INVALID_REQUEST)

        try:
            now = self._now()
        except ContractValidationError:
            return VisionServiceOutcome(
                code=VisionOutcomeCode.UNAVAILABLE,
                request_id=request.request_id,
            )
        except Exception:
            return VisionServiceOutcome(
                code=VisionOutcomeCode.UNAVAILABLE,
                request_id=request.request_id,
            )

        scope_key = (scoped.actor_id, scoped.session_id, request.request_id)
        with self._lock:
            existing_id = self._by_request.get(scope_key)
            if existing_id is not None:
                existing = self._get_scoped_locked(scoped, existing_id, now)
                if existing is not None and not existing.state.is_terminal:
                    if (
                        existing.handoff_id == request.handoff_id
                        and existing.capability is request.capability
                    ):
                        return self._outcome_for_record(existing)
                    return VisionServiceOutcome(
                        code=VisionOutcomeCode.INVALID_REQUEST,
                        request_id=request.request_id,
                    )
                self._by_request.pop(scope_key, None)

            try:
                analysis_id = self._new_analysis_id()
            except ContractValidationError:
                return VisionServiceOutcome(
                    code=VisionOutcomeCode.UNAVAILABLE,
                    request_id=request.request_id,
                )

            if analysis_id in self._by_id:
                return VisionServiceOutcome(
                    code=VisionOutcomeCode.UNAVAILABLE,
                    request_id=request.request_id,
                )

            record = VisionAnalysisRecord(
                analysis_id=analysis_id,
                request_id=request.request_id,
                actor_id=scoped.actor_id,
                session_id=scoped.session_id,
                handoff_id=request.handoff_id,
                capability=request.capability,
                state=VisionAnalysisState.AWAITING_IMAGE,
                created_at=now,
                expires_at=now + ANALYSIS_TTL_SECONDS,
                updated_at=now,
            )
            self._by_id[analysis_id] = record
            self._by_request[scope_key] = analysis_id
            return self._outcome_for_record(record)

    def attach_image(
        self,
        actor: ActorContext,
        analysis_id: str,
        handoff_id: str,
        transfer_id: str,
    ) -> VisionServiceOutcome:
        """Attach an accepted transfer reference while awaiting an image."""
        scoped = self._validate_actor(actor)
        if scoped is None:
            return VisionServiceOutcome(code=VisionOutcomeCode.INVALID_REQUEST)

        try:
            handoff_id = validate_handoff_id(handoff_id)
            transfer_id = validate_transfer_id(transfer_id)
            now = self._now()
        except ContractValidationError:
            return VisionServiceOutcome(code=VisionOutcomeCode.INVALID_REQUEST)
        except Exception:
            return VisionServiceOutcome(code=VisionOutcomeCode.UNAVAILABLE)

        with self._lock:
            record = self._get_scoped_locked(scoped, analysis_id, now)
            if record is None:
                return VisionServiceOutcome(code=VisionOutcomeCode.ANALYSIS_NOT_FOUND)
            if record.state is VisionAnalysisState.EXPIRED:
                return VisionServiceOutcome(
                    code=VisionOutcomeCode.EXPIRED,
                    analysis_id=record.analysis_id,
                    request_id=record.request_id,
                    state=record.state,
                )
            if record.handoff_id != handoff_id:
                return VisionServiceOutcome(
                    code=VisionOutcomeCode.INVALID_REQUEST,
                    analysis_id=record.analysis_id,
                    request_id=record.request_id,
                    state=record.state,
                )
            if record.state is not VisionAnalysisState.AWAITING_IMAGE:
                return VisionServiceOutcome(
                    code=VisionOutcomeCode.INVALID_REQUEST,
                    analysis_id=record.analysis_id,
                    request_id=record.request_id,
                    state=record.state,
                )
            if record.transfer_id is not None:
                if record.transfer_id == transfer_id:
                    return self._outcome_for_record(record)
                return VisionServiceOutcome(
                    code=VisionOutcomeCode.INVALID_REQUEST,
                    analysis_id=record.analysis_id,
                    request_id=record.request_id,
                    state=record.state,
                )

            updated = replace(
                record,
                transfer_id=transfer_id,
                updated_at=now,
            )
            self._by_id[record.analysis_id] = updated
            return self._outcome_for_record(updated)

    def begin_analysis(
        self,
        actor: ActorContext,
        analysis_id: str,
    ) -> VisionServiceOutcome:
        """Transition an image-ready analysis into analyzing."""
        scoped = self._validate_actor(actor)
        if scoped is None:
            return VisionServiceOutcome(code=VisionOutcomeCode.INVALID_REQUEST)

        try:
            now = self._now()
        except ContractValidationError:
            return VisionServiceOutcome(code=VisionOutcomeCode.UNAVAILABLE)
        except Exception:
            return VisionServiceOutcome(code=VisionOutcomeCode.UNAVAILABLE)

        with self._lock:
            record = self._get_scoped_locked(scoped, analysis_id, now)
            if record is None:
                return VisionServiceOutcome(code=VisionOutcomeCode.ANALYSIS_NOT_FOUND)
            if record.state is VisionAnalysisState.EXPIRED:
                return VisionServiceOutcome(
                    code=VisionOutcomeCode.EXPIRED,
                    analysis_id=record.analysis_id,
                    request_id=record.request_id,
                    state=record.state,
                )
            if record.state is VisionAnalysisState.ANALYZING:
                return self._outcome_for_record(record)
            if (
                record.state is not VisionAnalysisState.AWAITING_IMAGE
                or record.transfer_id is None
            ):
                return VisionServiceOutcome(
                    code=VisionOutcomeCode.INVALID_REQUEST,
                    analysis_id=record.analysis_id,
                    request_id=record.request_id,
                    state=record.state,
                )

            updated = replace(
                record,
                state=VisionAnalysisState.ANALYZING,
                updated_at=now,
            )
            self._by_id[record.analysis_id] = updated
            return self._outcome_for_record(updated)

    def complete(
        self,
        actor: ActorContext,
        analysis_id: str,
        observations: Sequence[VisionObservation] | Iterable[VisionObservation],
    ) -> VisionServiceOutcome:
        """Complete an analyzing record with bounded observations."""
        scoped = self._validate_actor(actor)
        if scoped is None:
            return VisionServiceOutcome(code=VisionOutcomeCode.INVALID_REQUEST)

        try:
            now = self._now()
            items = tuple(observations)
        except ContractValidationError:
            return VisionServiceOutcome(code=VisionOutcomeCode.UNAVAILABLE)
        except Exception:
            return VisionServiceOutcome(code=VisionOutcomeCode.INVALID_REQUEST)

        if len(items) < 1 or len(items) > MAX_OBSERVATIONS:
            return VisionServiceOutcome(code=VisionOutcomeCode.INVALID_REQUEST)
        if not all(isinstance(item, VisionObservation) for item in items):
            return VisionServiceOutcome(code=VisionOutcomeCode.INVALID_REQUEST)

        with self._lock:
            record = self._get_scoped_locked(scoped, analysis_id, now)
            if record is None:
                return VisionServiceOutcome(code=VisionOutcomeCode.ANALYSIS_NOT_FOUND)
            if record.state is VisionAnalysisState.EXPIRED:
                return VisionServiceOutcome(
                    code=VisionOutcomeCode.EXPIRED,
                    analysis_id=record.analysis_id,
                    request_id=record.request_id,
                    state=record.state,
                )
            if record.state is VisionAnalysisState.COMPLETED:
                return self._outcome_for_record(record)
            if record.state is not VisionAnalysisState.ANALYZING:
                return VisionServiceOutcome(
                    code=VisionOutcomeCode.INVALID_REQUEST,
                    analysis_id=record.analysis_id,
                    request_id=record.request_id,
                    state=record.state,
                )

            try:
                updated = replace(
                    record,
                    state=VisionAnalysisState.COMPLETED,
                    observations=items,
                    updated_at=now,
                )
            except ContractValidationError:
                return VisionServiceOutcome(
                    code=VisionOutcomeCode.INVALID_REQUEST,
                    analysis_id=record.analysis_id,
                    request_id=record.request_id,
                    state=record.state,
                )

            self._by_id[record.analysis_id] = updated
            return self._outcome_for_record(updated)

    def cancel(self, actor: ActorContext, analysis_id: str) -> VisionServiceOutcome:
        """Cancel a non-terminal analysis. Idempotent when already cancelled."""
        scoped = self._validate_actor(actor)
        if scoped is None:
            return VisionServiceOutcome(code=VisionOutcomeCode.INVALID_REQUEST)

        try:
            now = self._now()
        except ContractValidationError:
            return VisionServiceOutcome(code=VisionOutcomeCode.UNAVAILABLE)
        except Exception:
            return VisionServiceOutcome(code=VisionOutcomeCode.UNAVAILABLE)

        with self._lock:
            record = self._get_scoped_locked(scoped, analysis_id, now)
            if record is None:
                return VisionServiceOutcome(code=VisionOutcomeCode.ANALYSIS_NOT_FOUND)
            if record.state is VisionAnalysisState.CANCELLED:
                return self._outcome_for_record(record)
            if record.state.is_terminal:
                return VisionServiceOutcome(
                    code=VisionOutcomeCode.INVALID_REQUEST,
                    analysis_id=record.analysis_id,
                    request_id=record.request_id,
                    state=record.state,
                )

            updated = replace(
                record,
                state=VisionAnalysisState.CANCELLED,
                updated_at=now,
                observations=(),
            )
            self._by_id[record.analysis_id] = updated
            return self._outcome_for_record(updated)

    def status(self, actor: ActorContext, analysis_id: str) -> VisionServiceOutcome:
        """Return the current scoped analysis status without mutation beyond expiry."""
        scoped = self._validate_actor(actor)
        if scoped is None:
            return VisionServiceOutcome(code=VisionOutcomeCode.INVALID_REQUEST)

        try:
            now = self._now()
        except ContractValidationError:
            return VisionServiceOutcome(code=VisionOutcomeCode.UNAVAILABLE)
        except Exception:
            return VisionServiceOutcome(code=VisionOutcomeCode.UNAVAILABLE)

        with self._lock:
            record = self._get_scoped_locked(scoped, analysis_id, now)
            if record is None:
                return VisionServiceOutcome(code=VisionOutcomeCode.ANALYSIS_NOT_FOUND)
            return self._outcome_for_record(record)

    def fail(self, actor: ActorContext, analysis_id: str) -> VisionServiceOutcome:
        """Fail one scoped active analysis without retaining observations."""
        scoped = self._validate_actor(actor)
        if scoped is None:
            return VisionServiceOutcome(code=VisionOutcomeCode.INVALID_REQUEST)
        try:
            now = self._now()
        except ContractValidationError:
            return VisionServiceOutcome(code=VisionOutcomeCode.UNAVAILABLE)
        with self._lock:
            record = self._get_scoped_locked(scoped, analysis_id, now)
            if record is None:
                return VisionServiceOutcome(code=VisionOutcomeCode.ANALYSIS_NOT_FOUND)
            if record.state is VisionAnalysisState.FAILED:
                return self._outcome_for_record(record)
            if record.state.is_terminal:
                return VisionServiceOutcome(
                    code=VisionOutcomeCode.INVALID_REQUEST,
                    analysis_id=record.analysis_id,
                    request_id=record.request_id,
                    state=record.state,
                )
            updated = replace(
                record,
                state=VisionAnalysisState.FAILED,
                updated_at=now,
                observations=(),
            )
            self._by_id[record.analysis_id] = updated
            return self._outcome_for_record(updated)

    def _record_for_runtime(
        self, actor: ActorContext, analysis_id: str
    ) -> Optional[VisionAnalysisRecord]:
        """Return one exact scoped record to the trusted runtime boundary."""
        scoped = self._validate_actor(actor)
        if scoped is None:
            return None
        try:
            now = self._now()
        except ContractValidationError:
            return None
        with self._lock:
            return self._get_scoped_locked(scoped, analysis_id, now)

    def discard(self, actor: ActorContext, analysis_id: str) -> bool:
        """Remove one exact scoped record after its terminal response is built."""
        scoped = self._validate_actor(actor)
        if scoped is None:
            return False
        try:
            analysis_id = validate_analysis_id(analysis_id)
        except ContractValidationError:
            return False
        with self._lock:
            record = self._by_id.get(analysis_id)
            if record is None or (
                record.actor_id != scoped.actor_id
                or record.session_id != scoped.session_id
            ):
                return False
            self._by_id.pop(analysis_id, None)
            self._by_request.pop(
                (record.actor_id, record.session_id, record.request_id), None
            )
            return True

    def clear_session(self, actor: ActorContext) -> int:
        """Remove all transient analysis state for one exact actor/session."""
        scoped = self._validate_actor(actor)
        if scoped is None:
            return 0
        removed = 0
        with self._lock:
            for analysis_id, record in list(self._by_id.items()):
                if (
                    record.actor_id != scoped.actor_id
                    or record.session_id != scoped.session_id
                ):
                    continue
                self._by_id.pop(analysis_id, None)
                self._by_request.pop(
                    (record.actor_id, record.session_id, record.request_id), None
                )
                removed += 1
        return removed

    def expire_due(self) -> int:
        """Expire all non-terminal analyses whose deadline has passed."""
        try:
            now = self._now()
        except ContractValidationError:
            return 0
        except Exception:
            return 0

        expired = 0
        with self._lock:
            for analysis_id, record in list(self._by_id.items()):
                if record.state.is_terminal:
                    continue
                if now < record.expires_at:
                    continue
                self._by_id[analysis_id] = replace(
                    record,
                    state=VisionAnalysisState.EXPIRED,
                    updated_at=now,
                    observations=(),
                )
                expired += 1
        return expired
