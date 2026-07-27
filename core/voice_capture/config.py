"""Capture-backend configuration. Distinct from STT --voice-backend."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

CAPTURE_BACKEND_UTTERANCE_ONLY = "utterance-only"
CAPTURE_BACKEND_MACOS_COREAUDIO = "macos-coreaudio"
CAPTURE_BACKENDS = frozenset({CAPTURE_BACKEND_UTTERANCE_ONLY, CAPTURE_BACKEND_MACOS_COREAUDIO})

HELPER_RELATIVE = Path("native/macos_audio_capture/.build/release/hikari-macos-audio-capture")
HELPER_DEBUG_RELATIVE = Path("native/macos_audio_capture/.build/debug/hikari-macos-audio-capture")

MAX_FRAME_BYTES = 65_536
MAX_QUEUE_DEPTH = 64
MAX_STDERR_BYTES = 8_192
HANDSHAKE_TIMEOUT_S = 3.0
FRAME_READ_TIMEOUT_S = 2.0
SHUTDOWN_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class VoiceCaptureConfig:
    """Bounded capture settings. Clocks and paths are explicit."""

    handshake_timeout_s: float = HANDSHAKE_TIMEOUT_S
    frame_read_timeout_s: float = FRAME_READ_TIMEOUT_S
    shutdown_timeout_s: float = SHUTDOWN_TIMEOUT_S
    max_queue_depth: int = MAX_QUEUE_DEPTH
    max_frame_bytes: int = MAX_FRAME_BYTES
    max_stderr_bytes: int = MAX_STDERR_BYTES
    sample_rate: int = 16_000
    channels: int = 1
    sample_width: int = 2
    future_skew_ns: int = 1_000_000_000
    stale_skew_ns: int = 30_000_000_000

    def __post_init__(self) -> None:
        for value in (
            self.handshake_timeout_s,
            self.frame_read_timeout_s,
            self.shutdown_timeout_s,
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("invalid_timeout")
            if not math.isfinite(float(value)) or value <= 0 or value > 30:
                raise ValueError("invalid_timeout")
        if isinstance(self.max_queue_depth, bool) or not isinstance(self.max_queue_depth, int):
            raise ValueError("invalid_max_queue_depth")
        if self.max_queue_depth < 1 or self.max_queue_depth > MAX_QUEUE_DEPTH:
            raise ValueError("invalid_max_queue_depth")
        if isinstance(self.max_frame_bytes, bool) or not isinstance(self.max_frame_bytes, int):
            raise ValueError("invalid_max_frame_bytes")
        if self.max_frame_bytes < 2 or self.max_frame_bytes > MAX_FRAME_BYTES:
            raise ValueError("invalid_max_frame_bytes")
        if isinstance(self.max_stderr_bytes, bool) or not isinstance(self.max_stderr_bytes, int):
            raise ValueError("invalid_max_stderr_bytes")
        if self.max_stderr_bytes < 1 or self.max_stderr_bytes > MAX_STDERR_BYTES:
            raise ValueError("invalid_max_stderr_bytes")
        if self.sample_rate != 16_000 or self.channels != 1 or self.sample_width != 2:
            raise ValueError("invalid_audio_format")
        for name, value, hard_max in (
            ("future_skew_ns", self.future_skew_ns, 5_000_000_000),
            ("stale_skew_ns", self.stale_skew_ns, 120_000_000_000),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"invalid_{name}")
            if value < 1 or value > hard_max:
                raise ValueError(f"invalid_{name}")
        if self.future_skew_ns > self.stale_skew_ns:
            raise ValueError("invalid_skew_relationship")
