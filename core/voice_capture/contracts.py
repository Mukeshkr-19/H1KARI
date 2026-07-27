"""Content-free capture contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Optional


class CaptureMessageType(StrEnum):
    READY = "ready"
    PCM = "pcm"
    ERROR = "error"
    END = "end"
    CANCEL_ACK = "cancel_ack"


class CaptureCapabilityReason(StrEnum):
    OK = "ok"
    HELPER_MISSING = "helper_missing"
    HELPER_NOT_EXECUTABLE = "helper_not_executable"
    PROBE_FAILED = "probe_failed"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, repr=False)
class CaptureCapability:
    available: bool
    reason: CaptureCapabilityReason
    sample_rate: int = 16_000
    channels: int = 1
    sample_width: int = 2
    helper_path: Optional[str] = None
    opens_microphone: bool = False

    def __repr__(self) -> str:
        return (
            f"CaptureCapability(available={self.available}, "
            f"reason={self.reason.value!r}, opens_microphone={self.opens_microphone})"
        )


@dataclass(frozen=True, repr=False)
class DecodedCaptureFrame:
    message_type: CaptureMessageType
    sequence: int
    monotonic_ns: int
    sample_rate: int
    channels: int
    sample_width: int
    payload: bytes

    def __repr__(self) -> str:
        return (
            f"DecodedCaptureFrame(type={self.message_type.value!r}, "
            f"seq={self.sequence}, bytes={len(self.payload)})"
        )
