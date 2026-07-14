"""Voice runtime regressions that do not touch live audio devices."""

from __future__ import annotations

import numpy as np

from core.orchestrator import HIKARI_Orchestrator
from core.voice_memory import VoiceFeatureExtractor, VoiceMemory


def test_voice_similarity_keeps_euclidean_score_when_cosine_degenerate():
    voice_memory = object.__new__(VoiceMemory)
    current = np.array([1.0, 2.0, 3.0])
    profile = {
        "avg_features": [1.0, 2.0, 3.0],
        "std_features": [1.0, 1.0, 1.0],
    }

    assert voice_memory._compute_similarity(current, profile) > 0.0


def test_voice_similarity_uses_enrolled_profile_direction():
    voice_memory = object.__new__(VoiceMemory)
    profile = {
        "avg_features": [1.0, 2.0, 3.0],
        "std_features": [1.0, 1.0, 1.0],
    }

    matching = voice_memory._compute_similarity(np.array([2.0, 4.0, 6.0]), profile)
    opposite = voice_memory._compute_similarity(
        np.array([-2.0, -4.0, -6.0]), profile
    )

    assert matching > 0.75
    assert opposite == 0.0


def test_pitch_estimate_returns_frequency_in_hz_and_rejects_silence():
    extractor = VoiceFeatureExtractor()
    target_hz = 200
    samples = np.arange(int(extractor.sample_rate * 0.1))
    audio = np.sin(2 * np.pi * target_hz * samples / extractor.sample_rate)

    assert 195 <= extractor._estimate_pitch(audio) <= 205
    assert extractor._estimate_pitch(np.zeros(512)) == 0.0


def test_orchestrator_exposes_menu_bar_voice_loop_entrypoint():
    assert callable(getattr(HIKARI_Orchestrator, "run_voice_loop", None))
