"""In-memory pairing challenge store with injected factories and digest-only secrets."""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

from core.pairing.contracts import (
    CHALLENGE_TTL_SECONDS,
    MAX_CONFIRMATION_ATTEMPTS,
    CancelChallengeOutcome,
    ConfirmChallengeOutcome,
    ContractValidationError,
    PairingChallenge,
    PairingChallengeState,
    PairingErrorCode,
    PairingOutcomeStatus,
    PrepareChallengeOutcome,
    validate_challenge_id,
    validate_code,
    validate_device_label,
    validate_request_id,
)


def _digest_code(*, digest_key: bytes, code: str) -> str:
    return hmac.new(digest_key, code.encode("ascii"), hashlib.sha256).hexdigest()


def _default_code_factory() -> str:
    return secrets.token_hex(5).upper()


@dataclass
class _MutableChallenge:
    challenge_id: str
    request_id: str
    state: PairingChallengeState
    digest: str
    attempts: int
    created_at: float
    expires_at: float
    device_label: Optional[str] = None


class PairingChallengeStore:
    """Transport-independent challenge store with one active challenge per request."""

    def __init__(
        self,
        *,
        clock: Callable[[], float],
        challenge_id_factory: Callable[[], str],
        secret_code_factory: Callable[[], str] | None = None,
        digest_key: bytes,
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not callable(challenge_id_factory):
            raise TypeError("challenge_id_factory must be callable")
        if not isinstance(digest_key, (bytes, bytearray)) or not digest_key:
            raise TypeError("digest_key must be non-empty bytes")
        self._clock = clock
        self._challenge_id_factory = challenge_id_factory
        self._secret_code_factory = secret_code_factory or _default_code_factory
        self._digest_key = bytes(digest_key)
        self._lock = threading.Lock()
        self._by_challenge_id: dict[str, _MutableChallenge] = {}
        self._active_request_id: dict[str, str] = {}

    def _now(self) -> float:
        try:
            value = self._clock()
        except Exception:
            raise ContractValidationError("clock unavailable") from None
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ContractValidationError("clock returned an invalid timestamp")
        if not math.isfinite(float(value)):
            raise ContractValidationError("clock returned an invalid timestamp")
        return float(value)

    def _new_challenge_id(self) -> str:
        try:
            value = self._challenge_id_factory()
        except Exception:
            raise ContractValidationError("challenge id factory failed") from None
        return validate_challenge_id(value)

    def _new_secret_code(self) -> str:
        try:
            value = self._secret_code_factory()
        except Exception:
            raise ContractValidationError("secret code factory failed") from None
        return validate_code(value)

    def _snapshot(self, record: _MutableChallenge) -> PairingChallenge:
        return PairingChallenge(
            challenge_id=record.challenge_id,
            request_id=record.request_id,
            state=record.state,
            digest=record.digest,
            attempts=record.attempts,
            created_at=record.created_at,
            expires_at=record.expires_at,
            device_label=record.device_label,
        )

    def _expire_if_due_locked(self, record: _MutableChallenge, now: float) -> None:
        if (
            record.state is PairingChallengeState.PENDING
            and now >= record.expires_at
        ):
            record.state = PairingChallengeState.EXPIRED

    def prepare(
        self, request_id: str, device_label: str | None = None
    ) -> PrepareChallengeOutcome:
        try:
            request_id = validate_request_id(request_id)
            label = validate_device_label(device_label)
        except ContractValidationError:
            return PrepareChallengeOutcome(
                status=PairingOutcomeStatus.ERROR,
                error=PairingErrorCode.INVALID_INPUT,
            )

        try:
            now = self._now()
        except ContractValidationError:
            return PrepareChallengeOutcome(
                status=PairingOutcomeStatus.ERROR,
                error=PairingErrorCode.UNAVAILABLE,
            )

        with self._lock:
            existing_id = self._active_request_id.get(request_id)
            if existing_id is not None:
                existing = self._by_challenge_id.get(existing_id)
                if existing is not None:
                    self._expire_if_due_locked(existing, now)
                    if existing.state is PairingChallengeState.PENDING:
                        return PrepareChallengeOutcome(
                            status=PairingOutcomeStatus.OK,
                            challenge_id=existing.challenge_id,
                        )
                    self._active_request_id.pop(request_id, None)

            try:
                challenge_id = self._new_challenge_id()
                code = self._new_secret_code()
            except ContractValidationError:
                return PrepareChallengeOutcome(
                    status=PairingOutcomeStatus.ERROR,
                    error=PairingErrorCode.UNAVAILABLE,
                )
            if challenge_id in self._by_challenge_id:
                return PrepareChallengeOutcome(
                    status=PairingOutcomeStatus.ERROR,
                    error=PairingErrorCode.UNAVAILABLE,
                )

            record = _MutableChallenge(
                challenge_id=challenge_id,
                request_id=request_id,
                state=PairingChallengeState.PENDING,
                digest=_digest_code(digest_key=self._digest_key, code=code),
                attempts=0,
                created_at=now,
                expires_at=now + CHALLENGE_TTL_SECONDS,
                device_label=label,
            )
            self._by_challenge_id[challenge_id] = record
            self._active_request_id[request_id] = challenge_id
            return PrepareChallengeOutcome(
                status=PairingOutcomeStatus.OK,
                challenge_id=challenge_id,
                code=code,
            )

    def confirm(
        self, request_id: str, challenge_id: str, code: str
    ) -> ConfirmChallengeOutcome:
        try:
            request_id = validate_request_id(request_id)
            challenge_id = validate_challenge_id(challenge_id)
            code = validate_code(code)
        except ContractValidationError:
            return ConfirmChallengeOutcome(
                status=PairingOutcomeStatus.ERROR,
                error=PairingErrorCode.INVALID_INPUT,
            )

        try:
            now = self._now()
        except ContractValidationError:
            return ConfirmChallengeOutcome(
                status=PairingOutcomeStatus.ERROR,
                error=PairingErrorCode.UNAVAILABLE,
            )

        with self._lock:
            record = self._by_challenge_id.get(challenge_id)
            if record is None or record.request_id != request_id:
                return ConfirmChallengeOutcome(
                    status=PairingOutcomeStatus.ERROR,
                    error=PairingErrorCode.NOT_FOUND,
                )

            self._expire_if_due_locked(record, now)

            if record.state is PairingChallengeState.CONSUMED:
                return ConfirmChallengeOutcome(
                    status=PairingOutcomeStatus.ERROR,
                    error=PairingErrorCode.ALREADY_CONSUMED,
                )
            if record.state is PairingChallengeState.LOCKED:
                return ConfirmChallengeOutcome(
                    status=PairingOutcomeStatus.ERROR,
                    error=PairingErrorCode.LOCKED,
                )
            if record.state is PairingChallengeState.CANCELLED:
                return ConfirmChallengeOutcome(
                    status=PairingOutcomeStatus.ERROR,
                    error=PairingErrorCode.CANCELLED,
                )
            if record.state is PairingChallengeState.EXPIRED:
                return ConfirmChallengeOutcome(
                    status=PairingOutcomeStatus.ERROR,
                    error=PairingErrorCode.EXPIRED,
                )
            if record.state is not PairingChallengeState.PENDING:
                return ConfirmChallengeOutcome(
                    status=PairingOutcomeStatus.ERROR,
                    error=PairingErrorCode.UNAVAILABLE,
                )

            candidate = _digest_code(digest_key=self._digest_key, code=code)
            if hmac.compare_digest(candidate, record.digest):
                record.state = PairingChallengeState.CONSUMED
                self._active_request_id.pop(request_id, None)
                return ConfirmChallengeOutcome(status=PairingOutcomeStatus.OK)

            record.attempts += 1
            if record.attempts >= MAX_CONFIRMATION_ATTEMPTS:
                record.state = PairingChallengeState.LOCKED
                self._active_request_id.pop(request_id, None)
                return ConfirmChallengeOutcome(
                    status=PairingOutcomeStatus.ERROR,
                    error=PairingErrorCode.LOCKED,
                )

            remaining = MAX_CONFIRMATION_ATTEMPTS - record.attempts
            return ConfirmChallengeOutcome(
                status=PairingOutcomeStatus.ERROR,
                error=PairingErrorCode.WRONG_CODE,
                attempts_remaining=remaining,
            )

    def cancel(self, challenge_id: str) -> CancelChallengeOutcome:
        try:
            challenge_id = validate_challenge_id(challenge_id)
        except ContractValidationError:
            return CancelChallengeOutcome(
                status=PairingOutcomeStatus.ERROR,
                error=PairingErrorCode.INVALID_INPUT,
            )

        try:
            now = self._now()
        except ContractValidationError:
            return CancelChallengeOutcome(
                status=PairingOutcomeStatus.ERROR,
                error=PairingErrorCode.UNAVAILABLE,
            )

        with self._lock:
            record = self._by_challenge_id.get(challenge_id)
            if record is None:
                return CancelChallengeOutcome(
                    status=PairingOutcomeStatus.ERROR,
                    error=PairingErrorCode.NOT_FOUND,
                )

            self._expire_if_due_locked(record, now)

            if record.state is PairingChallengeState.CANCELLED:
                return CancelChallengeOutcome(status=PairingOutcomeStatus.OK)
            if record.state is not PairingChallengeState.PENDING:
                return CancelChallengeOutcome(
                    status=PairingOutcomeStatus.ERROR,
                    error=PairingErrorCode.UNAVAILABLE,
                )

            record.state = PairingChallengeState.CANCELLED
            self._active_request_id.pop(record.request_id, None)
            return CancelChallengeOutcome(status=PairingOutcomeStatus.OK)

    def expire_due(self) -> int:
        try:
            now = self._now()
        except ContractValidationError:
            return 0

        expired = 0
        with self._lock:
            for record in self._by_challenge_id.values():
                if (
                    record.state is PairingChallengeState.PENDING
                    and now >= record.expires_at
                ):
                    record.state = PairingChallengeState.EXPIRED
                    self._active_request_id.pop(record.request_id, None)
                    expired += 1
        return expired

    def get_challenge(self, challenge_id: str) -> Optional[PairingChallenge]:
        """Test/introspection helper returning a safe snapshot without the secret."""
        try:
            challenge_id = validate_challenge_id(challenge_id)
        except ContractValidationError:
            return None
        with self._lock:
            record = self._by_challenge_id.get(challenge_id)
            if record is None:
                return None
            try:
                now = self._now()
            except ContractValidationError:
                return None
            self._expire_if_due_locked(record, now)
            return self._snapshot(record)
