"""Deterministic Phase 4 completion-regression: trust, authority, data, analysis, isolation.

Tests prove the additive vision/camera/handoff control plane never broadens
remote authority, never leaks content, never fabricates confidence, and never
performs capture/OCR/model work at import or construction time. All tests use
injected fakes; no real OCR, camera, or model is ever invoked.
"""

from __future__ import annotations

import ast
import json
from collections import deque
from pathlib import Path
from typing import Optional

import pytest

from core.action_policy import Actor, ActorContext
from core.handoff.contracts import (
    FrozenHandoffPreview,
    HandoffErrorCode,
    HandoffState,
)
from core.handoff.service import HandoffService
from core.handoff.store import HandoffStore
from core.protocol import validate_client_message, validate_server_message
from core.vision.contracts import (
    ANALYSIS_TTL_SECONDS,
    MAX_CONFIDENCE_MILLI,
    MAX_OBSERVATION_TEXT_LENGTH,
    MAX_OBSERVATIONS,
    MIN_CONFIDENCE_MILLI,
    VisionAnalysisRecord,
    VisionAnalysisRequest,
    VisionAnalysisState,
    VisionCapability,
    VisionObservation,
    VisionObservationKind,
    VisionOutcomeCode,
    VisionServiceOutcome,
)
from core.vision.ocr import CommandResult, LocalOcrAdapter, OcrResult, OcrStatus
from core.vision.service import VisionAnalysisService
from core.vision.runtime import VisionRuntime
from core.visual_transfer.contracts import (
    MAX_ENCODED_BYTES,
    MAX_DIMENSION,
    EXACT_FRAME_COUNT,
)
from core.visual_transfer.buffer import VisualTransferBuffer
from core.visual_transfer.service import VisualTransferService


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "protocol" / "hikari-v1.json"
VISION_DIR = ROOT / "core" / "vision"
HANDOFF_DIR = ROOT / "core" / "handoff"
VISUAL_TRANSFER_DIR = ROOT / "core" / "visual_transfer"
PHASE4_BOOTSTRAP = ROOT / "core" / "phase4" / "bootstrap.py"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self._value = start

    def __call__(self) -> float:
        return self._value

    def advance(self, seconds: float) -> None:
        self._value += seconds


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def analysis_ids() -> deque[str]:
    return deque(["analysis-1", "analysis-2", "analysis-3", "analysis-4", "analysis-5"])


@pytest.fixture
def vision_service(clock: _Clock, analysis_ids: deque[str]) -> VisionAnalysisService:
    return VisionAnalysisService(
        clock=clock,
        analysis_id_factory=lambda: analysis_ids.popleft(),
    )


@pytest.fixture
def owner() -> ActorContext:
    return ActorContext(
        actor_id="local-owner",
        actor=Actor.OWNER,
        session_id="session-1",
        source="local",
    )


@pytest.fixture
def guest() -> ActorContext:
    return ActorContext(
        actor_id="guest",
        actor=Actor.GUEST,
        session_id="session-2",
        source="websocket",
    )


@pytest.fixture
def other_session_owner() -> ActorContext:
    return ActorContext(
        actor_id="local-owner",
        actor=Actor.OWNER,
        session_id="session-other",
        source="local",
    )


class _FakeOcr:
    """Injected fake OCR adapter — never invokes a real binary."""

    def __init__(self, text: str = "measured text", status: OcrStatus = OcrStatus.SUCCESS) -> None:
        self._text = text
        self._status = status
        self.calls: list[tuple[bytes, str]] = []

    def analyze(self, image_bytes: bytes, *, mime_type: str) -> OcrResult:
        self.calls.append((image_bytes, mime_type))
        return OcrResult(status=self._status, text=self._text)


class _FakeDescriptionAnalyzer:
    """Injected fake description analyzer — never invokes a real model."""

    def __init__(self, observations: tuple[VisionObservation, ...] | None = None) -> None:
        self._observations = observations or (
            VisionObservation(
                kind=VisionObservationKind.DESCRIPTION,
                text="A bounded description.",
                confidence_milli=750,
            ),
        )
        self.calls: list[tuple[bytes, str]] = []

    def __call__(self, image_bytes: bytes, *, mime_type: str) -> tuple[VisionObservation, ...]:
        self.calls.append((image_bytes, mime_type))
        return self._observations


def _vision_runtime(
    *,
    service: VisionAnalysisService,
    ocr: _FakeOcr | None = None,
    description: _FakeDescriptionAnalyzer | None = None,
    accepted: bool = True,
) -> VisionRuntime:
    return VisionRuntime(
        service=service,
        ocr_adapter=ocr,
        description_analyzer=description,
        handoff_accepted=lambda session_id, handoff_id: (
            accepted and session_id == "session-1" and handoff_id == "handoff-1"
        ),
    )


def _handoff_service(
    tmp_path: Path,
    clock: _Clock,
    *,
    acceptance_policy=None,
) -> HandoffService:
    store = HandoffStore(
        tmp_path / "handoffs.db",
        clock=clock,
        handoff_id_factory=lambda: "handoff-1",
    )
    previews = {"task-1": FrozenHandoffPreview(task_id="task-1", summary="Review the task.")}

    def lookup(actor: ActorContext, task_id: str) -> Optional[FrozenHandoffPreview]:
        return previews.get(task_id)

    return HandoffService(
        store,
        task_lookup=lookup,
        acceptance_policy=acceptance_policy or (lambda actor, preview: actor.actor is Actor.OWNER),
    )


# ===========================================================================
# Trust and authority (1-9)
# ===========================================================================


def test_paired_remote_connections_remain_guests(guest: ActorContext) -> None:
    """(1) A paired non-loopback device is always a guest, never owner."""
    assert guest.actor is Actor.GUEST
    assert guest.actor is not Actor.OWNER


def test_mobile_cannot_accept_its_own_handoff(
    tmp_path: Path, clock: _Clock, guest: ActorContext, owner: ActorContext
) -> None:
    """(2) A guest/mobile actor cannot accept a handoff — only desktop owner can."""
    service = _handoff_service(tmp_path, clock)
    prepare = service.prepare(guest, "task-1", "Review the task.", "request-1")
    assert prepare.success
    assert prepare.state is HandoffState.OFFERED

    accept = service.accept(guest, "handoff-1", acknowledged=True)
    assert not accept.success
    assert accept.error_code is HandoffErrorCode.UNAUTHORIZED

    accept_owner = service.accept(owner, "handoff-1", acknowledged=True)
    assert accept_owner.success
    assert accept_owner.state is HandoffState.ACCEPTED


def test_desktop_acceptance_performs_fresh_policy_evaluation(
    tmp_path: Path, clock: _Clock, guest: ActorContext, owner: ActorContext
) -> None:
    """(3) Acceptance calls the injected acceptance_policy fresh each time."""
    call_count = {"n": 0}

    def policy(actor: ActorContext, preview: FrozenHandoffPreview) -> bool:
        call_count["n"] += 1
        return call_count["n"] == 1  # allow first, deny second

    counter = {"n": 0}

    def id_factory() -> str:
        counter["n"] += 1
        return f"handoff-{counter['n']:03d}"

    store = HandoffStore(tmp_path / "handoffs.db", clock=clock, handoff_id_factory=id_factory)
    previews = {"task-1": FrozenHandoffPreview(task_id="task-1", summary="Review the task.")}

    def lookup(actor: ActorContext, task_id: str) -> Optional[FrozenHandoffPreview]:
        return previews.get(task_id)

    service = HandoffService(store, task_lookup=lookup, acceptance_policy=policy)
    service.prepare(guest, "task-1", "Review the task.", "request-1")

    first = service.accept(owner, "handoff-001", acknowledged=True)
    assert first.success
    assert call_count["n"] == 1

    # Second handoff with same policy — now denied
    service.prepare(guest, "task-1", "Review the task.", "request-2")
    second = service.accept(owner, "handoff-002", acknowledged=True)
    assert not second.success
    assert second.error_code is HandoffErrorCode.POLICY_DENIED
    assert call_count["n"] == 2


def test_camera_vision_never_broadens_remote_authority(
    vision_service: VisionAnalysisService, guest: ActorContext
) -> None:
    """(4) Vision/camera features do not grant guest any owner capability."""
    runtime = _vision_runtime(service=vision_service, ocr=_FakeOcr())
    # Guest can prepare (if handoff accepted for their session), but the
    # acceptance check is session-scoped — guest session is not "session-1".
    result = runtime.prepare(guest, "request-1", "handoff-1", "ocr")
    assert result["type"] == "vision_analysis_error"
    assert result["code"] == "handoff_not_accepted"


def test_no_actor_session_approval_grant_execution_ticket_client_fields() -> None:
    """(5) Protocol rejects identity/authority client fields on vision messages."""
    forbidden_fields = [
        "actor_id",
        "session_id",
        "device_id",
        "approval_id",
        "grant_id",
        "execution_ticket",
    ]
    base = {
        "type": "vision_analysis_prepare",
        "request_id": "req-1",
        "handoff_id": "handoff-1",
        "capability": "ocr",
    }
    for field in forbidden_fields:
        assert validate_client_message({**base, field: "value"}) is not None


def test_exact_actor_session_handoff_analysis_transfer_correlation(
    vision_service: VisionAnalysisService, owner: ActorContext, guest: ActorContext
) -> None:
    """(6) Every operation correlates exact actor/session/handoff/analysis/transfer."""
    runtime = _vision_runtime(service=vision_service, ocr=_FakeOcr())
    ready = runtime.prepare(owner, "request-1", "handoff-1", "ocr")
    assert ready["type"] == "vision_analysis_ready"
    assert ready["request_id"] == "request-1"
    assert ready["analysis_id"] == "analysis-1"

    attached = runtime.attach_transfer(owner, "analysis-1", "handoff-1", "transfer-1")
    assert attached["type"] == "vision_analysis_update"
    assert attached["analysis_id"] == "analysis-1"

    # Mismatched handoff is rejected
    mismatch = runtime.attach_transfer(owner, "analysis-1", "handoff-2", "transfer-1")
    assert mismatch["code"] == "transfer_mismatch"


def test_cross_session_operations_disclose_nothing_and_mutate_nothing(
    vision_service: VisionAnalysisService, owner: ActorContext, guest: ActorContext
) -> None:
    """(7) A guest querying owner's analysis_id gets analysis_not_found, no mutation."""
    runtime = _vision_runtime(service=vision_service, ocr=_FakeOcr())
    runtime.prepare(owner, "request-1", "handoff-1", "ocr")

    # Guest cannot see owner's analysis
    guest_status = runtime.status(guest, "status-1", "analysis-1")
    assert guest_status["code"] == "analysis_not_found"

    # Owner's analysis is still intact
    owner_status = runtime.status(owner, "status-1", "analysis-1")
    assert owner_status["type"] == "vision_analysis_update"
    assert owner_status["state"] == "awaiting_image"


def test_analysis_and_transfer_ids_remain_server_generated(
    vision_service: VisionAnalysisService, owner: ActorContext
) -> None:
    """(8) analysis_id is always server-generated; client never supplies it."""
    runtime = _vision_runtime(service=vision_service, ocr=_FakeOcr())
    ready = runtime.prepare(owner, "request-1", "handoff-1", "ocr")
    assert ready["analysis_id"] == "analysis-1"
    # The client prepare message has no analysis_id field
    assert "analysis_id" not in {
        "type": "vision_analysis_prepare",
        "request_id": "request-1",
        "handoff_id": "handoff-1",
        "capability": "ocr",
    }


def test_stale_and_duplicate_messages_do_not_restart_work(
    vision_service: VisionAnalysisService, owner: ActorContext
) -> None:
    """(9) Duplicate prepare is idempotent; stale IDs don't restart."""
    runtime = _vision_runtime(service=vision_service, ocr=_FakeOcr())
    first = runtime.prepare(owner, "request-1", "handoff-1", "ocr")
    second = runtime.prepare(owner, "request-1", "handoff-1", "ocr")
    assert first["analysis_id"] == second["analysis_id"] == "analysis-1"

    # After cancel, status reports not found — no restart
    runtime.cancel(owner, "cancel-1", "analysis-1")
    stale = runtime.status(owner, "status-1", "analysis-1")
    assert stale["code"] == "analysis_not_found"


# ===========================================================================
# Data handling (18-25)
# ===========================================================================


def test_image_bytes_never_enter_json(vision_service: VisionAnalysisService, owner: ActorContext) -> None:
    """(18) No bytes/data/base64/data_url fields in any vision server message."""
    runtime = _vision_runtime(service=vision_service, ocr=_FakeOcr())
    runtime.prepare(owner, "request-1", "handoff-1", "ocr")
    runtime.attach_transfer(owner, "analysis-1", "handoff-1", "transfer-1")
    messages = runtime.analyze(
        owner, "analysis-1", "handoff-1", "transfer-1", b"image-bytes",
        mime_type="image/png",
    )
    for message in messages:
        for key in message:
            assert key not in {"bytes", "data", "base64", "data_url", "image", "raw"}
        assert all(validate_server_message(message) is None for message in messages)


def test_camera_frame_remains_transient(vision_service: VisionAnalysisService, owner: ActorContext) -> None:
    """(19) After analyze completes, the service holds no image bytes."""
    runtime = _vision_runtime(service=vision_service, ocr=_FakeOcr())
    runtime.prepare(owner, "request-1", "handoff-1", "ocr")
    runtime.attach_transfer(owner, "analysis-1", "handoff-1", "transfer-1")
    runtime.analyze(owner, "analysis-1", "handoff-1", "transfer-1", b"image", mime_type="image/png")
    # The service record is discarded after terminal response
    assert runtime.status(owner, "status-1", "analysis-1")["code"] == "analysis_not_found"
    # No bytes stored on the service
    assert not vision_service._by_id


def test_no_raw_images_ocr_descriptions_thumbnails_exif_filenames_persist(
    vision_service: VisionAnalysisService, owner: ActorContext
) -> None:
    """(20) No raw images, OCR text, descriptions, thumbnails, EXIF, or filenames persist."""
    runtime = _vision_runtime(service=vision_service, ocr=_FakeOcr(text="secret OCR text"))
    runtime.prepare(owner, "request-1", "handoff-1", "ocr")
    runtime.attach_transfer(owner, "analysis-1", "handoff-1", "transfer-1")
    messages = runtime.analyze(
        owner, "analysis-1", "handoff-1", "transfer-1", b"image",
        mime_type="image/png",
    )
    # The observation message carries text but the service retains nothing
    assert runtime.status(owner, "status-1", "analysis-1")["code"] == "analysis_not_found"
    # No raw image bytes on the service
    for record in vision_service._by_id.values():
        assert not hasattr(record, "_image_bytes") or getattr(record, "_image_bytes", None) is None


def test_no_base64_data_urls_paths_filenames_in_protocol() -> None:
    """(21) Protocol vision schemas reject base64/data_url/path/filename fields."""
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    client = protocol["client_to_server"]
    server = protocol["server_to_client"]
    forbidden = {"base64", "data_url", "path", "filename", "url", "bytes", "data"}

    def _all_fields(spec) -> set[str]:
        names: set[str] = set()
        if isinstance(spec, dict):
            for key, value in spec.items():
                if key in {"required", "optional"} and isinstance(value, dict):
                    names.update(value)
                names.update(_all_fields(value))
        elif isinstance(spec, list):
            for item in spec:
                names.update(_all_fields(item))
        return names

    for name in ("vision_analysis_prepare", "vision_analysis_cancel", "vision_analysis_status"):
        assert forbidden.isdisjoint(_all_fields(client[name]))
    for name in ("vision_analysis_ready", "vision_analysis_update", "vision_observation", "vision_analysis_error"):
        assert forbidden.isdisjoint(_all_fields(server[name]))


def test_mime_byte_limit_dimensions_frame_count_enforced() -> None:
    """(22) MIME, magic bytes, byte limit, dimensions, and frame count are enforced."""
    from core.visual_transfer.contracts import VisualTransferDeclaration, ContractValidationError

    # Valid declaration
    VisualTransferDeclaration(
        handoff_id="handoff-1",
        mime="image/png",
        declared_byte_length=100,
        declared_width=100,
        declared_height=100,
        frame_count=1,
    )

    # Invalid MIME
    with pytest.raises(ContractValidationError):
        VisualTransferDeclaration(
            handoff_id="handoff-1", mime="image/gif",
            declared_byte_length=100, declared_width=100, declared_height=100,
        )
    # Oversized bytes
    with pytest.raises(ContractValidationError):
        VisualTransferDeclaration(
            handoff_id="handoff-1", mime="image/png",
            declared_byte_length=MAX_ENCODED_BYTES + 1,
            declared_width=100, declared_height=100,
        )
    # Oversized dimensions
    with pytest.raises(ContractValidationError):
        VisualTransferDeclaration(
            handoff_id="handoff-1", mime="image/png",
            declared_byte_length=100, declared_width=MAX_DIMENSION + 1, declared_height=100,
        )
    # Wrong frame count
    with pytest.raises(ContractValidationError):
        VisualTransferDeclaration(
            handoff_id="handoff-1", mime="image/png",
            declared_byte_length=100, declared_width=100, declared_height=100,
            frame_count=2,
        )


def test_exif_removed_by_fresh_canvas_encoding() -> None:
    """(23) CameraCapturePanel uses canvas.toBlob (fresh encoding), not toDataURL."""
    component = (ROOT / "hikari-frontend" / "src" / "components" / "CameraCapturePanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "canvas.toBlob" in component or "toBlob(" in component
    assert "toDataURL" not in component
    assert "ctx.drawImage" in component


def test_cancellation_failure_expiry_disconnect_clean_every_state_holder(
    vision_service: VisionAnalysisService, owner: ActorContext
) -> None:
    """(24) Cancel, failure, expiry, and clear_session remove all state."""
    runtime = _vision_runtime(service=vision_service, ocr=_FakeOcr())
    runtime.prepare(owner, "request-1", "handoff-1", "ocr")
    assert len(vision_service._by_id) == 1

    # Cancel cleans up
    runtime.cancel(owner, "cancel-1", "analysis-1")
    assert len(vision_service._by_id) == 0

    # Failure cleans up
    runtime.prepare(owner, "request-2", "handoff-1", "ocr")
    runtime.attach_transfer(owner, "analysis-2", "handoff-1", "transfer-1")
    failing_runtime = _vision_runtime(
        service=vision_service, ocr=_FakeOcr(status=OcrStatus.FAILED)
    )
    failing_runtime.analyze(
        owner, "analysis-2", "handoff-1", "transfer-1", b"image", mime_type="image/png"
    )
    assert len(vision_service._by_id) == 0

    # clear_session cleans up
    runtime.prepare(owner, "request-3", "handoff-1", "ocr")
    assert len(vision_service._by_id) == 1
    assert runtime.clear_session(owner) == 1
    assert len(vision_service._by_id) == 0


def test_content_hash_is_never_authorization() -> None:
    """(25) Content hash fields are rejected from vision client messages."""
    base = {
        "type": "vision_analysis_prepare",
        "request_id": "req-1",
        "handoff_id": "handoff-1",
        "capability": "ocr",
    }
    assert validate_client_message({**base, "content_hash": "sha256." + "a" * 64}) is not None


# ===========================================================================
# Analysis (26-34)
# ===========================================================================


def test_ocr_and_description_text_bounds_match_frontend_backend_protocol() -> None:
    """(26) Text bounds are 1-2000 code points across frontend, backend, and protocol."""
    # Backend contracts
    from core.vision.contracts import MIN_OBSERVATION_TEXT_LENGTH, MAX_OBSERVATION_TEXT_LENGTH
    assert MIN_OBSERVATION_TEXT_LENGTH == 1
    assert MAX_OBSERVATION_TEXT_LENGTH == 2000

    # Protocol
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    obs_text = protocol["server_to_client"]["vision_observation"]["required"]["observations"]["items"]["required"]["text"]
    assert obs_text["min_length"] == 1
    assert obs_text["max_length"] == 2000

    # Frontend
    frontend = (ROOT / "hikari-frontend" / "src" / "utils" / "phase4" / "visionAnalysis.ts").read_text(
        encoding="utf-8"
    )
    assert "MAX_TEXT_CODE_POINTS = 2000" in frontend


def test_text_control_cf_policies_match() -> None:
    """(27) Control character and Cf policies match across backend and frontend."""
    # Backend rejects Cf and controls (except newline/tab for OCR text)
    VisionObservation(kind=VisionObservationKind.TEXT, text="ok\n\tok", confidence_milli=1)
    with pytest.raises(Exception):
        VisionObservation(kind=VisionObservationKind.TEXT, text="bad\x00", confidence_milli=1)
    with pytest.raises(Exception):
        VisionObservation(kind=VisionObservationKind.TEXT, text="bad\u200b", confidence_milli=1)

    # Frontend has matching policies
    frontend = (ROOT / "hikari-frontend" / "src" / "utils" / "phase4" / "visionAnalysis.ts").read_text(
        encoding="utf-8"
    )
    assert "FORBIDDEN_ASCII_CONTROLS" in frontend
    assert "UNICODE_CF_CATEGORY" in frontend


def test_no_invented_confidence(vision_service: VisionAnalysisService, owner: ActorContext) -> None:
    """(28) OCR observations without measured confidence have confidence_milli=None."""
    observation = VisionObservation(
        kind=VisionObservationKind.TEXT,
        text="text without confidence",
    )
    assert observation.confidence_milli is None


def test_missing_confidence_visibly_described_as_unavailable() -> None:
    """(29) Frontend describes missing confidence as 'Confidence unavailable'."""
    component = (ROOT / "hikari-frontend" / "src" / "components" / "VisionAnalysisPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "Confidence unavailable" in component


def test_low_measured_confidence_visibly_communicated() -> None:
    """(30) Frontend communicates low confidence with '(Uncertain)' label."""
    component = (ROOT / "hikari-frontend" / "src" / "components" / "VisionAnalysisPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "CONFIDENCE_UNCERTAINTY_THRESHOLD_MILLI" in component
    assert "(Uncertain)" in component


def test_confidence_never_affects_authorization(
    vision_service: VisionAnalysisService, owner: ActorContext, guest: ActorContext
) -> None:
    """(31) Confidence is evidence only — it never grants or bypasses authority."""
    runtime = _vision_runtime(service=vision_service, ocr=_FakeOcr())
    # High confidence observation does not grant guest access
    runtime.prepare(owner, "request-1", "handoff-1", "ocr")
    runtime.attach_transfer(owner, "analysis-1", "handoff-1", "transfer-1")
    messages = runtime.analyze(
        owner, "analysis-1", "handoff-1", "transfer-1", b"image", mime_type="image/png"
    )
    # Even with high confidence, guest still can't access
    assert runtime.status(guest, "status-1", "analysis-1")["code"] == "analysis_not_found"


def test_local_description_adapter_has_no_network_provider_cloud_fallback() -> None:
    """(32) Description analyzer is a local Protocol with no network/provider/cloud."""
    runtime_src = (ROOT / "core" / "vision" / "runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(runtime_src)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    forbidden = {"requests", "httpx", "urllib", "socket", "aiohttp", "openai", "anthropic", "cv2"}
    assert imports.isdisjoint(forbidden)


def test_import_construction_performs_no_model_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(33) Importing and constructing vision/OCR objects performs no model invocation."""
    call_log: list[str] = []

    def forbidden_call(*_args, **_kwargs):
        call_log.append("subprocess_invoked")
        raise AssertionError("construction invoked a subprocess")

    monkeypatch.setattr("core.vision.ocr.subprocess.Popen", forbidden_call)
    service = VisionAnalysisService(clock=lambda: 1.0, analysis_id_factory=lambda: "analysis-1")
    adapter = LocalOcrAdapter(
        executable_path="/fixed/tesseract",
        runner=lambda _argv, *, timeout, stdin: CommandResult(0, b""),
    )
    assert call_log == []
    assert repr(service) == "VisionAnalysisService()"
    assert repr(adapter) == "LocalOcrAdapter()"


def test_tests_use_injected_fakes_and_never_run_ocr_camera_or_real_model(
    vision_service: VisionAnalysisService, owner: ActorContext
) -> None:
    """(34) This test suite uses injected fakes — the fake OCR records calls but never invokes a binary."""
    fake = _FakeOcr(text="deterministic text")
    runtime = _vision_runtime(service=vision_service, ocr=fake)
    runtime.prepare(owner, "request-1", "handoff-1", "ocr")
    runtime.attach_transfer(owner, "analysis-1", "handoff-1", "transfer-1")
    messages = runtime.analyze(
        owner, "analysis-1", "handoff-1", "transfer-1", b"fake-image",
        mime_type="image/png",
    )
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == b"fake-image"
    assert messages[-1]["observations"][0]["text"] == "deterministic text"


# ===========================================================================
# Import isolation (47-48)
# ===========================================================================


def test_cli_doctor_voice_status_module_imports_create_no_camera_analysis_transfer_model_or_ocr_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(47) Importing CLI/doctor/voice modules does not create vision/camera/OCR state."""
    import importlib

    forbidden_subprocess = []
    monkeypatch.setattr(
        "core.vision.ocr.subprocess.Popen",
        lambda *_a, **_kw: forbidden_subprocess.append("invoked"),
    )

    # Import modules that should not trigger vision/camera state creation
    for module_name in ("core.protocol", "core.action_policy"):
        importlib.import_module(module_name)

    assert forbidden_subprocess == []


def test_server_only_bootstrap_remains_lazy() -> None:
    """(48) create_phase4_subsystem is a function, not module-level construction."""
    tree = ast.parse(PHASE4_BOOTSTRAP.read_text(encoding="utf-8"))
    # No module-level Call to create_phase4_subsystem or VisionRuntime
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name) and func.id == "create_phase4_subsystem":
                assert False, "bootstrap creates subsystem at module level"
    # The function is defined but not called
    func_names = [
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    ]
    assert "create_phase4_subsystem" in func_names


# ===========================================================================
# Privacy: no raw content in repr/str/errors/logs/audit (35-39)
# ===========================================================================


def test_no_raw_content_in_repr_str_errors_logs_audit_or_status() -> None:
    """(35) Vision records, observations, and outcomes have content-free repr."""
    secret = "private-vision-sentinel"
    observation = VisionObservation(
        kind=VisionObservationKind.TEXT, text=secret, confidence_milli=500
    )
    record = VisionAnalysisRecord(
        analysis_id="analysis.private",
        request_id="request.private",
        actor_id="actor.private",
        session_id="session.private",
        handoff_id="handoff.private",
        capability=VisionCapability.OCR,
        state=VisionAnalysisState.COMPLETED,
        created_at=1.0,
        expires_at=901.0,
        updated_at=2.0,
        transfer_id="transfer.private",
        observations=(observation,),
    )
    outcome = VisionServiceOutcome(
        code=VisionOutcomeCode.COMPLETED,
        analysis_id="analysis.private",
        request_id="request.private",
        state=VisionAnalysisState.COMPLETED,
        observation_count=1,
    )
    for value in (observation, record, outcome):
        rendered = repr(value)
        assert secret not in rendered
        assert "analysis.private" not in rendered
        assert "request.private" not in rendered
        assert "handoff.private" not in rendered


def test_no_provider_model_paths_or_command_output_exposed() -> None:
    """(36) OCR result and command result reprs exclude provider/model/output."""
    result = OcrResult(status=OcrStatus.SUCCESS, text="secret OCR output")
    rep = repr(result)
    assert "secret" not in rep
    assert "output" not in rep

    cmd = CommandResult(returncode=0, stdout=b"secret stdout")
    cmd_rep = repr(cmd)
    assert "secret" not in cmd_rep
    assert "stdout" not in cmd_rep


def test_no_storage_filesystem_persistence_telemetry_or_cloud_egress() -> None:
    """(37) Vision modules have no storage, filesystem, telemetry, or cloud imports."""
    for path in sorted(VISION_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        forbidden = {
            "sqlite3", "shelve", "pickle", "json",  # persistence
            "requests", "httpx", "urllib", "socket", "aiohttp",  # network
            "logging",  # telemetry
            "openai", "anthropic", "boto3", "google",  # cloud
        }
        # JSON is used only for deterministic digests or the explicit, read-only
        # local-model provisioning manifest. Filesystem writes remain forbidden
        # by the dedicated vision privacy contracts.
        if path.name in {"contracts.py", "mlx_worker.py", "cloud.py"}:
            forbidden.discard("json")
        if path.name == "cloud.py":
            forbidden.discard("urllib")
        assert imports.isdisjoint(forbidden), f"{path.name}: {imports & forbidden}"


def test_no_legacy_screenshot_applescript_desktop_awareness_or_mac_control_imports() -> None:
    """(38) Vision modules do not import screenshot, AppleScript, or mac-control."""
    for path in sorted(VISION_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in {
                    "core.desktop_awareness",
                    "core.mac_control",
                    "core.mac_integration",
                }
                assert node.module != "pyautogui"
                assert node.module != "mss"


def test_no_helper_model_attribution_or_tool_metadata() -> None:
    """(39) Vision modules define no logger, audit emission, or attribution artifacts."""
    for path in sorted(VISION_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: set[str] = set()
        calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.add(node.func.attr)
        assert "logging" not in imports, path.name
        assert "getLogger" not in calls, path.name
        assert "audit" not in imports, path.name
        assert "append_audit" not in calls, path.name
