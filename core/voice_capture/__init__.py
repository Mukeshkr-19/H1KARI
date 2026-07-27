"""macOS CoreAudio capture adapter for HIKARI voice streaming.

Importing this package performs no I/O and does not launch helpers or open
the microphone. Adapted audio-engineering patterns from BasedHardware/Omi
(MIT) are attributed in docs/OMI_DERIVED_VOICE_PIPELINE.md.
"""

from core.voice_capture.capability import probe_macos_coreaudio_capability
from core.voice_capture.config import CAPTURE_BACKEND_MACOS_COREAUDIO, VoiceCaptureConfig
from core.voice_capture.macos_coreaudio import (
    MacOSCoreAudioFrameSource,
    try_create_macos_coreaudio_source,
)

__all__ = [
    "CAPTURE_BACKEND_MACOS_COREAUDIO",
    "MacOSCoreAudioFrameSource",
    "VoiceCaptureConfig",
    "probe_macos_coreaudio_capability",
    "try_create_macos_coreaudio_source",
]
