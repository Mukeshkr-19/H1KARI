"""Frozen pairing and device-session contracts (transport-independent, no I/O)."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Optional

CHALLENGE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
REQUEST_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
CODE_PATTERN = re.compile(r"^[0-9A-F]{6}$")

CHALLENGE_TTL_SECONDS = 120
MAX_CONFIRMATION_ATTEMPTS = 5
DEVICE_SESSION_TTL_SECONDS = 8 * 60 * 60

MAX_DEVICE_ID_LENGTH = 128
MIN_DEVICE_LABEL_LENGTH = 1
MAX_DEVICE_LABEL_LENGTH = 64


class PairingChallengeState(str, Enum):
    PENDING = "pending"
    CONSUMED = "consumed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    LOCKED = "locked"


class DeviceSessionState(str, Enum):
    ACTIVE = "active"
    STALE = "stale"
    REVOKED = "revoked"
    EXPIRED = "expired"


class PairingErrorCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    WRONG_CODE = "wrong_code"
    LOCKED = "locked"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    ALREADY_CONSUMED = "already_consumed"
    UNAVAILABLE = "unavailable"


class PairingOutcomeStatus(str, Enum):
    OK = "ok"
    ERROR = "error"


class DeviceErrorCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    EXPIRED = "expired"
    REVOKED = "revoked"
    UNAVAILABLE = "unavailable"


class DeviceOutcomeStatus(str, Enum):
    OK = "ok"
    ERROR = "error"


class ContractValidationError(ValueError):
    """Raised when a contract value fails validation."""


def _has_disallowed_label_char(value: str) -> bool:
    for char in value:
        code = ord(char)
        if code < 32 or code == 127:
            return True
        if unicodedata.category(char) == "Cf":
            return True
    return False


def validate_request_id(value: object) -> str:
    if not isinstance(value, str) or not REQUEST_ID_PATTERN.fullmatch(value):
        raise ContractValidationError("request_id is invalid")
    return value


def validate_challenge_id(value: object) -> str:
    if not isinstance(value, str) or not CHALLENGE_ID_PATTERN.fullmatch(value):
        raise ContractValidationError("challenge_id is invalid")
    return value


def validate_device_id(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ContractValidationError("device_id is invalid")
    if len(value) > MAX_DEVICE_ID_LENGTH:
        raise ContractValidationError("device_id is invalid")
    if not DEVICE_ID_PATTERN.fullmatch(value):
        raise ContractValidationError("device_id is invalid")
    return value


def validate_code(value: object) -> str:
    if not isinstance(value, str) or not CODE_PATTERN.fullmatch(value):
        raise ContractValidationError("code is invalid")
    return value


def validate_device_label(value: object) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractValidationError("device_label is invalid")
    if len(value) < MIN_DEVICE_LABEL_LENGTH or len(value) > MAX_DEVICE_LABEL_LENGTH:
        raise ContractValidationError("device_label is invalid")
    if _has_disallowed_label_char(value):
        raise ContractValidationError("device_label is invalid")
    return value


@dataclass(frozen=True)
class PairingChallenge:
    """Internal challenge record bound to a request correlation."""

    challenge_id: str
    request_id: str
    state: PairingChallengeState
    digest: str
    attempts: int
    created_at: float
    expires_at: float
    device_label: Optional[str] = None

    def __post_init__(self) -> None:
        validate_challenge_id(self.challenge_id)
        validate_request_id(self.request_id)
        if not isinstance(self.state, PairingChallengeState):
            raise ContractValidationError("state is invalid")
        if not isinstance(self.digest, str) or not self.digest:
            raise ContractValidationError("digest is invalid")
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int):
            raise ContractValidationError("attempts is invalid")
        if self.attempts < 0 or self.attempts > MAX_CONFIRMATION_ATTEMPTS:
            raise ContractValidationError("attempts is invalid")
        if not isinstance(self.created_at, (int, float)) or isinstance(
            self.created_at, bool
        ):
            raise ContractValidationError("created_at is invalid")
        if not isinstance(self.expires_at, (int, float)) or isinstance(
            self.expires_at, bool
        ):
            raise ContractValidationError("expires_at is invalid")
        if not math.isfinite(float(self.created_at)) or not math.isfinite(
            float(self.expires_at)
        ):
            raise ContractValidationError("timestamp is invalid")
        if self.expires_at < self.created_at:
            raise ContractValidationError("expires_at is invalid")
        if self.device_label is not None:
            validate_device_label(self.device_label)

    def __repr__(self) -> str:
        return (
            f"PairingChallenge(state={self.state.value!r}, attempts={self.attempts})"
        )


@dataclass(frozen=True)
class DeviceSessionRecord:
    """SQLite-backed device session row."""

    device_id: str
    challenge_id: str
    state: DeviceSessionState
    created_at: float
    expires_at: float
    updated_at: float
    device_label: Optional[str] = None

    def __post_init__(self) -> None:
        validate_device_id(self.device_id)
        validate_challenge_id(self.challenge_id)
        if not isinstance(self.state, DeviceSessionState):
            raise ContractValidationError("state is invalid")
        for name in ("created_at", "expires_at", "updated_at"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ContractValidationError(f"{name} is invalid")
            if not math.isfinite(float(value)):
                raise ContractValidationError(f"{name} is invalid")
        if self.expires_at < self.created_at:
            raise ContractValidationError("expires_at is invalid")
        if self.updated_at < self.created_at:
            raise ContractValidationError("updated_at is invalid")
        if self.device_label is not None:
            validate_device_label(self.device_label)

    def __repr__(self) -> str:
        return f"DeviceSessionRecord(state={self.state.value!r})"


@dataclass(frozen=True)
class DeviceDisplayRecord:
    """List-safe device session view without secrets or identifiers."""

    state: DeviceSessionState
    device_label: Optional[str] = None

    def __repr__(self) -> str:
        return f"DeviceDisplayRecord(state={self.state.value!r})"


@dataclass(frozen=True)
class PrepareChallengeOutcome:
    status: PairingOutcomeStatus
    challenge_id: Optional[str] = None
    code: Optional[str] = None
    error: Optional[PairingErrorCode] = None

    def __repr__(self) -> str:
        if self.status is PairingOutcomeStatus.OK:
            return (
                "PrepareChallengeOutcome(status='ok', "
                f"code_present={self.code is not None})"
            )
        return (
            f"PrepareChallengeOutcome(status={self.status.value!r}, "
            f"error={self.error.value!r})"  # type: ignore[union-attr]
        )


@dataclass(frozen=True)
class ConfirmChallengeOutcome:
    status: PairingOutcomeStatus
    error: Optional[PairingErrorCode] = None
    attempts_remaining: Optional[int] = None

    def __repr__(self) -> str:
        if self.status is PairingOutcomeStatus.OK:
            return "ConfirmChallengeOutcome(status='ok')"
        return (
            f"ConfirmChallengeOutcome(status={self.status.value!r}, "
            f"error={self.error.value!r})"  # type: ignore[union-attr]
        )


@dataclass(frozen=True)
class CancelChallengeOutcome:
    status: PairingOutcomeStatus
    error: Optional[PairingErrorCode] = None

    def __repr__(self) -> str:
        if self.status is PairingOutcomeStatus.OK:
            return "CancelChallengeOutcome(status='ok')"
        return (
            f"CancelChallengeOutcome(status={self.status.value!r}, "
            f"error={self.error.value!r})"  # type: ignore[union-attr]
        )


@dataclass(frozen=True)
class IssueDeviceOutcome:
    status: DeviceOutcomeStatus
    device_id: Optional[str] = None
    error: Optional[DeviceErrorCode] = None

    def __repr__(self) -> str:
        if self.status is DeviceOutcomeStatus.OK:
            return "IssueDeviceOutcome(status='ok')"
        return (
            f"IssueDeviceOutcome(status={self.status.value!r}, "
            f"error={self.error.value!r})"  # type: ignore[union-attr]
        )


@dataclass(frozen=True)
class DeviceMutationOutcome:
    status: DeviceOutcomeStatus
    error: Optional[DeviceErrorCode] = None

    def __repr__(self) -> str:
        if self.status is DeviceOutcomeStatus.OK:
            return "DeviceMutationOutcome(status='ok')"
        return (
            f"DeviceMutationOutcome(status={self.status.value!r}, "
            f"error={self.error.value!r})"  # type: ignore[union-attr]
        )


@dataclass(frozen=True)
class PairingConfirmOutcome:
    status: PairingOutcomeStatus
    device_id: Optional[str] = None
    error: Optional[PairingErrorCode] = None
    attempts_remaining: Optional[int] = None

    def __repr__(self) -> str:
        if self.status is PairingOutcomeStatus.OK:
            return "PairingConfirmOutcome(status='ok')"
        return (
            f"PairingConfirmOutcome(status={self.status.value!r}, "
            f"error={self.error.value!r})"  # type: ignore[union-attr]
        )


@dataclass(frozen=True)
class PairingPrepareOutcome:
    status: PairingOutcomeStatus
    challenge_id: Optional[str] = None
    code: Optional[str] = None
    error: Optional[PairingErrorCode] = None

    def __repr__(self) -> str:
        if self.status is PairingOutcomeStatus.OK:
            return (
                "PairingPrepareOutcome(status='ok', "
                f"code_present={self.code is not None})"
            )
        return (
            f"PairingPrepareOutcome(status={self.status.value!r}, "
            f"error={self.error.value!r})"  # type: ignore[union-attr]
        )
