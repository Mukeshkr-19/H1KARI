"""Voice runtime regressions that do not touch live audio devices."""

from __future__ import annotations

import numpy as np

from core.orchestrator import HIKARI_Orchestrator
from core.voice_memory import VoiceMemory


def test_voice_similarity_keeps_euclidean_score_when_cosine_degenerate():
    voice_memory = object.__new__(VoiceMemory)
    current = np.array([1.0, 2.0, 3.0])
    profile = {
        "avg_features": [1.0, 2.0, 3.0],
        "std_features": [1.0, 1.0, 1.0],
    }

    assert voice_memory._compute_similarity(current, profile) > 0.0


def test_orchestrator_exposes_menu_bar_voice_loop_entrypoint():
    assert callable(getattr(HIKARI_Orchestrator, "run_voice_loop", None))
