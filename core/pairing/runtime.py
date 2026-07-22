"""Transport-independent Phase 4 pairing runtime boundary.

Converts PairingService outcomes into canonical protocol server messages.
Secrets are delivered only to an injected local display sink and never appear
in returned dictionaries, repr, or protocol fields.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, Optional

from core.action_policy import Actor, ActorContext
from core.pairing.contracts import (
    DEVICE_SESSION_TTL_SECONDS,
    DeviceErrorCode,
    DeviceOutcomeStatus,
    PairingErrorCode,
    PairingOutcomeStatus,
    validate_challenge_id,
    validate_code,
    validate_device_id,
    validate_request_id,
    ContractValidationError,
)
from core.pairing.service import PairingService
from core.protocol import PROTOCOL_VERSION, validate_server_message


_FALLBACK_REQUEST_ID = "invalid-request"

_FORBIDDEN_CLIENT_FIELDS = frozenset(
    {
        "actor",
        "actor_id",
        "session",
        "session_id",
        "approval",
        "approval_id",
        "grant",
        "grant_id",
        "execution",
        "execution_token",
        "execution_ticket",
        "device_authority",
        "expires_at",
        "device_id",
        "challenge_id",
        "code",
    }
)

_PROTOCOL_ERROR_CODES = frozenset(
    {
        "unavailable",
        "unauthorized",
        "invalid_request",
        "challenge_invalid",
        "challenge_expired",
        "pairing_locked",
        "rate_limited",
        "device_not_found",
    }
)

_ERROR_MAP: dict[PairingErrorCode, str] = {
    PairingErrorCode.INVALID_INPUT: "invalid_request",
    PairingErrorCode.NOT_FOUND: "challenge_invalid",
    PairingErrorCode.WRONG_CODE: "challenge_invalid",
    PairingErrorCode.LOCKED: "pairing_locked",
    PairingErrorCode.EXPIRED: "challenge_expired",
    PairingErrorCode.CANCELLED: "challenge_invalid",
    PairingErrorCode.ALREADY_CONSUMED: "challenge_invalid",
    PairingErrorCode.UNAVAILABLE: "unavailable",
}


ChallengeDisplaySink = Callable[[str], None]


def _safe_request_id(value: object) -> str:
    try:
        return validate_request_id(value)
    except ContractValidationError:
        return _FALLBACK_REQUEST_ID


def pairing_error(request_id: object, code: str) -> dict[str, Any]:
    """Build a canonical pairing_error message."""
    safe_id = _safe_request_id(request_id)
    safe_code = code if code in _PROTOCOL_ERROR_CODES else "unavailable"
    message = {"type": "pairing_error", "request_id": safe_id, "code": safe_code}
    if validate_server_message(message) is not None:
        return {
            "type": "pairing_error",
            "request_id": _FALLBACK_REQUEST_ID,
            "code": "unavailable",
        }
    return message


def pairing_challenge(
    request_id: str, challenge_id: str, expires_at: float
) -> dict[str, Any]:
    """Build a canonical pairing_challenge without the secret."""
    message = {
        "type": "pairing_challenge",
        "request_id": request_id,
        "challenge_id": challenge_id,
        "expires_at": float(expires_at),
    }
    if validate_server_message(message) is not None:
        return pairing_error(request_id, "unavailable")
    return message


def pairing_confirmed(
    request_id: str,
    device_id: str,
    expires_at: float,
) -> dict[str, Any]:
    """Build a canonical pairing_confirmed message."""
    message = {
        "type": "pairing_confirmed",
        "request_id": request_id,
        "device_id": device_id,
        "expires_at": float(expires_at),
        "protocol_version": PROTOCOL_VERSION,
    }
    if validate_server_message(message) is not None:
        return pairing_error(request_id, "unavailable")
    return message


def pairing_update_cancelled(request_id: str, challenge_id: str) -> dict[str, Any]:
    """Build a canonical cancelled pairing_update."""
    message = {
        "type": "pairing_update",
        "request_id": request_id,
        "status": "cancelled",
        "challenge_id": challenge_id,
    }
    if validate_server_message(message) is not None:
        return pairing_error(request_id, "unavailable")
    return message


def pairing_update_revoked(request_id: str, device_id: str) -> dict[str, Any]:
    """Build a canonical revoked pairing_update."""
    message = {
        "type": "pairing_update",
        "request_id": request_id,
        "status": "revoked",
        "device_id": device_id,
    }
    if validate_server_message(message) is not None:
        return pairing_error(request_id, "unavailable")
    return message


class PairingRuntime:
    """Coordinate pairing without sockets, secrets in responses, or authority."""

    def __init__(
        self,
        service: PairingService,
        clock: Callable[[], float],
        display_sink: ChallengeDisplaySink,
    ) -> None:
        if not isinstance(service, PairingService):
            raise TypeError("service must be a PairingService")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not callable(display_sink):
            raise TypeError("display_sink must be callable")
        self._service = service
        self._clock = clock
        self._display_sink = display_sink

    def __repr__(self) -> str:
        return "PairingRuntime()"

    @staticmethod
    def _reject_forbidden(fields: dict[str, object]) -> bool:
        return any(key in _FORBIDDEN_CLIENT_FIELDS for key in fields)

    def _now(self) -> float | None:
        try:
            value = self._clock()
        except Exception:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            numeric = float(value)
        except (OverflowError, TypeError, ValueError):
            return None
        if not math.isfinite(numeric):
            return None
        return numeric

    def _map_error(self, request_id: object, error: Optional[PairingErrorCode]) -> dict[str, Any]:
        if error is None:
            return pairing_error(request_id, "unavailable")
        return pairing_error(request_id, _ERROR_MAP.get(error, "unavailable"))

    def prepare(self, request_id: str, **fields: object) -> dict[str, Any]:
        """Prepare a challenge and display the secret only through the local sink."""
        if self._reject_forbidden(fields):
            return pairing_error(request_id, "invalid_request")
        try:
            safe_request_id = validate_request_id(request_id)
        except ContractValidationError:
            return pairing_error(request_id, "invalid_request")

        if self._now() is None:
            return pairing_error(safe_request_id, "unavailable")

        try:
            outcome = self._service.prepare(safe_request_id)
        except Exception:
            return pairing_error(safe_request_id, "unavailable")

        if outcome.status is not PairingOutcomeStatus.OK or outcome.challenge_id is None:
            return self._map_error(safe_request_id, outcome.error)

        secret = outcome.code
        if secret is not None:
            try:
                validate_code(secret)
                self._display_sink(secret)
            except Exception:
                try:
                    self._service.cancel(outcome.challenge_id)
                except Exception:
                    pass
                return pairing_error(safe_request_id, "unavailable")

        snapshot = self._service._get_challenge(outcome.challenge_id)
        if snapshot is None or snapshot.request_id != safe_request_id:
            return pairing_error(safe_request_id, "unavailable")
        return pairing_challenge(
            safe_request_id,
            outcome.challenge_id,
            snapshot.expires_at,
        )

    def confirm(
        self,
        request_id: str,
        challenge_id: str,
        code: str,
        **fields: object,
    ) -> dict[str, Any]:
        """Confirm a challenge and return a validated pairing_confirmed message."""
        if self._reject_forbidden(fields):
            return pairing_error(request_id, "invalid_request")
        try:
            safe_request_id = validate_request_id(request_id)
            safe_challenge_id = validate_challenge_id(challenge_id)
            safe_code = validate_code(code)
        except ContractValidationError:
            return pairing_error(request_id, "invalid_request")

        now = self._now()
        if now is None:
            return pairing_error(safe_request_id, "unavailable")

        try:
            outcome = self._service.confirm(safe_request_id, safe_challenge_id, safe_code)
        except Exception:
            return pairing_error(safe_request_id, "unavailable")

        if outcome.status is not PairingOutcomeStatus.OK or outcome.device_id is None:
            return self._map_error(safe_request_id, outcome.error)

        try:
            validate_device_id(outcome.device_id)
        except ContractValidationError:
            return pairing_error(safe_request_id, "unavailable")

        record = self._service._get_device(outcome.device_id)
        if record is not None and math.isfinite(float(record.expires_at)):
            expires_at = float(record.expires_at)
        else:
            expires_at = now + DEVICE_SESSION_TTL_SECONDS
        return pairing_confirmed(safe_request_id, outcome.device_id, expires_at)

    def cancel(
        self,
        request_id: str,
        challenge_id: str,
        **fields: object,
    ) -> dict[str, Any]:
        """Cancel an exact challenge and return a validated update."""
        if self._reject_forbidden(fields):
            return pairing_error(request_id, "invalid_request")
        try:
            safe_request_id = validate_request_id(request_id)
            safe_challenge_id = validate_challenge_id(challenge_id)
        except ContractValidationError:
            return pairing_error(request_id, "invalid_request")

        if self._now() is None:
            return pairing_error(safe_request_id, "unavailable")

        snapshot = self._service._get_challenge(safe_challenge_id)
        if snapshot is None or snapshot.request_id != safe_request_id:
            return pairing_error(safe_request_id, "challenge_invalid")

        try:
            outcome = self._service.cancel(safe_challenge_id)
        except Exception:
            return pairing_error(safe_request_id, "unavailable")

        if outcome.status is not PairingOutcomeStatus.OK:
            return self._map_error(safe_request_id, outcome.error)
        return pairing_update_cancelled(safe_request_id, safe_challenge_id)

    def revoke(
        self,
        actor: ActorContext,
        request_id: str,
        device_id: str,
        **fields: object,
    ) -> dict[str, Any]:
        """Owner-only device revocation through the device-store boundary."""
        if self._reject_forbidden(fields):
            return pairing_error(request_id, "invalid_request")
        if not isinstance(actor, ActorContext) or not isinstance(actor.actor, Actor):
            return pairing_error(request_id, "unauthorized")
        try:
            safe_request_id = validate_request_id(request_id)
            safe_device_id = validate_device_id(device_id)
        except ContractValidationError:
            return pairing_error(request_id, "invalid_request")

        if actor.actor is not Actor.OWNER:
            return pairing_error(safe_request_id, "unauthorized")

        if self._now() is None:
            return pairing_error(safe_request_id, "unavailable")

        try:
            outcome = self._service._revoke_device(safe_device_id)
        except Exception:
            return pairing_error(safe_request_id, "unavailable")

        if outcome.status is DeviceOutcomeStatus.OK:
            return pairing_update_revoked(safe_request_id, safe_device_id)

        # Missing, expired, and other failures collapse to the same safe code.
        if outcome.error in (
            DeviceErrorCode.NOT_FOUND,
            DeviceErrorCode.EXPIRED,
            DeviceErrorCode.INVALID_INPUT,
            DeviceErrorCode.REVOKED,
            DeviceErrorCode.UNAVAILABLE,
            None,
        ):
            return pairing_error(safe_request_id, "device_not_found")
        return pairing_error(safe_request_id, "unavailable")

    def expire_due(self) -> tuple[int, int]:
        """Expire due challenges and device sessions; return counts only."""
        try:
            return self._service.expire_due()
        except Exception:
            return (0, 0)

    def disconnect(self, device_id: object) -> bool:
        """Mark an exact issued device stale without returning identifiers."""
        try:
            safe_device_id = validate_device_id(device_id)
            outcome = self._service._mark_device_stale(safe_device_id)
        except Exception:
            return False
        return outcome.status is DeviceOutcomeStatus.OK
