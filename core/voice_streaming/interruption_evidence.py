"""Immutable barge-in / interruption evidence contracts.

Caller-supplied booleans are not trusted. Authorization requires a verified
evidence object bound to stream, utterance correlation, timestamp, and source.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.voice_streaming.contracts import validate_monotonic_ns, validate_stream_id

_ID_MAX = 128


class InterruptionVerificationSource(StrEnum):
    SPEAKER_AUTH = "speaker_auth"
    OWNER_POLICY = "owner_policy"


def _require_id(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _ID_MAX:
        raise ValueError(f"invalid_{name}")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"invalid_{name}")
    return value


@dataclass(frozen=True, repr=False)
class InterruptionEvidence:
    """One-time interruption authorization evidence.

    ``speech_observed_ns`` must be generated after assistant playback began.
    It must not reuse prior command-turn VAD timestamps.
    """

    stream_id: str
    interruption_id: str
    target_assistant_utterance_id: str
    speaker_verified: bool
    verification_source: InterruptionVerificationSource
    speech_observed_ns: int
    observed_at_ns: int
    expires_at_ns: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "stream_id", validate_stream_id(self.stream_id))
        object.__setattr__(
            self, "interruption_id", _require_id(self.interruption_id, "interruption_id")
        )
        object.__setattr__(
            self,
            "target_assistant_utterance_id",
            _require_id(self.target_assistant_utterance_id, "target_assistant_utterance_id"),
        )
        if not isinstance(self.speaker_verified, bool):
            raise ValueError("invalid_speaker_verified")
        if not isinstance(self.verification_source, InterruptionVerificationSource):
            raise ValueError("invalid_verification_source")
        object.__setattr__(
            self, "speech_observed_ns", validate_monotonic_ns(self.speech_observed_ns)
        )
        object.__setattr__(self, "observed_at_ns", validate_monotonic_ns(self.observed_at_ns))
        object.__setattr__(self, "expires_at_ns", validate_monotonic_ns(self.expires_at_ns))
        if self.expires_at_ns < self.observed_at_ns:
            raise ValueError("invalid_expiry")
        if self.speech_observed_ns > self.observed_at_ns:
            raise ValueError("invalid_speech_vs_observed")

    def __repr__(self) -> str:
        return (
            f"InterruptionEvidence(verified={self.speaker_verified}, "
            f"source={self.verification_source.value!r})"
        )


__all__ = [
    "InterruptionEvidence",
    "InterruptionVerificationSource",
]
