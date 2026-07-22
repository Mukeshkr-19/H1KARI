"""Frozen Phase 4 vision observation contracts (transport-independent, no I/O)."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

CANONICAL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")

ANALYSIS_TTL_SECONDS = 15 * 60
MAX_OBSERVATIONS = 16
MIN_OBSERVATION_TEXT_LENGTH = 1
MAX_OBSERVATION_TEXT_LENGTH = 2000
MIN_CONFIDENCE_MILLI = 0
MAX_CONFIDENCE_MILLI = 1000


class ContractValidationError(ValueError):
    """Raised when a contract value fails validation.

    Messages are fixed reason codes and never include the offending value.
    """


class VisionCapability(str, Enum):
    OCR = "ocr"
    DESCRIBE = "describe"


class VisionAnalysisState(str, Enum):
    AWAITING_IMAGE = "awaiting_image"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATES


class VisionObservationKind(str, Enum):
    TEXT = "text"
    DESCRIPTION = "description"


class VisionOutcomeCode(str, Enum):
    READY = "ready"
    AWAITING_IMAGE = "awaiting_image"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    ANALYSIS_NOT_FOUND = "analysis_not_found"
    INVALID_REQUEST = "invalid_request"
    UNAVAILABLE = "unavailable"


_TERMINAL_STATES = frozenset(
    {
        VisionAnalysisState.COMPLETED,
        VisionAnalysisState.CANCELLED,
        VisionAnalysisState.EXPIRED,
        VisionAnalysisState.FAILED,
    }
)

_ERROR_OUTCOME_CODES = frozenset(
    {
        VisionOutcomeCode.ANALYSIS_NOT_FOUND,
        VisionOutcomeCode.INVALID_REQUEST,
        VisionOutcomeCode.UNAVAILABLE,
    }
)


def validate_canonical_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not CANONICAL_ID_PATTERN.fullmatch(value):
        raise ContractValidationError(f"{field} is invalid")
    return value


def validate_analysis_id(value: object) -> str:
    return validate_canonical_id(value, field="analysis_id")


def validate_request_id(value: object) -> str:
    return validate_canonical_id(value, field="request_id")


def validate_handoff_id(value: object) -> str:
    return validate_canonical_id(value, field="handoff_id")


def validate_transfer_id(value: object) -> str:
    return validate_canonical_id(value, field="transfer_id")


def _has_disallowed_ocr_char(value: str) -> bool:
    for char in value:
        code = ord(char)
        if char in ("\n", "\t"):
            continue
        if code < 32 or code == 127:
            return True
        if unicodedata.category(char) == "Cf":
            return True
    return False


def _has_disallowed_description_char(value: str) -> bool:
    for char in value:
        code = ord(char)
        if code < 32 or code == 127:
            return True
        if unicodedata.category(char) == "Cf":
            return True
    return False


def _validate_observation_text(kind: VisionObservationKind, text: str) -> None:
    if not isinstance(text, str):
        raise ContractValidationError("observation text is invalid")
    length = len(text)
    if length < MIN_OBSERVATION_TEXT_LENGTH or length > MAX_OBSERVATION_TEXT_LENGTH:
        raise ContractValidationError("observation text is invalid")
    if kind is VisionObservationKind.TEXT:
        if _has_disallowed_ocr_char(text):
            raise ContractValidationError("observation text is invalid")
    elif kind is VisionObservationKind.DESCRIPTION:
        if _has_disallowed_description_char(text):
            raise ContractValidationError("observation text is invalid")
        if not text.strip():
            raise ContractValidationError("observation text is invalid")
    else:
        raise ContractValidationError("observation kind is invalid")


def _validate_finite_timestamp(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractValidationError(f"{name} is invalid")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ContractValidationError(f"{name} is invalid")
    return numeric


@dataclass(frozen=True)
class VisionAnalysisRequest:
    """Client-declared analysis intent bound to an existing handoff."""

    request_id: str
    handoff_id: str
    capability: VisionCapability

    def __post_init__(self) -> None:
        validate_request_id(self.request_id)
        validate_handoff_id(self.handoff_id)
        if not isinstance(self.capability, VisionCapability):
            raise ContractValidationError("capability is invalid")

    def __repr__(self) -> str:
        return f"VisionAnalysisRequest(capability={self.capability.value!r})"

    def __str__(self) -> str:
        return self.__repr__()


@dataclass(frozen=True)
class VisionObservation:
    """One bounded OCR or description observation."""

    kind: VisionObservationKind
    text: str
    confidence_milli: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, VisionObservationKind):
            raise ContractValidationError("observation kind is invalid")
        _validate_observation_text(self.kind, self.text)
        if self.confidence_milli is None:
            return
        if isinstance(self.confidence_milli, bool) or not isinstance(
            self.confidence_milli, int
        ):
            raise ContractValidationError("confidence_milli is invalid")
        if (
            self.confidence_milli < MIN_CONFIDENCE_MILLI
            or self.confidence_milli > MAX_CONFIDENCE_MILLI
        ):
            raise ContractValidationError("confidence_milli is invalid")

    def __repr__(self) -> str:
        return (
            f"VisionObservation(kind={self.kind.value!r}, "
            f"confidence_milli={self.confidence_milli})"
        )

    def __str__(self) -> str:
        return self.__repr__()


@dataclass(frozen=True)
class VisionAnalysisRecord:
    """Internal analysis lifecycle record. Never contains image bytes."""

    analysis_id: str
    request_id: str
    actor_id: str
    session_id: str
    handoff_id: str
    capability: VisionCapability
    state: VisionAnalysisState
    created_at: float
    expires_at: float
    updated_at: float
    transfer_id: Optional[str] = None
    observations: Tuple[VisionObservation, ...] = ()

    def __post_init__(self) -> None:
        validate_analysis_id(self.analysis_id)
        validate_request_id(self.request_id)
        if not isinstance(self.actor_id, str) or not self.actor_id:
            raise ContractValidationError("actor_id is invalid")
        if not isinstance(self.session_id, str) or not self.session_id:
            raise ContractValidationError("session_id is invalid")
        validate_handoff_id(self.handoff_id)
        if not isinstance(self.capability, VisionCapability):
            raise ContractValidationError("capability is invalid")
        if not isinstance(self.state, VisionAnalysisState):
            raise ContractValidationError("state is invalid")
        created_at = _validate_finite_timestamp("created_at", self.created_at)
        expires_at = _validate_finite_timestamp("expires_at", self.expires_at)
        updated_at = _validate_finite_timestamp("updated_at", self.updated_at)
        if expires_at != created_at + ANALYSIS_TTL_SECONDS:
            raise ContractValidationError("expires_at is invalid")
        if updated_at < created_at:
            raise ContractValidationError("updated_at is invalid")
        if self.transfer_id is not None:
            validate_transfer_id(self.transfer_id)
        if not isinstance(self.observations, tuple):
            raise ContractValidationError("observations are invalid")
        if len(self.observations) > MAX_OBSERVATIONS:
            raise ContractValidationError("observations are invalid")
        for item in self.observations:
            if not isinstance(item, VisionObservation):
                raise ContractValidationError("observations are invalid")
        if self.state is VisionAnalysisState.COMPLETED and not self.observations:
            raise ContractValidationError("observations are invalid")
        if self.state is not VisionAnalysisState.COMPLETED and self.observations:
            raise ContractValidationError("observations are invalid")
        if (
            self.state
            in (
                VisionAnalysisState.ANALYZING,
                VisionAnalysisState.COMPLETED,
            )
            and self.transfer_id is None
        ):
            raise ContractValidationError("transfer_id is invalid")

    def is_expired(self, now: float) -> bool:
        numeric = _validate_finite_timestamp("now", now)
        return numeric >= self.expires_at

    def __repr__(self) -> str:
        return (
            f"VisionAnalysisRecord(state={self.state.value!r}, "
            f"capability={self.capability.value!r}, "
            f"observation_count={len(self.observations)})"
        )

    def __str__(self) -> str:
        return self.__repr__()


@dataclass(frozen=True)
class VisionServiceOutcome:
    """Fixed, content-safe public result of a vision service operation."""

    code: VisionOutcomeCode
    analysis_id: Optional[str] = None
    request_id: Optional[str] = None
    state: Optional[VisionAnalysisState] = None
    observation_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.code, VisionOutcomeCode):
            raise ContractValidationError("outcome code is invalid")
        if self.analysis_id is not None:
            validate_analysis_id(self.analysis_id)
        if self.request_id is not None:
            validate_request_id(self.request_id)
        if self.state is not None and not isinstance(self.state, VisionAnalysisState):
            raise ContractValidationError("state is invalid")
        if isinstance(self.observation_count, bool) or not isinstance(
            self.observation_count, int
        ):
            raise ContractValidationError("observation_count is invalid")
        if self.observation_count < 0 or self.observation_count > MAX_OBSERVATIONS:
            raise ContractValidationError("observation_count is invalid")

    @property
    def ok(self) -> bool:
        return self.code not in _ERROR_OUTCOME_CODES

    def __repr__(self) -> str:
        return (
            f"VisionServiceOutcome(code={self.code.value!r}, "
            f"state={self.state.value if self.state is not None else None!r}, "
            f"observation_count={self.observation_count})"
        )

    def __str__(self) -> str:
        return self.__repr__()
