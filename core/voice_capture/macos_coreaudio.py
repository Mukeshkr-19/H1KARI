"""AudioFrameSource backed by hikari-macos-audio-capture helper.

Importing this module does not open the microphone or launch a process.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from core.voice_capture.capability import probe_macos_coreaudio_capability, resolve_helper_executable
from core.voice_capture.config import VoiceCaptureConfig
from core.voice_capture.contracts import CaptureCapabilityReason, CaptureMessageType
from core.voice_capture.framing import HEADER_SIZE, decode_frame, decode_header
from core.voice_capture.process import HelperProcess, HelperProcessError
from core.voice_streaming.live_audio import (
    AudioFrameSourceReason,
    AudioFrameSourceResult,
    AudioInputCapability,
    CaptureSourceCategory,
    LiveAudioFrame,
    UnavailableAudioFrameSource,
)


class MacOSCoreAudioFrameSource:
    """Framed IPC reader implementing AudioFrameSource."""

    def __init__(
        self,
        *,
        stream_id: str,
        config: Optional[VoiceCaptureConfig] = None,
        helper_path: Optional[Path] = None,
        now_ns: Optional[Callable[[], int]] = None,
        root: Optional[Path] = None,
        helper_root: Optional[Path] = None,
    ) -> None:
        if not isinstance(stream_id, str) or not stream_id or len(stream_id) > 128:
            raise ValueError("invalid_stream_id")
        self._stream_id = stream_id
        self._config = config or VoiceCaptureConfig()
        if helper_path is not None and helper_root is None:
            raise ValueError("helper_root_required")
        self._helper_path = Path(helper_path) if helper_path is not None else None
        self._helper_root = Path(helper_root) if helper_root is not None else None
        self._root = root
        self._now_ns = now_ns or (lambda: time.monotonic_ns())
        self._proc: Optional[HelperProcess] = None
        self._open = False
        self._cancelled = False
        self._closed = False
        self._ready = False
        self._last_sequence = -1
        self._last_monotonic_ns = 0
        self._frame_counter = 0

    @property
    def capability(self) -> AudioInputCapability:
        if self._closed or self._cancelled:
            return AudioInputCapability.UNAVAILABLE
        return AudioInputCapability.FRAME_STREAM

    def open(self) -> AudioFrameSourceResult:
        if self._closed:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.CLOSED)
        if self._cancelled:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.CANCELLED)
        if self._open:
            return AudioFrameSourceResult(True, AudioFrameSourceReason.OK)
        helper = self._helper_path or resolve_helper_executable(root=self._root)
        if helper is None:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.UNAVAILABLE)
        try:
            proc = HelperProcess(
                executable=helper,
                args=("--capture",),
                config=self._config,
                allowed_root=(
                    self._helper_root
                    if self._helper_root is not None
                    else (Path(self._root) if self._root is not None else Path(__file__).resolve().parents[2])
                    / "native"
                    / "macos_audio_capture"
                    / ".build"
                ),
            )
            proc.start()
            self._proc = proc
            if not self._handshake():
                self._safe_stop()
                return AudioFrameSourceResult(False, AudioFrameSourceReason.HARDWARE_ERROR)
            self._open = True
            return AudioFrameSourceResult(True, AudioFrameSourceReason.OK)
        except HelperProcessError:
            self._safe_stop()
            return AudioFrameSourceResult(False, AudioFrameSourceReason.HARDWARE_ERROR)
        except Exception:
            self._safe_stop()
            return AudioFrameSourceResult(False, AudioFrameSourceReason.HARDWARE_ERROR)

    def _handshake(self) -> bool:
        assert self._proc is not None
        header = self._proc.read_exact(HEADER_SIZE, timeout_s=self._config.handshake_timeout_s)
        meta = decode_header(header)
        if meta is None:
            return False
        message_type, seq, mono, rate, channels, width, plen = meta
        if plen > self._config.max_frame_bytes:
            return False
        payload = b""
        if plen:
            payload = self._proc.read_exact(plen, timeout_s=self._config.handshake_timeout_s)
        frame = decode_frame(header + payload)
        if frame is None or frame.message_type != CaptureMessageType.READY:
            return False
        if rate != self._config.sample_rate or channels != self._config.channels:
            return False
        if width != self._config.sample_width:
            return False
        self._ready = True
        self._last_sequence = seq
        self._last_monotonic_ns = mono
        return True

    def read_frame(self) -> AudioFrameSourceResult:
        if self._closed:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.CLOSED)
        if self._cancelled:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.CANCELLED)
        if not self._open or self._proc is None:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.NOT_OPEN)
        try:
            header = self._proc.read_exact(HEADER_SIZE, timeout_s=self._config.frame_read_timeout_s)
            meta = decode_header(header)
            if meta is None:
                return AudioFrameSourceResult(False, AudioFrameSourceReason.INVALID_FRAME)
            message_type, seq, mono, rate, channels, width, plen = meta
            if plen > self._config.max_frame_bytes:
                return AudioFrameSourceResult(False, AudioFrameSourceReason.FRAME_TOO_LARGE)
            payload = b""
            if plen:
                payload = self._proc.read_exact(plen, timeout_s=self._config.frame_read_timeout_s)
            frame = decode_frame(header + payload)
            if frame is None:
                return AudioFrameSourceResult(False, AudioFrameSourceReason.INVALID_FRAME)
            if frame.message_type == CaptureMessageType.END:
                self.close()
                return AudioFrameSourceResult(False, AudioFrameSourceReason.CLOSED)
            if frame.message_type == CaptureMessageType.ERROR:
                return AudioFrameSourceResult(False, AudioFrameSourceReason.HARDWARE_ERROR)
            if frame.message_type == CaptureMessageType.CANCEL_ACK:
                return AudioFrameSourceResult(False, AudioFrameSourceReason.CANCELLED)
            if frame.message_type != CaptureMessageType.PCM:
                return AudioFrameSourceResult(False, AudioFrameSourceReason.INVALID_FRAME)
            return self._accept_pcm(frame.sequence, frame.monotonic_ns, frame.sample_rate,
                                    frame.channels, frame.sample_width, frame.payload)
        except HelperProcessError as exc:
            msg = str(exc)
            if msg == "read_timeout":
                return AudioFrameSourceResult(False, AudioFrameSourceReason.TIMEOUT)
            if msg in {"helper_eof", "helper_exited"}:
                self.close()
                return AudioFrameSourceResult(False, AudioFrameSourceReason.CLOSED)
            return AudioFrameSourceResult(False, AudioFrameSourceReason.HARDWARE_ERROR)

    def _accept_pcm(
        self,
        sequence: int,
        monotonic_ns: int,
        sample_rate: int,
        channels: int,
        sample_width: int,
        pcm: bytes,
    ) -> AudioFrameSourceResult:
        if sample_rate != self._config.sample_rate or channels != self._config.channels:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.INVALID_FRAME)
        if sample_width != self._config.sample_width:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.INVALID_FRAME)
        if not pcm or len(pcm) % (channels * sample_width) != 0:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.INVALID_FRAME)
        if sequence <= self._last_sequence:
            reason = (
                AudioFrameSourceReason.DUPLICATE_FRAME
                if sequence == self._last_sequence
                else AudioFrameSourceReason.OUT_OF_ORDER
            )
            return AudioFrameSourceResult(False, reason)
        if monotonic_ns < self._last_monotonic_ns:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.OUT_OF_ORDER)
        now = self._now_ns()
        if monotonic_ns > now + self._config.future_skew_ns:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.FUTURE_TIMESTAMP)
        if now - monotonic_ns > self._config.stale_skew_ns:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.STALE_TIMESTAMP)
        self._last_sequence = sequence
        self._last_monotonic_ns = monotonic_ns
        self._frame_counter += 1
        live = LiveAudioFrame(
            stream_id=self._stream_id,
            frame_id=f"{self._stream_id}:f:{self._frame_counter}",
            sequence=sequence,
            monotonic_ns=monotonic_ns,
            sample_rate=sample_rate,
            channels=channels,
            sample_width=sample_width,
            pcm=pcm,
            capture_source=CaptureSourceCategory.MICROPHONE,
        )
        return AudioFrameSourceResult(True, AudioFrameSourceReason.OK, live)

    def cancel(self) -> AudioFrameSourceResult:
        self._cancelled = True
        if self._proc is not None:
            try:
                self._proc.request_cancel()
            except Exception:
                pass
            self._safe_stop()
        self._open = False
        return AudioFrameSourceResult(True, AudioFrameSourceReason.CANCELLED)

    def close(self) -> AudioFrameSourceResult:
        self._closed = True
        self._open = False
        self._safe_stop()
        return AudioFrameSourceResult(True, AudioFrameSourceReason.CLOSED)

    def _safe_stop(self) -> None:
        if self._proc is not None:
            try:
                self._proc.stop()
            except Exception:
                pass
            self._proc = None

    def __repr__(self) -> str:
        return (
            f"MacOSCoreAudioFrameSource(open={self._open}, "
            f"cancelled={self._cancelled}, closed={self._closed})"
        )


def try_create_macos_coreaudio_source(
    *,
    stream_id: str,
    root: Optional[Path] = None,
    config: Optional[VoiceCaptureConfig] = None,
    require_probe: bool = True,
) -> object:
    """Return a frame source when helper is present; else unavailable stub.

    Probe does not open the microphone. Construction does not launch capture.
    """
    cfg = config or VoiceCaptureConfig()
    if require_probe:
        cap = probe_macos_coreaudio_capability(root=root, config=cfg)
        if not cap.available:
            return UnavailableAudioFrameSource()
        helper = Path(cap.helper_path) if cap.helper_path else None
    else:
        helper = resolve_helper_executable(root=root)
        if helper is None:
            return UnavailableAudioFrameSource()
    return MacOSCoreAudioFrameSource(
        stream_id=stream_id,
        config=cfg,
        helper_path=helper,
        root=root,
        helper_root=(
            (Path(root) if root is not None else Path(__file__).resolve().parents[2])
            / "native"
            / "macos_audio_capture"
            / ".build"
        ),
    )


# Keep reason enum referenced for status tooling.
_ = CaptureCapabilityReason
