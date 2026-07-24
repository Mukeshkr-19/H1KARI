"""Bounded speech adapter interfaces and implementations.

This module provides small, typed wrappers around STT and TTS backends.
All heavy dependencies are imported lazily so that importing this module
(or core.voice) does not load model packages, access the network, or touch
private runtime data.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import ClassVar, Optional, Protocol, runtime_checkable

from core.voice_config import tts_rate, tts_voice_name
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


def prepare_spoken_text(text: str) -> str:
    """Return plain, speakable prose without changing sentence content.

    Model replies can contain Markdown, links, and emoji that are useful on a
    screen but distracting when spoken.  Keep ordinary Unicode letters,
    numbers, mathematical notation, punctuation, and complete sentences while
    removing presentation-only syntax and pictographs.
    """

    if not isinstance(text, str):
        raise SynthesisError("speech text must be a string")
    clean = re.sub(r"```(?:[^\n]*)\n?(.*?)```", r" \1 ", text, flags=re.DOTALL)
    clean = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r" \1 ", clean)
    clean = re.sub(r"\[([^\]]+)\]\([^)]*\)", r" \1 ", clean)
    clean = re.sub(r"https?://\S+|www\.\S+", " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\[\[[^\]\r\n]{0,100}\]\]", " ", clean)
    clean = re.sub(r"(?m)^\s{0,3}(?:#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)", "", clean)
    clean = clean.replace("`", "").replace("**", "").replace("__", "")
    clean = clean.replace("*", "").replace("_", " ").replace("~", "")
    # macOS voices support bracketed inline control commands. Model output is
    # prose, never a trusted speech-control program.
    clean = clean.replace("[", "").replace("]", "")

    spoken: list[str] = []
    for char in clean:
        codepoint = ord(char)
        category = unicodedata.category(char)
        if category in {"Cf", "Cs", "Co", "Sk"}:
            continue
        if category == "So" and codepoint >= 0x2000:
            continue
        if 0x1F1E6 <= codepoint <= 0x1F1FF or 0x1F300 <= codepoint <= 0x1FAFF:
            continue
        if codepoint < 32 and char not in {"\n", "\t"}:
            continue
        if codepoint == 127:
            continue
        spoken.append(char)

    result = " ".join("".join(spoken).split())
    return re.sub(r"\s+([,.;:!?])", r"\1", result)


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

    def _transcribe(self, audio: CapturedAudio, *, short_utterance: bool) -> str:
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
        options: dict = {"language": "en", "beam_size": 1}
        if short_utterance:
            # Wake phrases are commonly shorter than Whisper's default
            # no-speech filter expects. Decode them without that filter, while
            # retaining exact wake-phrase matching and speaker verification at
            # the daemon boundary.
            options.update(
                condition_on_previous_text=False,
                hotwords="HIKARI stop done",
                initial_prompt="HIKARI. Stop. Done.",
                no_speech_threshold=None,
                without_timestamps=True,
            )
        segments, _info = model.transcribe(samples, **options)
        text = "".join(seg.text for seg in segments).strip()
        if not text:
            raise TranscriptionError("faster-whisper returned empty transcription")
        return text

    def transcribe(self, audio: CapturedAudio) -> str:
        return self._transcribe(audio, short_utterance=False)

    def transcribe_short_utterance(self, audio: CapturedAudio) -> str:
        """Transcribe a short local wake phrase without default no-speech filtering."""
        return self._transcribe(audio, short_utterance=True)


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

    def __init__(self, *, rate: Optional[int] = None, voice: Optional[str] = None) -> None:
        self._voice = voice or tts_voice_name()
        self._rate = tts_rate() if rate is None else max(120, min(220, int(rate)))

    def is_available(self) -> bool:
        return sys.platform == "darwin" and self._say_path() is not None

    @staticmethod
    def _say_path() -> Optional[str]:
        import shutil

        return shutil.which("say")

    def _sanitize_text(self, text: str) -> str:
        """Convert display-oriented output into complete spoken prose."""
        return prepare_spoken_text(text)

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
                [say_path, "-v", self._voice, "-r", str(self._rate), clean],
                shell=False,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise SynthesisError(f"say failed with return code {exc.returncode}") from exc


class PocketTTSAdapter:
    """Optional free, local Pocket TTS adapter with lazy model loading.

    Import and construction perform no model load, download, filesystem write,
    or audio playback.  The separately installed ``pocket-tts`` distribution
    loads only on the first explicit synthesis request.
    """

    def __init__(self, *, voice: Optional[str] = None) -> None:
        self._voice = voice or tts_voice_name(default="alba")
        self._model = None
        self._voice_state = None

    def is_available(self) -> bool:
        return (
            sys.platform == "darwin"
            and importlib.util.find_spec("pocket_tts") is not None
            and Path("/usr/bin/afplay").is_file()
        )

    def _load(self):
        if self._model is not None:
            return self._model, self._voice_state
        if not self.is_available():
            raise SpeechBackendUnavailable("Pocket TTS is not installed")
        try:
            from pocket_tts import TTSModel

            model = TTSModel.load_model()
            voice_state = model.get_state_for_audio_prompt(self._voice)
        except Exception as exc:
            raise SpeechBackendUnavailable("Pocket TTS model is unavailable") from exc
        self._model = model
        self._voice_state = voice_state
        return model, voice_state

    def render_wav(self, text: str, output: str | os.PathLike[str]) -> None:
        """Render one utterance to a caller-owned temporary WAV path."""

        clean = prepare_spoken_text(text)
        if not clean.strip():
            raise SynthesisError("No speakable text after sanitization")
        model, voice_state = self._load()
        try:
            import scipy.io.wavfile

            audio = model.generate_audio(voice_state, clean)
            if hasattr(audio, "detach"):
                audio = audio.detach()
            if hasattr(audio, "cpu"):
                audio = audio.cpu()
            if hasattr(audio, "numpy"):
                audio = audio.numpy()
            scipy.io.wavfile.write(str(output), model.sample_rate, audio)
        except SpeechAdapterError:
            raise
        except Exception as exc:
            raise SynthesisError("Pocket TTS synthesis failed") from exc

    def synthesize(self, text: str) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix="hikari-tts-") as temp_dir:
                output = os.path.join(temp_dir, "speech.wav")
                self.render_wav(text, output)
                subprocess.run(
                    ["/usr/bin/afplay", "-r", "1.0", output],
                    shell=False,
                    check=True,
                    capture_output=True,
                )
        except subprocess.CalledProcessError as exc:
            raise SynthesisError("Pocket TTS playback failed") from exc
        except SpeechAdapterError:
            raise


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

    Supported backends are ``macos-say`` and optional local ``pocket-tts``.

    Raises:
        SpeechBackendUnavailable: if the backend name is unknown.
    """
    if backend_name == "macos-say":
        return MacOSSayTTSAdapter()
    if backend_name == "pocket-tts":
        return PocketTTSAdapter()
    raise SpeechBackendUnavailable(f"unknown TTS backend: {backend_name}")
