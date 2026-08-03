"""Focused tests for the pure daemon recovery state machine.

The policy performs no external I/O: it never calls ``launchctl``, inspects
processes, kills anything, acquires real locks, accesses disk, or starts the
daemon.  A deterministic fake clock drives all time-based logic.
"""

from __future__ import annotations

import pytest

from core.voice_safety.daemon_recovery import (
    DaemonRecoveryPolicy,
    FailureKind,
    RecoveryDecision,
    RecoveryState,
)


class FakeClock:
    def __init__(self, start_ns: int = 1_000_000_000_000) -> None:
        self._now_ns = start_ns

    def __call__(self) -> int:
        return self._now_ns

    def advance(self, ns: int) -> None:
        self._now_ns += ns


def _policy(**kwargs) -> DaemonRecoveryPolicy:
    clock = FakeClock()
    defaults = dict(
        max_attempts_in_window=5,
        window_ns=300_000_000_000,
        base_backoff_ns=1_000_000_000,
        max_backoff_ns=60_000_000_000,
        stable_run_ns=120_000_000_000,
    )
    defaults.update(kwargs)
    policy = DaemonRecoveryPolicy(**defaults, clock=clock)
    return policy


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


def test_initial_state_is_stopped_and_not_latched() -> None:
    policy = _policy()
    assert policy.state is RecoveryState.STOPPED
    assert policy.is_latched() is False


def test_start_transitions_to_starting() -> None:
    policy = _policy()
    action = policy.record_start()
    assert action.state is RecoveryState.STARTING
    assert action.decision is RecoveryDecision.START
    assert action.reason == "start_requested"


# ---------------------------------------------------------------------------
# Healthy and stable-run reset
# ---------------------------------------------------------------------------


def test_healthy_after_start() -> None:
    policy = _policy()
    policy.record_start()
    action = policy.record_healthy()
    assert action.state is RecoveryState.HEALTHY
    assert action.decision is RecoveryDecision.HEALTHY


def test_stable_run_resets_failure_budget() -> None:
    policy = _policy(stable_run_ns=120_000_000_000)
    policy.record_failure(FailureKind.TRANSIENT)
    policy.record_failure(FailureKind.TRANSIENT)
    policy.record_start()
    policy.record_healthy()
    # Remain in the window (not yet a stable run).
    clock = policy._clock
    clock.advance(121_000_000_000)
    action = policy.record_healthy()
    assert action.state is RecoveryState.HEALTHY
    assert action.decision is RecoveryDecision.HEALTHY
    assert action.reason == "stable_run_reset"
    assert action.attempts_in_window == 0


def test_recovered_within_window_is_degraded() -> None:
    policy = _policy(stable_run_ns=120_000_000_000)
    policy.record_failure(FailureKind.TRANSIENT)
    policy.record_start()
    clock = policy._clock
    clock.advance(1)
    action = policy.record_healthy()
    assert action.state is RecoveryState.DEGRADED
    assert action.decision is RecoveryDecision.DEGRADED


# ---------------------------------------------------------------------------
# Bounded attempts, backoff, and latch
# ---------------------------------------------------------------------------


def test_transient_failure_enters_backoff() -> None:
    policy = _policy()
    action = policy.record_failure(FailureKind.TRANSIENT)
    assert action.state is RecoveryState.BACKOFF
    assert action.decision is RecoveryDecision.BACKOFF
    assert action.attempts_in_window == 1
    assert action.backoff_seconds > 0
    assert action.next_attempt_at_ns is not None


def test_backoff_is_exponential() -> None:
    policy = _policy(base_backoff_ns=1_000_000_000, max_backoff_ns=60_000_000_000)
    first = policy.record_failure(FailureKind.TRANSIENT)
    second = policy.record_failure(FailureKind.TRANSIENT)
    third = policy.record_failure(FailureKind.TRANSIENT)
    assert second.backoff_seconds > first.backoff_seconds
    assert third.backoff_seconds > second.backoff_seconds


def test_backoff_is_capped_at_maximum() -> None:
    policy = _policy(base_backoff_ns=1_000_000_000, max_backoff_ns=4_000_000_000)
    durations = []
    for _ in range(8):
        action = policy.record_failure(FailureKind.TRANSIENT)
        durations.append(action.backoff_seconds)
        if action.state is RecoveryState.LATCHED_FAILED:
            break
    assert max(durations) <= 4.0


def test_attempts_are_bounded_in_window_and_latch() -> None:
    policy = _policy(max_attempts_in_window=3)
    # The budget allows max_attempts failures; the next one latches.
    for _ in range(3):
        action = policy.record_failure(FailureKind.TRANSIENT)
        assert action.state is RecoveryState.BACKOFF
    action = policy.record_failure(FailureKind.TRANSIENT)
    assert policy.is_latched() is True
    assert action.state is RecoveryState.LATCHED_FAILED
    assert action.decision is RecoveryDecision.LATCH
    assert action.reason == "max_attempts_exceeded"


def test_start_ignored_when_latched() -> None:
    policy = _policy(max_attempts_in_window=2)
    policy.record_failure(FailureKind.TRANSIENT)
    policy.record_failure(FailureKind.TRANSIENT)
    policy.record_failure(FailureKind.TRANSIENT)
    assert policy.is_latched() is True
    action = policy.record_start()
    assert action.decision is RecoveryDecision.IGNORED
    assert action.reason == "latched"
    # No restart is ever scheduled from a latched state.
    assert action.next_attempt_at_ns is None


def test_attempts_roll_out_of_window() -> None:
    policy = _policy(
        max_attempts_in_window=2,
        window_ns=300_000_000_000,
    )
    policy.record_failure(FailureKind.TRANSIENT)
    policy.record_failure(FailureKind.TRANSIENT)
    policy.record_failure(FailureKind.TRANSIENT)
    assert policy.is_latched() is True
    # After the rolling window elapses the budget is available again only after
    # an operator reset or a stable run; a latched terminal state cannot reset
    # on its own.
    clock = policy._clock
    clock.advance(301_000_000_000)
    assert policy.is_latched() is True


def test_terminal_failure_latches_immediately() -> None:
    policy = _policy()
    action = policy.record_failure(FailureKind.TERMINAL)
    assert policy.is_latched() is True
    assert action.state is RecoveryState.LATCHED_FAILED
    assert action.decision is RecoveryDecision.LATCH
    assert action.reason == "terminal_failure_latched"
    assert action.next_attempt_at_ns is None


def test_terminal_failure_cannot_create_infinite_restart_loop() -> None:
    policy = _policy()
    policy.record_failure(FailureKind.TERMINAL)
    for _ in range(20):
        action = policy.record_start()
        assert action.decision is RecoveryDecision.IGNORED
        assert policy.is_latched() is True


# ---------------------------------------------------------------------------
# Duplicate instance / lease denied
# ---------------------------------------------------------------------------


def test_duplicate_instance_does_not_consume_attempts() -> None:
    policy = _policy()
    action = policy.record_duplicate_instance()
    assert action.state is RecoveryState.STOPPED
    assert action.decision is RecoveryDecision.DUPLICATE_INSTANCE
    assert action.attempts_in_window == 0
    assert action.next_attempt_at_ns is None


def test_lease_denied_does_not_schedule_restart() -> None:
    policy = _policy()
    action = policy.record_lease_denied()
    assert action.decision is RecoveryDecision.LEASE_DENIED
    assert action.state is RecoveryState.STOPPED
    assert action.next_attempt_at_ns is None


# ---------------------------------------------------------------------------
# Operator reset
# ---------------------------------------------------------------------------


def test_operator_reset_clears_latch() -> None:
    policy = _policy(max_attempts_in_window=2)
    policy.record_failure(FailureKind.TRANSIENT)
    policy.record_failure(FailureKind.TRANSIENT)
    policy.record_failure(FailureKind.TRANSIENT)
    assert policy.is_latched() is True
    action = policy.operator_reset()
    assert action.decision is RecoveryDecision.OPERATOR_RESET
    assert action.state is RecoveryState.STOPPED
    assert policy.is_latched() is False
    assert action.attempts_in_window == 0
    # After an operator reset a fresh start is permitted.
    start = policy.record_start()
    assert start.decision is RecoveryDecision.START


def test_operator_reset_is_the_only_way_out_of_terminal_latch() -> None:
    policy = _policy()
    policy.record_failure(FailureKind.TERMINAL)
    # A stable run cannot clear a terminal latch.
    policy.record_healthy()
    assert policy.is_latched() is True
    policy.operator_reset()
    assert policy.is_latched() is False


# ---------------------------------------------------------------------------
# Content-safety and no-side-effects guarantees
# ---------------------------------------------------------------------------


def test_snapshot_is_content_free() -> None:
    policy = _policy()
    policy.record_failure(FailureKind.TRANSIENT)
    snapshot = policy.snapshot()
    assert set(snapshot) == {
        "state",
        "attempts_in_window",
        "backoff_seconds",
        "next_attempt_at_ns",
        "latched",
    }
    assert snapshot["state"] == RecoveryState.BACKOFF.value
    assert snapshot["attempts_in_window"] == 1
    assert snapshot["latched"] is False


def test_policy_exposes_no_external_side_effect_handles() -> None:
    policy = _policy()
    # The policy cannot launchctl, kill, lock, or start anything.
    for attr in ("launchctl", "kill", "acquire_lock", "start_daemon", "inspect_process"):
        assert not hasattr(policy, attr)


def test_action_reason_codes_are_stable_enums() -> None:
    from core.voice_safety.daemon_recovery import RecoveryReason

    policy = _policy()
    start = policy.record_start()
    assert isinstance(start.reason, RecoveryReason)
    assert start.reason == RecoveryReason.START_REQUESTED
    failure = policy.record_failure(FailureKind.TRANSIENT)
    assert isinstance(failure.reason, RecoveryReason)
    assert failure.reason == RecoveryReason.TRANSIENT_FAILURE


def test_stale_retry_deadline_cleared_after_recovery() -> None:
    policy = _policy()
    policy.record_failure(FailureKind.TRANSIENT)
    assert policy.snapshot()["next_attempt_at_ns"] is not None
    policy.record_start()
    policy.record_healthy()
    snapshot = policy.snapshot()
    assert snapshot["next_attempt_at_ns"] is None
    assert snapshot["state"] == RecoveryState.DEGRADED.value


# ---------------------------------------------------------------------------
# Boundary semantics: inclusive maximum, retry timing, pruning
# ---------------------------------------------------------------------------


def test_exactly_max_attempts_then_next_latches() -> None:
    policy = _policy(max_attempts_in_window=3)
    for _ in range(3):
        action = policy.record_failure(FailureKind.TRANSIENT)
        assert action.state is RecoveryState.BACKOFF
        assert policy.is_latched() is False
    # One beyond the configured maximum latches (no off-by-one extra restart).
    action = policy.record_failure(FailureKind.TRANSIENT)
    assert policy.is_latched() is True
    assert action.state is RecoveryState.LATCHED_FAILED
    assert action.decision is RecoveryDecision.LATCH


def test_retry_before_deadline_is_delayed() -> None:
    policy = _policy()
    policy.record_failure(FailureKind.TRANSIENT)
    action = policy.record_start()
    assert action.decision is RecoveryDecision.RETRY_DELAYED
    assert action.state is RecoveryState.BACKOFF
    assert action.reason == "backoff_not_elapsed"


def test_retry_at_deadline_is_allowed() -> None:
    policy = _policy()
    failure = policy.record_failure(FailureKind.TRANSIENT)
    assert failure.next_attempt_at_ns is not None
    clock = policy._clock
    clock.advance(failure.next_attempt_at_ns - clock())  # advance exactly to deadline
    action = policy.record_start()
    assert action.decision is RecoveryDecision.START
    assert action.state is RecoveryState.STARTING


def test_rolling_window_pruning() -> None:
    policy = _policy(max_attempts_in_window=2, window_ns=300_000_000_000)
    policy.record_failure(FailureKind.TRANSIENT)
    clock = policy._clock
    clock.advance(301_000_000_000)  # first attempt rolls out of the window
    action = policy.record_failure(FailureKind.TRANSIENT)
    assert action.attempts_in_window == 1
    assert action.state is RecoveryState.BACKOFF
    assert policy.is_latched() is False


def test_transient_failure_after_operator_reset_restarts_budget() -> None:
    policy = _policy(max_attempts_in_window=2)
    policy.record_failure(FailureKind.TRANSIENT)
    policy.record_failure(FailureKind.TRANSIENT)
    policy.record_failure(FailureKind.TRANSIENT)
    assert policy.is_latched() is True
    policy.operator_reset()
    action = policy.record_failure(FailureKind.TRANSIENT)
    assert action.state is RecoveryState.BACKOFF
    assert action.attempts_in_window == 1
    assert policy.is_latched() is False


def test_repeated_healthy_reports_stay_healthy() -> None:
    policy = _policy()
    policy.record_start()
    first = policy.record_healthy()
    assert first.state is RecoveryState.HEALTHY
    second = policy.record_healthy()
    assert second.state is RecoveryState.HEALTHY
    assert second.decision is RecoveryDecision.HEALTHY


def test_operator_reset_clears_stale_retry_deadline_and_latch_reason() -> None:
    policy = _policy(max_attempts_in_window=2)
    policy.record_failure(FailureKind.TRANSIENT)
    policy.record_failure(FailureKind.TRANSIENT)
    policy.record_failure(FailureKind.TRANSIENT)
    assert policy.is_latched() is True
    action = policy.operator_reset()
    assert action.decision is RecoveryDecision.OPERATOR_RESET
    assert action.attempts_in_window == 0
    assert action.next_attempt_at_ns is None
    assert policy.snapshot()["next_attempt_at_ns"] is None
    assert policy.is_latched() is False
    # The cleared latch means a fresh start is no longer ignored.
    start = policy.record_start()
    assert start.decision is RecoveryDecision.START


# ---------------------------------------------------------------------------
# Terminal latch is never cleared except by operator reset
# ---------------------------------------------------------------------------


def test_duplicate_instance_does_not_clear_terminal_latch() -> None:
    policy = _policy()
    policy.record_failure(FailureKind.TERMINAL)
    action = policy.record_duplicate_instance()
    assert action.decision is RecoveryDecision.IGNORED
    assert policy.is_latched() is True


def test_lease_denied_does_not_clear_terminal_latch() -> None:
    policy = _policy()
    policy.record_failure(FailureKind.TERMINAL)
    action = policy.record_lease_denied()
    assert action.decision is RecoveryDecision.IGNORED
    assert policy.is_latched() is True


def test_failure_while_latched_is_ignored() -> None:
    policy = _policy()
    policy.record_failure(FailureKind.TERMINAL)
    action = policy.record_failure(FailureKind.TRANSIENT)
    assert action.decision is RecoveryDecision.IGNORED
    assert action.reason == "latched"
    assert policy.is_latched() is True
    assert policy.snapshot()["next_attempt_at_ns"] is None
    assert policy.snapshot()["state"] == RecoveryState.LATCHED_FAILED.value


# ---------------------------------------------------------------------------
# Strict parameter validation
# ---------------------------------------------------------------------------


def test_policy_rejects_boolean_parameters() -> None:
    with pytest.raises(TypeError):
        DaemonRecoveryPolicy(max_attempts_in_window=True)
    with pytest.raises(TypeError):
        DaemonRecoveryPolicy(window_ns=False)
    with pytest.raises(TypeError):
        DaemonRecoveryPolicy(base_backoff_ns=True)


def test_policy_rejects_zero_and_negative_durations() -> None:
    with pytest.raises(ValueError):
        DaemonRecoveryPolicy(window_ns=0)
    with pytest.raises(ValueError):
        DaemonRecoveryPolicy(max_backoff_ns=-1)
    with pytest.raises(ValueError):
        DaemonRecoveryPolicy(stable_run_ns=0)
    with pytest.raises(ValueError):
        DaemonRecoveryPolicy(max_attempts_in_window=0)


def test_policy_accepts_overlarge_values_without_crashing() -> None:
    policy = DaemonRecoveryPolicy(
        max_attempts_in_window=10**6,
        window_ns=10**15,
        base_backoff_ns=10**12,
        max_backoff_ns=10**14,
        stable_run_ns=10**15,
        clock=FakeClock(),
    )
    action = policy.record_failure(FailureKind.TRANSIENT)
    assert action.state is RecoveryState.BACKOFF
    assert action.backoff_seconds > 0
