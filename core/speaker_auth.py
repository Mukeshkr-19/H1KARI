"""
Local speaker verification (voice lock) for HIKARI.

Goal: gate wake-word activation and command handling so HIKARI responds only to the
enrolled speaker — stored locally beside the private brain.

Implementation notes:
- Uses SpeechBrain ECAPA speaker embeddings when available.
- Stores only embeddings (not raw audio) in the private legacy runtime folder.
- Designed to work with SpeechRecognition's `AudioData` (raw PCM) inputs.
"""

from __future__ import annotations

import json
import os
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from core.runtime_paths import legacy_data_dir


VOICE_AUTH_FILE = legacy_data_dir() / "voice_auth.json"
HF_CACHE_DIR = legacy_data_dir() / "hf_cache"


def _ensure_dirs():
    VOICE_AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    HF_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    return dot / denom if denom else 0.0


def _mean(vectors: List[List[float]]) -> List[float]:
    if not vectors:
        return []
    n = len(vectors)
    d = len(vectors[0])
    out = [0.0] * d
    for vec in vectors:
        for i in range(d):
            out[i] += float(vec[i])
    for i in range(d):
        out[i] /= n
    return out


@dataclass
class VerifyResult:
    ok: bool
    score: float
    threshold: float
    reason: str


class SpeakerAuth:
    """
    Speaker verification wrapper.

    Typical thresholds for ECAPA cosine similarity vary by environment.
    Start conservative and tune with real recordings.
    """

    def __init__(self, *, threshold: float = 0.78):
        self.threshold = threshold
        self._model = None
        self._enrolled_embedding: Optional[List[float]] = None
        _ensure_dirs()

    def available(self) -> bool:
        try:
            self._lazy_load_model()
            return self._model is not None
        except Exception:
            return False

    def is_enrolled(self) -> bool:
        if self._enrolled_embedding is not None:
            return True
        self._load_enrollment()
        return self._enrolled_embedding is not None

    def enroll_from_embeddings(self, embeddings: List[List[float]]):
        if not embeddings:
            raise ValueError("No embeddings provided for enrollment")
        self._enrolled_embedding = _mean(embeddings)
        self._save_enrollment()

    def verify_embedding(self, embedding: List[float]) -> VerifyResult:
        if not self.is_enrolled():
            return VerifyResult(
                ok=False,
                score=0.0,
                threshold=self.threshold,
                reason="not_enrolled",
            )
        if not embedding:
            return VerifyResult(
                ok=False,
                score=0.0,
                threshold=self.threshold,
                reason="empty_embedding",
            )
        score = _cosine_similarity(self._enrolled_embedding or [], embedding)
        return VerifyResult(
            ok=score >= self.threshold,
            score=score,
            threshold=self.threshold,
            reason="ok" if score >= self.threshold else "below_threshold",
        )

    def embedding_from_speech_recognition_audio(
        self, audio, *, sample_rate: int = 16000
    ) -> List[float]:
        """
        Convert SpeechRecognition AudioData -> embedding.
        The `audio` object is expected to have `get_raw_data(convert_rate=..., convert_width=2)`.
        """
        self._lazy_load_model()
        if self._model is None:
            raise RuntimeError("Speaker model unavailable")

        # SpeechRecognition's AudioData -> 16-bit PCM mono at 16k
        pcm = audio.get_raw_data(convert_rate=sample_rate, convert_width=2)

        import numpy as np

        wav = np.frombuffer(pcm, dtype=np.int16).astype("float32") / 32768.0

        import torch

        waveform = torch.from_numpy(wav).unsqueeze(0)  # (1, T)

        # SpeechBrain 1.1+: encode_batch(wavs, wav_lens=None, ...); omit wav_lens for full-length batch
        with torch.inference_mode():
            emb = self._model.encode_batch(waveform)
        emb = emb.squeeze(0).squeeze(0).cpu().numpy()
        return [float(x) for x in emb.tolist()]

    def _lazy_load_model(self):
        if self._model is not None:
            return

        # Keep Hugging Face cache inside the project (local-first requirement)
        os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))
        os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(HF_CACHE_DIR))
        os.environ.setdefault("TRANSFORMERS_CACHE", str(HF_CACHE_DIR))

        try:
            from speechbrain.inference.speaker import EncoderClassifier
        except Exception as e:
            raise RuntimeError(
                "speechbrain not installed (speaker verification unavailable)"
            ) from e

        # Downloads model weights into HF_HOME on first run
        self._model = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=str(HF_CACHE_DIR / "speechbrain_spkrec_ecapa"),
        )

    def _load_enrollment(self):
        try:
            if not VOICE_AUTH_FILE.exists():
                return
            data = json.loads(VOICE_AUTH_FILE.read_text(encoding="utf-8"))
            emb = data.get("embedding")
            if isinstance(emb, list) and emb and all(
                isinstance(x, (int, float)) for x in emb[:10]
            ):
                self._enrolled_embedding = [float(x) for x in emb]
        except Exception:
            return

    def _save_enrollment(self):
        if not self._enrolled_embedding:
            return
        payload = {
            "version": 1,
            "threshold": self.threshold,
            "embedding": self._enrolled_embedding,
        }
        VOICE_AUTH_FILE.write_text(json.dumps(payload), encoding="utf-8")
