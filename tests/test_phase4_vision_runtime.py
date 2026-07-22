"""Deterministic integration tests for the bounded vision runtime."""

from __future__ import annotations

from core.action_policy import Actor, ActorContext
from core.protocol import validate_server_message
from core.vision import VisionAnalysisService, VisionRuntime
from core.vision.contracts import VisionObservation, VisionObservationKind
from core.vision.ocr import OcrResult, OcrStatus


class _Ocr:
    def analyze(self, image_bytes: bytes, *, mime_type: str) -> OcrResult:
        assert image_bytes == b"image"
        assert mime_type == "image/png"
        return OcrResult(status=OcrStatus.SUCCESS, text="measured text")


class _Description:
    def __init__(self) -> None:
        self.cancel_count = 0

    def __call__(self, image_bytes: bytes, *, mime_type: str):
        return (
            VisionObservation(
                kind=VisionObservationKind.DESCRIPTION,
                text="bounded description",
            ),
        )

    def cancel(self) -> None:
        self.cancel_count += 1


def _actor(session_id: str = "session-1") -> ActorContext:
    return ActorContext(
        actor_id="guest",
        actor=Actor.GUEST,
        session_id=session_id,
        source="websocket",
    )


def _runtime(*, accepted=True, description_analyzer=None) -> VisionRuntime:
    service = VisionAnalysisService(
        clock=lambda: 1000.0,
        analysis_id_factory=lambda: "analysis-1",
    )
    return VisionRuntime(
        service=service,
        ocr_adapter=_Ocr(),
        description_analyzer=description_analyzer,
        handoff_accepted=lambda session_id, handoff_id: (
            accepted and session_id == "session-1" and handoff_id == "handoff-1"
        ),
    )


def test_runtime_binds_handoff_transfer_session_and_emits_valid_observation() -> None:
    runtime = _runtime()
    actor = _actor()
    ready = runtime.prepare(actor, "request-1", "handoff-1", "ocr")
    assert ready["type"] == "vision_analysis_ready"
    assert ready["analysis_id"] == "analysis-1"
    attached = runtime.attach_transfer(
        actor, "analysis-1", "handoff-1", "transfer-1"
    )
    assert attached["state"] == "awaiting_image"
    messages = runtime.analyze(
        actor,
        "analysis-1",
        "handoff-1",
        "transfer-1",
        b"image",
        mime_type="image/png",
    )
    assert [message["type"] for message in messages] == [
        "vision_analysis_update",
        "vision_observation",
    ]
    assert messages[-1]["observations"] == [
        {"kind": "text", "text": "measured text"}
    ]
    assert all(validate_server_message(message) is None for message in messages)
    assert runtime.status(actor, "status-1", "analysis-1")["code"] == (
        "analysis_not_found"
    )


def test_runtime_rejects_unaccepted_mismatched_and_cross_session_requests() -> None:
    denied = _runtime(accepted=False)
    assert denied.prepare(_actor(), "request-1", "handoff-1", "ocr")["code"] == (
        "handoff_not_accepted"
    )

    runtime = _runtime()
    actor = _actor()
    runtime.prepare(actor, "request-1", "handoff-1", "ocr")
    assert runtime.attach_transfer(
        actor, "analysis-1", "handoff-2", "transfer-1"
    )["code"] == "transfer_mismatch"
    assert runtime.status(_actor("session-2"), "status-1", "analysis-1")["code"] == (
        "analysis_not_found"
    )


def test_unprovisioned_description_fails_before_analysis_state_is_created() -> None:
    runtime = _runtime()
    result = runtime.prepare(_actor(), "request-1", "handoff-1", "describe")

    assert result == {
        "type": "vision_analysis_error",
        "request_id": "request-1",
        "code": "capability_unavailable",
    }
    assert runtime.status(_actor(), "status-1", "analysis-1")["code"] == (
        "analysis_not_found"
    )


def test_runtime_cancel_and_session_cleanup_discard_analysis_state() -> None:
    runtime = _runtime()
    actor = _actor()
    runtime.prepare(actor, "request-1", "handoff-1", "ocr")
    cancelled = runtime.cancel(actor, "cancel-1", "analysis-1")
    assert cancelled["state"] == "cancelled"
    assert runtime.status(actor, "status-1", "analysis-1")["code"] == (
        "analysis_not_found"
    )

    other = _runtime()
    other.prepare(actor, "request-1", "handoff-1", "ocr")
    assert other.clear_session(actor) == 1
    assert other.status(actor, "status-1", "analysis-1")["code"] == (
        "analysis_not_found"
    )


def test_description_cancel_is_scoped_to_the_exact_owner_analysis() -> None:
    analyzer = _Description()
    runtime = _runtime(description_analyzer=analyzer)
    actor = _actor()
    runtime.prepare(actor, "request-1", "handoff-1", "describe")

    cross_session = runtime.cancel(
        _actor("session-2"), "cancel-1", "analysis-1"
    )
    assert cross_session["code"] == "analysis_not_found"
    assert analyzer.cancel_count == 0

    cancelled = runtime.cancel(actor, "cancel-2", "analysis-1")
    assert cancelled["state"] == "cancelled"
    assert analyzer.cancel_count == 1


def test_ocr_cancel_does_not_interrupt_description_runner() -> None:
    analyzer = _Description()
    runtime = _runtime(description_analyzer=analyzer)
    actor = _actor()
    runtime.prepare(actor, "request-1", "handoff-1", "ocr")

    assert runtime.cancel(actor, "cancel-1", "analysis-1")["state"] == (
        "cancelled"
    )
    assert analyzer.cancel_count == 0
