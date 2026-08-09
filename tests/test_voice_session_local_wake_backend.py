from __future__ import annotations

import inspect
import math
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.speech_adapters import LocalTranscriptionResult, WakeTranscriptionEvidence
from core.voice_safety.contracts import OwnerVerification, PlaybackState
from core.voice_session import local_wake_backend as module
from core.voice_session.local_wake_backend import (
    LocalWakeBackendLoad,
    LocalWakePcmBackend,
    LocalWakeStatus,
    LocalWakeStatusCode,
    load_local_wake_backend,
    local_wake_opted_in,
    resolve_local_wake_reference_root,
)
from core.voice_session.wake_admission import admit_local_wake
from services import hikari_daemon as daemon


def _pcm(value: int, samples: int = 12_800) -> bytes:
    return int(value).to_bytes(2, "little", signed=True) * samples


def _write_wav(path: Path, value: int, *, sample_rate: int = 16_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(_pcm(value))


class MeanDistanceDetector:
    def __init__(self) -> None:
        self.sample_rates: list[int] = []
        self.last_samples = ()

    def extract(self, normalized_audio, *, sample_rate: int):
        self.sample_rates.append(sample_rate)
        self.last_samples = tuple(normalized_audio)
        return sum(self.last_samples) / len(self.last_samples)

    def distance(self, candidate, reference) -> float:
        return abs(float(candidate) - float(reference))


def _reference_tree(root: Path, *, overlapping_negative: bool = False) -> None:
    for index, value in enumerate((900, 1000, 1100, 950)):
        _write_wav(root / "positive" / f"p{index}.wav", value)
    negative_values = (
        (1000,) * 8
        if overlapping_negative
        else (-2400, -2200, -2000, -1800, 2500, 2700, 2900, 3100)
    )
    for index, value in enumerate(negative_values):
        _write_wav(root / "negative" / f"n{index}.wav", value)


def test_local_cosine_dtw_distance_is_finite_and_symmetric() -> None:
    np = pytest.importorskip("numpy")
    first = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    second = np.asarray([[1.0, 1.0], [0.0, 0.0]], dtype=np.float32)
    forward = module._cosine_dtw_normalized_distance(first, second)
    backward = module._cosine_dtw_normalized_distance(second, first)
    assert math.isfinite(forward)
    assert math.isclose(forward, backward)
    assert module._cosine_dtw_normalized_distance(first, first) == 0.0


def test_package_unavailable_and_missing_references_are_distinct(tmp_path: Path) -> None:
    unavailable = load_local_wake_backend(
        tmp_path,
        enabled=True,
        package_available=lambda: False,
        detector_factory=MeanDistanceDetector,
    )
    assert unavailable.status.code is LocalWakeStatusCode.PACKAGE_UNAVAILABLE
    missing = load_local_wake_backend(
        tmp_path,
        enabled=True,
        package_available=lambda: True,
        detector_factory=MeanDistanceDetector,
    )
    assert missing.status.code is LocalWakeStatusCode.REFERENCES_MISSING
    assert missing.backend is None


def test_unexpected_package_version_fails_closed(tmp_path: Path) -> None:
    loaded = load_local_wake_backend(
        tmp_path,
        enabled=True,
        package_available=lambda: True,
        package_version=lambda: "0.1.1",
        detector_factory=MeanDistanceDetector,
    )
    assert loaded.status.code is LocalWakeStatusCode.PACKAGE_UNAVAILABLE
    assert loaded.backend is None


def test_positive_and_negative_evidence_calibrates_but_opt_in_controls_activation(tmp_path: Path) -> None:
    _reference_tree(tmp_path)
    disabled = load_local_wake_backend(
        tmp_path,
        enabled=False,
        package_available=lambda: True,
        detector_factory=MeanDistanceDetector,
    )
    assert disabled.status.code is LocalWakeStatusCode.READY_DISABLED
    assert disabled.backend is not None
    assert disabled.backend.evaluate_pcm16(
        _pcm(1000),
        observed_monotonic_ns=100,
        vad_observed_monotonic_ns=90,
        vad_has_speech=True,
    ) is None

    active = load_local_wake_backend(
        tmp_path,
        enabled=True,
        package_available=lambda: True,
        detector_factory=MeanDistanceDetector,
    )
    assert active.status.code is LocalWakeStatusCode.ACTIVE
    assert active.backend is not None


def test_overlapping_negative_scores_fail_false_accept_calibration(tmp_path: Path) -> None:
    _reference_tree(tmp_path, overlapping_negative=True)
    loaded = load_local_wake_backend(
        tmp_path,
        enabled=True,
        package_available=lambda: True,
        detector_factory=MeanDistanceDetector,
    )
    assert loaded.status.code is LocalWakeStatusCode.CALIBRATION_INCOMPLETE
    assert loaded.backend is None


def test_detector_emits_only_real_score_with_fresh_vad_and_16k_conversion(tmp_path: Path) -> None:
    _reference_tree(tmp_path)
    detector = MeanDistanceDetector()
    loaded = load_local_wake_backend(
        tmp_path,
        enabled=True,
        package_available=lambda: True,
        detector_factory=lambda: detector,
    )
    assert loaded.backend is not None
    evidence = loaded.backend.evaluate_pcm16(
        _pcm(1000),
        observed_monotonic_ns=1_000_000_000,
        vad_observed_monotonic_ns=950_000_000,
        vad_has_speech=True,
    )
    assert isinstance(evidence, WakeTranscriptionEvidence)
    assert detector.sample_rates[-1] == 16_000
    assert math.isclose(detector.last_samples[0], 1000 / 32768.0)
    expected_distance = min(abs((1000 - ref) / 32768.0) for ref in (900, 1000, 1100, 950))
    assert math.isclose(evidence.calibrated_score, 1.0 - expected_distance)


def test_false_positive_and_stale_vad_each_emit_no_evidence(tmp_path: Path) -> None:
    _reference_tree(tmp_path)
    loaded = load_local_wake_backend(
        tmp_path,
        enabled=True,
        package_available=lambda: True,
        detector_factory=MeanDistanceDetector,
    )
    assert loaded.backend is not None
    assert loaded.backend.evaluate_pcm16(
        _pcm(-2200),
        observed_monotonic_ns=1_000_000_000,
        vad_observed_monotonic_ns=990_000_000,
        vad_has_speech=True,
    ) is None
    assert loaded.backend.evaluate_pcm16(
        _pcm(1000),
        observed_monotonic_ns=1_000_000_000,
        vad_observed_monotonic_ns=100_000_000,
        vad_has_speech=True,
    ) is None


def test_malformed_reference_format_fails_closed_without_partial_backend(tmp_path: Path) -> None:
    _reference_tree(tmp_path)
    _write_wav(tmp_path / "positive" / "p0.wav", 1000, sample_rate=8_000)
    loaded = load_local_wake_backend(
        tmp_path,
        enabled=True,
        package_available=lambda: True,
        detector_factory=MeanDistanceDetector,
    )
    assert loaded.status.code is LocalWakeStatusCode.CALIBRATION_INCOMPLETE
    assert loaded.backend is None


def test_wake_gate_uses_calibrated_similarity_threshold_and_owner_policy(tmp_path: Path) -> None:
    _reference_tree(tmp_path)
    loaded = load_local_wake_backend(
        tmp_path,
        enabled=True,
        package_available=lambda: True,
        detector_factory=MeanDistanceDetector,
    )
    assert loaded.backend is not None
    gate = loaded.backend.build_wake_gate(clock=lambda: 1_000_000_000)
    assert gate.calibrated is True
    assert gate.aliases == ()
    assert gate.awaiting_command_window_ns == 9_000_000_000
    assert gate.confidence_threshold == loaded.backend.calibration.similarity_threshold


def test_local_evidence_still_requires_owner_idle_playback_and_one_time_window(
    tmp_path: Path,
) -> None:
    _reference_tree(tmp_path)
    loaded = load_local_wake_backend(
        tmp_path,
        enabled=True,
        package_available=lambda: True,
        detector_factory=MeanDistanceDetector,
    )
    assert loaded.backend is not None
    now_ns = 1_000_000_000
    evidence = loaded.backend.evaluate_pcm16(
        _pcm(1000),
        observed_monotonic_ns=now_ns,
        vad_observed_monotonic_ns=now_ns - 10_000_000,
        vad_has_speech=True,
    )
    assert evidence is not None
    transcription = LocalTranscriptionResult("", evidence)

    rejected_owner = admit_local_wake(
        gate=loaded.backend.build_wake_gate(clock=lambda: now_ns),
        transcription=transcription,
        detected_wake_name="Hikari",
        session_id="session-1",
        event_id="event-owner",
        owner_verification=OwnerVerification.rejected(),
        playback=PlaybackState.idle(),
        now_ns=now_ns,
    )
    assert rejected_owner.admitted is False

    rejected_playback = admit_local_wake(
        gate=loaded.backend.build_wake_gate(clock=lambda: now_ns),
        transcription=transcription,
        detected_wake_name="Hikari",
        session_id="session-1",
        event_id="event-playback",
        owner_verification=OwnerVerification.verified(),
        playback=PlaybackState.playing(),
        now_ns=now_ns,
    )
    assert rejected_playback.admitted is False

    gate = loaded.backend.build_wake_gate(clock=lambda: now_ns)
    accepted = admit_local_wake(
        gate=gate,
        transcription=transcription,
        detected_wake_name="Hikari",
        session_id="session-1",
        event_id="event-accepted",
        owner_verification=OwnerVerification.verified(),
        playback=PlaybackState.idle(),
        now_ns=now_ns,
    )
    assert accepted.admitted is True
    assert gate.confirm_command(now_ns=now_ns + 1).accepted is True
    assert gate.confirm_command(now_ns=now_ns + 2).accepted is False


def test_private_runtime_path_is_bounded_and_opt_in_is_exact(tmp_path: Path) -> None:
    env = {"HIKARI_HOME": str(tmp_path), "HIKARI_VOICE_WAKE_BACKEND": "local-wake"}
    assert resolve_local_wake_reference_root(environ=env, private_home=tmp_path) == (
        tmp_path / "voice" / "local-wake"
    )
    assert local_wake_opted_in(environ=env) is True
    assert local_wake_opted_in(environ={"HIKARI_VOICE_WAKE_BACKEND": "LOCAL-WAKE"}) is False
    with pytest.raises(ValueError, match="outside_private_runtime"):
        resolve_local_wake_reference_root(
            environ={"HIKARI_LOCAL_WAKE_DIR": str(tmp_path.parent)},
            private_home=tmp_path,
        )


def test_adapter_source_contains_no_microphone_stream_or_recording_call() -> None:
    source = inspect.getsource(module)
    assert "InputStream(" not in source
    assert "lwake.listen(" not in source
    assert "lwake.record(" not in source


class MagicMicrophone:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("local-wake must not create a second microphone stream")


def test_daemon_opt_in_skips_legacy_microphone_wake_stream(monkeypatch) -> None:
    monkeypatch.setenv("HIKARI_VOICE_WAKE_BACKEND", "local-wake")
    daemon.hikari_state = daemon.HikariState.LISTENING
    runtime = daemon._get_streaming_runtime()
    assert runtime.is_wake_listening
    microphone = MagicMicrophone()
    monkeypatch.setattr(daemon, "sr", SimpleNamespace(Microphone=microphone))
    sleep = []
    monkeypatch.setattr(daemon.time, "sleep", sleep.append)

    daemon._listen_for_wake_word()

    assert microphone.calls == 0
    assert sleep == [0.05]


def test_daemon_status_is_content_free_and_disabled_without_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("HIKARI_VOICE_WAKE_BACKEND", raising=False)
    daemon._local_wake_load = None
    expected = LocalWakeBackendLoad(
        LocalWakeStatus(
            LocalWakeStatusCode.READY_DISABLED,
            package_version="0.1.2",
            positive_count=4,
            negative_count=8,
            calibrated=True,
            enabled=False,
        )
    )
    monkeypatch.setattr(daemon, "_load_local_wake", lambda *, enabled: expected)

    status = daemon.get_local_wake_status()

    assert status == {
        "code": "ready_disabled",
        "package_version": "0.1.2",
        "positive_count": 4,
        "negative_count": 8,
        "calibrated": True,
        "enabled": False,
    }
    assert "path" not in status
    assert "identity" not in status
    assert "transcript" not in status
