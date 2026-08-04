"""Pure supervisor/lease boundary for a future voice daemon integration.

This module never invokes launchctl, starts a daemon, inspects processes, or
touches a lock file.  Lease ownership is injected and recovery decisions come
from the existing bounded ``DaemonRecoveryPolicy``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.voice_safety.daemon_recovery import (
    DaemonRecoveryPolicy,
    FailureKind,
    RecoveryAction,
)
from core.voice_session.contracts import validate_non_empty_str


class DaemonLeaseProtocol(Protocol):
    def try_acquire(self, instance_id: str) -> bool: ...
    def release(self, instance_id: str) -> bool: ...


@dataclass(frozen=True, repr=False)
class SupervisorLeaseSnapshot:
    held: bool
    instance_id: str

    def __repr__(self) -> str:
        return f"SupervisorLeaseSnapshot(held={self.held})"


class DaemonSupervisorBoundary:
    """Own exactly one injected lease and return content-free recovery actions."""

    def __init__(
        self,
        *,
        instance_id: str,
        lease: DaemonLeaseProtocol,
        recovery_policy: DaemonRecoveryPolicy,
    ) -> None:
        self._instance_id = validate_non_empty_str(instance_id, "instance_id")
        if not callable(getattr(lease, "try_acquire", None)) or not callable(
            getattr(lease, "release", None)
        ):
            raise TypeError("invalid daemon lease adapter")
        if not isinstance(recovery_policy, DaemonRecoveryPolicy):
            raise TypeError("recovery_policy must be a DaemonRecoveryPolicy")
        self._lease = lease
        self._policy = recovery_policy
        self._lease_held = False

    @property
    def lease_snapshot(self) -> SupervisorLeaseSnapshot:
        return SupervisorLeaseSnapshot(self._lease_held, self._instance_id)

    def request_start(self, *, now_ns: int) -> RecoveryAction:
        if not self._lease_held:
            try:
                acquired = self._lease.try_acquire(self._instance_id)
            except Exception:
                acquired = False
            if acquired is not True:
                return self._policy.record_lease_denied(now_ns=now_ns)
            self._lease_held = True
        return self._policy.record_start(now_ns=now_ns)

    def record_healthy(self, *, now_ns: int) -> RecoveryAction:
        if not self._lease_held:
            return self._policy.record_lease_denied(now_ns=now_ns)
        return self._policy.record_healthy(now_ns=now_ns)

    def record_failure(self, kind: FailureKind, *, now_ns: int) -> RecoveryAction:
        if not isinstance(kind, FailureKind):
            raise TypeError("kind must be a FailureKind")
        return self._policy.record_failure(kind, now_ns=now_ns)

    def release(self) -> bool:
        if not self._lease_held:
            return True
        try:
            released = self._lease.release(self._instance_id)
        except Exception:
            return False
        if released is True:
            self._lease_held = False
            return True
        return False


__all__ = [
    "DaemonLeaseProtocol",
    "DaemonSupervisorBoundary",
    "SupervisorLeaseSnapshot",
]
