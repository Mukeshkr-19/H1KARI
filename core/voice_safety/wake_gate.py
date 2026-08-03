"""Fail-closed wake acceptance gate.

The ``WakeSafetyGate`` decides whether a wake candidate may transition a
session from sleeping into an awaiting-command window.  It is a pure,
deterministic, local policy object: it never speaks, never invokes the
orchestrator, never starts playback, and never opens a microphone.  Its
strongest positive result is an accepted session-transition decision or an
optional non-spoken cue recommendation.

Fail-closed rules enforced here:

- A configured, calibrated confidence threshold is required.
- Wake names must match the configured wake name or an explicitly configured,
  exact alias.  Broad Kari/Carrie/Carry-style aliases are never enabled by
  default.
- Hotword-biased STT confidence is never treated as proof.
- Fresh VAD speech evidence is required.
- Candidate and observation timestamps must lie within a bounded window.
- Playback or echo suppression suppresses the candidate.
- Stale, future, replayed, duplicated, out-of-order, and over-capacity events
  are rejected.
- A cooldown is enforced after an accepted wake.
- Verified ownership is required when an enrolled verifier is available.
- An accepted wake opens exactly one bounded awaiting-command authorization
  (8-10 seconds).  A qualified command consumes that authorization exactly
  once; a second confirmation in the same window fails closed.  Expired or
  missing authorization can never manufacture an intent.
- Correlation uses the exact accepted session and wake event; no session id is
  ever fabricated.
- A same-utterance wake-and-command may be returned as correlated qualified
  intent, but the gate itself never calls Hikari.
- The optional confirmation cue defaults to non-spoken.
- When uncertain, the gate remains silent.
"""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Callable, Deque, Dict, FrozenSet, Optional, Tuple

from core.voice_safety.contracts import (
    AwaitingCommandDeadline,
    ConfirmationCue,
    CorrelatedIntent,
    InvalidCandidateError,
    OwnerVerificationState,
    SessionID,
    WakeCandidate,
    WakeDecision,
    WakeEvaluation,
    WakeEventID,
    WakeReason,
    _validate_name,
)

# Default awaiting-command window is between 8 and 10 seconds.
DEFAULT_AWAITING_COMMAND_NS = 9_000_000_000


class WakeSafetyGate:
    """Content-safe, fail-closed wake acceptance policy."""

    DEFAULT_WAKE_NAME = "Hikari"

    def __init__(
        self,
        *,
        wake_name: str = DEFAULT_WAKE_NAME,
        aliases: Tuple[str, ...] = (),
        confidence_threshold: Optional[float] = None,
        calibrated: bool = False,
        max_candidate_age_ns: int = 2_000_000_000,
        max_future_skew_ns: int = 250_000_000,
        max_candidate_observation_skew_ns: int = 750_000_000,
        max_candidate_vad_skew_ns: int = 1_500_000_000,
        max_vad_age_ns: int = 750_000_000,
        cooldown_ns: int = 3_000_000_000,
        awaiting_command_ns: int = DEFAULT_AWAITING_COMMAND_NS,
        confirmation_cue: ConfirmationCue = ConfirmationCue.NONE,
        duplicate_window_ns: int = 2_000_000_000,
        replay_memory_ns: int = 60_000_000_000,
        capacity_window_ns: int = 5_000_000_000,
        max_events_per_window: int = 8,
        max_replay_cache_size: int = 1024,
        require_verified_owner_when_available: bool = True,
        clock: Optional[Callable[[], int]] = None,
    ) -> None:
        """Configure the gate.

        Args:
            wake_name: Configured wake name (default "Hikari").
            aliases: Explicit, exact alias set.  Empty by default; broad
                Kari/Carrie/Carry aliases are never silently enabled.
            confidence_threshold: Calibrated threshold; required for acceptance.
            calibrated: Whether calibration has succeeded.  ``False`` fails
                closed to ``not_calibrated``.
            max_candidate_age_ns: Max age of a candidate timestamp.
            max_future_skew_ns: Allowed future skew for a candidate timestamp.
            max_candidate_observation_skew_ns: Max skew between candidate and
                observation timestamps.
            max_candidate_vad_skew_ns: Max skew between candidate and VAD
                evidence timestamps.
            max_vad_age_ns: Max age of VAD speech evidence.
            cooldown_ns: Cooldown after an accepted wake.
            awaiting_command_ns: Bounded awaiting-command window (default 9s).
            confirmation_cue: Optional non-spoken cue (default NONE).
            duplicate_window_ns: Window in which a repeated event id is a
                duplicate; older repeats are replays.
            replay_memory_ns: How long event ids are remembered.
            capacity_window_ns: Rolling window for capacity accounting.
            max_events_per_window: Max events admitted per rolling window.
                The internal timestamp deque is hard-bounded to this value:
                over-capacity events are rejected WITHOUT being inserted, so a
                flood of rejected events can never grow memory unboundedly.
            max_replay_cache_size: Hard maximum number of live event ids kept
                for duplicate/replay detection.  When the cache is full of
                unexpired ids, new unique events fail closed with
                ``replay_cache_full``; live ids are never evicted.
            require_verified_owner_when_available: If True, an enrolled,
                available verifier must return VERIFIED; unavailable fails
                closed to ``owner_verification_required``.
            clock: Callable returning monotonic ns (defaults to
                ``time.monotonic_ns``).
        """
        # Strictly validate every configurable parameter (fail closed on
        # malformed configuration; booleans are rejected even though ``bool``
        # subclasses ``int``).  The wake name and aliases use the same bounded
        # exact-form normalization as candidates.
        canonical_folded = _validate_name(wake_name, name="wake_name")
        self._wake_name = wake_name.strip()

        if isinstance(aliases, str):
            raise TypeError("aliases must be a sequence of strings, not a single string")
        folded_aliases = set()
        for alias in aliases:
            folded = _validate_name(alias, name="alias")
            if folded in folded_aliases:
                raise ValueError("aliases must not contain duplicates after normalization")
            if folded == canonical_folded:
                raise ValueError("an alias must not equal the canonical wake name")
            folded_aliases.add(folded)
        self._aliases: FrozenSet[str] = frozenset(folded_aliases)
        self._all_names: FrozenSet[str] = frozenset({canonical_folded} | folded_aliases)

        if confidence_threshold is not None:
            if isinstance(confidence_threshold, bool) or not isinstance(
                confidence_threshold, (int, float)
            ):
                raise TypeError("confidence_threshold must be a number")
            threshold = float(confidence_threshold)
            if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
                raise ValueError("confidence_threshold must be within [0.0, 1.0]")
            self._confidence_threshold = threshold
        else:
            self._confidence_threshold = None
        if not isinstance(calibrated, bool):
            raise TypeError("calibrated must be a boolean")
        self._calibrated = calibrated

        for label, value in (
            ("max_candidate_age_ns", max_candidate_age_ns),
            ("max_future_skew_ns", max_future_skew_ns),
            ("max_candidate_observation_skew_ns", max_candidate_observation_skew_ns),
            ("max_candidate_vad_skew_ns", max_candidate_vad_skew_ns),
            ("max_vad_age_ns", max_vad_age_ns),
            ("cooldown_ns", cooldown_ns),
            ("awaiting_command_ns", awaiting_command_ns),
            ("duplicate_window_ns", duplicate_window_ns),
            ("replay_memory_ns", replay_memory_ns),
            ("capacity_window_ns", capacity_window_ns),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{label} must be an integer")
            if value < 0:
                raise ValueError(f"{label} must be a non-negative integer")
        self._max_candidate_age_ns = max_candidate_age_ns
        self._max_future_skew_ns = max_future_skew_ns
        self._max_candidate_observation_skew_ns = max_candidate_observation_skew_ns
        self._max_candidate_vad_skew_ns = max_candidate_vad_skew_ns
        self._max_vad_age_ns = max_vad_age_ns
        self._cooldown_ns = cooldown_ns
        if not 8_000_000_000 <= awaiting_command_ns <= 10_000_000_000:
            raise ValueError("awaiting_command_ns must be between 8 and 10 seconds")
        self._awaiting_command_ns = awaiting_command_ns
        if not isinstance(confirmation_cue, ConfirmationCue):
            raise TypeError("confirmation_cue must be a ConfirmationCue")
        self._confirmation_cue = confirmation_cue
        self._duplicate_window_ns = duplicate_window_ns
        self._replay_memory_ns = replay_memory_ns
        self._capacity_window_ns = capacity_window_ns
        if isinstance(max_events_per_window, bool) or not isinstance(max_events_per_window, int):
            raise TypeError("max_events_per_window must be an integer")
        if max_events_per_window < 1:
            raise ValueError("max_events_per_window must be >= 1")
        self._max_events_per_window = max_events_per_window
        if isinstance(max_replay_cache_size, bool) or not isinstance(max_replay_cache_size, int):
            raise TypeError("max_replay_cache_size must be an integer")
        if max_replay_cache_size < 1:
            raise ValueError("max_replay_cache_size must be >= 1")
        self._max_replay_cache_size = max_replay_cache_size
        if not isinstance(require_verified_owner_when_available, bool):
            raise TypeError("require_verified_owner_when_available must be a boolean")
        self._require_verified_owner = require_verified_owner_when_available

        self._clock: Callable[[], int] = clock if clock is not None else time.monotonic_ns

        # Internal policy state (content-free, in-memory only).
        self._cooldown_until_ns: int = 0
        self._awaiting_command_deadline_ns: Optional[int] = None
        self._command_consumed: bool = False
        self._last_candidate_ts_ns: Optional[int] = None
        self._authorized_session: Optional[SessionID] = None
        self._authorized_event: Optional[WakeEventID] = None
        self._seen_event_ids: Dict[str, int] = {}  # event_id -> first_seen_ns
        self._event_times: Deque[int] = deque()
        self._intent_counter: int = 0

    # ------------------------------------------------------------------
    # Configuration introspection
    # ------------------------------------------------------------------

    @property
    def wake_name(self) -> str:
        return self._wake_name

    @property
    def aliases(self) -> Tuple[str, ...]:
        return tuple(sorted(self._aliases))

    @property
    def calibrated(self) -> bool:
        return self._calibrated

    @property
    def confidence_threshold(self) -> Optional[float]:
        return self._confidence_threshold

    @property
    def awaiting_command_window_ns(self) -> int:
        return self._awaiting_command_ns

    @property
    def confirmation_cue(self) -> ConfirmationCue:
        return self._confirmation_cue

    @property
    def awaiting_command_deadline_ns(self) -> Optional[int]:
        """Remaining awaiting-command deadline, or None when sleeping."""
        return self._awaiting_command_deadline_ns

    def is_sleeping(self) -> bool:
        return self._awaiting_command_deadline_ns is None

    def is_authorization_available(self, *, now_ns: Optional[int] = None) -> bool:
        """True only while a single command authorization is unexpired and
        unspent.  Consuming the authorization (or its expiry) makes this False
        even though the raw window timestamp may still be set."""
        now = self._clock() if now_ns is None else now_ns
        if self._awaiting_command_deadline_ns is None:
            return False
        if self._command_consumed:
            return False
        if now >= self._awaiting_command_deadline_ns:
            return False
        return self._authorized_session is not None and self._authorized_event is not None

    # ------------------------------------------------------------------
    # Core evaluation
    # ------------------------------------------------------------------

    def evaluate(self, candidate: WakeCandidate, *, now_ns: Optional[int] = None) -> WakeEvaluation:
        """Evaluate one wake candidate.

        Always returns an evaluation; never raises for a rejected candidate.
        ``now_ns`` is optional for deterministic tests and defaults to the
        configured clock.
        """
        now = self._clock() if now_ns is None else now_ns
        if not isinstance(candidate, WakeCandidate):
            raise InvalidCandidateError("evaluate() requires a WakeCandidate")

        if not self._calibrated or self._confidence_threshold is None:
            return self._reject(WakeDecision.NOT_CALIBRATED, WakeReason.THRESHOLD_NOT_CALIBRATED)

        # Exact wake-name match against the configured name or explicit aliases.
        name_key = candidate.wake_name.casefold()
        if name_key not in self._all_names:
            return self._reject(WakeDecision.ALIAS_REJECTED, WakeReason.NAME_NOT_CONFIGURED)

        # Timestamp sanity: future, stale, and out-of-order candidates.
        candidate_ts = candidate.candidate_timestamp.value_ns
        if candidate_ts > now + self._max_future_skew_ns:
            return self._reject(WakeDecision.FUTURE_TIMESTAMP, WakeReason.CANDIDATE_IN_FUTURE)
        if candidate_ts < now - self._max_candidate_age_ns:
            return self._reject(WakeDecision.INVALID, WakeReason.CANDIDATE_STALE)
        if self._last_candidate_ts_ns is not None and candidate_ts < self._last_candidate_ts_ns:
            return self._reject(WakeDecision.INVALID, WakeReason.OUT_OF_ORDER)
        self._last_candidate_ts_ns = candidate_ts

        # Bounded candidate/observation window.
        observation_ts = candidate.observation_timestamp.value_ns
        if abs(candidate_ts - observation_ts) > self._max_candidate_observation_skew_ns:
            return self._reject(WakeDecision.INVALID, WakeReason.TIMESTAMP_SKEW)

        # Fresh VAD speech evidence.
        vad_ts = candidate.vad_evidence_timestamp.value_ns
        if not candidate.vad_has_speech:
            return self._reject(WakeDecision.VAD_MISSING, WakeReason.NO_VAD_SPEECH)
        if vad_ts > now or vad_ts < now - self._max_vad_age_ns:
            return self._reject(WakeDecision.VAD_STALE, WakeReason.VAD_EVIDENCE_STALE)
        # Bounded skew between the candidate timestamp and the VAD evidence.
        if abs(candidate_ts - vad_ts) > self._max_candidate_vad_skew_ns:
            return self._reject(WakeDecision.VAD_STALE, WakeReason.VAD_TIMESTAMP_SKEW)

        # Playback / echo suppression.
        if candidate.playback.is_playing or candidate.playback.echo_suppression_active:
            return self._reject(WakeDecision.PLAYBACK_SUPPRESSED, WakeReason.PLAYBACK_OR_ECHO_ACTIVE)

        # Owner verification is fail-closed.  A definitive REJECTED verdict is
        # always fatal; every other non-VERIFIED state fails closed whenever
        # the configured policy requires verified ownership.
        state = candidate.owner_verification.state
        if state is OwnerVerificationState.REJECTED:
            return self._reject(WakeDecision.OWNER_REJECTED, WakeReason.OWNER_VERIFICATION_REJECTED)
        if self._require_verified_owner and state is not OwnerVerificationState.VERIFIED:
            return self._reject(
                WakeDecision.OWNER_VERIFICATION_REQUIRED, WakeReason.OWNER_VERIFICATION_UNAVAILABLE
            )

        # Hotword-biased STT confidence is never proof.
        if candidate.confidence_is_hotword_bias:
            return self._reject(WakeDecision.CONFIDENCE_LOW, WakeReason.HOTWORD_BIASED_CONFIDENCE)

        # Configured calibrated threshold.
        if candidate.confidence.value < self._confidence_threshold:
            return self._reject(WakeDecision.CONFIDENCE_LOW, WakeReason.BELOW_CALIBRATED_THRESHOLD)

        # Duplicate / replay detection with a hard-bounded replay cache runs
        # BEFORE the rolling-capacity gate so that an already-retained event
        # keeps its meaningful duplicate/replay classification even under
        # capacity pressure (lookup is non-mutating).  Ids older than the
        # replay memory window are pruned first so the cache is time-bounded
        # AND size-bounded.  A live (unexpired) id is never evicted to make
        # room; when the cache is full of live ids a new unique event fails
        # closed with ``replay_cache_full``.
        cutoff_ns = now - self._replay_memory_ns
        for key in [k for k, ts in self._seen_event_ids.items() if ts < cutoff_ns]:
            del self._seen_event_ids[key]
        event_key = candidate.event_id.value
        first_seen = self._seen_event_ids.get(event_key)
        if first_seen is not None:
            age_ns = now - first_seen
            if age_ns <= self._duplicate_window_ns:
                return self._reject(WakeDecision.DUPLICATE, WakeReason.DUPLICATE_EVENT)
            return self._reject(WakeDecision.REPLAY, WakeReason.REPLAYED_EVENT)

        # Rolling capacity within a bounded window.  Expired timestamps are
        # pruned first; when the window already holds ``max_events_per_window``
        # admitted events, the new event is rejected WITHOUT being inserted,
        # so the timestamp deque can never exceed ``max_events_per_window``
        # even under an adversarial flood of otherwise-valid unique events.
        while self._event_times and now - self._event_times[0] > self._capacity_window_ns:
            self._event_times.popleft()
        if len(self._event_times) >= self._max_events_per_window:
            return self._reject(WakeDecision.CAPACITY_EXCEEDED, WakeReason.CAPACITY_EXCEEDED)

        # The replay cache has its own hard cap; a full cache fails closed
        # without evicting live ids and without admitting the new event.
        if len(self._seen_event_ids) >= self._max_replay_cache_size:
            return self._reject(WakeDecision.REPLAY_CACHE_FULL, WakeReason.REPLAY_CACHE_FULL)

        # Admit the event into the rolling window and the replay cache exactly
        # once; every admitted event consumes one capacity slot.  Cooldown is
        # deliberately evaluated AFTER admission (per the policy ordering), so
        # a cooldown-rejected event still consumed its capacity/replay slot;
        # both structures remain hard-bounded regardless.
        self._event_times.append(now)
        self._seen_event_ids[event_key] = now

        # Cooldown after an accepted wake.
        if now < self._cooldown_until_ns:
            return self._reject(WakeDecision.COOLDOWN, WakeReason.COOLDOWN_ACTIVE)

        # --- Accepted: exactly one bounded session authorization ----------
        self._cooldown_until_ns = now + self._cooldown_ns
        self._awaiting_command_deadline_ns = now + self._awaiting_command_ns
        self._command_consumed = False
        self._authorized_session = candidate.session_id
        self._authorized_event = candidate.event_id

        correlated_intent: Optional[CorrelatedIntent] = None
        if candidate.same_utterance_command:
            # A same-utterance command qualifies and consumes the single
            # authorization immediately; a later confirmation fails closed.
            correlated_intent = self._build_correlated_intent()
            self._command_consumed = True

        return WakeEvaluation(
            decision=WakeDecision.ACCEPTED,
            accepted=True,
            reason=WakeReason.ACCEPTED,
            awaiting_command_deadline_ns=self._awaiting_command_deadline_ns,
            cue=self._confirmation_cue,
            correlated_intent=correlated_intent,
        )

    # ------------------------------------------------------------------
    # Awaiting-command window
    # ------------------------------------------------------------------

    def command_window_status(self, *, now_ns: Optional[int] = None) -> AwaitingCommandDeadline:
        """Remaining awaiting-command window, expiring to sleeping."""
        now = self._clock() if now_ns is None else now_ns
        if self._awaiting_command_deadline_ns is None:
            return AwaitingCommandDeadline(deadline_ns=0, remaining_ns=0)
        remaining = self._awaiting_command_deadline_ns - now
        if remaining <= 0:
            # Expiry returns to sleeping and never authorizes later speech.
            self._clear_authorization()
            return AwaitingCommandDeadline(deadline_ns=0, remaining_ns=0)
        return AwaitingCommandDeadline(
            deadline_ns=self._awaiting_command_deadline_ns, remaining_ns=remaining
        )

    def is_within_awaiting_command_window(self, *, now_ns: Optional[int] = None) -> bool:
        status = self.command_window_status(now_ns=now_ns)
        return not status.expired and self._awaiting_command_deadline_ns is not None

    def expire_confirmation(self) -> None:
        """Explicitly expire the awaiting-command window (returns to sleeping)."""
        self._clear_authorization()

    def confirm_command(self, *, now_ns: Optional[int] = None) -> WakeEvaluation:
        """Qualify the single command authorized by the accepted wake.

        Exactly one command may be confirmed per accepted wake.  The first
        confirmation consumes the authorization and returns an intent
        correlated with the exact accepted session and wake event.  A second
        confirmation in the same window fails closed with
        ``AUTHORIZATION_CONSUMED``.  Expired or missing authorization can
        never manufacture an intent, and no session id is ever fabricated.
        """
        now = self._clock() if now_ns is None else now_ns
        if self._awaiting_command_deadline_ns is None:
            return self._reject(WakeDecision.INVALID, WakeReason.NO_AWAITING_COMMAND_WINDOW)
        if now >= self._awaiting_command_deadline_ns:
            self._clear_authorization()
            return self._reject(WakeDecision.CONFIRMATION_EXPIRED, WakeReason.AWAITING_COMMAND_EXPIRED)
        if self._command_consumed:
            return self._reject(WakeDecision.AUTHORIZATION_CONSUMED, WakeReason.AUTHORIZATION_CONSUMED)
        if self._authorized_session is None or self._authorized_event is None:
            # Correlation identifiers must exist for a real accepted wake;
            # without them no intent can be manufactured.
            return self._reject(WakeDecision.INVALID, WakeReason.CORRELATION_UNAVAILABLE)

        # Consume the single authorization exactly once.
        self._command_consumed = True
        correlated = self._build_correlated_intent()
        return WakeEvaluation(
            decision=WakeDecision.ACCEPTED,
            accepted=True,
            reason=WakeReason.WITHIN_AWAITING_COMMAND_WINDOW,
            awaiting_command_deadline_ns=self._awaiting_command_deadline_ns,
            cue=ConfirmationCue.NONE,
            correlated_intent=correlated,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_correlated_intent(self) -> CorrelatedIntent:
        """Build a correlated intent bound to the exact accepted identifiers.

        The generated intent id is a short, opaque, monotonic token that never
        embeds the raw wake event id; exact correlation is preserved through
        the typed ``session_id`` and ``wake_event_id`` fields.  The token stays
        well within ``MAX_IDENTIFIER_LENGTH``.
        """
        if self._authorized_session is None or self._authorized_event is None:
            raise InvalidCandidateError("cannot correlate intent without accepted identifiers")
        self._intent_counter += 1
        return CorrelatedIntent(
            intent_id=f"intent-{self._intent_counter}",
            session_id=self._authorized_session,
            wake_event_id=self._authorized_event,
            qualifies=True,
        )

    def _clear_authorization(self) -> None:
        self._awaiting_command_deadline_ns = None
        self._command_consumed = False
        self._authorized_session = None
        self._authorized_event = None

    def _reject(self, decision: WakeDecision, reason: WakeReason) -> WakeEvaluation:
        return WakeEvaluation(
            decision=decision,
            accepted=False,
            reason=reason,
            awaiting_command_deadline_ns=self._awaiting_command_deadline_ns,
            cue=ConfirmationCue.NONE,
            correlated_intent=None,
        )
