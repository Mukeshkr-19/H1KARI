from core.voice_safety.daemon_recovery import (
    DaemonRecoveryPolicy,
    FailureKind,
    RecoveryDecision,
    RecoveryState,
)
from core.voice_session.daemon_supervisor import DaemonSupervisorBoundary


class MemoryLease:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.owner: str | None = None

    def try_acquire(self, instance_id: str) -> bool:
        if not self.allowed or self.owner is not None:
            return False
        self.owner = instance_id
        return True

    def release(self, instance_id: str) -> bool:
        if self.owner != instance_id:
            return False
        self.owner = None
        return True


def test_supervisor_requires_lease_before_returning_start() -> None:
    denied = DaemonSupervisorBoundary(
        instance_id="voice_1",
        lease=MemoryLease(allowed=False),
        recovery_policy=DaemonRecoveryPolicy(clock=lambda: 0),
    )
    action = denied.request_start(now_ns=0)
    assert action.decision is RecoveryDecision.LEASE_DENIED
    assert denied.lease_snapshot.held is False


def test_supervisor_holds_one_lease_and_exposes_pure_bounded_recovery() -> None:
    lease = MemoryLease()
    supervisor = DaemonSupervisorBoundary(
        instance_id="voice_1",
        lease=lease,
        recovery_policy=DaemonRecoveryPolicy(
            max_attempts_in_window=1,
            base_backoff_ns=10,
            max_backoff_ns=10,
            window_ns=1_000,
            stable_run_ns=100,
            clock=lambda: 0,
        ),
    )
    assert supervisor.request_start(now_ns=0).decision is RecoveryDecision.START
    assert supervisor.lease_snapshot.held is True
    assert supervisor.record_healthy(now_ns=1).state is RecoveryState.HEALTHY
    first = supervisor.record_failure(FailureKind.TRANSIENT, now_ns=2)
    assert first.decision is RecoveryDecision.BACKOFF
    terminal = supervisor.record_failure(FailureKind.TERMINAL, now_ns=3)
    assert terminal.state is RecoveryState.LATCHED_FAILED
    assert supervisor.release() is True
    assert supervisor.lease_snapshot.held is False
