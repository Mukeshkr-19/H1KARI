"""Pure, bounded daemon recovery state machine.

``DaemonRecoveryPolicy`` models restart recovery for the always-on voice
daemon without performing any side effects: it does not call ``launchctl``,
inspect processes, kill anything, acquire real locks, touch the filesystem, or
start the daemon.  Decisions it produces are content-free and suitable for a
later production wiring layer (Mira) to consume.

Guarantees:

- attempts are bounded inside a rolling time window,
- backoff is exponential with a hard maximum,
- a stable run resets the failure budget,
- a duplicate-instance or lease-denied event yields a no-restart decision,
- terminal failure latches the policy so a terminal capture/device failure can
  never create an infinite restart loop,
- only an explicit operator reset clears a terminal latch.

Attempt semantics are inclusive: ``max_attempts_in_window`` transient
failures are tolerated within the rolling window (each returns ``BACKOFF`` and
schedules a retry), and the very next transient failure beyond that maximum
latches the policy.  No restart is ever scheduled from a latched state, so the
policy can never restart the daemon more times than its configured budget.

States: ``stopped``, ``starting``, ``healthy``, ``degraded``, ``backoff``,
``latched_failed``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Deque, Optional
from collections import deque


class RecoveryState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BACKOFF = "backoff"
    LATCHED_FAILED = "latched_failed"


class RecoveryDecision(StrEnum):
    """Content-free decisions suitable for downstream wiring."""

    START = "start"
    RETRY_DELAYED = "retry_delayed"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BACKOFF = "backoff"
    LATCH = "latch"
    DUPLICATE_INSTANCE = "duplicate_instance"
    LEASE_DENIED = "lease_denied"
    OPERATOR_RESET = "operator_reset"
    IGNORED = "ignored"


class RecoveryReason(StrEnum):
    """Stable, content-free reason codes for recovery actions."""

    LATCHED = "latched"
    BACKOFF_NOT_ELAPSED = "backoff_not_elapsed"
    START_REQUESTED = "start_requested"
    STABLE_RUN_RESET = "stable_run_reset"
    RECOVERED_WITHIN_WINDOW = "recovered_within_window"
    HEALTHY = "healthy"
    TERMINAL_FAILURE_LATCHED = "terminal_failure_latched"
    MAX_ATTEMPTS_EXCEEDED = "max_attempts_exceeded"
    TRANSIENT_FAILURE = "transient_failure"
    DUPLICATE_INSTANCE = "duplicate_instance"
    LEASE_DENIED = "lease_denied"
    OPERATOR_RESET = "operator_reset"


class FailureKind(StrEnum):
    """Content-free failure classification.

    ``TRANSIENT`` failures count against the bounded retry budget and drive
    exponential backoff.  ``TERMINAL`` failures latch the policy immediately;
    a terminal capture/device failure can never cause an infinite restart loop.
    """

    TRANSIENT = "transient"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class RecoveryAction:
    """Content-free recovery decision record."""

    state: RecoveryState
    decision: RecoveryDecision
    attempts_in_window: int
    backoff_seconds: float
    next_attempt_at_ns: Optional[int]
    reason: RecoveryReason


class DaemonRecoveryPolicy:
    """Pure restart-recovery state machine (no external side effects)."""

    def __init__(
        self,
        *,
        max_attempts_in_window: int = 5,
        window_ns: int = 300_000_000_000,  # 5 minutes
        base_backoff_ns: int = 1_000_000_000,  # 1 second
        max_backoff_ns: int = 60_000_000_000,  # 60 seconds
        stable_run_ns: int = 120_000_000_000,  # 2 minutes
        clock: Optional[Callable[[], int]] = None,
    ) -> None:
        # Strict parameter validation: booleans are rejected (``bool`` is a
        # subclass of ``int``), non-integers are rejected, and all durations
        # must be positive.  Overlarge integers are accepted; Python integers
        # cannot overflow.
        if isinstance(max_attempts_in_window, bool) or not isinstance(max_attempts_in_window, int):
            raise TypeError("max_attempts_in_window must be an integer")
        if max_attempts_in_window < 1:
            raise ValueError("max_attempts_in_window must be >= 1")
        for label, value in (
            ("window_ns", window_ns),
            ("base_backoff_ns", base_backoff_ns),
            ("max_backoff_ns", max_backoff_ns),
            ("stable_run_ns", stable_run_ns),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{label} must be an integer")
            if value <= 0:
                raise ValueError(f"{label} must be positive")
        self._max_attempts = max_attempts_in_window
        self._window_ns = window_ns
        self._base_backoff_ns = base_backoff_ns
        self._max_backoff_ns = max_backoff_ns
        self._stable_run_ns = stable_run_ns
        self._clock: Callable[[], int] = clock if clock is not None else time.monotonic_ns

        # Internal content-free state.
        self._state: RecoveryState = RecoveryState.STOPPED
        self._attempt_times: Deque[int] = deque()
        self._next_attempt_at_ns: Optional[int] = None
        self._stable_since_ns: Optional[int] = None
        self._latch_reason: Optional[RecoveryReason] = None

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def state(self) -> RecoveryState:
        return self._state

    def snapshot(self, *, now_ns: Optional[int] = None) -> dict:
        """Content-free snapshot for diagnostics and later wiring."""
        now = self._clock() if now_ns is None else now_ns
        self._prune(now)
        return {
            "state": self._state.value,
            "attempts_in_window": len(self._attempt_times),
            "backoff_seconds": self._current_backoff_seconds(now),
            "next_attempt_at_ns": self._next_attempt_at_ns,
            "latched": self._state is RecoveryState.LATCHED_FAILED,
        }

    def is_latched(self) -> bool:
        return self._state is RecoveryState.LATCHED_FAILED

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def record_start(self, *, now_ns: Optional[int] = None) -> RecoveryAction:
        """The daemon has been asked to start (or retry)."""
        now = self._clock() if now_ns is None else now_ns
        self._prune(now)
        if self._state is RecoveryState.LATCHED_FAILED:
            return RecoveryAction(
                state=self._state,
                decision=RecoveryDecision.IGNORED,
                attempts_in_window=len(self._attempt_times),
                backoff_seconds=0.0,
                next_attempt_at_ns=None,
                reason=RecoveryReason.LATCHED,
            )
        if self._state is RecoveryState.BACKOFF and self._next_attempt_at_ns is not None:
            if now < self._next_attempt_at_ns:
                return RecoveryAction(
                    state=RecoveryState.BACKOFF,
                    decision=RecoveryDecision.RETRY_DELAYED,
                    attempts_in_window=len(self._attempt_times),
                    backoff_seconds=self._current_backoff_seconds(now),
                    next_attempt_at_ns=self._next_attempt_at_ns,
                    reason=RecoveryReason.BACKOFF_NOT_ELAPSED,
                )
        self._state = RecoveryState.STARTING
        self._stable_since_ns = None
        self._next_attempt_at_ns = None
        return RecoveryAction(
            state=RecoveryState.STARTING,
            decision=RecoveryDecision.START,
            attempts_in_window=len(self._attempt_times),
            backoff_seconds=0.0,
            next_attempt_at_ns=None,
            reason=RecoveryReason.START_REQUESTED,
        )

    def record_healthy(self, *, now_ns: Optional[int] = None) -> RecoveryAction:
        """The daemon reported healthy (startup succeeded)."""
        now = self._clock() if now_ns is None else now_ns
        self._prune(now)
        if self._state is RecoveryState.LATCHED_FAILED:
            return RecoveryAction(
                state=RecoveryState.LATCHED_FAILED,
                decision=RecoveryDecision.IGNORED,
                attempts_in_window=len(self._attempt_times),
                backoff_seconds=0.0,
                next_attempt_at_ns=None,
                reason=RecoveryReason.LATCHED,
            )
        if self._stable_since_ns is None:
            self._stable_since_ns = now

        if now - self._stable_since_ns >= self._stable_run_ns:
            # A stable run resets the failure budget.
            self._attempt_times.clear()
            self._next_attempt_at_ns = None
            self._state = RecoveryState.HEALTHY
            self._stable_since_ns = now
            return RecoveryAction(
                state=RecoveryState.HEALTHY,
                decision=RecoveryDecision.HEALTHY,
                attempts_in_window=0,
                backoff_seconds=0.0,
                next_attempt_at_ns=None,
                reason=RecoveryReason.STABLE_RUN_RESET,
            )

        if self._attempt_times:
            self._state = RecoveryState.DEGRADED
            self._next_attempt_at_ns = None
            return RecoveryAction(
                state=RecoveryState.DEGRADED,
                decision=RecoveryDecision.DEGRADED,
                attempts_in_window=len(self._attempt_times),
                backoff_seconds=0.0,
                next_attempt_at_ns=None,
                reason=RecoveryReason.RECOVERED_WITHIN_WINDOW,
            )
        self._state = RecoveryState.HEALTHY
        self._next_attempt_at_ns = None
        return RecoveryAction(
            state=RecoveryState.HEALTHY,
            decision=RecoveryDecision.HEALTHY,
            attempts_in_window=0,
            backoff_seconds=0.0,
            next_attempt_at_ns=None,
            reason=RecoveryReason.HEALTHY,
        )

    def record_failure(
        self, kind: FailureKind, *, now_ns: Optional[int] = None
    ) -> RecoveryAction:
        """Record a failure of the given kind.

        Terminal failures latch immediately; transient failures consume a
        bounded retry slot and schedule exponential backoff.
        """
        now = self._clock() if now_ns is None else now_ns
        if not isinstance(kind, FailureKind):
            raise TypeError("kind must be a FailureKind")
        self._prune(now)

        if self._state is RecoveryState.LATCHED_FAILED:
            # A latched policy absorbs further failures without mutation: a
            # terminal latch can only be cleared by an explicit operator reset.
            return RecoveryAction(
                state=self._state,
                decision=RecoveryDecision.IGNORED,
                attempts_in_window=len(self._attempt_times),
                backoff_seconds=0.0,
                next_attempt_at_ns=None,
                reason=RecoveryReason.LATCHED,
            )

        if kind is FailureKind.TERMINAL:
            self._state = RecoveryState.LATCHED_FAILED
            self._latch_reason = RecoveryReason.TERMINAL_FAILURE_LATCHED
            self._next_attempt_at_ns = None
            self._stable_since_ns = None
            return RecoveryAction(
                state=RecoveryState.LATCHED_FAILED,
                decision=RecoveryDecision.LATCH,
                attempts_in_window=len(self._attempt_times),
                backoff_seconds=0.0,
                next_attempt_at_ns=None,
                reason=RecoveryReason.TERMINAL_FAILURE_LATCHED,
            )

        # Transient failure.
        self._attempt_times.append(now)
        self._stable_since_ns = None
        self._next_attempt_at_ns = None
        if len(self._attempt_times) > self._max_attempts:
            self._state = RecoveryState.LATCHED_FAILED
            self._latch_reason = RecoveryReason.MAX_ATTEMPTS_EXCEEDED
            self._next_attempt_at_ns = None
            return RecoveryAction(
                state=RecoveryState.LATCHED_FAILED,
                decision=RecoveryDecision.LATCH,
                attempts_in_window=len(self._attempt_times),
                backoff_seconds=0.0,
                next_attempt_at_ns=None,
                reason=RecoveryReason.MAX_ATTEMPTS_EXCEEDED,
            )
        backoff_ns = self._exponential_backoff_ns(len(self._attempt_times))
        self._state = RecoveryState.BACKOFF
        self._next_attempt_at_ns = now + backoff_ns
        return RecoveryAction(
            state=RecoveryState.BACKOFF,
            decision=RecoveryDecision.BACKOFF,
            attempts_in_window=len(self._attempt_times),
            backoff_seconds=backoff_ns / 1_000_000_000,
            next_attempt_at_ns=self._next_attempt_at_ns,
            reason=RecoveryReason.TRANSIENT_FAILURE,
        )

    def record_duplicate_instance(self, *, now_ns: Optional[int] = None) -> RecoveryAction:
        """Another instance holds the lease; do not start a second daemon.

        This does not consume a retry slot and never schedules a restart.
        """
        now = self._clock() if now_ns is None else now_ns
        self._prune(now)
        if self._state is RecoveryState.LATCHED_FAILED:
            # A terminal latch is never cleared by a duplicate-instance event;
            # only an explicit operator reset clears it.
            return RecoveryAction(
                state=self._state,
                decision=RecoveryDecision.IGNORED,
                attempts_in_window=len(self._attempt_times),
                backoff_seconds=0.0,
                next_attempt_at_ns=None,
                reason=RecoveryReason.LATCHED,
            )
        self._state = RecoveryState.STOPPED
        self._next_attempt_at_ns = None
        return RecoveryAction(
            state=RecoveryState.STOPPED,
            decision=RecoveryDecision.DUPLICATE_INSTANCE,
            attempts_in_window=len(self._attempt_times),
            backoff_seconds=0.0,
            next_attempt_at_ns=None,
            reason=RecoveryReason.DUPLICATE_INSTANCE,
        )

    def record_lease_denied(self, *, now_ns: Optional[int] = None) -> RecoveryAction:
        """The daemon lease was denied; do not retry until operator reset."""
        now = self._clock() if now_ns is None else now_ns
        self._prune(now)
        if self._state is RecoveryState.LATCHED_FAILED:
            # A terminal latch is never cleared by a lease-denied event; only
            # an explicit operator reset clears it.
            return RecoveryAction(
                state=self._state,
                decision=RecoveryDecision.IGNORED,
                attempts_in_window=len(self._attempt_times),
                backoff_seconds=0.0,
                next_attempt_at_ns=None,
                reason=RecoveryReason.LATCHED,
            )
        self._state = RecoveryState.STOPPED
        self._next_attempt_at_ns = None
        return RecoveryAction(
            state=RecoveryState.STOPPED,
            decision=RecoveryDecision.LEASE_DENIED,
            attempts_in_window=len(self._attempt_times),
            backoff_seconds=0.0,
            next_attempt_at_ns=None,
            reason=RecoveryReason.LEASE_DENIED,
        )

    def operator_reset(self, *, now_ns: Optional[int] = None) -> RecoveryAction:
        """Explicit operator reset: clear the latch and all failure state."""
        now = self._clock() if now_ns is None else now_ns
        self._state = RecoveryState.STOPPED
        self._attempt_times.clear()
        self._next_attempt_at_ns = None
        self._stable_since_ns = None
        self._latch_reason = None
        return RecoveryAction(
            state=RecoveryState.STOPPED,
            decision=RecoveryDecision.OPERATOR_RESET,
            attempts_in_window=0,
            backoff_seconds=0.0,
            next_attempt_at_ns=None,
            reason=RecoveryReason.OPERATOR_RESET,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _prune(self, now: int) -> None:
        """Drop attempts outside the rolling window."""
        while self._attempt_times and now - self._attempt_times[0] > self._window_ns:
            self._attempt_times.popleft()

    def _exponential_backoff_ns(self, attempt_count: int) -> int:
        raw = self._base_backoff_ns * (2 ** (attempt_count - 1))
        return min(raw, self._max_backoff_ns)

    def _current_backoff_seconds(self, now: int) -> float:
        if self._next_attempt_at_ns is None:
            return 0.0
        remaining = self._next_attempt_at_ns - now
        return max(0, remaining) / 1_000_000_000
