"""Pairing service coordinating challenge consumption and device-session issuance."""

from __future__ import annotations

from core.pairing.challenge_store import PairingChallengeStore
from core.pairing.contracts import (
    CancelChallengeOutcome,
    DeviceOutcomeStatus,
    PairingConfirmOutcome,
    PairingErrorCode,
    PairingOutcomeStatus,
    PairingPrepareOutcome,
    PairingChallenge,
    DeviceSessionRecord,
    DeviceMutationOutcome,
)
from core.pairing.device_store import DeviceSessionStore


_FORBIDDEN_SERVICE_FIELDS = frozenset(
    {
        "actor",
        "actor_id",
        "session",
        "session_id",
        "approval",
        "approval_id",
        "grant",
        "execution",
        "execution_token",
    }
)


class PairingService:
    """Transport-independent pairing coordination with fixed public outcomes."""

    def __init__(
        self,
        *,
        challenge_store: PairingChallengeStore,
        device_store: DeviceSessionStore,
    ) -> None:
        if not isinstance(challenge_store, PairingChallengeStore):
            raise TypeError("challenge_store must be a PairingChallengeStore")
        if not isinstance(device_store, DeviceSessionStore):
            raise TypeError("device_store must be a DeviceSessionStore")
        self._challenge_store = challenge_store
        self._device_store = device_store

    @staticmethod
    def _reject_forbidden_fields(fields: dict[str, object]) -> bool:
        return any(key in _FORBIDDEN_SERVICE_FIELDS for key in fields)

    def prepare(
        self,
        request_id: str,
        *,
        device_label: str | None = None,
        **fields: object,
    ) -> PairingPrepareOutcome:
        if self._reject_forbidden_fields(fields):
            return PairingPrepareOutcome(
                status=PairingOutcomeStatus.ERROR,
                error=PairingErrorCode.INVALID_INPUT,
            )
        outcome = self._challenge_store.prepare(request_id, device_label)
        return PairingPrepareOutcome(
            status=outcome.status,
            challenge_id=outcome.challenge_id,
            code=outcome.code,
            error=outcome.error,
        )

    def confirm(
        self,
        request_id: str,
        challenge_id: str,
        code: str,
        **fields: object,
    ) -> PairingConfirmOutcome:
        if self._reject_forbidden_fields(fields):
            return PairingConfirmOutcome(
                status=PairingOutcomeStatus.ERROR,
                error=PairingErrorCode.INVALID_INPUT,
            )

        outcome = self._challenge_store.confirm(request_id, challenge_id, code)
        if outcome.status is not PairingOutcomeStatus.OK:
            return PairingConfirmOutcome(
                status=outcome.status,
                error=outcome.error,
                attempts_remaining=outcome.attempts_remaining,
            )

        snapshot = self._challenge_store.get_challenge(challenge_id)
        issue = self._device_store.issue(
            challenge_id=challenge_id,
            device_label=snapshot.device_label if snapshot is not None else None,
        )
        if issue.status is not DeviceOutcomeStatus.OK or issue.device_id is None:
            return PairingConfirmOutcome(
                status=PairingOutcomeStatus.ERROR,
                error=PairingErrorCode.UNAVAILABLE,
            )
        return PairingConfirmOutcome(
            status=PairingOutcomeStatus.OK,
            device_id=issue.device_id,
        )

    def cancel(self, challenge_id: str, **fields: object) -> CancelChallengeOutcome:
        if self._reject_forbidden_fields(fields):
            return CancelChallengeOutcome(
                status=PairingOutcomeStatus.ERROR,
                error=PairingErrorCode.INVALID_INPUT,
            )
        return self._challenge_store.cancel(challenge_id)

    def _get_challenge(self, challenge_id: str) -> PairingChallenge | None:
        """Return one bounded challenge snapshot for runtime correlation."""
        return self._challenge_store.get_challenge(challenge_id)

    def _get_device(self, device_id: str) -> DeviceSessionRecord | None:
        """Return one device-session snapshot without exposing store internals."""
        return self._device_store.get_record(device_id)

    def _revoke_device(self, device_id: str) -> DeviceMutationOutcome:
        """Revoke one server-generated device identifier."""
        return self._device_store.revoke(device_id)

    def _mark_device_stale(self, device_id: str) -> DeviceMutationOutcome:
        """Mark one issued device stale for runtime disconnect cleanup."""
        return self._device_store.mark_stale(device_id)

    def expire_due(self) -> tuple[int, int]:
        return (
            self._challenge_store.expire_due(),
            self._device_store.expire_due(),
        )
