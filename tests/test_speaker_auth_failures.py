"""Speaker enrollment and model failures must remain local and fail closed."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
from types import ModuleType

import pytest

from core import speaker_auth


@pytest.fixture
def private_paths(tmp_path: Path, monkeypatch):
    enrollment = tmp_path / "private" / "voice_auth.json"
    cache = tmp_path / "private" / "hf_cache"
    monkeypatch.setattr(speaker_auth, "VOICE_AUTH_FILE", enrollment)
    monkeypatch.setattr(speaker_auth, "HF_CACHE_DIR", cache)
    return enrollment, cache


def test_enrollment_round_trip_and_owner_only_permissions(private_paths):
    enrollment, _cache = private_paths
    auth = speaker_auth.SpeakerAuth()

    auth.enroll_from_embeddings([[1.0, 0.0], [0.8, 0.2]])

    restored = speaker_auth.SpeakerAuth()
    assert restored.is_enrolled() is True
    assert restored.enrollment_version() == 2
    assert restored.verify_embedding([0.9, 0.1]).ok is True
    assert stat.S_IMODE(enrollment.stat().st_mode) == 0o600


def test_enrollment_keeps_distinct_voice_templates(private_paths):
    auth = speaker_auth.SpeakerAuth(threshold=0.9)
    auth.enroll_from_embeddings([[1.0, 0.0], [0.0, 1.0]])

    restored = speaker_auth.SpeakerAuth(threshold=0.9)

    assert restored.verify_embedding([1.0, 0.0]).ok is True
    assert restored.verify_embedding([0.0, 1.0]).ok is True


def test_legacy_centroid_profile_remains_loadable(private_paths):
    enrollment, _cache = private_paths
    enrollment.parent.mkdir(parents=True, exist_ok=True)
    enrollment.write_text(
        json.dumps({"version": 1, "threshold": 0.78, "embedding": [1.0, 0.0]}),
        encoding="utf-8",
    )

    auth = speaker_auth.SpeakerAuth(threshold=0.5)

    assert auth.is_enrolled() is True
    assert auth.enrollment_version() == 1
    assert auth.verify_embedding([1.0, 0.0]).ok is True


def test_malformed_template_profile_fails_closed(private_paths):
    enrollment, _cache = private_paths
    enrollment.parent.mkdir(parents=True, exist_ok=True)
    enrollment.write_text(
        json.dumps({"version": 2, "templates": [[1.0, 0.0], [1.0]]}),
        encoding="utf-8",
    )

    auth = speaker_auth.SpeakerAuth()

    assert auth.is_enrolled() is False


def test_enrollment_template_count_is_bounded(private_paths):
    auth = speaker_auth.SpeakerAuth()

    with pytest.raises(ValueError, match="Too many enrollment embeddings"):
        auth.enroll_from_embeddings([[1.0, 0.0]] * 6)


def test_bounded_window_verification_uses_best_owner_match(private_paths):
    auth = speaker_auth.SpeakerAuth(threshold=0.5)
    auth.enroll_from_embeddings([[1.0, 0.0]])

    result = auth.verify_embeddings([[0.0, 1.0], [0.9, 0.1], [-1.0, 0.0]])

    assert result.ok is True
    assert result.reason == "ok_window"
    assert result.score > 0.5


def test_verification_windows_are_bounded_and_cover_long_utterance(private_paths, monkeypatch):
    auth = speaker_auth.SpeakerAuth()
    payloads = []

    def embed(audio, *, sample_rate=16000):
        payload = audio.get_raw_data(convert_rate=sample_rate, convert_width=2)
        payloads.append(payload)
        return [float(payload[0]), 1.0]

    monkeypatch.setattr(auth, "embedding_from_speech_recognition_audio", embed)

    class Audio:
        def get_raw_data(self, *, convert_rate, convert_width):
            assert (convert_rate, convert_width) == (16000, 2)
            return b"\x01" * 64_000 + b"\x02" * 64_000 + b"\x03" * 64_000

    embeddings = auth.verification_embeddings_from_speech_recognition_audio(Audio())

    assert len(embeddings) == 3
    assert all(len(payload) == 64_000 for payload in payloads)
    assert [payload[0] for payload in payloads] == [1, 2, 3]


def test_default_threshold_matches_speechbrain_verifier_boundary(private_paths):
    auth = speaker_auth.SpeakerAuth()

    assert auth.threshold == speaker_auth.DEFAULT_SPEAKER_THRESHOLD == 0.25


@pytest.mark.parametrize("threshold", [False, 0.0, 1.0, float("nan")])
def test_invalid_speaker_threshold_is_rejected(private_paths, threshold):
    with pytest.raises(ValueError, match="speaker threshold is invalid"):
        speaker_auth.SpeakerAuth(threshold=threshold)


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        json.dumps([]),
        json.dumps({"embedding": []}),
        json.dumps({"embedding": [1.0, "invalid"]}),
        '{"embedding": [NaN, 1.0]}',
    ],
)
def test_malformed_enrollment_is_not_treated_as_identity(private_paths, payload):
    enrollment, _cache = private_paths
    enrollment.parent.mkdir(parents=True, exist_ok=True)
    enrollment.write_text(payload, encoding="utf-8")

    auth = speaker_auth.SpeakerAuth()

    assert auth.is_enrolled() is False
    assert auth.verify_embedding([1.0, 0.0]).reason == "not_enrolled"


@pytest.mark.parametrize(
    "embeddings,match",
    [
        ([], "No embeddings"),
        ([[1.0, float("nan")]], "finite numbers"),
        ([[True, 0.0]], "finite numbers"),
        ([[1.0, 0.0], [1.0]], "consistent dimensions"),
    ],
)
def test_invalid_new_enrollment_is_rejected(private_paths, embeddings, match):
    auth = speaker_auth.SpeakerAuth()

    with pytest.raises(ValueError, match=match):
        auth.enroll_from_embeddings(embeddings)


def test_model_loader_uses_exact_id_and_private_cache_without_downloading(
    private_paths,
    monkeypatch,
):
    _enrollment, cache = private_paths
    calls = []
    model = object()

    class EncoderClassifier:
        @classmethod
        def from_hparams(cls, **kwargs):
            calls.append(kwargs)
            return model

    class FetchConfig:
        def __init__(self, *, revision=None, huggingface_cache_dir=None):
            self.revision = revision
            self.huggingface_cache_dir = huggingface_cache_dir

    speechbrain_module = ModuleType("speechbrain")
    inference_module = ModuleType("speechbrain.inference")
    speaker_module = ModuleType("speechbrain.inference.speaker")
    speaker_module.EncoderClassifier = EncoderClassifier
    utils_module = ModuleType("speechbrain.utils")
    fetching_module = ModuleType("speechbrain.utils.fetching")
    fetching_module.FetchConfig = FetchConfig
    monkeypatch.setitem(sys.modules, "speechbrain", speechbrain_module)
    monkeypatch.setitem(sys.modules, "speechbrain.inference", inference_module)
    monkeypatch.setitem(sys.modules, "speechbrain.inference.speaker", speaker_module)
    monkeypatch.setitem(sys.modules, "speechbrain.utils", utils_module)
    monkeypatch.setitem(sys.modules, "speechbrain.utils.fetching", fetching_module)
    for name in ("HF_HOME", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE"):
        monkeypatch.delenv(name, raising=False)

    auth = speaker_auth.SpeakerAuth()
    auth._lazy_load_model()

    assert auth._model is model
    assert len(calls) == 1
    assert calls[0]["source"] == "speechbrain/spkrec-ecapa-voxceleb"
    assert calls[0]["savedir"] == str(cache / "speechbrain_spkrec_ecapa")
    fetch_config = calls[0]["fetch_config"]
    assert fetch_config.revision == speaker_auth.SPEECHBRAIN_ECAPA_REVISION
    assert fetch_config.huggingface_cache_dir == str(cache)
    assert os.environ["HF_HOME"] == str(cache)
    assert os.environ["HUGGINGFACE_HUB_CACHE"] == str(cache)
    assert os.environ["TRANSFORMERS_CACHE"] == str(cache)


def test_model_load_failure_reports_unavailable(private_paths, monkeypatch):
    auth = speaker_auth.SpeakerAuth()

    def fail():
        raise RuntimeError("offline")

    monkeypatch.setattr(auth, "_lazy_load_model", fail)

    assert auth.available() is False
    assert auth._model is None
