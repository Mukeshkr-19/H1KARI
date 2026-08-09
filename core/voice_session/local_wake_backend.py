"""Frame-fed, fail-closed adapter for optional ``local-wake`` detection.

The adapter never opens a microphone, calls ``lwake.listen``/``lwake.record``,
downloads assets, or writes audio. It accepts only bounded 16 kHz mono PCM16
buffers already owned by Hikari capture and reads explicit private reference
WAV files when an operator opts in.
"""

from __future__ import annotations

import importlib.util
import importlib.metadata
import math
import os
import wave
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable, Mapping, Optional, Protocol, Sequence

from core.runtime_paths import hikari_home
from core.speech_adapters import WakeTranscriptionEvidence
from core.voice_safety.wake_gate import WakeSafetyGate

LOCAL_WAKE_VERSION = "0.1.2"
LOCAL_WAKE_ENV = "HIKARI_VOICE_WAKE_BACKEND"
LOCAL_WAKE_DIR_ENV = "HIKARI_LOCAL_WAKE_DIR"
SAMPLE_RATE = 16_000
SAMPLE_WIDTH = 2
CHANNELS = 1
MIN_POSITIVE_REFERENCES = 3
MAX_POSITIVE_REFERENCES = 8
MIN_NEGATIVE_REFERENCES = 8
MAX_NEGATIVE_REFERENCES = 64
MIN_AUDIO_SAMPLES = 6_400
MAX_AUDIO_SAMPLES = 64_000
DEFAULT_FALSE_ACCEPT_CEILING = 0.01
DEFAULT_MIN_DISTANCE_MARGIN = 0.005
DEFAULT_MAX_VAD_AGE_NS = 750_000_000


class LocalWakeStatusCode(StrEnum):
    PACKAGE_UNAVAILABLE = "package_unavailable"
    REFERENCES_MISSING = "references_missing"
    CALIBRATION_INCOMPLETE = "calibration_incomplete"
    READY_DISABLED = "ready_disabled"
    ACTIVE = "active"


@dataclass(frozen=True, repr=False)
class LocalWakeStatus:
    code: LocalWakeStatusCode
    package_version: str = ""
    positive_count: int = 0
    negative_count: int = 0
    calibrated: bool = False
    enabled: bool = False

    def __repr__(self) -> str:
        return (
            "LocalWakeStatus("
            f"code={self.code.value!r}, has_package_version={bool(self.package_version)}, "
            f"positive_count={self.positive_count}, negative_count={self.negative_count}, "
            f"calibrated={self.calibrated}, enabled={self.enabled})"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "package_version": self.package_version,
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "calibrated": self.calibrated,
            "enabled": self.enabled,
        }


@dataclass(frozen=True, repr=False)
class LocalWakeCalibration:
    calibrated: bool
    distance_threshold: float = 0.0
    similarity_threshold: float = 1.0
    false_accept_rate: float = 1.0
    positive_accept_rate: float = 0.0
    positive_count: int = 0
    negative_count: int = 0

    def __repr__(self) -> str:
        return (
            "LocalWakeCalibration("
            f"calibrated={self.calibrated}, false_accept_rate={self.false_accept_rate:.4f}, "
            f"positive_accept_rate={self.positive_accept_rate:.4f}, "
            f"positive_count={self.positive_count}, negative_count={self.negative_count})"
        )


class LocalWakeFeatureDetector(Protocol):
    def extract(self, normalized_audio: Sequence[float], *, sample_rate: int) -> object: ...
    def distance(self, candidate: object, reference: object) -> float: ...


class LwakeFeatureDetector:
    """Lazy score-only bridge; never imports or calls local-wake stream helpers."""

    def __init__(self) -> None:
        # Import only feature extraction.  ``local-wake`` delegates its DTW
        # distance function to librosa, whose Numba cache setup can fail during
        # import on some packaged Python runtimes.  Keep scoring local and
        # deterministic so that a dependency import issue cannot masquerade as
        # a bad calibration sample.
        from lwake.features import extract_embedding_features

        self._extract = extract_embedding_features

    def extract(self, normalized_audio: Sequence[float], *, sample_rate: int) -> object:
        import numpy as np

        samples = np.asarray(tuple(normalized_audio), dtype=np.float32)
        return self._extract(y=samples, sample_rate=sample_rate)

    def distance(self, candidate: object, reference: object) -> float:
        return _cosine_dtw_normalized_distance(candidate, reference)


def _cosine_dtw_normalized_distance(candidate: object, reference: object) -> float:
    """Return local-wake-compatible cosine DTW distance without librosa I/O."""
    import numpy as np

    first = np.asarray(candidate, dtype=np.float32)
    second = np.asarray(reference, dtype=np.float32)
    if first.ndim != 2 or second.ndim != 2 or first.shape[0] != second.shape[0]:
        raise ValueError("invalid_feature_shape")
    if first.shape[1] < 1 or second.shape[1] < 1:
        raise ValueError("empty_feature_sequence")

    first_norms = np.linalg.norm(first, axis=0)
    second_norms = np.linalg.norm(second, axis=0)
    if not np.all(np.isfinite(first_norms)) or not np.all(np.isfinite(second_norms)):
        raise ValueError("nonfinite_feature")
    if np.any(first_norms <= 0.0) or np.any(second_norms <= 0.0):
        raise ValueError("zero_feature_norm")

    similarity = (first.T @ second) / np.outer(first_norms, second_norms)
    costs = 1.0 - np.clip(similarity, -1.0, 1.0)
    if not np.all(np.isfinite(costs)):
        raise ValueError("nonfinite_feature")
    rows, columns = costs.shape
    cumulative = np.full((rows + 1, columns + 1), np.inf, dtype=np.float64)
    cumulative[0, 0] = 0.0
    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            cumulative[row, column] = float(costs[row - 1, column - 1]) + min(
                cumulative[row - 1, column],
                cumulative[row, column - 1],
                cumulative[row - 1, column - 1],
            )
    return float(cumulative[rows, columns] / (rows + columns))


def _pcm16_to_normalized(pcm: bytes) -> tuple[float, ...]:
    if not isinstance(pcm, bytes) or len(pcm) % 2 != 0:
        raise ValueError("invalid_pcm16")
    sample_count = len(pcm) // 2
    if sample_count < MIN_AUDIO_SAMPLES or sample_count > MAX_AUDIO_SAMPLES:
        raise ValueError("pcm_duration_out_of_range")
    import array

    samples = array.array("h")
    samples.frombytes(pcm)
    return tuple(float(value) / 32768.0 for value in samples)


def _read_reference_wav(path: Path) -> tuple[float, ...]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("invalid_reference_file")
    try:
        with wave.open(str(path), "rb") as stream:
            if (
                stream.getframerate() != SAMPLE_RATE
                or stream.getnchannels() != CHANNELS
                or stream.getsampwidth() != SAMPLE_WIDTH
                or stream.getcomptype() != "NONE"
            ):
                raise ValueError("invalid_reference_format")
            frames = stream.getnframes()
            if frames < MIN_AUDIO_SAMPLES or frames > MAX_AUDIO_SAMPLES:
                raise ValueError("invalid_reference_duration")
            pcm = stream.readframes(frames)
    except (OSError, EOFError, wave.Error) as exc:
        raise ValueError("unreadable_reference") from exc
    return _pcm16_to_normalized(pcm)


def _bounded_reference_files(folder: Path, *, maximum: int) -> list[Path]:
    if folder.is_symlink() or not folder.is_dir():
        return []
    try:
        files = sorted(
            path for path in folder.iterdir() if path.suffix.casefold() == ".wav"
        )
    except OSError:
        return []
    if len(files) > maximum:
        return []
    return files


def _calibrate(
    detector: LocalWakeFeatureDetector,
    positives: Sequence[object],
    negatives: Sequence[object],
    *,
    false_accept_ceiling: float,
    min_distance_margin: float,
) -> LocalWakeCalibration:
    if len(positives) < MIN_POSITIVE_REFERENCES or len(negatives) < MIN_NEGATIVE_REFERENCES:
        return LocalWakeCalibration(False, positive_count=len(positives), negative_count=len(negatives))
    try:
        positive_scores = [
            min(
                float(detector.distance(candidate, other))
                for other_index, other in enumerate(positives)
                if other_index != index
            )
            for index, candidate in enumerate(positives)
        ]
        negative_scores = [
            min(float(detector.distance(candidate, ref)) for ref in positives)
            for candidate in negatives
        ]
    except Exception:
        return LocalWakeCalibration(False, positive_count=len(positives), negative_count=len(negatives))
    scores = positive_scores + negative_scores
    if any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in scores):
        return LocalWakeCalibration(False, positive_count=len(positives), negative_count=len(negatives))
    positive_worst = max(positive_scores)
    negative_best = min(negative_scores)
    if negative_best - positive_worst < min_distance_margin:
        return LocalWakeCalibration(False, positive_count=len(positives), negative_count=len(negatives))
    threshold = (positive_worst + negative_best) / 2.0
    false_accepts = sum(score <= threshold for score in negative_scores)
    accepted_positives = sum(score <= threshold for score in positive_scores)
    false_accept_rate = false_accepts / len(negative_scores)
    positive_accept_rate = accepted_positives / len(positive_scores)
    calibrated = false_accept_rate <= false_accept_ceiling and positive_accept_rate >= 0.75
    return LocalWakeCalibration(
        calibrated,
        distance_threshold=threshold if calibrated else 0.0,
        similarity_threshold=(max(0.0, min(1.0, 1.0 - threshold)) if calibrated else 1.0),
        false_accept_rate=false_accept_rate,
        positive_accept_rate=positive_accept_rate,
        positive_count=len(positives),
        negative_count=len(negatives),
    )


class LocalWakePcmBackend:
    """Calibrated detector over existing bounded Hikari PCM buffers."""

    def __init__(
        self,
        *,
        detector: LocalWakeFeatureDetector,
        positive_features: Sequence[object],
        negative_features: Sequence[object],
        enabled: bool,
        package_version: str = LOCAL_WAKE_VERSION,
        false_accept_ceiling: float = DEFAULT_FALSE_ACCEPT_CEILING,
        min_distance_margin: float = DEFAULT_MIN_DISTANCE_MARGIN,
        max_vad_age_ns: int = DEFAULT_MAX_VAD_AGE_NS,
    ) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a boolean")
        if (
            isinstance(false_accept_ceiling, bool)
            or not isinstance(false_accept_ceiling, (int, float))
            or not 0.0 <= float(false_accept_ceiling) <= 0.05
        ):
            raise ValueError("invalid_false_accept_ceiling")
        if (
            isinstance(min_distance_margin, bool)
            or not isinstance(min_distance_margin, (int, float))
            or not 0.0 < float(min_distance_margin) <= 0.25
        ):
            raise ValueError("invalid_distance_margin")
        if isinstance(max_vad_age_ns, bool) or not isinstance(max_vad_age_ns, int) or max_vad_age_ns <= 0:
            raise ValueError("invalid_max_vad_age_ns")
        self._detector = detector
        self._positive_features = tuple(positive_features)
        self._calibration = _calibrate(
            detector,
            self._positive_features,
            tuple(negative_features),
            false_accept_ceiling=float(false_accept_ceiling),
            min_distance_margin=float(min_distance_margin),
        )
        self._enabled = enabled
        self._package_version = package_version
        self._max_vad_age_ns = max_vad_age_ns

    @property
    def calibration(self) -> LocalWakeCalibration:
        return self._calibration

    @property
    def status(self) -> LocalWakeStatus:
        if not self._calibration.calibrated:
            code = LocalWakeStatusCode.CALIBRATION_INCOMPLETE
        elif not self._enabled:
            code = LocalWakeStatusCode.READY_DISABLED
        else:
            code = LocalWakeStatusCode.ACTIVE
        return LocalWakeStatus(
            code,
            package_version=self._package_version,
            positive_count=self._calibration.positive_count,
            negative_count=self._calibration.negative_count,
            calibrated=self._calibration.calibrated,
            enabled=self._enabled and self._calibration.calibrated,
        )

    def build_wake_gate(self, *, clock: Callable[[], int]) -> WakeSafetyGate:
        return WakeSafetyGate(
            wake_name="Hikari",
            aliases=(),
            confidence_threshold=self._calibration.similarity_threshold,
            calibrated=self._enabled and self._calibration.calibrated,
            clock=clock,
        )

    def evaluate_pcm16(
        self,
        pcm: bytes,
        *,
        observed_monotonic_ns: int,
        vad_observed_monotonic_ns: int,
        vad_has_speech: bool,
    ) -> Optional[WakeTranscriptionEvidence]:
        if not self._enabled or not self._calibration.calibrated or not self._positive_features:
            return None
        if (
            isinstance(observed_monotonic_ns, bool)
            or not isinstance(observed_monotonic_ns, int)
            or observed_monotonic_ns < 0
            or isinstance(vad_observed_monotonic_ns, bool)
            or not isinstance(vad_observed_monotonic_ns, int)
            or vad_observed_monotonic_ns < 0
            or not isinstance(vad_has_speech, bool)
            or not vad_has_speech
            or vad_observed_monotonic_ns > observed_monotonic_ns
            or observed_monotonic_ns - vad_observed_monotonic_ns > self._max_vad_age_ns
        ):
            return None
        try:
            normalized = _pcm16_to_normalized(pcm)
            candidate = self._detector.extract(normalized, sample_rate=SAMPLE_RATE)
            distance = min(
                float(self._detector.distance(candidate, reference))
                for reference in self._positive_features
            )
        except Exception:
            return None
        if (
            not math.isfinite(distance)
            or distance < 0.0
            or distance > 1.0
            or distance > self._calibration.distance_threshold
        ):
            return None
        return WakeTranscriptionEvidence(
            calibrated_score=max(0.0, min(1.0, 1.0 - distance)),
            observed_monotonic_ns=observed_monotonic_ns,
            vad_observed_monotonic_ns=vad_observed_monotonic_ns,
            vad_has_speech=True,
        )


@dataclass(frozen=True, repr=False)
class LocalWakeBackendLoad:
    status: LocalWakeStatus
    backend: Optional[LocalWakePcmBackend] = None

    def __repr__(self) -> str:
        return f"LocalWakeBackendLoad(status={self.status.code.value!r}, has_backend={self.backend is not None})"


def load_local_wake_backend(
    reference_root: Path,
    *,
    enabled: bool,
    detector_factory: Callable[[], LocalWakeFeatureDetector] = LwakeFeatureDetector,
    package_available: Optional[Callable[[], bool]] = None,
    package_version: Optional[Callable[[], str]] = None,
) -> LocalWakeBackendLoad:
    """Load explicit private samples; all malformed states fail closed silently."""
    available = package_available or (lambda: importlib.util.find_spec("lwake") is not None)
    try:
        version = package_version or (lambda: importlib.metadata.version("local-wake"))
        if not available() or version() != LOCAL_WAKE_VERSION:
            return LocalWakeBackendLoad(LocalWakeStatus(LocalWakeStatusCode.PACKAGE_UNAVAILABLE))
    except Exception:
        return LocalWakeBackendLoad(LocalWakeStatus(LocalWakeStatusCode.PACKAGE_UNAVAILABLE))
    positives = _bounded_reference_files(reference_root / "positive", maximum=MAX_POSITIVE_REFERENCES)
    negatives = _bounded_reference_files(reference_root / "negative", maximum=MAX_NEGATIVE_REFERENCES)
    if len(positives) < MIN_POSITIVE_REFERENCES or len(negatives) < MIN_NEGATIVE_REFERENCES:
        return LocalWakeBackendLoad(
            LocalWakeStatus(
                LocalWakeStatusCode.REFERENCES_MISSING,
                package_version=LOCAL_WAKE_VERSION,
                positive_count=len(positives),
                negative_count=len(negatives),
            )
        )
    try:
        detector = detector_factory()
        positive_features = [
            detector.extract(_read_reference_wav(path), sample_rate=SAMPLE_RATE)
            for path in positives
        ]
        negative_features = [
            detector.extract(_read_reference_wav(path), sample_rate=SAMPLE_RATE)
            for path in negatives
        ]
        backend = LocalWakePcmBackend(
            detector=detector,
            positive_features=positive_features,
            negative_features=negative_features,
            enabled=enabled,
        )
    except Exception:
        return LocalWakeBackendLoad(
            LocalWakeStatus(
                LocalWakeStatusCode.CALIBRATION_INCOMPLETE,
                package_version=LOCAL_WAKE_VERSION,
                positive_count=len(positives),
                negative_count=len(negatives),
            )
        )
    return LocalWakeBackendLoad(backend.status, backend if backend.calibration.calibrated else None)


def resolve_local_wake_reference_root(
    *, environ: Optional[Mapping[str, str]] = None, private_home: Optional[Path] = None
) -> Path:
    env = os.environ if environ is None else environ
    root = (private_home or hikari_home(environ=env)).expanduser().resolve()
    configured = env.get(LOCAL_WAKE_DIR_ENV, "").strip()
    candidate = Path(configured).expanduser().resolve() if configured else root / "voice" / "local-wake"
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("local_wake_path_outside_private_runtime") from exc
    return candidate


def local_wake_opted_in(*, environ: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get(LOCAL_WAKE_ENV, "").strip() == "local-wake"


__all__ = [
    "LOCAL_WAKE_DIR_ENV",
    "LOCAL_WAKE_ENV",
    "LOCAL_WAKE_VERSION",
    "LocalWakeBackendLoad",
    "LocalWakeCalibration",
    "LocalWakeFeatureDetector",
    "LocalWakePcmBackend",
    "LocalWakeStatus",
    "LocalWakeStatusCode",
    "load_local_wake_backend",
    "local_wake_opted_in",
    "resolve_local_wake_reference_root",
]
