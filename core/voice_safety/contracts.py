"""Content-safe contracts for the wake-safety layer.

Every type in this module is immutable, validated at construction, and
deliberately free of transcript text, speaker identity, raw audio, device
identifiers, and private correlation values.  Representations, exceptions,
diagnostics, and event metadata stay content-free by construction so that
downstream wiring (logging, websockets, the daemon) can never leak private
speech content through this layer.

Reason fields use stable, content-free enum codes rather than arbitrary
free-form strings.  The strongest positive result any contract here can
express is an accepted session-transition decision or an optional non-spoken
local cue recommendation.  Nothing in this module can speak, start playback,
open a microphone, or invoke the orchestrator.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional

# Conservative maximum length for opaque identifiers (session ids, wake
# event ids, intent ids).  Identifiers are correlation handles only; they must
# stay short so diagnostics and generated tokens can never grow unbounded.
MAX_IDENTIFIER_LENGTH = 64


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class VoiceSafetyError(Exception):
    """Base error for the voice safety layer (content-free)."""


class InvalidCandidateError(VoiceSafetyError):
    """A wake candidate failed structural validation (content-free)."""


class NotCalibratedError(VoiceSafetyError):
    """Raised when a wake decision is requested without a valid calibration."""


# ---------------------------------------------------------------------------
# Stable content-free decision codes
# ---------------------------------------------------------------------------


class WakeDecision(StrEnum):
    """Stable, content-free wake decision codes.

    ``ACCEPTED`` is the only positive result; it represents an accepted
    session-transition decision, never an instruction to speak or act.
    """

    ACCEPTED = "accepted"
    INVALID = "invalid"
    CONFIDENCE_LOW = "confidence_low"
    NOT_CALIBRATED = "not_calibrated"
    VAD_MISSING = "vad_missing"
    VAD_STALE = "vad_stale"
    PLAYBACK_SUPPRESSED = "playback_suppressed"
    OWNER_REJECTED = "owner_rejected"
    OWNER_VERIFICATION_REQUIRED = "owner_verification_required"
    ALIAS_REJECTED = "alias_rejected"
    DUPLICATE = "duplicate"
    REPLAY = "replay"
    COOLDOWN = "cooldown"
    FUTURE_TIMESTAMP = "future_timestamp"
    CONFIRMATION_EXPIRED = "confirmation_expired"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    REPLAY_CACHE_FULL = "replay_cache_full"
    AUTHORIZATION_CONSUMED = "authorization_consumed"


class WakeReason(StrEnum):
    """Stable, content-free reason codes for wake evaluations."""

    THRESHOLD_NOT_CALIBRATED = "threshold_not_calibrated"
    NAME_NOT_CONFIGURED = "name_not_configured"
    CANDIDATE_IN_FUTURE = "candidate_in_future"
    CANDIDATE_STALE = "candidate_stale"
    OUT_OF_ORDER = "out_of_order"
    TIMESTAMP_SKEW = "timestamp_skew"
    NO_VAD_SPEECH = "no_vad_speech"
    VAD_EVIDENCE_STALE = "vad_evidence_stale"
    VAD_TIMESTAMP_SKEW = "vad_timestamp_skew"
    PLAYBACK_OR_ECHO_ACTIVE = "playback_or_echo_active"
    OWNER_VERIFICATION_REJECTED = "owner_verification_rejected"
    OWNER_VERIFICATION_UNAVAILABLE = "owner_verification_unavailable"
    HOTWORD_BIASED_CONFIDENCE = "hotword_biased_confidence"
    BELOW_CALIBRATED_THRESHOLD = "below_calibrated_threshold"
    DUPLICATE_EVENT = "duplicate_event"
    REPLAYED_EVENT = "replayed_event"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    REPLAY_CACHE_FULL = "replay_cache_full"
    COOLDOWN_ACTIVE = "cooldown_active"
    ACCEPTED = "accepted"
    WITHIN_AWAITING_COMMAND_WINDOW = "within_awaiting_command_window"
    NO_AWAITING_COMMAND_WINDOW = "no_awaiting_command_window"
    AWAITING_COMMAND_EXPIRED = "awaiting_command_expired"
    AUTHORIZATION_CONSUMED = "authorization_consumed"
    CORRELATION_UNAVAILABLE = "correlation_unavailable"


class OwnerVerificationState(StrEnum):
    """Owner verification outcome states (content-safe).

    ``VERIFIED`` is the only permissive state.  Every other state fails
    closed: ``UNAVAILABLE`` must never be reported as ``VERIFIED``, and an
    unverified, errored, stale, ambiguous, or unavailable verifier verdict is
    never treated as proof of ownership.
    """

    VERIFIED = "verified"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"
    UNVERIFIED = "unverified"
    ERROR = "error"
    STALE = "stale"
    AMBIGUOUS = "ambiguous"


class OwnerVerificationReason(StrEnum):
    """Stable, content-free reason codes for owner verification results."""

    NOT_REQUESTED = "not_requested"
    ENROLLED_MATCH = "enrolled_match"
    NO_MATCH = "no_match"
    VERIFIER_UNAVAILABLE = "verifier_unavailable"
    UNVERIFIED = "unverified"
    VERIFIER_ERROR = "verifier_error"
    VERIFIER_STALE = "verifier_stale"
    AMBIGUOUS = "ambiguous"
    THIRD_PARTY_EVIDENCE = "third_party_evidence"
    MISMATCHED_EVIDENCE = "mismatched_evidence"


class PlaybackReason(StrEnum):
    """Stable, content-free reason codes for playback state."""

    IDLE = "idle"
    PLAYBACK_ACTIVE = "playback_active"
    ECHO_SUPPRESSION_ACTIVE = "echo_suppression_active"


class ConfirmationCue(StrEnum):
    """Optional wake acknowledgment cue.

    The default is ``NONE`` and cues are never spoken; at most a quiet visual
    or local non-audio cue may be recommended.
    """

    NONE = "none"
    VISUAL = "visual"
    LOCAL_CUE = "local_cue"


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_ns(value: int, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer nanosecond count")
    if value < 0:
        raise ValueError(f"{name} cannot be negative")
    return value


def _validate_bounded(value: float, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    val = float(value)
    if not math.isfinite(val):
        raise ValueError(f"{name} must be finite")
    if not 0.0 <= val <= 1.0:
        raise ValueError(f"{name} must be within [0.0, 1.0]")
    return val


def _validate_identifier(value: str, *, name: str) -> str:
    """Validate an opaque, content-free identifier.

    Rejects booleans and non-strings with a ``TypeError``, and empty,
    overlong, or unsafe-character identifiers with a ``ValueError``.  All
    errors are content-free (they never echo the offending value).
    """
    if isinstance(value, bool) or not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > MAX_IDENTIFIER_LENGTH:
        raise ValueError(f"{name} exceeds the maximum identifier length")
    # Opaque identifiers use a safe charset so they can never smuggle content.
    if not all(ch.isalnum() or ch in "-_." for ch in value):
        raise ValueError(f"{name} contains characters outside the safe set")
    return value


def _validate_name(value: str, *, name: str) -> str:
    """Normalize a wake name to its exact canonical form (never fuzzy).

    Normalization is bounded: it folds case, trims surrounding whitespace,
    collapses internal whitespace, restricts the character set, and rejects
    names longer than a fixed limit so that overlong aliases cannot be
    configured.  This is exact-form normalization only; it never performs
    substring, prefix, edit-distance, phonetic, or regex matching.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    folded = value.casefold().strip()
    # Collapse internal whitespace and drop surrounding punctuation; this is an
    # exact-form normalization, never substring or phonetic matching.
    tokens = [tok for tok in folded.split() if tok]
    if not tokens:
        raise ValueError(f"{name} cannot be normalized to a non-empty form")
    canonical = " ".join(tokens)
    if not all(ch.isalnum() or ch.isspace() for ch in canonical):
        raise ValueError(f"{name} contains characters outside the safe set")
    if len(canonical) > 64:
        raise ValueError(f"{name} exceeds the maximum normalized length")
    return canonical


# ---------------------------------------------------------------------------
# Identifier and timestamp value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionID:
    """Opaque session identifier (content-free)."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _validate_identifier(self.value, name="session_id"))

    def __repr__(self) -> str:
        # Content-safe: communicates only that an opaque session id is present,
        # never the id itself (which is a private correlation value).
        return f"{type(self).__name__}(present=True)"


@dataclass(frozen=True)
class WakeEventID:
    """Opaque wake event identifier (content-free)."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _validate_identifier(self.value, name="wake_event_id"))

    def __repr__(self) -> str:
        # Content-safe: communicates only that an opaque wake event id is
        # present, never the id itself.
        return f"{type(self).__name__}(present=True)"


@dataclass(frozen=True)
class CandidateTimestamp:
    """Monotonic nanosecond timestamp of the wake candidate detection."""

    value_ns: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "value_ns", _validate_ns(self.value_ns, name="candidate_timestamp"))


@dataclass(frozen=True)
class ObservationTimestamp:
    """Monotonic nanosecond timestamp of the accompanying observation."""

    value_ns: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "value_ns", _validate_ns(self.value_ns, name="observation_timestamp"))


@dataclass(frozen=True)
class VADEvidenceTimestamp:
    """Monotonic nanosecond timestamp of the VAD speech evidence."""

    value_ns: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "value_ns", _validate_ns(self.value_ns, name="vad_evidence_timestamp"))


# ---------------------------------------------------------------------------
# Bounded scores
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoundedConfidence:
    """Bounded wake confidence score in [0.0, 1.0].

    Hotword-biased STT output is not confidence proof; callers must supply a
    score from a source that is not biased by the hotword itself.
    """

    value: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _validate_bounded(self.value, name="confidence"))


@dataclass(frozen=True)
class BoundedQualityScore:
    """Bounded observation quality score in [0.0, 1.0]."""

    value: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _validate_bounded(self.value, name="quality"))


# ---------------------------------------------------------------------------
# Context state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlaybackState:
    """Playback / echo-suppression state at observation time.

    Content-free: carries only booleans and a stable reason code.
    """

    is_playing: bool
    echo_suppression_active: bool = False
    reason: PlaybackReason = PlaybackReason.IDLE

    def __post_init__(self) -> None:
        if not isinstance(self.is_playing, bool):
            raise TypeError("is_playing must be a boolean")
        if not isinstance(self.echo_suppression_active, bool):
            raise TypeError("echo_suppression_active must be a boolean")
        if not isinstance(self.reason, PlaybackReason):
            raise TypeError("playback reason must be a PlaybackReason")

    @classmethod
    def idle(cls) -> "PlaybackState":
        return cls(is_playing=False, echo_suppression_active=False, reason=PlaybackReason.IDLE)

    @classmethod
    def playing(cls) -> "PlaybackState":
        return cls(is_playing=True, echo_suppression_active=False, reason=PlaybackReason.PLAYBACK_ACTIVE)

    @classmethod
    def echo_suppressed(cls) -> "PlaybackState":
        return cls(is_playing=False, echo_suppression_active=True, reason=PlaybackReason.ECHO_SUPPRESSION_ACTIVE)


@dataclass(frozen=True)
class CooldownState:
    """Cooldown state following an accepted wake (content-free)."""

    in_cooldown: bool
    cooldown_until_ns: int = 0
    remaining_ns: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.in_cooldown, bool):
            raise TypeError("in_cooldown must be a boolean")
        object.__setattr__(self, "cooldown_until_ns", _validate_ns(self.cooldown_until_ns, name="cooldown_until"))
        object.__setattr__(self, "remaining_ns", _validate_ns(self.remaining_ns, name="cooldown_remaining"))


# ---------------------------------------------------------------------------
# Owner verification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OwnerVerification:
    """Tri-state owner verification result.

    ``state == UNAVAILABLE`` is never treated as verified; the gate treats an
    unavailable verifier as a fail-closed ``owner_verification_required``.
    """

    state: OwnerVerificationState
    reason: OwnerVerificationReason = OwnerVerificationReason.NOT_REQUESTED

    def __post_init__(self) -> None:
        if not isinstance(self.state, OwnerVerificationState):
            raise TypeError("owner verification state must be an OwnerVerificationState")
        if not isinstance(self.reason, OwnerVerificationReason):
            raise TypeError("owner verification reason must be an OwnerVerificationReason")

    @classmethod
    def verified(cls, reason: OwnerVerificationReason = OwnerVerificationReason.ENROLLED_MATCH) -> "OwnerVerification":
        return cls(state=OwnerVerificationState.VERIFIED, reason=reason)

    @classmethod
    def rejected(cls, reason: OwnerVerificationReason = OwnerVerificationReason.NO_MATCH) -> "OwnerVerification":
        return cls(state=OwnerVerificationState.REJECTED, reason=reason)

    @classmethod
    def unavailable(
        cls, reason: OwnerVerificationReason = OwnerVerificationReason.VERIFIER_UNAVAILABLE
    ) -> "OwnerVerification":
        return cls(state=OwnerVerificationState.UNAVAILABLE, reason=reason)

    @classmethod
    def unverified(cls) -> "OwnerVerification":
        """No verification was performed; fails closed."""
        return cls(state=OwnerVerificationState.UNVERIFIED, reason=OwnerVerificationReason.UNVERIFIED)

    @classmethod
    def error(cls) -> "OwnerVerification":
        """The verifier reported an error; fails closed."""
        return cls(state=OwnerVerificationState.ERROR, reason=OwnerVerificationReason.VERIFIER_ERROR)

    @classmethod
    def stale(cls) -> "OwnerVerification":
        """The verifier evidence is stale; fails closed."""
        return cls(state=OwnerVerificationState.STALE, reason=OwnerVerificationReason.VERIFIER_STALE)

    @classmethod
    def ambiguous(cls) -> "OwnerVerification":
        """The verifier verdict was ambiguous; fails closed."""
        return cls(state=OwnerVerificationState.AMBIGUOUS, reason=OwnerVerificationReason.AMBIGUOUS)

    @classmethod
    def third_party(cls) -> "OwnerVerification":
        """The evidence belongs to a third party; rejected."""
        return cls(
            state=OwnerVerificationState.REJECTED, reason=OwnerVerificationReason.THIRD_PARTY_EVIDENCE
        )

    @classmethod
    def mismatched(cls) -> "OwnerVerification":
        """The evidence does not match the enrolled speaker; rejected."""
        return cls(
            state=OwnerVerificationState.REJECTED, reason=OwnerVerificationReason.MISMATCHED_EVIDENCE
        )

    @property
    def is_verified(self) -> bool:
        """True only for an explicit VERIFIED verdict, never for UNAVAILABLE."""
        return self.state is OwnerVerificationState.VERIFIED


# ---------------------------------------------------------------------------
# Awaiting-command window
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AwaitingCommandDeadline:
    """Bounded awaiting-command window after an accepted wake.

    Expiry returns the session to sleeping; a bare wake never authorizes
    unrelated speech beyond this deadline.
    """

    deadline_ns: int
    remaining_ns: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "deadline_ns", _validate_ns(self.deadline_ns, name="deadline"))
        object.__setattr__(self, "remaining_ns", _validate_ns(self.remaining_ns, name="remaining"))

    @property
    def expired(self) -> bool:
        return self.remaining_ns <= 0


# ---------------------------------------------------------------------------
# Correlated same-utterance intent
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CorrelatedIntent:
    """Content-free marker for a same-utterance wake-and-command.

    Carries no transcript text: only an opaque intent id, the exact accepted
    session id, the exact accepted wake event id, and a qualification flag.
    The gate never calls Hikari on its own and never fabricates identifiers.
    """

    intent_id: str
    session_id: SessionID
    wake_event_id: WakeEventID
    qualifies: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_id", _validate_identifier(self.intent_id, name="intent_id"))
        if not isinstance(self.session_id, SessionID):
            raise TypeError("session_id must be a SessionID")
        if not isinstance(self.wake_event_id, WakeEventID):
            raise TypeError("wake_event_id must be a WakeEventID")
        if not isinstance(self.qualifies, bool):
            raise TypeError("qualifies must be a boolean")

    def __repr__(self) -> str:
        # Content-safe: boolean presence flags and the qualification boolean
        # only; the intent id and the correlated session/event ids are never
        # echoed.
        return (
            f"{type(self).__name__}(has_intent_id=True, has_session_id=True, "
            f"has_wake_event_id=True, qualifies={self.qualifies})"
        )


# ---------------------------------------------------------------------------
# Wake candidate (input)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WakeCandidate:
    """Immutable, content-free wake candidate submitted to the gate.

    ``wake_name`` is the detected activation name normalized to its exact
    canonical form (never fuzzy, never substring-matched).  The name is kept
    out of representations and metadata (``repr=False``, excluded from
    ``content_free_metadata``) so diagnostics never expose wake text.  All
    other fields are validated at construction; no transcript or speaker
    content is stored.
    """

    event_id: WakeEventID
    session_id: SessionID
    wake_name: str = field(repr=False)
    candidate_timestamp: CandidateTimestamp
    observation_timestamp: ObservationTimestamp
    vad_evidence_timestamp: VADEvidenceTimestamp
    vad_has_speech: bool
    confidence: BoundedConfidence
    quality: BoundedQualityScore
    playback: PlaybackState
    owner_verification: OwnerVerification
    confidence_is_hotword_bias: bool = False
    same_utterance_command: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, WakeEventID):
            raise TypeError("event_id must be a WakeEventID")
        if not isinstance(self.session_id, SessionID):
            raise TypeError("session_id must be a SessionID")
        object.__setattr__(self, "wake_name", _validate_name(self.wake_name, name="wake_name"))
        if not isinstance(self.candidate_timestamp, CandidateTimestamp):
            raise TypeError("candidate_timestamp must be a CandidateTimestamp")
        if not isinstance(self.observation_timestamp, ObservationTimestamp):
            raise TypeError("observation_timestamp must be an ObservationTimestamp")
        if not isinstance(self.vad_evidence_timestamp, VADEvidenceTimestamp):
            raise TypeError("vad_evidence_timestamp must be a VADEvidenceTimestamp")
        if not isinstance(self.vad_has_speech, bool):
            raise TypeError("vad_has_speech must be a boolean")
        if not isinstance(self.confidence, BoundedConfidence):
            raise TypeError("confidence must be a BoundedConfidence")
        if not isinstance(self.quality, BoundedQualityScore):
            raise TypeError("quality must be a BoundedQualityScore")
        if not isinstance(self.playback, PlaybackState):
            raise TypeError("playback must be a PlaybackState")
        if not isinstance(self.owner_verification, OwnerVerification):
            raise TypeError("owner_verification must be an OwnerVerification")
        if not isinstance(self.confidence_is_hotword_bias, bool):
            raise TypeError("confidence_is_hotword_bias must be a boolean")
        if not isinstance(self.same_utterance_command, bool):
            raise TypeError("same_utterance_command must be a boolean")

    def __repr__(self) -> str:
        # Content-safe: only structural information (presence flags, bounded
        # timestamp/score numbers, booleans, enums).  Never the wake name, raw
        # session/event ids, or any transcript/speaker content.
        return (
            f"{type(self).__name__}(event_id=present, session_id=present, "
            f"candidate_timestamp_ns={self.candidate_timestamp.value_ns}, "
            f"vad_has_speech={self.vad_has_speech}, "
            f"confidence={self.confidence.value}, "
            f"same_utterance_command={self.same_utterance_command})"
        )

    def content_free_metadata(self) -> dict:
        """Content-free event metadata for diagnostics and logs.

        Raw session/event identifiers are never emitted; only non-reversible
        presence flags plus bounded timestamps, scores, booleans, and enum
        codes.  Excludes wake name text and any transcript or speaker content.
        """
        return {
            "has_event_id": True,
            "has_session_id": True,
            "candidate_timestamp_ns": self.candidate_timestamp.value_ns,
            "observation_timestamp_ns": self.observation_timestamp.value_ns,
            "vad_evidence_timestamp_ns": self.vad_evidence_timestamp.value_ns,
            "vad_has_speech": self.vad_has_speech,
            "confidence": self.confidence.value,
            "quality": self.quality.value,
            "playback_is_playing": self.playback.is_playing,
            "echo_suppression_active": self.playback.echo_suppression_active,
            "owner_verification": self.owner_verification.state.value,
            "confidence_is_hotword_bias": self.confidence_is_hotword_bias,
            "same_utterance_command": self.same_utterance_command,
        }


# ---------------------------------------------------------------------------
# Wake evaluation (output)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WakeEvaluation:
    """Fail-closed outcome of a wake candidate evaluation.

    ``accepted`` is True only for ``decision == ACCEPTED``.  The only positive
    result is a session-transition decision plus an optional non-spoken cue;
    this type can never express an instruction to speak, play audio, or open
    the microphone.
    """

    decision: WakeDecision
    accepted: bool
    reason: WakeReason
    awaiting_command_deadline_ns: Optional[int] = None
    cue: ConfirmationCue = ConfirmationCue.NONE
    correlated_intent: Optional[CorrelatedIntent] = None

    def __post_init__(self) -> None:
        if not isinstance(self.decision, WakeDecision):
            raise TypeError("decision must be a WakeDecision")
        if not isinstance(self.accepted, bool):
            raise TypeError("accepted must be a boolean")
        if not isinstance(self.reason, WakeReason):
            raise TypeError("evaluation reason must be a WakeReason")
        if self.awaiting_command_deadline_ns is not None:
            object.__setattr__(
                self,
                "awaiting_command_deadline_ns",
                _validate_ns(self.awaiting_command_deadline_ns, name="awaiting_command_deadline"),
            )
        if not isinstance(self.cue, ConfirmationCue):
            raise TypeError("cue must be a ConfirmationCue")
        if self.correlated_intent is not None and not isinstance(self.correlated_intent, CorrelatedIntent):
            raise TypeError("correlated_intent must be a CorrelatedIntent or None")
        # Invariant: accepted implies ACCEPTED decision.
        if self.accepted and self.decision is not WakeDecision.ACCEPTED:
            raise ValueError("accepted evaluations must carry the ACCEPTED decision")

    def __repr__(self) -> str:
        # Content-safe: decision/reason/cue enum codes, the accepted boolean,
        # and a presence flag for any correlated intent.  Raw session, event,
        # and intent ids are never echoed.
        return (
            f"{type(self).__name__}(decision={self.decision.value}, "
            f"accepted={self.accepted}, reason={self.reason.value}, "
            f"has_correlated_intent={self.correlated_intent is not None}, "
            f"cue={self.cue.value})"
        )

    def content_free_metadata(self) -> dict:
        """Content-free outcome metadata for diagnostics and logs.

        Never emits raw session, event, or intent identifiers; only enum
        codes, booleans, and bounded timestamps.
        """
        return {
            "decision": self.decision.value,
            "accepted": self.accepted,
            "reason": self.reason.value,
            "awaiting_command_deadline_ns": self.awaiting_command_deadline_ns,
            "cue": self.cue.value,
            "has_correlated_intent": self.correlated_intent is not None,
        }
