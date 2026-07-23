"""Privacy and side-effect contracts for the bounded Phase 4 vision slice."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from core.action_policy import Actor, ActorContext
from core.vision.contracts import (
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


ROOT = Path(__file__).resolve().parents[1]
VISION_ROOT = ROOT / "core" / "vision"
PROTOCOL_PATH = ROOT / "protocol" / "hikari-v1.json"
VISION_MODULES = tuple(sorted(VISION_ROOT.glob("*.py")))

FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "aiohttp",
        "anthropic",
        "boto3",
        "cv2",
        "httpx",
        "openai",
        "pyautogui",
        "requests",
        "socket",
        "urllib",
        "webbrowser",
        "websockets",
    }
)
FORBIDDEN_PERSISTENCE_CALLS = frozenset(
    {
        "NamedTemporaryFile",
        "TemporaryDirectory",
        "mkdtemp",
        "mkstemp",
        "open",
        "write_bytes",
        "write_text",
    }
)
FORBIDDEN_EFFECT_CALLS = frozenset(
    {
        "VideoCapture",
        "getUserMedia",
        "post",
        "print",
        "put",
        "screenshot",
        "send",
        "urlopen",
        "upload",
    }
)
FORBIDDEN_WIRE_FIELDS = frozenset(
    {
        "actor_id",
        "session_id",
        "device_id",
        "approval_id",
        "grant_id",
        "execution_ticket",
        "provider",
        "model",
        "destination",
        "bytes",
        "data",
        "base64",
        "data_url",
        "filename",
        "path",
        "url",
        "task_content",
        "task_payload",
        "message",
        "detail",
        "stack",
    }
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _import_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _called_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _schema_field_names(schema: object) -> set[str]:
    names: set[str] = set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in {"required", "optional"} and isinstance(value, dict):
                names.update(value)
            names.update(_schema_field_names(value))
    elif isinstance(schema, list):
        for item in schema:
            names.update(_schema_field_names(item))
    return names


def test_vision_modules_have_no_capture_network_provider_or_persistence_boundary() -> None:
    assert VISION_MODULES
    for path in VISION_MODULES:
        tree = _tree(path)
        imports = _import_roots(tree)
        calls = _called_names(tree)
        if path.name != "cloud.py":
            assert imports.isdisjoint(FORBIDDEN_IMPORT_ROOTS), path.name
        assert "tempfile" not in imports, path.name
        allowed_effects = {"send"} if path.name == "mlx_worker.py" else set()
        allowed_reads = (
            {"open", "read_text"} if path.name == "mlx_worker.py" else set()
        )
        assert calls.isdisjoint(FORBIDDEN_EFFECT_CALLS - allowed_effects), path.name
        assert calls.isdisjoint(
            FORBIDDEN_PERSISTENCE_CALLS - allowed_reads
        ), path.name
        if path.name == "mlx_worker.py":
            assert calls.isdisjoint(
                {"write_bytes", "write_text", "NamedTemporaryFile", "mkstemp"}
            )
        if path.name == "cloud.py":
            assert "http" in imports
            assert calls.isdisjoint(FORBIDDEN_PERSISTENCE_CALLS)
            assert calls.isdisjoint({"print", "upload", "urlopen"})

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in {
                    "core.desktop_awareness",
                    "core.mac_control",
                    "core.mac_integration",
                }


def test_vision_records_errors_and_command_results_have_content_free_repr() -> None:
    secret = "private-vision-sentinel"
    request = VisionAnalysisRequest(
        request_id="request.private",
        handoff_id="handoff.private",
        capability=VisionCapability.OCR,
    )
    observation = VisionObservation(
        kind=VisionObservationKind.TEXT,
        text=secret,
        confidence_milli=500,
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
    values = (
        request,
        observation,
        record,
        outcome,
        OcrResult(OcrStatus.SUCCESS, secret),
        CommandResult(0, secret.encode()),
    )
    forbidden_values = {
        secret,
        "request.private",
        "handoff.private",
        "analysis.private",
        "actor.private",
        "session.private",
        "transfer.private",
    }
    for value in values:
        rendered = repr(value)
        assert all(item not in rendered for item in forbidden_values)

    with pytest.raises(ValueError) as exc_info:
        VisionObservation(
            kind=VisionObservationKind.TEXT,
            text=f"{secret}\u200b",
            confidence_milli=500,
        )
    assert secret not in str(exc_info.value)


def test_vision_protocol_excludes_identity_authority_image_and_raw_error_fields() -> None:
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    client = protocol["client_to_server"]
    server = protocol["server_to_client"]
    client_names = {
        "vision_analysis_prepare",
        "vision_analysis_cancel",
        "vision_analysis_status",
    }
    server_names = {
        "vision_analysis_ready",
        "vision_analysis_update",
        "vision_observation",
        "vision_analysis_error",
    }
    assert client_names <= client.keys()
    assert server_names <= server.keys()

    for name in client_names:
        fields = _schema_field_names(client[name])
        assert fields.isdisjoint(FORBIDDEN_WIRE_FIELDS), name
        assert "content_hash" not in fields
    for name in server_names:
        fields = _schema_field_names(server[name])
        assert fields.isdisjoint(FORBIDDEN_WIRE_FIELDS), name

    visual_begin_fields = _schema_field_names(client["visual_transfer_begin"])
    assert "content_hash" not in visual_begin_fields
    assert {"bytes", "data", "base64", "data_url", "path", "filename"}.isdisjoint(
        visual_begin_fields
    )


def test_import_and_construction_do_not_run_ocr_or_create_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_process(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("construction invoked a process")

    monkeypatch.setattr("core.vision.ocr.subprocess.Popen", forbidden_process)
    service = VisionAnalysisService(clock=lambda: 1.0, analysis_id_factory=lambda: "analysis-1")
    adapter = LocalOcrAdapter(
        executable_path="/fixed/tesseract",
        runner=lambda _argv, *, timeout, stdin: CommandResult(0, b""),
    )
    assert repr(service) == "VisionAnalysisService()"
    assert repr(adapter) == "LocalOcrAdapter()"
    assert not service._by_id
    assert not service._by_request


def test_vision_sources_define_no_logger_or_audit_emission() -> None:
    for path in VISION_MODULES:
        tree = _tree(path)
        imports = _import_roots(tree)
        calls = _called_names(tree)
        assert "logging" not in imports, path.name
        assert "getLogger" not in calls, path.name
        assert "audit" not in imports, path.name
        assert "append_audit" not in calls, path.name
