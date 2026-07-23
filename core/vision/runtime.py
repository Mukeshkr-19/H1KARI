"""Bounded Phase 4 vision runtime.

The runtime binds a prepared analysis to one exact actor, session, handoff and
validated transfer. It performs no capture, upload, provider selection or
durable persistence. Image bytes exist only for the duration of ``analyze``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from core.action_policy import ActorContext
from core.protocol import validate_server_message
from core.vision.contracts import (
    MAX_OBSERVATION_TEXT_LENGTH,
    VisionAnalysisState,
    VisionCapability,
    VisionObservation,
    VisionObservationKind,
    VisionOutcomeCode,
    VisionProcessingMode,
)
from core.vision.ocr import LocalOcrAdapter, OcrStatus
from core.vision.service import VisionAnalysisService


class DescriptionAnalyzer(Protocol):
    def __call__(
        self, image_bytes: bytes, *, mime_type: str
    ) -> tuple[VisionObservation, ...]: ...


class CloudVisionAnalyzer(Protocol):
    def __call__(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
        capability: VisionCapability,
    ) -> tuple[VisionObservation, ...]: ...


class VisionRuntime:
    """Canonical server-message boundary around the pure vision service."""

    def __init__(
        self,
        *,
        service: VisionAnalysisService,
        ocr_adapter: LocalOcrAdapter | None = None,
        description_analyzer: DescriptionAnalyzer | None = None,
        cloud_vision_analyzer: CloudVisionAnalyzer | None = None,
        handoff_accepted: Callable[[str, str], bool] | None = None,
    ) -> None:
        if not isinstance(service, VisionAnalysisService):
            raise TypeError("vision service is required")
        if description_analyzer is not None and not callable(description_analyzer):
            raise TypeError("description analyzer must be callable")
        if cloud_vision_analyzer is not None and not callable(cloud_vision_analyzer):
            raise TypeError("cloud vision analyzer must be callable")
        self._service = service
        self._ocr_adapter = ocr_adapter
        self._description_analyzer = description_analyzer
        self._cloud_vision_analyzer = cloud_vision_analyzer
        self._handoff_accepted = handoff_accepted

    def __repr__(self) -> str:
        return "VisionRuntime()"

    @staticmethod
    def _safe_request_id(value: object) -> str:
        if isinstance(value, str):
            from core.vision.contracts import CANONICAL_ID_PATTERN

            if CANONICAL_ID_PATTERN.fullmatch(value):
                return value
        return "invalid-request"

    def _message(self, message: dict) -> dict:
        try:
            if validate_server_message(message) is None:
                return message
        except Exception:
            pass
        request_id = self._safe_request_id(message.get("request_id"))
        fallback = {
            "type": "vision_analysis_error",
            "request_id": request_id,
            "code": "unavailable",
        }
        if validate_server_message(fallback) is None:
            return fallback
        return {
            "type": "vision_analysis_error",
            "request_id": "invalid-request",
            "code": "unavailable",
        }

    def _error(
        self, request_id: object, code: str, analysis_id: object | None = None
    ) -> dict:
        message = {
            "type": "vision_analysis_error",
            "request_id": self._safe_request_id(request_id),
            "code": code,
        }
        if isinstance(analysis_id, str):
            from core.vision.contracts import CANONICAL_ID_PATTERN

            if CANONICAL_ID_PATTERN.fullmatch(analysis_id):
                message["analysis_id"] = analysis_id
        return self._message(message)

    def prepare(
        self,
        actor: ActorContext,
        request_id: str,
        handoff_id: str,
        capability: object,
        mode: object = VisionProcessingMode.PRIVATE_LOCAL,
    ) -> dict:
        try:
            parsed_capability = VisionCapability(capability)
        except (TypeError, ValueError):
            return self._error(request_id, "invalid_request")
        try:
            parsed_mode = VisionProcessingMode(mode)
        except (TypeError, ValueError):
            return self._error(request_id, "invalid_request")
        local_unavailable = (
            parsed_mode is VisionProcessingMode.PRIVATE_LOCAL
            and (
                (
                    parsed_capability is VisionCapability.OCR
                    and self._ocr_adapter is None
                )
                or (
                    parsed_capability is VisionCapability.DESCRIBE
                    and self._description_analyzer is None
                )
            )
        )
        cloud_unavailable = (
            parsed_mode is VisionProcessingMode.CLOUD
            and self._cloud_vision_analyzer is None
        )
        if local_unavailable or cloud_unavailable:
            return self._error(request_id, "capability_unavailable")
        if self._handoff_accepted is None:
            return self._error(request_id, "unavailable")
        try:
            if not self._handoff_accepted(actor.session_id, handoff_id):
                return self._error(request_id, "handoff_not_accepted")
        except Exception:
            return self._error(request_id, "unavailable")
        try:
            outcome = self._service.prepare(
                actor,
                request_id,
                handoff_id,
                parsed_capability,
                parsed_mode,
            )
        except Exception:
            return self._error(request_id, "unavailable")
        if outcome.code not in {
            VisionOutcomeCode.AWAITING_IMAGE,
            VisionOutcomeCode.READY,
        } or outcome.analysis_id is None:
            code = (
                "invalid_request"
                if outcome.code is VisionOutcomeCode.INVALID_REQUEST
                else "unavailable"
            )
            return self._error(request_id, code)
        record = self._service._record_for_runtime(actor, outcome.analysis_id)
        if record is None:
            return self._error(request_id, "unavailable")
        return self._message(
            {
                "type": "vision_analysis_ready",
                "request_id": record.request_id,
                "analysis_id": record.analysis_id,
                "expires_at": record.expires_at,
            }
        )

    def attach_transfer(
        self,
        actor: ActorContext,
        analysis_id: str,
        handoff_id: str,
        transfer_id: str,
    ) -> dict:
        record = self._service._record_for_runtime(actor, analysis_id)
        if record is None:
            return self._error("invalid-request", "analysis_not_found", analysis_id)
        request_id = record.request_id
        if record.handoff_id != handoff_id:
            return self._error(request_id, "transfer_mismatch", analysis_id)
        try:
            outcome = self._service.attach_image(
                actor, analysis_id, handoff_id, transfer_id
            )
        except Exception:
            return self._error(request_id, "unavailable", analysis_id)
        if outcome.code is not VisionOutcomeCode.READY:
            return self._error(request_id, "transfer_mismatch", analysis_id)
        return self._message(
            {
                "type": "vision_analysis_update",
                "request_id": request_id,
                "analysis_id": analysis_id,
                "state": "awaiting_image",
            }
        )

    @staticmethod
    def _ocr_observations(text: str) -> tuple[VisionObservation, ...]:
        if not text:
            return ()
        return tuple(
            VisionObservation(
                kind=VisionObservationKind.TEXT,
                text=text[index : index + MAX_OBSERVATION_TEXT_LENGTH],
            )
            for index in range(0, len(text), MAX_OBSERVATION_TEXT_LENGTH)
        )

    def analyze(
        self,
        actor: ActorContext,
        analysis_id: str,
        handoff_id: str,
        transfer_id: str,
        image_bytes: bytes,
        *,
        mime_type: str,
    ) -> tuple[dict, ...]:
        record = self._service._record_for_runtime(actor, analysis_id)
        if record is None:
            return (
                self._error("invalid-request", "analysis_not_found", analysis_id),
            )
        request_id = record.request_id
        if (
            record.handoff_id != handoff_id
            or record.transfer_id != transfer_id
        ):
            return (self._error(request_id, "transfer_mismatch", analysis_id),)
        started = self._service.begin_analysis(actor, analysis_id)
        if started.code is not VisionOutcomeCode.ANALYZING:
            return (self._error(request_id, "analysis_failed", analysis_id),)
        update = self._message(
            {
                "type": "vision_analysis_update",
                "request_id": request_id,
                "analysis_id": analysis_id,
                "state": "analyzing",
            }
        )
        try:
            if record.mode is VisionProcessingMode.CLOUD:
                if self._cloud_vision_analyzer is None:
                    raise LookupError
                observations = tuple(
                    self._cloud_vision_analyzer(
                        image_bytes,
                        mime_type=mime_type,
                        capability=record.capability,
                    )
                )
            elif record.capability is VisionCapability.OCR:
                if self._ocr_adapter is None:
                    raise LookupError
                result = self._ocr_adapter.analyze(image_bytes, mime_type=mime_type)
                if result.status is not OcrStatus.SUCCESS:
                    raise RuntimeError
                observations = self._ocr_observations(result.text)
            else:
                if self._description_analyzer is None:
                    self._service.fail(actor, analysis_id)
                    self._service.discard(actor, analysis_id)
                    return (
                        update,
                        self._error(
                            request_id, "capability_unavailable", analysis_id
                        ),
                    )
                observations = tuple(
                    self._description_analyzer(image_bytes, mime_type=mime_type)
                )
        except LookupError:
            self._service.fail(actor, analysis_id)
            self._service.discard(actor, analysis_id)
            return (
                update,
                self._error(request_id, "capability_unavailable", analysis_id),
            )
        except Exception:
            self._service.fail(actor, analysis_id)
            self._service.discard(actor, analysis_id)
            return (
                update,
                self._error(request_id, "analysis_failed", analysis_id),
            )
        if not observations:
            self._service.fail(actor, analysis_id)
            self._service.discard(actor, analysis_id)
            return (
                update,
                self._error(request_id, "analysis_failed", analysis_id),
            )
        completed = self._service.complete(actor, analysis_id, observations)
        if completed.code is not VisionOutcomeCode.COMPLETED:
            self._service.fail(actor, analysis_id)
            self._service.discard(actor, analysis_id)
            return (
                update,
                self._error(request_id, "analysis_failed", analysis_id),
            )
        payload = []
        for observation in observations:
            item = {
                "kind": observation.kind.value,
                "text": observation.text,
            }
            if observation.confidence_milli is not None:
                item["confidence_milli"] = observation.confidence_milli
            payload.append(item)
        terminal = self._message(
            {
                "type": "vision_observation",
                "request_id": request_id,
                "analysis_id": analysis_id,
                "observations": payload,
            }
        )
        self._service.discard(actor, analysis_id)
        return (update, terminal)

    def cancel(
        self, actor: ActorContext, request_id: str, analysis_id: str
    ) -> dict:
        record = self._service._record_for_runtime(actor, analysis_id)
        try:
            outcome = self._service.cancel(actor, analysis_id)
        except Exception:
            return self._error(request_id, "unavailable", analysis_id)
        if outcome.code is VisionOutcomeCode.ANALYSIS_NOT_FOUND:
            return self._error(request_id, "analysis_not_found", analysis_id)
        if outcome.code is not VisionOutcomeCode.CANCELLED:
            return self._error(request_id, "analysis_failed", analysis_id)
        if record is not None and record.state is VisionAnalysisState.ANALYZING:
            if record.mode is VisionProcessingMode.CLOUD:
                adapter = self._cloud_vision_analyzer
            else:
                adapter = (
                    self._ocr_adapter
                    if record.capability is VisionCapability.OCR
                    else self._description_analyzer
                )
            adapter_cancel = getattr(adapter, "cancel", None)
            if callable(adapter_cancel):
                try:
                    adapter_cancel()
                except Exception:
                    pass
        message = self._message(
            {
                "type": "vision_analysis_update",
                "request_id": request_id,
                "analysis_id": analysis_id,
                "state": "cancelled",
            }
        )
        self._service.discard(actor, analysis_id)
        return message

    def status(
        self, actor: ActorContext, request_id: str, analysis_id: str
    ) -> dict:
        record = self._service._record_for_runtime(actor, analysis_id)
        if record is None:
            return self._error(request_id, "analysis_not_found", analysis_id)
        if record.state is VisionAnalysisState.EXPIRED:
            self._service.discard(actor, analysis_id)
            return self._message(
                {
                    "type": "vision_analysis_update",
                    "request_id": request_id,
                    "analysis_id": analysis_id,
                    "state": "expired",
                }
            )
        if record.state is VisionAnalysisState.ANALYZING:
            state = "analyzing"
        else:
            state = "awaiting_image"
        return self._message(
            {
                "type": "vision_analysis_update",
                "request_id": request_id,
                "analysis_id": analysis_id,
                "state": state,
            }
        )

    def cancel_bound(self, actor: ActorContext, analysis_id: str) -> dict:
        record = self._service._record_for_runtime(actor, analysis_id)
        if record is None:
            return self._error("invalid-request", "analysis_not_found", analysis_id)
        return self.cancel(actor, record.request_id, analysis_id)

    def clear_session(self, actor: ActorContext) -> int:
        try:
            return self._service.clear_session(actor)
        except Exception:
            return 0

    def expire_due(self) -> int:
        try:
            return self._service.expire_due()
        except Exception:
            return 0


__all__ = ("CloudVisionAnalyzer", "DescriptionAnalyzer", "VisionRuntime")
