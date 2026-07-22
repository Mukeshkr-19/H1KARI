"""Tests for core.vision.contracts."""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

from core.vision.contracts import (
    ANALYSIS_TTL_SECONDS,
    MAX_CONFIDENCE_MILLI,
    MAX_OBSERVATION_TEXT_LENGTH,
    MAX_OBSERVATIONS,
    MIN_CONFIDENCE_MILLI,
    MIN_OBSERVATION_TEXT_LENGTH,
    ContractValidationError,
    VisionAnalysisRecord,
    VisionAnalysisRequest,
    VisionAnalysisState,
    VisionCapability,
    VisionObservation,
    VisionObservationKind,
    VisionOutcomeCode,
    VisionServiceOutcome,
    validate_analysis_id,
    validate_handoff_id,
    validate_request_id,
    validate_transfer_id,
)


def test_canonical_limits() -> None:
    assert ANALYSIS_TTL_SECONDS == 15 * 60
    assert MAX_OBSERVATIONS == 16
    assert MIN_OBSERVATION_TEXT_LENGTH == 1
    assert MAX_OBSERVATION_TEXT_LENGTH == 2000
    assert MIN_CONFIDENCE_MILLI == 0
    assert MAX_CONFIDENCE_MILLI == 1000


def test_enum_values() -> None:
    assert {item.value for item in VisionCapability} == {"ocr", "describe"}
    assert {item.value for item in VisionAnalysisState} == {
        "awaiting_image",
        "analyzing",
        "completed",
        "cancelled",
        "expired",
        "failed",
    }
    assert {item.value for item in VisionObservationKind} == {"text", "description"}
    assert {item.value for item in VisionOutcomeCode} == {
        "ready",
        "awaiting_image",
        "analyzing",
        "completed",
        "cancelled",
        "expired",
        "analysis_not_found",
        "invalid_request",
        "unavailable",
    }


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("analysis-1", True),
        ("a", True),
        ("req.request_1", True),
        ("", False),
        ("BAD", False),
        ("bad space", False),
        ("x" * 81, False),
    ],
)
def test_canonical_id_validators(value: str, valid: bool) -> None:
    for validator in (
        validate_analysis_id,
        validate_request_id,
        validate_handoff_id,
        validate_transfer_id,
    ):
        if valid:
            assert validator(value) == value
        else:
            with pytest.raises(ContractValidationError):
                validator(value)


def test_analysis_request_valid() -> None:
    request = VisionAnalysisRequest(
        request_id="request-1",
        handoff_id="handoff-1",
        capability=VisionCapability.OCR,
    )
    assert request.capability is VisionCapability.OCR
    rep = repr(request)
    assert "request-1" not in rep
    assert "handoff-1" not in rep
    assert "ocr" in rep


def test_observation_preserves_exact_text() -> None:
    text = "Exact OCR line\nwith tab\tkept"
    observation = VisionObservation(
        kind=VisionObservationKind.TEXT,
        text=text,
        confidence_milli=875,
    )
    assert observation.text == text


def test_observation_rejects_controls_except_newline_tab_for_ocr() -> None:
    VisionObservation(
        kind=VisionObservationKind.TEXT,
        text="ok\n\tok",
        confidence_milli=1,
    )
    with pytest.raises(ContractValidationError):
        VisionObservation(
            kind=VisionObservationKind.TEXT,
            text="bad\x00",
            confidence_milli=1,
        )
    with pytest.raises(ContractValidationError):
        VisionObservation(
            kind=VisionObservationKind.TEXT,
            text="bad\u200b",
            confidence_milli=1,
        )


def test_description_rejects_whitespace_only() -> None:
    with pytest.raises(ContractValidationError):
        VisionObservation(
            kind=VisionObservationKind.DESCRIPTION,
            text="   ",
            confidence_milli=10,
        )
    with pytest.raises(ContractValidationError):
        VisionObservation(
            kind=VisionObservationKind.DESCRIPTION,
            text="\u2003\u3000",
            confidence_milli=10,
        )
    with pytest.raises(ContractValidationError):
        VisionObservation(
            kind=VisionObservationKind.DESCRIPTION,
            text="line\nbreak",
            confidence_milli=10,
        )


def test_observation_bounds() -> None:
    VisionObservation(
        kind=VisionObservationKind.TEXT,
        text="x" * MAX_OBSERVATION_TEXT_LENGTH,
        confidence_milli=MAX_CONFIDENCE_MILLI,
    )
    with pytest.raises(ContractValidationError):
        VisionObservation(
            kind=VisionObservationKind.TEXT,
            text="",
            confidence_milli=0,
        )
    with pytest.raises(ContractValidationError):
        VisionObservation(
            kind=VisionObservationKind.TEXT,
            text="x" * (MAX_OBSERVATION_TEXT_LENGTH + 1),
            confidence_milli=0,
        )
    with pytest.raises(ContractValidationError):
        VisionObservation(
            kind=VisionObservationKind.TEXT,
            text="ok",
            confidence_milli=1001,
        )
    with pytest.raises(ContractValidationError):
        VisionObservation(
            kind=VisionObservationKind.TEXT,
            text="ok",
            confidence_milli=True,  # type: ignore[arg-type]
        )


def test_observation_repr_is_content_free() -> None:
    observation = VisionObservation(
        kind=VisionObservationKind.DESCRIPTION,
        text="secret private description",
        confidence_milli=42,
    )
    rep = repr(observation)
    assert "secret" not in rep
    assert "private" not in rep
    assert str(observation) == rep


def test_observation_allows_unavailable_confidence_without_fabrication() -> None:
    observation = VisionObservation(
        kind=VisionObservationKind.TEXT,
        text="measured text",
    )
    assert observation.confidence_milli is None


def test_analysis_record_valid_and_frozen() -> None:
    record = VisionAnalysisRecord(
        analysis_id="analysis-1",
        request_id="request-1",
        actor_id="local-owner",
        session_id="session-1",
        handoff_id="handoff-1",
        capability=VisionCapability.DESCRIBE,
        state=VisionAnalysisState.AWAITING_IMAGE,
        created_at=1000.0,
        expires_at=1000.0 + ANALYSIS_TTL_SECONDS,
        updated_at=1000.0,
    )
    assert record.is_expired(1000.0 + ANALYSIS_TTL_SECONDS) is True
    assert record.is_expired(1000.0 + ANALYSIS_TTL_SECONDS - 1) is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.state = VisionAnalysisState.COMPLETED  # type: ignore[misc]


def test_analysis_record_completed_requires_observations() -> None:
    observation = VisionObservation(
        kind=VisionObservationKind.TEXT,
        text="line",
        confidence_milli=1,
    )
    with pytest.raises(ContractValidationError):
        VisionAnalysisRecord(
            analysis_id="analysis-1",
            request_id="request-1",
            actor_id="local-owner",
            session_id="session-1",
            handoff_id="handoff-1",
            capability=VisionCapability.OCR,
            state=VisionAnalysisState.COMPLETED,
            created_at=1000.0,
            expires_at=1000.0 + ANALYSIS_TTL_SECONDS,
            updated_at=1000.0,
            transfer_id="transfer-1",
            observations=(),
        )
    record = VisionAnalysisRecord(
        analysis_id="analysis-1",
        request_id="request-1",
        actor_id="local-owner",
        session_id="session-1",
        handoff_id="handoff-1",
        capability=VisionCapability.OCR,
        state=VisionAnalysisState.COMPLETED,
        created_at=1000.0,
        expires_at=1000.0 + ANALYSIS_TTL_SECONDS,
        updated_at=1000.0,
        transfer_id="transfer-1",
        observations=(observation,),
    )
    rep = repr(record)
    for forbidden in (
        "analysis-1",
        "request-1",
        "local-owner",
        "session-1",
        "handoff-1",
        "transfer-1",
        "line",
        "1000",
    ):
        assert forbidden not in rep


def test_analysis_record_rejects_observations_before_completed() -> None:
    observation = VisionObservation(
        kind=VisionObservationKind.TEXT,
        text="line",
        confidence_milli=1,
    )
    with pytest.raises(ContractValidationError):
        VisionAnalysisRecord(
            analysis_id="analysis-1",
            request_id="request-1",
            actor_id="local-owner",
            session_id="session-1",
            handoff_id="handoff-1",
            capability=VisionCapability.OCR,
            state=VisionAnalysisState.ANALYZING,
            created_at=1000.0,
            expires_at=1000.0 + ANALYSIS_TTL_SECONDS,
            updated_at=1000.0,
            transfer_id="transfer-1",
            observations=(observation,),
        )


def test_service_outcome_repr_and_ok() -> None:
    ok = VisionServiceOutcome(
        code=VisionOutcomeCode.COMPLETED,
        analysis_id="analysis-1",
        request_id="request-1",
        state=VisionAnalysisState.COMPLETED,
        observation_count=2,
    )
    assert ok.ok is True
    rep = repr(ok)
    assert "analysis-1" not in rep
    assert "request-1" not in rep
    err = VisionServiceOutcome(code=VisionOutcomeCode.ANALYSIS_NOT_FOUND)
    assert err.ok is False


def test_contracts_module_has_no_forbidden_imports() -> None:
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "core"
        / "vision"
        / "contracts.py"
    )
    forbidden = {
        "subprocess",
        "socket",
        "threading",
        "asyncio",
        "requests",
        "http",
        "urllib",
        "sqlite3",
        "secrets",
        "time",
        "pathlib",
        "os",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert forbidden.isdisjoint(imported)
