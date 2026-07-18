"""Bounded speech adapter interfaces and implementations.

This module provides small, typed wrappers around STT and TTS backends.
All heavy dependencies are imported lazily so that importing this module
(or core.voice) does not load model packages, access the network, or touch
private runtime data.
"""

from __future__ import annotations

import dataclasses
import re
import subprocess
import sys
from typing import ClassVar, Optional, Protocol, runtime_checkable

from core.voice_status import FASTER_WHISPER_REVISION


class SpeechAdapterError(Exception):
    """Base class for bounded speech adapter errors.

    Callers can catch this to fall back to text input/output.
    """


class SpeechBackendUnavailable(SpeechAdapterError):
    """Raised when a configured voice backend is not available on this host."""


class TranscriptionError(SpeechAdapterError):
    """Raised when speech-to-text fails for a captured audio buffer."""


class SynthesisError(SpeechAdapterError):
    """Raised when text-to-speech fails."""


class InvalidAudioError(SpeechAdapterError):
    """Raised when captured audio metadata or payload is unusable."""


@dataclasses.dataclass(frozen=True)
class CapturedAudio:
    """Privacy-minimal container for a captured audio buffer.

    Fields:
        pcm_bytes: Raw PCM sample bytes.
        sample_rate: Samples per second (e.g. 16000).
        sample_width: Bytes per sample (e.g. 2 for 16-bit).
        channel_count: Number of channels (typically 1).
    """

    pcm_bytes: bytes
    sample_rate: int
    sample_width: int
    channel_count: int

    SUPPORTED_SAMPLE_WIDTHS: ClassVar[tuple[int, ...]] = (2,)

    def __post_init__(self) -> None:
        if not isinstance(self.pcm_bytes, bytes):
            raise InvalidAudioError("pcm_bytes must be bytes")
        if self.sample_rate <= 0:
            raise InvalidAudioError("sample_rate must be positive")
        if self.sample_width <= 0:
            raise InvalidAudioError("sample_width must be positive")
        if self.channel_count <= 0:
            raise InvalidAudioError("channel_count must be positive")
        if len(self.pcm_bytes) == 0:
            raise InvalidAudioError("pcm_bytes must not be empty")
        if self.sample_width not in self.SUPPORTED_SAMPLE_WIDTHS:
            raise InvalidAudioError(
                f"unsupported sample_width {self.sample_width}; "
                f"supported widths are {self.SUPPORTED_SAMPLE_WIDTHS}"
            )
        expected = self.sample_width * self.channel_count
        if len(self.pcm_bytes) % expected != 0:
            raise InvalidAudioError(
                "pcm_bytes length is not aligned to sample_width * channel_count"
            )

    def to_mono_16k(self) -> "CapturedAudio":
        """Return a 16 kHz mono 16-bit copy, downmixing stereo if needed."""
        if self.sample_rate == 16000 and self.sample_width == 2 and self.channel_count == 1:
            return self
        try:
            import numpy as np
        except ImportError as exc:
            raise SpeechBackendUnavailable("numpy is required for audio conversion") from exc

        samples = np.frombuffer(self.pcm_bytes, dtype=np.int16)
        if self.channel_count > 1:
            samples = samples.reshape(-1, self.channel_count).mean(axis=1).astype(np.int16)
        if self.sample_rate != 16000:
            ratio = 16000 / self.sample_rate
            new_len = int(len(samples) * ratio)
            indices = (np.arange(new_len) / ratio).astype(np.int32)
            indices = np.clip(indices, 0, len(samples) - 1)
            samples = samples[indices]
        return CapturedAudio(
            pcm_bytes=samples.astype(np.int16).tobytes(),
            sample_rate=16000,
            sample_width=2,
            channel_count=1,
        )


@runtime_checkable
class STTAdapter(Protocol):
    """Protocol for speech-to-text adapters."""

    @property
    def audio_egress(self) -> bool:
        """True if this adapter sends audio off-device."""
        ...

    def is_available(self) -> bool:
        ...

    def prepare(self) -> None:
        """Eagerly load any heavy resources (model, cache, etc.).

        This is optional; callers may call it to warm up the backend before
        the first transcription. It must remain a no-op if called repeatedly.
        """
        ...

    def transcribe(self, audio: CapturedAudio) -> str:
        """Return the transcription of the captured audio.

        Raises:
            SpeechBackendUnavailable: if the backend cannot be used.
            TranscriptionError: if transcription fails.
            InvalidAudioError: if the audio payload is unusable.
        """
        ...


@runtime_checkable
class TTSAdapter(Protocol):
    """Protocol for text-to-speech adapters."""

    def is_available(self) -> bool:
        ...

    def synthesize(self, text: str) -> None:
        """Synthesize the provided text.

        Raises:
            SpeechBackendUnavailable: if the backend cannot be used.
            SynthesisError: if synthesis fails.
        """
        ...


class OpenAIWhisperSTTAdapter:
    """Local OpenAI Whisper STT adapter with lazy model import."""

    def __init__(self, model_size: str = "base") -> None:
        self.model_size = model_size
        self._model: Optional[object] = None

    @property
    def audio_egress(self) -> bool:
        return False

    def is_available(self) -> bool:
        try:
            import whisper  # noqa: F401
            import numpy  # noqa: F401
        except ImportError:
            return False
        return True

    def prepare(self) -> None:
        """Load the Whisper model into memory if available."""
        self._load_model()

    def _load_model(self) -> object:
        if self._model is None:
            import whisper

            self._model = whisper.load_model(self.model_size)
        return self._model

    def transcribe(self, audio: CapturedAudio) -> str:
        if not self.is_available():
            raise SpeechBackendUnavailable(
                "OpenAI Whisper is not installed; install whisper and numpy"
            )
        try:
            import numpy as np
        except ImportError as exc:
            raise SpeechBackendUnavailable("numpy is required for Whisper STT") from exc

        converted = audio.to_mono_16k()
        samples = np.frombuffer(converted.pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        model = self._load_model()
        result = model.transcribe(samples, language="en", fp16=False)
        text = result.get("text", "").strip()
        if not text:
            raise TranscriptionError("Whisper returned empty transcription")
        return text


class FasterWhisperSTTAdapter:
    """Local faster-whisper STT adapter with lazy model import."""

    def __init__(self, model_size: str = "base", *, revision: str | None = FASTER_WHISPER_REVISION) -> None:
        self.model_size = model_size
        self.revision = revision
        self._model: Optional[object] = None

    @property
    def audio_egress(self) -> bool:
        return False

    def is_available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
            import numpy  # noqa: F401
        except ImportError:
            return False
        return True

    def prepare(self) -> None:
        """Load the faster-whisper model into memory if available."""
        self._load_model()

    def _load_model(self) -> object:
        if self._model is None:
            from faster_whisper import WhisperModel

            kwargs: dict = {"device": "cpu", "compute_type": "int8"}
            if self.revision is not None:
                kwargs["revision"] = self.revision
            self._model = WhisperModel(self.model_size, **kwargs)
        return self._model

    def transcribe(self, audio: CapturedAudio) -> str:
        if not self.is_available():
            raise SpeechBackendUnavailable(
                "faster-whisper is not installed; install faster-whisper and numpy"
            )
        try:
            import numpy as np
        except ImportError as exc:
            raise SpeechBackendUnavailable("numpy is required for faster-whisper STT") from exc

        converted = audio.to_mono_16k()
        samples = np.frombuffer(converted.pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        model = self._load_model()
        segments, _info = model.transcribe(samples, language="en", beam_size=1)
        text = "".join(seg.text for seg in segments).strip()
        if not text:
            raise TranscriptionError("faster-whisper returned empty transcription")
        return text


class GoogleSpeechRecognitionSTTAdapter:
    """Explicit Google SpeechRecognition STT adapter.

    This adapter only sends audio off-device when it is explicitly selected.
    It never silently replaces a local backend.
    """

    def __init__(self) -> None:
        self._recognizer_instance: Optional[object] = None

    @property
    def audio_egress(self) -> bool:
        return True

    def is_available(self) -> bool:
        try:
            import speech_recognition as sr  # noqa: F401
        except ImportError:
            return False
        return True

    def prepare(self) -> None:
        """No-op: Google SpeechRecognition has no local model to load."""

    def _get_recognizer(self) -> object:
        if self._recognizer_instance is None:
            import speech_recognition as sr

            self._recognizer_instance = sr.Recognizer()
        return self._recognizer_instance

    def transcribe(self, audio: CapturedAudio) -> str:
        if not self.is_available():
            raise SpeechBackendUnavailable(
                "Google SpeechRecognition is not installed; install SpeechRecognition"
            )
        import speech_recognition as sr

        recognizer = self._get_recognizer()
        sr_audio = sr.AudioData(
            audio.pcm_bytes,
            sample_rate=audio.sample_rate,
            sample_width=audio.sample_width,
        )
        try:
            text = recognizer.recognize_google(sr_audio)
        except sr.UnknownValueError as exc:
            raise TranscriptionError("Google could not understand the audio") from exc
        except sr.RequestError as exc:
            raise TranscriptionError("Google STT request failed") from exc
        if not isinstance(text, str):
            raise TranscriptionError("Google STT returned non-string result")
        return text.strip()


class MacOSSayTTSAdapter:
    """macOS ``say`` TTS adapter using subprocess with an argv list."""

    def __init__(self) -> None:
        self._voice: Optional[str] = None

    def is_available(self) -> bool:
        return sys.platform == "darwin" and self._say_path() is not None

    @staticmethod
    def _say_path() -> Optional[str]:
        import shutil

        return shutil.which("say")

    def _sanitize_text(self, text: str) -> str:
        """Remove characters that could be interpreted by the speech engine.

        Keeps alphanumerics, common punctuation, and whitespace.
        """
        return re.sub(r"[^\w\s:,.!?]", "", text)

    def synthesize(self, text: str) -> None:
        if not self.is_available():
            raise SpeechBackendUnavailable("macOS say is not available on this platform")
        clean = self._sanitize_text(text)
        if not clean.strip():
            raise SynthesisError("No speakable text after sanitization")
        say_path = self._say_path()
        if say_path is None:
            raise SpeechBackendUnavailable("say executable not found")
        try:
            subprocess.run(
                [say_path, clean],
                shell=False,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise SynthesisError(f"say failed with return code {exc.returncode}") from exc


def build_stt_adapter(backend_name: str, *, whisper_model: str = "base") -> STTAdapter:
    """Build an STT adapter for the named backend.

    Args:
        backend_name: One of ``openai-whisper``, ``faster-whisper``, or ``google-speech``.
        whisper_model: Whisper model size when ``backend_name`` is ``openai-whisper``.

    Raises:
        SpeechBackendUnavailable: if the backend name is unknown.
    """
    if backend_name == "openai-whisper":
        return OpenAIWhisperSTTAdapter(model_size=whisper_model)
    if backend_name == "faster-whisper":
        return FasterWhisperSTTAdapter(model_size=whisper_model)
    if backend_name == "google-speech":
        return GoogleSpeechRecognitionSTTAdapter()
    raise SpeechBackendUnavailable(f"unknown STT backend: {backend_name}")


def build_tts_adapter(backend_name: str) -> TTSAdapter:
    """Build a TTS adapter for the named backend.

    Currently only ``macos-say`` is supported.

    Raises:
        SpeechBackendUnavailable: if the backend name is unknown.
    """
    if backend_name == "macos-say":
        return MacOSSayTTSAdapter()
    raise SpeechBackendUnavailable(f"unknown TTS backend: {backend_name}")
