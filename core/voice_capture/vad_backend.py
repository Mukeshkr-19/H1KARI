"""Speech-probability VAD backend with honest unavailable fallback.

Prefers onnxruntime + Silero when a reviewed model path is configured.
Does not download models. Does not claim capability without evidence.
"""

from __future__ import annotations

import math
import threading
from pathlib import Path
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional, Protocol, Tuple

# Silero v6 window constants (documented; model optional).
VAD_SAMPLE_RATE = 16_000
VAD_WINDOW_SAMPLES = 512
VAD_CONTEXT_SAMPLES = 64


class VadBackendReason(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    MODEL_MISSING = "model_missing"
    ORT_MISSING = "ort_missing"
    INVALID_PCM = "invalid_pcm"


@dataclass(frozen=True, repr=False)
class VadProbabilityResult:
    available: bool
    reason: VadBackendReason
    speech_probability: float = 0.0
    measurement_ready: bool = True
    measurement_duration_ms: Optional[float] = None

    def __repr__(self) -> str:
        return (
            f"VadProbabilityResult(available={self.available}, "
            f"reason={self.reason.value!r})"
        )


class SpeechProbabilityBackend(Protocol):
    @property
    def available(self) -> bool: ...

    def reset(self) -> None: ...

    def process_pcm16_mono(self, pcm: bytes, *, sample_rate: int = 16_000) -> VadProbabilityResult: ...


class UnavailableVadBackend:
    @property
    def available(self) -> bool:
        return False

    def reset(self) -> None:
        return None

    def process_pcm16_mono(self, pcm: bytes, *, sample_rate: int = 16_000) -> VadProbabilityResult:
        del pcm, sample_rate
        return VadProbabilityResult(False, VadBackendReason.UNAVAILABLE)


class EnergyFallbackVadBackend:
    """Deterministic energy heuristic used only when Silero is unavailable.

    Never advertised as Silero. Used for endpointing tests and degraded mode.
    """

    def __init__(self, *, threshold: float = 0.02) -> None:
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isfinite(float(threshold))
            or not 0.001 <= float(threshold) <= 0.5
        ):
            raise ValueError("invalid_energy_threshold")
        self._threshold = threshold
        self._available = True

    @property
    def available(self) -> bool:
        return self._available

    def reset(self) -> None:
        return None

    def process_pcm16_mono(self, pcm: bytes, *, sample_rate: int = 16_000) -> VadProbabilityResult:
        if sample_rate != VAD_SAMPLE_RATE or len(pcm) < 2 or len(pcm) % 2 != 0:
            return VadProbabilityResult(False, VadBackendReason.INVALID_PCM)
        import array

        samples = array.array("h")
        samples.frombytes(pcm)
        if not samples:
            return VadProbabilityResult(True, VadBackendReason.OK, 0.0)
        peak = max(abs(s) for s in samples) / 32768.0
        # Map peak to soft probability; not Silero.
        if peak < self._threshold:
            prob = min(0.2, peak / max(self._threshold, 1e-6) * 0.2)
        else:
            prob = min(0.99, 0.55 + (peak - self._threshold) * 2.0)
        if not math.isfinite(prob):
            prob = 0.0
        return VadProbabilityResult(True, VadBackendReason.OK, float(prob))


class SileroOnnxVadBackend:
    """Optional Silero ONNX backend. Model path must be explicitly provided."""

    def __init__(self, *, model_path: str) -> None:
        self._model_path = model_path
        self._lock = threading.Lock()
        self._session = None
        self._h = None
        self._c = None
        self._context = None
        self._buffer = None
        self._last_prob = 0.0
        self._reason = VadBackendReason.UNAVAILABLE
        self._init_session()

    def _init_session(self) -> None:
        from pathlib import Path

        path = Path(self._model_path)
        if not path.is_file():
            self._reason = VadBackendReason.MODEL_MISSING
            return
        try:
            import numpy as np
            import onnxruntime as ort
        except Exception:
            self._reason = VadBackendReason.ORT_MISSING
            return
        try:
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 1
            opts.inter_op_num_threads = 1
            opts.log_severity_level = 3
            self._session = ort.InferenceSession(str(path), sess_options=opts)
            self._np = np
            self.reset()
            self._reason = VadBackendReason.OK
        except Exception:
            self._session = None
            self._reason = VadBackendReason.UNAVAILABLE

    @property
    def available(self) -> bool:
        return self._session is not None and self._reason == VadBackendReason.OK

    def reset(self) -> None:
        if self._session is None:
            return
        np = self._np
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, VAD_CONTEXT_SAMPLES), dtype=np.float32)
        self._buffer = np.array([], dtype=np.float32)
        self._last_prob = 0.0

    def process_pcm16_mono(self, pcm: bytes, *, sample_rate: int = 16_000) -> VadProbabilityResult:
        if not self.available:
            return VadProbabilityResult(False, self._reason)
        if sample_rate != VAD_SAMPLE_RATE or len(pcm) < 2 or len(pcm) % 2 != 0:
            return VadProbabilityResult(False, VadBackendReason.INVALID_PCM)
        np = self._np
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        with self._lock:
            try:
                self._buffer = np.concatenate([self._buffer, audio])
                measurement_ready = False
                while len(self._buffer) >= VAD_WINDOW_SAMPLES:
                    window = self._buffer[:VAD_WINDOW_SAMPLES]
                    self._buffer = self._buffer[VAD_WINDOW_SAMPLES:]
                    self._last_prob, self._h, self._c, self._context = self._run_window(window)
                    measurement_ready = True
                if not math.isfinite(float(self._last_prob)):
                    raise ValueError("invalid_vad_output")
                return VadProbabilityResult(
                    True,
                    VadBackendReason.OK,
                    float(self._last_prob),
                    measurement_ready=measurement_ready,
                    measurement_duration_ms=(32.0 if measurement_ready else None),
                )
            except Exception:
                self._reason = VadBackendReason.UNAVAILABLE
                self._session = None
                self._h = None
                self._c = None
                self._context = None
                self._buffer = None
                self._last_prob = 0.0
                return VadProbabilityResult(False, VadBackendReason.UNAVAILABLE)

    def _run_window(self, audio_window) -> Tuple[float, object, object, object]:
        np = self._np
        audio_2d = audio_window.reshape(1, -1).astype(np.float32)
        x = np.concatenate([self._context, audio_2d], axis=1)
        output, new_h, new_c = self._session.run(
            None,
            {"input": x, "h": self._h, "c": self._c},
        )
        new_context = audio_2d[:, -VAD_CONTEXT_SAMPLES:]
        return float(output.reshape(-1)[0]), new_h, new_c, new_context


def installed_silero_model_path() -> Optional[str]:
    """Return faster-whisper's installed Silero asset without downloading."""
    try:
        from faster_whisper.utils import get_assets_path

        candidate = Path(get_assets_path()) / "silero_vad_v6.onnx"
    except Exception:
        return None
    return str(candidate) if candidate.is_file() else None


def create_vad_backend(*, model_path: Optional[str] = None, allow_energy_fallback: bool = False) -> SpeechProbabilityBackend:
    """Create the best available backend. Never pretends Silero when missing."""
    if model_path is None:
        model_path = installed_silero_model_path()
    if model_path:
        backend = SileroOnnxVadBackend(model_path=model_path)
        if backend.available:
            return backend
    if allow_energy_fallback:
        return EnergyFallbackVadBackend()
    return UnavailableVadBackend()
