"""Focused tests for the fail-closed wake safety gate.

These tests perform no external I/O: no microphone, network, filesystem, or
subprocess access.  A deterministic fake clock drives all time-based checks.
"""

from __future__ import annotations

import pytest

from core.voice_safety.contracts import (
    MAX_IDENTIFIER_LENGTH,
    AwaitingCommandDeadline,
    BoundedConfidence,
    BoundedQualityScore,
    CandidateTimestamp,
    ConfirmationCue,
    CorrelatedIntent,
    ObservationTimestamp,
    OwnerVerification,
    PlaybackState,
    SessionID,
    VADEvidenceTimestamp,
    WakeCandidate,
    WakeDecision,
    WakeEventID,
)
from core.voice_safety.wake_gate import WakeSafetyGate


class FakeClock:
    """Deterministic monotonic nanosecond clock for tests."""

    def __init__(self, start_ns: int = 1_000_000_000_000) -> None:
        self._now_ns = start_ns

    def __call__(self) -> int:
        return self._now_ns

    def advance(self, ns: int) -> None:
        self._now_ns += ns


SESSION = SessionID("sess-1")
EVENT_1 = WakeEventID("evt-1")
EVENT_2 = WakeEventID("evt-2")
EVENT_3 = WakeEventID("evt-3")


def _candidate(
    *,
    event_id: WakeEventID = EVENT_1,
    wake_name: str = "Hikari",
    confidence: float = 0.9,
    candidate_ts_ns: int = 1_000_000_000_000,
    observation_ts_ns: int = 1_000_000_000_000,
    vad_ts_ns: int = 1_000_000_000_000,
    vad_has_speech: bool = True,
    playback=None,
    owner_verification=None,
    hotword_bias: bool = False,
    same_utterance_command: bool = False,
) -> WakeCandidate:
    return WakeCandidate(
        event_id=event_id,
        session_id=SESSION,
        wake_name=wake_name,
        candidate_timestamp=CandidateTimestamp(candidate_ts_ns),
        observation_timestamp=ObservationTimestamp(observation_ts_ns),
        vad_evidence_timestamp=VADEvidenceTimestamp(vad_ts_ns),
        vad_has_speech=vad_has_speech,
        confidence=BoundedConfidence(confidence),
        quality=BoundedQualityScore(0.8),
        playback=playback if playback is not None else _idle_playback(),
        owner_verification=(
            owner_verification if owner_verification is not None else OwnerVerification.verified()
        ),
        confidence_is_hotword_bias=hotword_bias,
        same_utterance_command=same_utterance_command,
    )


def _idle_playback():
    from core.voice_safety.contracts import PlaybackState

    return PlaybackState.idle()


def _gate(**kwargs) -> WakeSafetyGate:
    clock = FakeClock()
    defaults = dict(
        wake_name="Hikari",
        aliases=(),
        confidence_threshold=0.7,
        calibrated=True,
        max_candidate_age_ns=2_000_000_000,
        max_future_skew_ns=250_000_000,
        max_candidate_observation_skew_ns=750_000_000,
        max_vad_age_ns=750_000_000,
        cooldown_ns=3_000_000_000,
        awaiting_command_ns=9_000_000_000,
        confirmation_cue=ConfirmationCue.NONE,
        duplicate_window_ns=2_000_000_000,
        replay_memory_ns=60_000_000_000,
        capacity_window_ns=5_000_000_000,
        max_events_per_window=8,
        require_verified_owner_when_available=True,
    )
    defaults.update(kwargs)
    defaults["clock"] = clock
    return WakeSafetyGate(**defaults)


# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------


def test_default_wake_name_is_hikari_and_aliases_are_empty() -> None:
    gate = WakeSafetyGate(clock=FakeClock())
    assert gate.wake_name == "Hikari"
    assert gate.aliases == ()


def test_default_awaiting_command_window_is_between_8_and_10_seconds() -> None:
    gate = WakeSafetyGate(clock=FakeClock())
    assert 8_000_000_000 <= gate.awaiting_command_window_ns <= 10_000_000_000


def test_default_confirmation_cue_is_non_spoken() -> None:
    gate = WakeSafetyGate(clock=FakeClock())
    assert gate.confirmation_cue is ConfirmationCue.NONE


def test_configurable_exact_aliases_are_accepted() -> None:
    gate = _gate(aliases=("Hey Hikari",))
    candidate = _candidate(wake_name="Hey Hikari")
    result = gate.evaluate(candidate, now_ns=1_000_000_000_000)
    assert result.decision is WakeDecision.ACCEPTED
    # The alias must match exactly; a near variant is rejected.
    near = gate.evaluate(_candidate(wake_name="Hey Hikari now"), now_ns=1_000_000_000_000)
    assert near.decision is WakeDecision.ALIAS_REJECTED


def test_broad_aliases_are_not_silently_enabled() -> None:
    gate = _gate()
    for risky in ("Kari", "Carrie", "Carry", "hickory", "Kiki"):
        result = gate.evaluate(_candidate(wake_name=risky), now_ns=1_000_000_000_000)
        assert result.decision is WakeDecision.ALIAS_REJECTED, risky


def test_unconfigured_name_is_rejected() -> None:
    gate = _gate()
    result = gate.evaluate(_candidate(wake_name="Alexa"), now_ns=1_000_000_000_000)
    assert result.decision is WakeDecision.ALIAS_REJECTED


# ---------------------------------------------------------------------------
# Fail-closed calibration
# ---------------------------------------------------------------------------


def test_uncalibrated_gate_rejects_fail_closed() -> None:
    gate = _gate(calibrated=False, confidence_threshold=0.7)
    result = gate.evaluate(_candidate(), now_ns=1_000_000_000_000)
    assert result.decision is WakeDecision.NOT_CALIBRATED
    assert result.accepted is False


def test_missing_threshold_rejects_fail_closed() -> None:
    gate = _gate(calibrated=True, confidence_threshold=None)
    result = gate.evaluate(_candidate(), now_ns=1_000_000_000_000)
    assert result.decision is WakeDecision.NOT_CALIBRATED


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


def test_weak_confidence_is_rejected() -> None:
    gate = _gate(confidence_threshold=0.7)
    result = gate.evaluate(_candidate(confidence=0.4), now_ns=1_000_000_000_000)
    assert result.decision is WakeDecision.CONFIDENCE_LOW


def test_hotword_biased_confidence_is_never_proof() -> None:
    gate = _gate(confidence_threshold=0.7)
    result = gate.evaluate(
        _candidate(confidence=0.99, hotword_bias=True), now_ns=1_000_000_000_000
    )
    assert result.decision is WakeDecision.CONFIDENCE_LOW


# ---------------------------------------------------------------------------
# VAD evidence
# ---------------------------------------------------------------------------


def test_missing_vad_speech_is_rejected() -> None:
    gate = _gate()
    result = gate.evaluate(_candidate(vad_has_speech=False), now_ns=1_000_000_000_000)
    assert result.decision is WakeDecision.VAD_MISSING


def test_stale_vad_evidence_is_rejected() -> None:
    gate = _gate(max_vad_age_ns=750_000_000)
    result = gate.evaluate(
        _candidate(vad_ts_ns=1_000_000_000_000 - 800_000_000),
        now_ns=1_000_000_000_000,
    )
    assert result.decision is WakeDecision.VAD_STALE


def test_future_vad_evidence_is_rejected() -> None:
    gate = _gate()
    result = gate.evaluate(
        _candidate(vad_ts_ns=1_000_000_000_000 + 500_000_000),
        now_ns=1_000_000_000_000,
    )
    assert result.decision is WakeDecision.VAD_STALE


# ---------------------------------------------------------------------------
# Playback and echo suppression
# ---------------------------------------------------------------------------


def test_playback_active_rejects_candidate() -> None:
    from core.voice_safety.contracts import PlaybackState

    gate = _gate()
    result = gate.evaluate(
        _candidate(playback=PlaybackState.playing()), now_ns=1_000_000_000_000
    )
    assert result.decision is WakeDecision.PLAYBACK_SUPPRESSED


def test_echo_suppression_active_rejects_candidate() -> None:
    from core.voice_safety.contracts import PlaybackState

    gate = _gate()
    result = gate.evaluate(
        _candidate(playback=PlaybackState.echo_suppressed()), now_ns=1_000_000_000_000
    )
    assert result.decision is WakeDecision.PLAYBACK_SUPPRESSED


# ---------------------------------------------------------------------------
# Owner verification (tri-state)
# ---------------------------------------------------------------------------


def test_rejected_owner_fails_closed() -> None:
    gate = _gate()
    result = gate.evaluate(
        _candidate(owner_verification=OwnerVerification.rejected()),
        now_ns=1_000_000_000_000,
    )
    assert result.decision is WakeDecision.OWNER_REJECTED


def test_unavailable_owner_verification_is_not_verified() -> None:
    gate = _gate()
    result = gate.evaluate(
        _candidate(owner_verification=OwnerVerification.unavailable()),
        now_ns=1_000_000_000_000,
    )
    assert result.decision is WakeDecision.OWNER_VERIFICATION_REQUIRED
    # Unavailable must never be represented as verified.
    assert OwnerVerification.unavailable().is_verified is False
    assert result.accepted is False


def test_owner_verification_required_when_policy_requires_it() -> None:
    gate = _gate(require_verified_owner_when_available=True)
    result = gate.evaluate(
        _candidate(owner_verification=OwnerVerification.unavailable()),
        now_ns=1_000_000_000_000,
    )
    assert result.decision is WakeDecision.OWNER_VERIFICATION_REQUIRED


# ---------------------------------------------------------------------------
# Timestamps: future, stale, out-of-order
# ---------------------------------------------------------------------------


def test_future_candidate_is_rejected() -> None:
    gate = _gate(max_future_skew_ns=250_000_000)
    result = gate.evaluate(
        _candidate(candidate_ts_ns=1_000_000_000_000 + 500_000_000),
        now_ns=1_000_000_000_000,
    )
    assert result.decision is WakeDecision.FUTURE_TIMESTAMP


def test_stale_candidate_is_rejected() -> None:
    gate = _gate(max_candidate_age_ns=2_000_000_000)
    result = gate.evaluate(
        _candidate(candidate_ts_ns=1_000_000_000_000 - 3_000_000_000),
        now_ns=1_000_000_000_000,
    )
    assert result.decision is WakeDecision.INVALID


def test_out_of_order_candidate_is_rejected() -> None:
    gate = _gate()
    clock = gate._clock
    clock.advance(1)
    first = gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    assert first.decision is WakeDecision.ACCEPTED
    clock.advance(1)
    # A candidate with an earlier timestamp than the last accepted one.
    second = gate.evaluate(
        _candidate(
            event_id=EVENT_2,
            candidate_ts_ns=1_000_000_000_000 - 100_000_000,
            observation_ts_ns=1_000_000_000_000 - 100_000_000,
            vad_ts_ns=1_000_000_000_000 - 100_000_000,
        ),
        now_ns=clock(),
    )
    assert second.decision is WakeDecision.INVALID


def test_candidate_observation_timestamp_skew_is_rejected() -> None:
    gate = _gate(max_candidate_observation_skew_ns=750_000_000)
    result = gate.evaluate(
        _candidate(
            candidate_ts_ns=1_000_000_000_000,
            observation_ts_ns=1_000_000_000_000 + 800_000_000,
        ),
        now_ns=1_000_000_000_000,
    )
    assert result.decision is WakeDecision.INVALID


# ---------------------------------------------------------------------------
# Duplicate, replay, capacity, cooldown
# ---------------------------------------------------------------------------


def test_duplicate_event_is_rejected() -> None:
    gate = _gate()
    clock = gate._clock
    first = gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    assert first.decision is WakeDecision.ACCEPTED
    clock.advance(1)
    duplicate = gate.evaluate(
        _candidate(
            event_id=EVENT_1,
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        ),
        now_ns=clock(),
    )
    assert duplicate.decision is WakeDecision.DUPLICATE


def test_replayed_event_is_rejected() -> None:
    gate = _gate(duplicate_window_ns=2_000_000_000, replay_memory_ns=60_000_000_000)
    clock = gate._clock
    first = gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    assert first.decision is WakeDecision.ACCEPTED
    clock.advance(3_000_000_000)  # past duplicate window
    replay = gate.evaluate(
        _candidate(
            event_id=EVENT_1,
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        ),
        now_ns=clock(),
    )
    assert replay.decision is WakeDecision.REPLAY


def test_replay_memory_expiry_allows_event_id_reuse() -> None:
    gate = _gate(duplicate_window_ns=2_000_000_000, replay_memory_ns=60_000_000_000)
    clock = gate._clock
    first = gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    assert first.decision is WakeDecision.ACCEPTED
    # Past the duplicate window AND the replay memory: the event id is pruned
    # and a fresh, fully qualified candidate is a brand-new decision.
    clock.advance(61_000_000_000)
    fresh = gate.evaluate(
        _candidate(
            event_id=EVENT_1,
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        ),
        now_ns=clock(),
    )
    assert fresh.decision is WakeDecision.ACCEPTED


def test_aliases_reject_bare_string_input() -> None:
    with pytest.raises(TypeError):
        _gate(aliases="Kari")


def test_capacity_exceeded_rejects_event() -> None:
    gate = _gate(
        max_events_per_window=3,
        capacity_window_ns=5_000_000_000,
        cooldown_ns=0,
    )
    clock = gate._clock
    results = []
    for i in range(5):
        event_id = WakeEventID(f"evt-cap-{i}")
        clock.advance(1)
        candidate = _candidate(
            event_id=event_id,
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        )
        results.append(gate.evaluate(candidate, now_ns=clock()))
    assert results[0].decision is WakeDecision.ACCEPTED
    assert results[1].decision is WakeDecision.ACCEPTED
    assert results[2].decision is WakeDecision.ACCEPTED
    # The rolling window holds more than 3 events -> over capacity.
    assert results[3].decision is WakeDecision.CAPACITY_EXCEEDED
    assert results[4].decision is WakeDecision.CAPACITY_EXCEEDED


def test_cooldown_after_accepted_wake() -> None:
    gate = _gate(cooldown_ns=3_000_000_000)
    clock = gate._clock
    first = gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    assert first.decision is WakeDecision.ACCEPTED
    clock.advance(1)
    second = gate.evaluate(_candidate(event_id=EVENT_2), now_ns=clock())
    assert second.decision is WakeDecision.COOLDOWN
    clock.advance(3_000_000_000)
    third = gate.evaluate(
        _candidate(
            event_id=EVENT_3,
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        ),
        now_ns=clock(),
    )
    assert third.decision is WakeDecision.ACCEPTED


# ---------------------------------------------------------------------------
# Accepted wake exactly once; awaiting-command window
# ---------------------------------------------------------------------------


def test_fresh_calibrated_vad_backed_owner_wake_accepted_exactly_once() -> None:
    gate = _gate()
    clock = gate._clock
    first = gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    assert first.decision is WakeDecision.ACCEPTED
    assert first.accepted is True
    # Same event again is never accepted a second time.
    clock.advance(1)
    second = gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    assert second.decision is WakeDecision.DUPLICATE


def test_accepted_wake_opens_awaiting_command_window() -> None:
    gate = _gate()
    clock = gate._clock
    result = gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    assert result.decision is WakeDecision.ACCEPTED
    assert result.awaiting_command_deadline_ns == clock() + gate.awaiting_command_window_ns
    status = gate.command_window_status(now_ns=clock())
    assert isinstance(status, AwaitingCommandDeadline)
    assert status.expired is False


def test_awaiting_command_window_expires_and_returns_to_sleeping() -> None:
    gate = _gate()
    clock = gate._clock
    gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    clock.advance(gate.awaiting_command_window_ns + 1)
    status = gate.command_window_status(now_ns=clock())
    assert status.expired is True
    assert gate.is_sleeping() is True
    assert gate.awaiting_command_deadline_ns is None


def test_late_command_confirmation_is_rejected_as_expired() -> None:
    gate = _gate()
    clock = gate._clock
    gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    # A command confirmation within the window is qualified.
    within = gate.confirm_command(now_ns=clock())
    assert within.decision is WakeDecision.ACCEPTED
    assert within.accepted is True
    # After the window elapses, confirmation expires and returns to sleeping.
    clock.advance(gate.awaiting_command_window_ns + 1)
    expired = gate.confirm_command(now_ns=clock())
    assert expired.decision is WakeDecision.CONFIRMATION_EXPIRED
    assert expired.accepted is False
    assert gate.is_sleeping() is True
    assert gate.awaiting_command_deadline_ns is None


def test_confirm_command_with_no_active_window_is_invalid() -> None:
    gate = _gate()
    result = gate.confirm_command(now_ns=gate._clock())
    assert result.decision is WakeDecision.INVALID
    assert result.accepted is False


def test_single_authorization_consumed_exactly_once() -> None:
    gate = _gate()
    clock = gate._clock
    gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    first = gate.confirm_command(now_ns=clock())
    assert first.decision is WakeDecision.ACCEPTED
    assert first.accepted is True
    assert first.correlated_intent is not None
    # A second confirmation in the same window fails closed.
    second = gate.confirm_command(now_ns=clock())
    assert second.decision is WakeDecision.AUTHORIZATION_CONSUMED
    assert second.accepted is False
    assert second.correlated_intent is None


def test_second_confirmation_fails_closed_after_consumption() -> None:
    gate = _gate()
    clock = gate._clock
    gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    gate.confirm_command(now_ns=clock())
    for _ in range(3):
        repeated = gate.confirm_command(now_ns=clock())
        assert repeated.decision is WakeDecision.AUTHORIZATION_CONSUMED
        assert repeated.accepted is False


def test_same_utterance_command_consumes_authorization_immediately() -> None:
    gate = _gate()
    clock = gate._clock
    accepted = gate.evaluate(
        _candidate(event_id=EVENT_1, same_utterance_command=True), now_ns=clock()
    )
    assert accepted.decision is WakeDecision.ACCEPTED
    assert accepted.correlated_intent is not None
    # The same-utterance command already consumed the single authorization.
    late = gate.confirm_command(now_ns=clock())
    assert late.decision is WakeDecision.AUTHORIZATION_CONSUMED


def test_correlation_uses_exact_accepted_session_and_wake_event() -> None:
    gate = _gate()
    clock = gate._clock
    accepted = gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    assert accepted.decision is WakeDecision.ACCEPTED
    confirmed = gate.confirm_command(now_ns=clock())
    assert confirmed.decision is WakeDecision.ACCEPTED
    assert confirmed.correlated_intent is not None
    assert confirmed.correlated_intent.session_id == SESSION
    assert confirmed.correlated_intent.wake_event_id == EVENT_1


def test_correlation_never_fabricates_a_fallback_session_id() -> None:
    gate = _gate()
    clock = gate._clock
    gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    confirmed = gate.confirm_command(now_ns=clock())
    assert confirmed.correlated_intent is not None
    # The correlated intent must carry the exact accepted session identifier,
    # never a synthetic fallback such as "local-owner".
    assert confirmed.correlated_intent.session_id == SESSION
    assert confirmed.correlated_intent.session_id.value != "local-owner"


def test_expired_authorization_cannot_manufacture_an_intent() -> None:
    gate = _gate()
    clock = gate._clock
    gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    clock.advance(gate.awaiting_command_window_ns + 1)
    expired = gate.confirm_command(now_ns=clock())
    assert expired.decision is WakeDecision.CONFIRMATION_EXPIRED
    assert expired.accepted is False
    assert expired.correlated_intent is None


def test_speech_after_expiry_requires_a_fresh_qualified_wake() -> None:
    gate = _gate(cooldown_ns=3_000_000_000)
    clock = gate._clock
    gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    clock.advance(gate.awaiting_command_window_ns + 5_000_000_000)
    # No authorization remains from the old wake.
    late = gate.confirm_command(now_ns=clock())
    assert late.decision is WakeDecision.CONFIRMATION_EXPIRED
    # A fresh, fully qualified wake opens a new single authorization.
    fresh = gate.evaluate(
        _candidate(
            event_id=EVENT_2,
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        ),
        now_ns=clock(),
    )
    assert fresh.decision is WakeDecision.ACCEPTED
    one = gate.confirm_command(now_ns=clock())
    assert one.decision is WakeDecision.ACCEPTED
    two = gate.confirm_command(now_ns=clock())
    assert two.decision is WakeDecision.AUTHORIZATION_CONSUMED


def test_reason_codes_are_stable_enums() -> None:
    from core.voice_safety.contracts import WakeReason

    gate = _gate()
    weak = gate.evaluate(_candidate(confidence=0.1), now_ns=1_000_000_000_000)
    assert isinstance(weak.reason, WakeReason)
    assert weak.reason == WakeReason.BELOW_CALIBRATED_THRESHOLD
    assert weak.content_free_metadata()["reason"] == "below_calibrated_threshold"


def test_authorization_available_tracks_consumption() -> None:
    gate = _gate()
    clock = gate._clock
    # No wake yet: no authorization.
    assert gate.is_authorization_available(now_ns=clock()) is False
    gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    assert gate.is_authorization_available(now_ns=clock()) is True
    gate.confirm_command(now_ns=clock())
    # Consumed: no longer available, even though the window timestamp remains.
    assert gate.is_authorization_available(now_ns=clock()) is False
    # A fresh wake after the cooldown restores a single authorization; expiry
    # then removes it again.  (Default gate cooldown is 3s.)
    clock.advance(3_000_000_001)
    fresh = gate.evaluate(
        _candidate(
            event_id=EVENT_2,
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        ),
        now_ns=clock(),
    )
    assert fresh.decision is WakeDecision.ACCEPTED
    assert gate.is_authorization_available(now_ns=clock()) is True
    clock.advance(gate.awaiting_command_window_ns + 1)
    assert gate.is_authorization_available(now_ns=clock()) is False


def test_wake_candidate_repr_excludes_wake_text() -> None:
    candidate = _candidate(wake_name="Hikari")
    rendered = repr(candidate)
    # Reprs must not expose wake/transcript text.
    assert "Hikari" not in rendered
    metadata = candidate.content_free_metadata()
    assert "wake_name" not in metadata


def test_bare_wake_never_authorizes_unrelated_speech_later() -> None:
    gate = _gate(cooldown_ns=3_000_000_000)
    clock = gate._clock
    gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    clock.advance(gate.awaiting_command_window_ns + 5_000_000_000)
    # A command confirmation long after the wake is never qualified.
    late = gate.confirm_command(now_ns=clock())
    assert late.decision is WakeDecision.CONFIRMATION_EXPIRED
    assert late.accepted is False
    # A new candidate after the window is gated fresh; it is accepted only when
    # fully qualified again (cooldown has also elapsed here).
    fresh = gate.evaluate(
        _candidate(
            event_id=EVENT_2,
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        ),
        now_ns=clock(),
    )
    # The bare wake from before did not authorize anything: a fresh, fully
    # qualified candidate is a brand-new decision, not a continuation.
    assert fresh.decision is WakeDecision.ACCEPTED
    assert gate.awaiting_command_deadline_ns is not None


def test_same_utterance_command_returns_correlated_intent_but_never_calls_hikari() -> None:
    gate = _gate()
    clock = gate._clock
    result = gate.evaluate(
        _candidate(event_id=EVENT_1, same_utterance_command=True), now_ns=clock()
    )
    assert result.decision is WakeDecision.ACCEPTED
    assert result.correlated_intent is not None
    assert result.correlated_intent.qualifies is True
    # The gate itself exposes no way to invoke Hikari, speak, or play audio.
    assert not hasattr(gate, "process")
    assert not hasattr(gate, "speak")


def test_uncertain_candidates_remain_silent_with_no_cue() -> None:
    gate = _gate()
    for candidate in (
        _candidate(confidence=0.1),
        _candidate(vad_has_speech=False),
        _candidate(owner_verification=OwnerVerification.rejected()),
    ):
        result = gate.evaluate(candidate, now_ns=1_000_000_000_000)
        assert result.accepted is False
        assert result.cue is ConfirmationCue.NONE


def test_confirmation_cue_defaults_to_non_spoken_on_acceptance() -> None:
    gate = _gate(confirmation_cue=ConfirmationCue.VISUAL)
    result = gate.evaluate(_candidate(event_id=EVENT_1), now_ns=1_000_000_000_000)
    assert result.decision is WakeDecision.ACCEPTED
    assert result.cue is ConfirmationCue.VISUAL
    assert result.cue is not None
    # Cues are never spoken (only visual/local-cue values are allowed).


# ---------------------------------------------------------------------------
# Alias policy: explicit, exact, validated
# ---------------------------------------------------------------------------


def test_alias_validation_rejects_empty_aliases() -> None:
    with pytest.raises(ValueError):
        _gate(aliases=("",))


def test_alias_validation_rejects_whitespace_only_aliases() -> None:
    with pytest.raises(ValueError):
        _gate(aliases=("   ",))


def test_alias_validation_rejects_overlong_aliases() -> None:
    with pytest.raises(ValueError):
        _gate(aliases=("h" * 65,))


def test_alias_validation_rejects_duplicate_aliases_after_normalization() -> None:
    # "Kari" and " kari " normalize to the same exact form -> duplicate.
    with pytest.raises(ValueError):
        _gate(aliases=("Kari", " kari "))


def test_alias_validation_rejects_alias_equal_to_canonical_name() -> None:
    with pytest.raises(ValueError):
        _gate(aliases=("hikari",))


def test_overlong_wake_name_is_rejected_at_contract_boundary() -> None:
    with pytest.raises(ValueError):
        _candidate(wake_name="h" * 65)


# ---------------------------------------------------------------------------
# Candidate/VAD timestamp skew
# ---------------------------------------------------------------------------


def test_candidate_vad_timestamp_skew_is_rejected() -> None:
    gate = _gate(max_vad_age_ns=2_000_000_000, max_candidate_vad_skew_ns=300_000_000)
    # VAD evidence is fresh relative to now but far from the candidate time.
    result = gate.evaluate(
        _candidate(vad_ts_ns=1_000_000_000_000 - 700_000_000),
        now_ns=1_000_000_000_000,
    )
    assert result.decision is WakeDecision.VAD_STALE
    assert result.accepted is False


# ---------------------------------------------------------------------------
# Same-utterance command still requires the exact wake name
# ---------------------------------------------------------------------------


def test_same_utterance_command_still_requires_exact_wake_name() -> None:
    gate = _gate()
    wrong = gate.evaluate(
        _candidate(wake_name="NotHikari", same_utterance_command=True),
        now_ns=1_000_000_000_000,
    )
    assert wrong.decision is WakeDecision.ALIAS_REJECTED
    assert wrong.accepted is False
    assert wrong.correlated_intent is None


# ---------------------------------------------------------------------------
# Once-only authorization: edge cases
# ---------------------------------------------------------------------------


def test_parallel_tightly_repeated_confirmations_only_one_succeeds() -> None:
    gate = _gate()
    clock = gate._clock
    gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    accepted = 0
    for _ in range(5):
        result = gate.confirm_command(now_ns=clock())
        accepted += 1 if result.accepted else 0
    assert accepted == 1


def test_wake_only_speech_never_becomes_a_command() -> None:
    gate = _gate()
    clock = gate._clock
    accepted = gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    assert accepted.decision is WakeDecision.ACCEPTED
    # A bare wake (no asserted command speech) produces no intent.
    assert accepted.correlated_intent is None


def test_command_without_wake_never_creates_an_intent() -> None:
    gate = _gate()
    result = gate.confirm_command(now_ns=gate._clock())
    assert result.decision is WakeDecision.INVALID
    assert result.accepted is False
    assert result.correlated_intent is None


def test_authorization_cleared_after_explicit_reset() -> None:
    gate = _gate()
    clock = gate._clock
    gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    assert gate.is_authorization_available(now_ns=clock()) is True
    gate.expire_confirmation()
    assert gate.is_sleeping() is True
    assert gate.is_authorization_available(now_ns=clock()) is False
    late = gate.confirm_command(now_ns=clock())
    assert late.decision is WakeDecision.INVALID
    assert late.correlated_intent is None


def test_replayed_accepted_event_cannot_create_a_second_authorization() -> None:
    gate = _gate(duplicate_window_ns=2_000_000_000)
    clock = gate._clock
    first = gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    assert first.decision is WakeDecision.ACCEPTED
    clock.advance(3_000_000_000)
    replay = gate.evaluate(
        _candidate(
            event_id=EVENT_1,
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        ),
        now_ns=clock(),
    )
    assert replay.decision is WakeDecision.REPLAY
    # Only the original single authorization exists: one confirmation
    # succeeds, and the second fails closed.
    one = gate.confirm_command(now_ns=clock())
    assert one.decision is WakeDecision.ACCEPTED
    two = gate.confirm_command(now_ns=clock())
    assert two.decision is WakeDecision.AUTHORIZATION_CONSUMED


# ---------------------------------------------------------------------------
# Strict configuration validation
# ---------------------------------------------------------------------------


def test_gate_parameter_validation_rejects_booleans_and_non_ints() -> None:
    with pytest.raises(TypeError):
        WakeSafetyGate(clock=FakeClock(), max_candidate_age_ns=True)
    with pytest.raises(TypeError):
        WakeSafetyGate(clock=FakeClock(), cooldown_ns=1.5)
    with pytest.raises(TypeError):
        WakeSafetyGate(clock=FakeClock(), confidence_threshold=True)
    with pytest.raises(TypeError):
        WakeSafetyGate(clock=FakeClock(), max_events_per_window=False)


def test_gate_duration_parameters_reject_negative_values() -> None:
    with pytest.raises(ValueError):
        WakeSafetyGate(clock=FakeClock(), max_vad_age_ns=-1)
    with pytest.raises(ValueError):
        WakeSafetyGate(clock=FakeClock(), max_events_per_window=0)


# ---------------------------------------------------------------------------
# Owner verification: fail-closed for every non-VERIFIED state
# ---------------------------------------------------------------------------


def test_owner_verification_fail_closed_states() -> None:
    gate = _gate()
    for verification in (
        OwnerVerification.unverified(),
        OwnerVerification.error(),
        OwnerVerification.stale(),
        OwnerVerification.ambiguous(),
    ):
        result = gate.evaluate(
            _candidate(owner_verification=verification), now_ns=1_000_000_000_000
        )
        assert result.decision is WakeDecision.OWNER_VERIFICATION_REQUIRED
        assert result.accepted is False
    for verification in (
        OwnerVerification.third_party(),
        OwnerVerification.mismatched(),
    ):
        result = gate.evaluate(
            _candidate(owner_verification=verification), now_ns=1_000_000_000_000
        )
        assert result.decision is WakeDecision.OWNER_REJECTED
        assert result.accepted is False


def test_rejected_owner_fails_closed_even_without_required_policy() -> None:
    gate = _gate(require_verified_owner_when_available=False)
    result = gate.evaluate(
        _candidate(owner_verification=OwnerVerification.rejected()),
        now_ns=1_000_000_000_000,
    )
    assert result.decision is WakeDecision.OWNER_REJECTED
    assert result.accepted is False


# ---------------------------------------------------------------------------
# Content-safe reprs and metadata redaction
# ---------------------------------------------------------------------------


def test_session_id_repr_does_not_expose_value() -> None:
    sid = SessionID("sentinel-session-9f8e7d6c")
    rendered = repr(sid)
    assert "sentinel-session-9f8e7d6c" not in rendered
    assert "9f8e7d6c" not in rendered


def test_wake_event_id_repr_does_not_expose_value() -> None:
    evt = WakeEventID("sentinel-event-9f8e7d6c")
    rendered = repr(evt)
    assert "sentinel-event-9f8e7d6c" not in rendered
    assert "9f8e7d6c" not in rendered


def test_correlated_intent_repr_does_not_expose_values() -> None:
    intent = CorrelatedIntent(
        intent_id="sentinel-intent-9f8e7d6c",
        session_id=SessionID("sentinel-session-9f8e7d6c"),
        wake_event_id=WakeEventID("sentinel-event-9f8e7d6c"),
    )
    rendered = repr(intent)
    assert "sentinel-intent-9f8e7d6c" not in rendered
    assert "sentinel-session-9f8e7d6c" not in rendered
    assert "sentinel-event-9f8e7d6c" not in rendered
    # Safe structural information remains.
    assert "qualifies=True" in rendered


def test_wake_candidate_repr_does_not_expose_values() -> None:
    candidate = WakeCandidate(
        event_id=WakeEventID("sentinel-event-9f8e7d6c"),
        session_id=SessionID("sentinel-session-9f8e7d6c"),
        wake_name="Hikari",
        candidate_timestamp=CandidateTimestamp(1_000_000_000_000),
        observation_timestamp=ObservationTimestamp(1_000_000_000_000),
        vad_evidence_timestamp=VADEvidenceTimestamp(1_000_000_000_000),
        vad_has_speech=True,
        confidence=BoundedConfidence(0.9),
        quality=BoundedQualityScore(0.8),
        playback=PlaybackState.idle(),
        owner_verification=OwnerVerification.verified(),
    )
    rendered = repr(candidate)
    assert "sentinel-session-9f8e7d6c" not in rendered
    assert "sentinel-event-9f8e7d6c" not in rendered
    assert "Hikari" not in rendered


def test_accepted_wake_evaluation_repr_does_not_expose_values() -> None:
    gate = _gate()
    clock = gate._clock
    accepted = gate.evaluate(
        _candidate(
            event_id=WakeEventID("sentinel-event-9f8e7d6c"), same_utterance_command=True
        ),
        now_ns=clock(),
    )
    assert accepted.decision is WakeDecision.ACCEPTED
    rendered = repr(accepted)
    assert "sentinel-event-9f8e7d6c" not in rendered
    assert "sess-1" not in rendered
    assert "has_correlated_intent=True" in rendered


def test_content_free_metadata_excludes_all_identifiers() -> None:
    gate = _gate()
    clock = gate._clock
    candidate = _candidate(event_id=WakeEventID("sentinel-event-9f8e7d6c"))
    candidate_meta = candidate.content_free_metadata()
    rendered_candidate = str(candidate_meta)
    assert "sentinel-event-9f8e7d6c" not in rendered_candidate
    assert "sess-1" not in rendered_candidate
    assert candidate_meta["has_event_id"] is True
    assert candidate_meta["has_session_id"] is True
    assert "event_id" not in candidate_meta
    assert "session_id" not in candidate_meta

    accepted = gate.evaluate(
        _candidate(
            event_id=WakeEventID("sentinel-event-9f8e7d6c"), same_utterance_command=True
        ),
        now_ns=clock(),
    )
    eval_meta = accepted.content_free_metadata()
    rendered_eval = str(eval_meta)
    assert "sentinel-event-9f8e7d6c" not in rendered_eval
    assert "sess-1" not in rendered_eval
    assert eval_meta["has_correlated_intent"] is True
    assert "correlated_intent" not in eval_meta


# ---------------------------------------------------------------------------
# Identifier length bounds
# ---------------------------------------------------------------------------


def test_maximum_length_identifiers_are_accepted() -> None:
    sid = SessionID("a" * MAX_IDENTIFIER_LENGTH)
    evt = WakeEventID("b" * MAX_IDENTIFIER_LENGTH)
    assert len(sid.value) == MAX_IDENTIFIER_LENGTH
    assert len(evt.value) == MAX_IDENTIFIER_LENGTH


def test_overlong_identifiers_are_rejected() -> None:
    with pytest.raises(ValueError):
        SessionID("a" * (MAX_IDENTIFIER_LENGTH + 1))
    with pytest.raises(ValueError):
        WakeEventID("b" * (MAX_IDENTIFIER_LENGTH + 1))


def test_identifier_validation_rejects_booleans_and_unsafe_values() -> None:
    with pytest.raises(TypeError):
        SessionID(True)
    with pytest.raises(ValueError):
        SessionID("")
    with pytest.raises(ValueError):
        SessionID("unsafe id!")
    with pytest.raises(TypeError):
        WakeEventID(123)


def test_generated_intent_identifiers_stay_within_maximum() -> None:
    gate = _gate()
    clock = gate._clock
    accepted = gate.evaluate(
        _candidate(event_id=EVENT_1, same_utterance_command=True), now_ns=clock()
    )
    assert accepted.correlated_intent is not None
    assert len(accepted.correlated_intent.intent_id) <= MAX_IDENTIFIER_LENGTH
    # A confirmed-command intent is also bounded, content-free, and still
    # correlated to the exact accepted event through the typed field.
    gate2 = _gate()
    clock2 = gate2._clock
    gate2.evaluate(_candidate(event_id=EVENT_2), now_ns=clock2())
    confirmed = gate2.confirm_command(now_ns=clock2())
    assert confirmed.correlated_intent is not None
    assert len(confirmed.correlated_intent.intent_id) <= MAX_IDENTIFIER_LENGTH
    assert confirmed.correlated_intent.wake_event_id == EVENT_2


# ---------------------------------------------------------------------------
# Hard-bounded replay cache
# ---------------------------------------------------------------------------


def test_rapid_unique_events_cannot_grow_replay_cache_beyond_cap() -> None:
    gate = _gate(max_replay_cache_size=4, cooldown_ns=0, max_events_per_window=100)
    clock = gate._clock
    decisions = []
    for i in range(20):
        clock.advance(1)
        result = gate.evaluate(
            _candidate(
                event_id=WakeEventID(f"evt-rapid-{i}"),
                candidate_ts_ns=clock(),
                observation_ts_ns=clock(),
                vad_ts_ns=clock(),
            ),
            now_ns=clock(),
        )
        decisions.append(result.decision)
    # The replay cache can never exceed its hard cap under a rapid unique
    # stream; the fully saturated stream proves the maximum exactly.
    assert len(gate._seen_event_ids) == 4
    # Once full, new unique events fail closed rather than growing the cache.
    assert WakeDecision.REPLAY_CACHE_FULL in decisions


def test_full_replay_cache_fails_closed_without_evicting_live_ids() -> None:
    gate = _gate(max_replay_cache_size=4, cooldown_ns=0, max_events_per_window=100)
    clock = gate._clock
    for i in range(4):
        clock.advance(1)
        gate.evaluate(
            _candidate(
                event_id=WakeEventID(f"evt-live-{i}"),
                candidate_ts_ns=clock(),
                observation_ts_ns=clock(),
                vad_ts_ns=clock(),
            ),
            now_ns=clock(),
        )
    assert len(gate._seen_event_ids) == 4
    clock.advance(1)
    new_event = gate.evaluate(
        _candidate(
            event_id=WakeEventID("evt-new-9f8e7d6c"),
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        ),
        now_ns=clock(),
    )
    assert new_event.decision is WakeDecision.REPLAY_CACHE_FULL
    # No live id was evicted and the cache did not grow.
    assert len(gate._seen_event_ids) == 4
    # Live ids are still protected: a replay of a live id is still rejected.
    clock.advance(1)
    replay = gate.evaluate(
        _candidate(
            event_id=WakeEventID("evt-live-0"),
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        ),
        now_ns=clock(),
    )
    assert replay.decision is WakeDecision.DUPLICATE


def test_expired_replay_ids_are_pruned_and_reusable() -> None:
    gate = _gate(replay_memory_ns=60_000_000_000, max_replay_cache_size=64)
    clock = gate._clock
    gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    assert len(gate._seen_event_ids) == 1
    clock.advance(61_000_000_000)
    # The next evaluation prunes the expired id before processing the new one.
    fresh = gate.evaluate(
        _candidate(
            event_id=EVENT_2,
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        ),
        now_ns=clock(),
    )
    assert fresh.decision is WakeDecision.ACCEPTED
    assert len(gate._seen_event_ids) == 1  # evt-1 pruned, evt-2 inserted
    # The expired id can safely be re-used after the memory elapses (and the
    # cooldown from the fresh wake has also elapsed).
    clock.advance(4_000_000_000)
    reused = gate.evaluate(
        _candidate(
            event_id=EVENT_1,
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        ),
        now_ns=clock(),
    )
    assert reused.decision is WakeDecision.ACCEPTED


def test_rolling_capacity_rejected_events_do_not_grow_replay_cache() -> None:
    gate = _gate(max_events_per_window=3, cooldown_ns=0, max_replay_cache_size=64)
    clock = gate._clock
    for i in range(10):
        clock.advance(1)
        gate.evaluate(
            _candidate(
                event_id=WakeEventID(f"evt-cap-rej-{i}"),
                candidate_ts_ns=clock(),
                observation_ts_ns=clock(),
                vad_ts_ns=clock(),
            ),
            now_ns=clock(),
        )
    # Only events that passed the rate gate entered the replay cache;
    # capacity-rejected unique ids never grow it.
    assert len(gate._seen_event_ids) == 3


# ---------------------------------------------------------------------------
# Hard-bounded rolling-capacity window
# ---------------------------------------------------------------------------


def test_ten_thousand_event_flood_leaves_event_times_at_capacity_bound() -> None:
    # The exact adversarial case from the defect report: 10,000 unique,
    # otherwise qualified events at nearly the same monotonic time with
    # max_events_per_window=3 and max_replay_cache_size=4.  The reported
    # pre-fix observation was len(_event_times)=10,000; it must now be 3.
    gate = _gate(max_events_per_window=3, cooldown_ns=0, max_replay_cache_size=4)
    clock = gate._clock
    for i in range(10_000):
        clock.advance(1)
        gate.evaluate(
            _candidate(
                event_id=WakeEventID(f"evt-flood-{i}"),
                candidate_ts_ns=clock(),
                observation_ts_ns=clock(),
                vad_ts_ns=clock(),
            ),
            now_ns=clock(),
        )
    # The rolling-window deque is hard-bounded to max_events_per_window: the
    # flood leaves exactly 3 retained timestamps, never 10,000.
    assert len(gate._event_times) == 3
    assert len(gate._event_times) <= gate._max_events_per_window
    # Capacity-rejected unique ids never entered the replay cache either.
    assert len(gate._seen_event_ids) == 3
    assert len(gate._seen_event_ids) <= gate._max_replay_cache_size


def test_capacity_rejected_events_do_not_enter_either_bounded_structure() -> None:
    gate = _gate(max_events_per_window=3, cooldown_ns=0, max_replay_cache_size=64)
    clock = gate._clock
    decisions = []
    for i in range(6):
        clock.advance(1)
        result = gate.evaluate(
            _candidate(
                event_id=WakeEventID(f"evt-noinsert-{i}"),
                candidate_ts_ns=clock(),
                observation_ts_ns=clock(),
                vad_ts_ns=clock(),
            ),
            now_ns=clock(),
        )
        decisions.append(result.decision)
    assert decisions[:3] == [WakeDecision.ACCEPTED] * 3
    assert decisions[3:] == [WakeDecision.CAPACITY_EXCEEDED] * 3
    # Rejected over-capacity events were never inserted into either structure.
    assert len(gate._event_times) == 3
    assert len(gate._seen_event_ids) == 3


def test_fresh_event_admitted_after_rolling_window_expires() -> None:
    gate = _gate(
        max_events_per_window=3,
        capacity_window_ns=5_000_000_000,
        cooldown_ns=0,
        max_replay_cache_size=64,
    )
    clock = gate._clock
    for i in range(3):
        clock.advance(1)
        gate.evaluate(
            _candidate(
                event_id=WakeEventID(f"evt-fill-{i}"),
                candidate_ts_ns=clock(),
                observation_ts_ns=clock(),
                vad_ts_ns=clock(),
            ),
            now_ns=clock(),
        )
    assert len(gate._event_times) == 3
    # Still inside the window: capacity is exhausted.
    clock.advance(1)
    rejected = gate.evaluate(
        _candidate(
            event_id=WakeEventID("evt-fill-3"),
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        ),
        now_ns=clock(),
    )
    assert rejected.decision is WakeDecision.CAPACITY_EXCEEDED
    assert len(gate._event_times) == 3
    # Once the rolling window elapses, expired timestamps are pruned and a
    # fresh qualified event is admitted again.
    clock.advance(6_000_000_000)
    fresh = gate.evaluate(
        _candidate(
            event_id=WakeEventID("evt-fresh-after-expiry"),
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        ),
        now_ns=clock(),
    )
    assert fresh.decision is WakeDecision.ACCEPTED
    assert len(gate._event_times) == 1


def test_duplicate_and_replay_classification_survive_capacity_pressure() -> None:
    gate = _gate(
        max_events_per_window=3,
        duplicate_window_ns=2_000_000_000,
        cooldown_ns=0,
        max_replay_cache_size=64,
    )
    clock = gate._clock
    first = gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    assert first.decision is WakeDecision.ACCEPTED
    clock.advance(1)
    second = gate.evaluate(_candidate(event_id=EVENT_2), now_ns=clock())
    assert second.decision is WakeDecision.ACCEPTED
    # Duplicate detection runs before capacity: a repeat of a retained event
    # is still classified DUPLICATE even while the window is nearly full.
    clock.advance(1)
    dup = gate.evaluate(
        _candidate(
            event_id=EVENT_1,
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        ),
        now_ns=clock(),
    )
    assert dup.decision is WakeDecision.DUPLICATE
    assert len(gate._event_times) == 2  # the duplicate was not inserted
    # Capacity now full: a distinct event is rejected as over-capacity.
    clock.advance(1)
    third = gate.evaluate(
        _candidate(
            event_id=EVENT_3,
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        ),
        now_ns=clock(),
    )
    assert third.decision is WakeDecision.ACCEPTED
    assert len(gate._event_times) == 3
    clock.advance(1)
    overflow = gate.evaluate(
        _candidate(
            event_id=WakeEventID("evt-overflow-1"),
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        ),
        now_ns=clock(),
    )
    assert overflow.decision is WakeDecision.CAPACITY_EXCEEDED
    # A replay past the duplicate window is still classified REPLAY even
    # under capacity pressure (duplicate/replay detection runs first).
    clock.advance(2_100_000_000)
    replay = gate.evaluate(
        _candidate(
            event_id=EVENT_1,
            candidate_ts_ns=clock(),
            observation_ts_ns=clock(),
            vad_ts_ns=clock(),
        ),
        now_ns=clock(),
    )
    assert replay.decision is WakeDecision.REPLAY


def test_flood_keeps_replay_cache_at_or_below_its_hard_cap() -> None:
    gate = _gate(max_events_per_window=100, cooldown_ns=0, max_replay_cache_size=4)
    clock = gate._clock
    for i in range(10_000):
        clock.advance(1)
        gate.evaluate(
            _candidate(
                event_id=WakeEventID(f"evt-capflood-{i}"),
                candidate_ts_ns=clock(),
                observation_ts_ns=clock(),
                vad_ts_ns=clock(),
            ),
            now_ns=clock(),
        )
    # Both bounded structures sit at their configured caps after a flood of
    # unique otherwise-valid events; neither ever exceeds its bound.
    assert len(gate._seen_event_ids) == 4
    assert len(gate._seen_event_ids) <= gate._max_replay_cache_size
    assert len(gate._event_times) == 4
    assert len(gate._event_times) <= gate._max_events_per_window


def test_cooldown_and_once_only_authorization_still_pass_after_flood() -> None:
    gate = _gate(
        max_events_per_window=3,
        cooldown_ns=3_000_000_000,
        duplicate_window_ns=2_000_000_000,
        max_replay_cache_size=64,
    )
    clock = gate._clock
    first = gate.evaluate(_candidate(event_id=EVENT_1), now_ns=clock())
    assert first.decision is WakeDecision.ACCEPTED
    # A tight flood of unique events cannot manufacture extra wakes: capacity
    # and cooldown fail closed, and nothing is silently accepted.
    flood_accepted = 0
    for i in range(50):
        clock.advance(1)
        result = gate.evaluate(
            _candidate(
                event_id=WakeEventID(f"evt-cdflood-{i}"),
                candidate_ts_ns=clock(),
                observation_ts_ns=clock(),
                vad_ts_ns=clock(),
            ),
            now_ns=clock(),
        )
        if result.accepted:
            flood_accepted += 1
    assert flood_accepted == 0
    # The single authorization from the original wake is still consumed
    # exactly once; the second confirmation fails closed.
    one = gate.confirm_command(now_ns=clock())
    assert one.decision is WakeDecision.ACCEPTED
    two = gate.confirm_command(now_ns=clock())
    assert two.decision is WakeDecision.AUTHORIZATION_CONSUMED
