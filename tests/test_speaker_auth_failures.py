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
    assert restored.verify_embedding([0.9, 0.1]).ok is True
    assert stat.S_IMODE(enrollment.stat().st_mode) == 0o600


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

    speechbrain_module = ModuleType("speechbrain")
    inference_module = ModuleType("speechbrain.inference")
    speaker_module = ModuleType("speechbrain.inference.speaker")
    speaker_module.EncoderClassifier = EncoderClassifier
    monkeypatch.setitem(sys.modules, "speechbrain", speechbrain_module)
    monkeypatch.setitem(sys.modules, "speechbrain.inference", inference_module)
    monkeypatch.setitem(sys.modules, "speechbrain.inference.speaker", speaker_module)
    for name in ("HF_HOME", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE"):
        monkeypatch.delenv(name, raising=False)

    auth = speaker_auth.SpeakerAuth()
    auth._lazy_load_model()

    assert auth._model is model
    assert calls == [
        {
            "source": "speechbrain/spkrec-ecapa-voxceleb",
            "savedir": str(cache / "speechbrain_spkrec_ecapa"),
        }
    ]
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
