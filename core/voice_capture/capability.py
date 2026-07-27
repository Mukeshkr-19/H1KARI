"""Capability probing without opening the microphone."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from core.voice_capture.config import HELPER_DEBUG_RELATIVE, HELPER_RELATIVE, VoiceCaptureConfig
from core.voice_capture.contracts import CaptureCapability, CaptureCapabilityReason, CaptureMessageType
from core.voice_capture.framing import HEADER_SIZE, decode_frame
from core.voice_capture.process import HelperProcess, HelperProcessError


def repository_root() -> Path:
    # core/voice_capture/capability.py -> repo root
    return Path(__file__).resolve().parents[2]


def resolve_helper_executable(*, root: Optional[Path] = None) -> Optional[Path]:
    base = repository_root() if root is None else Path(root)
    for rel in (HELPER_RELATIVE, HELPER_DEBUG_RELATIVE):
        candidate = (base / rel).resolve()
        if candidate.is_file() and os_access_exec(candidate):
            return candidate
    return None


def os_access_exec(path: Path) -> bool:
    import os

    return os.access(path, os.X_OK)


def probe_macos_coreaudio_capability(
    *,
    root: Optional[Path] = None,
    config: Optional[VoiceCaptureConfig] = None,
) -> CaptureCapability:
    """Probe helper readiness. Does not pass --capture; microphone stays closed."""
    cfg = config or VoiceCaptureConfig()
    if sys.platform != "darwin":
        return CaptureCapability(False, CaptureCapabilityReason.UNSUPPORTED_PLATFORM)
    helper = resolve_helper_executable(root=root)
    if helper is None:
        return CaptureCapability(False, CaptureCapabilityReason.HELPER_MISSING)
    if not os_access_exec(helper):
        return CaptureCapability(
            False,
            CaptureCapabilityReason.HELPER_NOT_EXECUTABLE,
            helper_path=str(helper),
        )
    # Probe mode: no --capture argument.
    try:
        proc = HelperProcess(
            executable=helper,
            args=(),
            config=cfg,
            allowed_root=(repository_root() if root is None else Path(root))
            / "native"
            / "macos_audio_capture"
            / ".build",
        )
        proc.start()
        header = proc.read_exact(HEADER_SIZE, timeout_s=cfg.handshake_timeout_s)
        from core.voice_capture.framing import decode_header

        meta = decode_header(header)
        if meta is None:
            proc.stop()
            return CaptureCapability(
                False,
                CaptureCapabilityReason.PROBE_FAILED,
                helper_path=str(helper),
            )
        message_type, _seq, _mono, rate, channels, width, plen = meta
        payload = b""
        if plen:
            payload = proc.read_exact(plen, timeout_s=cfg.handshake_timeout_s)
        frame = decode_frame(header + payload)
        proc.stop()
        if frame is None or frame.message_type != CaptureMessageType.READY:
            return CaptureCapability(
                False,
                CaptureCapabilityReason.PROBE_FAILED,
                helper_path=str(helper),
            )
        return CaptureCapability(
            True,
            CaptureCapabilityReason.OK,
            sample_rate=rate,
            channels=channels,
            sample_width=width,
            helper_path=str(helper),
            opens_microphone=False,
        )
    except HelperProcessError:
        return CaptureCapability(
            False,
            CaptureCapabilityReason.PROBE_FAILED,
            helper_path=str(helper),
        )
    except Exception:
        return CaptureCapability(
            False,
            CaptureCapabilityReason.PROBE_FAILED,
            helper_path=str(helper),
        )
