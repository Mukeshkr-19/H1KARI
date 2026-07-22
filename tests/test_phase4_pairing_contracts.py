"""Tests for core.pairing.contracts."""

from __future__ import annotations

import ast
import math
import pathlib

import pytest

from core.pairing.contracts import (
    CHALLENGE_TTL_SECONDS,
    DEVICE_SESSION_TTL_SECONDS,
    MAX_CONFIRMATION_ATTEMPTS,
    CancelChallengeOutcome,
    ConfirmChallengeOutcome,
    ContractValidationError,
    DeviceDisplayRecord,
    DeviceErrorCode,
    DeviceMutationOutcome,
    DeviceOutcomeStatus,
    DeviceSessionRecord,
    DeviceSessionState,
    IssueDeviceOutcome,
    PairingChallenge,
    PairingChallengeState,
    PairingConfirmOutcome,
    PairingErrorCode,
    PairingOutcomeStatus,
    PairingPrepareOutcome,
    PrepareChallengeOutcome,
    validate_challenge_id,
    validate_code,
    validate_device_id,
    validate_device_label,
    validate_request_id,
)


def test_canonical_limits() -> None:
    assert CHALLENGE_TTL_SECONDS == 120
    assert MAX_CONFIRMATION_ATTEMPTS == 5
    assert DEVICE_SESSION_TTL_SECONDS == 8 * 60 * 60


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_contract_timestamps_reject_non_finite_values(value: float) -> None:
    with pytest.raises(ContractValidationError):
        PairingChallenge(
            challenge_id="challenge-1",
            request_id="request-1",
            state=PairingChallengeState.PENDING,
            digest="digest",
            attempts=0,
            created_at=value,
            expires_at=value,
        )
    with pytest.raises(ContractValidationError):
        DeviceSessionRecord(
            device_id="device-1",
            challenge_id="challenge-1",
            state=DeviceSessionState.ACTIVE,
            created_at=1000.0,
            expires_at=value,
            updated_at=1000.0,
        )


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("req-1", True),
        ("a", True),
        ("A", False),
        ("", False),
        ("x" * 81, False),
        ("bad space", False),
    ],
)
def test_validate_request_id(value: str, valid: bool) -> None:
    if valid:
        assert validate_request_id(value) == value
    else:
        with pytest.raises(ContractValidationError):
            validate_request_id(value)


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("ch-1", True),
        ("Bad-ID", False),
        ("", False),
    ],
)
def test_validate_challenge_id(value: str, valid: bool) -> None:
    if valid:
        assert validate_challenge_id(value) == value
    else:
        with pytest.raises(ContractValidationError):
            validate_challenge_id(value)


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("device-1", True),
        ("dev:session.1", True),
        ("", False),
        ("bad id", False),
        ("x" * 129, False),
    ],
)
def test_validate_device_id(value: str, valid: bool) -> None:
    if valid:
        assert validate_device_id(value) == value
    else:
        with pytest.raises(ContractValidationError):
            validate_device_id(value)


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("ABC123", True),
        ("abc123", False),
        ("ABC12", False),
        ("ABC1234", True),
        ("ABC123DEF4", True),
        ("ABC123DEF45", False),
        ("GHIJKL", False),
    ],
)
def test_validate_code(value: str, valid: bool) -> None:
    if valid:
        assert validate_code(value) == value
    else:
        with pytest.raises(ContractValidationError):
            validate_code(value)


def test_validate_device_label_rejects_controls_and_format_chars() -> None:
    assert validate_device_label("Phone") == "Phone"
    assert validate_device_label(None) is None
    with pytest.raises(ContractValidationError):
        validate_device_label("")
    with pytest.raises(ContractValidationError):
        validate_device_label("a" * 65)
    with pytest.raises(ContractValidationError):
        validate_device_label("bad\nlabel")
    with pytest.raises(ContractValidationError):
        validate_device_label("zero\u200bwidth")


def test_pairing_challenge_repr_is_content_free() -> None:
    challenge = PairingChallenge(
        challenge_id="challenge-secret",
        request_id="request-secret",
        state=PairingChallengeState.PENDING,
        digest="digest-secret",
        attempts=2,
        created_at=1000.0,
        expires_at=1120.0,
        device_label="My Phone",
    )
    rep = repr(challenge)
    forbidden = (
        "challenge-secret",
        "request-secret",
        "digest-secret",
        "My Phone",
        "1000.0",
        "1120.0",
    )
    for value in forbidden:
        assert value not in rep
    assert rep == "PairingChallenge(state='pending', attempts=2)"


def test_device_session_record_repr_is_content_free() -> None:
    record = DeviceSessionRecord(
        device_id="device-secret",
        challenge_id="challenge-secret",
        state=DeviceSessionState.ACTIVE,
        created_at=1000.0,
        expires_at=29800.0,
        updated_at=1000.0,
        device_label="Watch",
    )
    rep = repr(record)
    forbidden = (
        "device-secret",
        "challenge-secret",
        "Watch",
        "1000.0",
        "29800.0",
    )
    for value in forbidden:
        assert value not in rep
    assert rep == "DeviceSessionRecord(state='active')"


def test_outcome_reprs_are_content_free() -> None:
    prepare = PrepareChallengeOutcome(
        status=PairingOutcomeStatus.OK,
        challenge_id="challenge-secret",
        code="ABC123",
    )
    assert "challenge-secret" not in repr(prepare)
    assert "ABC123" not in repr(prepare)
    assert repr(prepare) == "PrepareChallengeOutcome(status='ok', code_present=True)"

    confirm = ConfirmChallengeOutcome(
        status=PairingOutcomeStatus.ERROR,
        error=PairingErrorCode.WRONG_CODE,
        attempts_remaining=2,
    )
    assert repr(confirm) == (
        "ConfirmChallengeOutcome(status='error', error='wrong_code')"
    )

    cancel = CancelChallengeOutcome(status=PairingOutcomeStatus.OK)
    assert repr(cancel) == "CancelChallengeOutcome(status='ok')"

    issue = IssueDeviceOutcome(
        status=DeviceOutcomeStatus.OK,
        device_id="device-secret",
    )
    assert "device-secret" not in repr(issue)
    assert repr(issue) == "IssueDeviceOutcome(status='ok')"

    mutation = DeviceMutationOutcome(
        status=DeviceOutcomeStatus.ERROR,
        error=DeviceErrorCode.NOT_FOUND,
    )
    assert repr(mutation) == (
        "DeviceMutationOutcome(status='error', error='not_found')"
    )

    service_prepare = PairingPrepareOutcome(
        status=PairingOutcomeStatus.OK,
        challenge_id="challenge-secret",
        code="ABC123",
    )
    assert "challenge-secret" not in repr(service_prepare)
    assert "ABC123" not in repr(service_prepare)

    service_confirm = PairingConfirmOutcome(
        status=PairingOutcomeStatus.OK,
        device_id="device-secret",
    )
    assert "device-secret" not in repr(service_confirm)
    assert repr(service_confirm) == "PairingConfirmOutcome(status='ok')"


def test_device_display_record_repr_omits_label() -> None:
    record = DeviceDisplayRecord(
        state=DeviceSessionState.STALE,
        device_label="Private Label",
    )
    rep = repr(record)
    assert "Private Label" not in rep
    assert rep == "DeviceDisplayRecord(state='stale')"


def test_contracts_module_has_no_forbidden_imports() -> None:
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "core"
        / "pairing"
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
        "browser",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert forbidden.isdisjoint(imported), f"forbidden imports: {forbidden & imported}"
