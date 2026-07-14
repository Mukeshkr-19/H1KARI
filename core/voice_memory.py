"""
HIKARI v2.0 - Deep Voice Memory System
Stores, learns, and improves voice recognition over time
Adapts when user is sick, tired, or voice changes
"""

import os
import json
import time
import hashlib
import struct
import wave
import math
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from pathlib import Path
import numpy as np

from core.runtime_paths import legacy_data_dir

DATA_DIR = legacy_data_dir()
VOICE_DIR = DATA_DIR / "voice_memory"
VOICE_PROFILES_FILE = VOICE_DIR / "profiles.json"
VOICE_SESSIONS_FILE = VOICE_DIR / "sessions.json"


class VoiceFeatureExtractor:
    """Extract voice features for speaker identification"""

    def __init__(self):
        self.sample_rate = 16000
        self.frame_length = 512
        self.hop_length = 256

    def extract_features(self, audio_data: np.ndarray) -> Dict[str, Any]:
        """Extract comprehensive voice features"""
        features = {}

        # Basic statistics
        features["rms"] = float(np.sqrt(np.mean(audio_data**2)))
        features["zero_crossing_rate"] = float(
            np.mean(np.abs(np.diff(np.signbit(audio_data))))
        )
        features["peak_amplitude"] = float(np.max(np.abs(audio_data)))
        features["mean_amplitude"] = float(np.mean(np.abs(audio_data)))
        features["std_amplitude"] = float(np.std(audio_data))

        # Energy features
        frames = self._frame_signal(audio_data)
        frame_energies = [np.sum(frame**2) for frame in frames]
        features["mean_energy"] = float(np.mean(frame_energies))
        features["std_energy"] = float(np.std(frame_energies))
        features["max_energy"] = float(np.max(frame_energies))
        features["energy_entropy"] = float(self._compute_entropy(frame_energies))

        # Spectral features (simplified)
        if len(audio_data) > 0:
            fft_data = np.abs(np.fft.rfft(audio_data))
            features["spectral_centroid"] = float(
                np.sum(np.arange(len(fft_data)) * fft_data) / (np.sum(fft_data) + 1e-10)
            )
            features["spectral_rolloff"] = float(self._spectral_rolloff(fft_data))
            features["spectral_bandwidth"] = float(
                np.sqrt(
                    np.sum(
                        (
                            (np.arange(len(fft_data)) - features["spectral_centroid"])
                            ** 2
                        )
                        * fft_data
                    )
                    / (np.sum(fft_data) + 1e-10)
                )
            )

            # MFCC-like features (simplified)
            mel_bands = self._mel_filterbank(len(fft_data), 13)
            mel_energies = [np.sum(fft_data * band) for band in mel_bands]
            features["mel_energies"] = [float(e) for e in mel_energies]
            features["mel_centroid"] = float(np.mean(mel_energies))
            features["mel_std"] = float(np.std(mel_energies))

        # Temporal features
        if len(frames) > 1:
            energy_diff = np.diff(frame_energies)
            features["energy_flux"] = float(np.mean(np.abs(energy_diff)))
            features["temporal_entropy"] = float(
                self._compute_entropy(np.abs(energy_diff))
            )

        # Voice quality indicators
        features["voicing_ratio"] = float(self._estimate_voicing(audio_data))
        features["pitch_estimate"] = float(self._estimate_pitch(audio_data))

        # Create feature vector for comparison
        feature_vector = [
            features["rms"],
            features["zero_crossing_rate"],
            features["peak_amplitude"],
            features["mean_amplitude"],
            features["std_amplitude"],
            features["mean_energy"],
            features["std_energy"],
            features["energy_entropy"],
            features.get("spectral_centroid", 0),
            features.get("spectral_rolloff", 0),
            features.get("spectral_bandwidth", 0),
            features.get("mel_centroid", 0),
            features.get("mel_std", 0),
            features.get("energy_flux", 0),
            features.get("temporal_entropy", 0),
            features.get("voicing_ratio", 0),
            features.get("pitch_estimate", 0),
        ]
        features["feature_vector"] = feature_vector

        return features

    def _frame_signal(self, audio: np.ndarray) -> List[np.ndarray]:
        frames = []
        for i in range(0, len(audio) - self.frame_length, self.hop_length):
            frames.append(audio[i : i + self.frame_length])
        return frames

    def _compute_entropy(self, values) -> float:
        if isinstance(values, np.ndarray):
            values = values.tolist()
        if not values or len(values) == 0:
            return 0.0
        total = sum(values) + 1e-10
        probs = [v / total for v in values]
        return -sum(p * math.log2(p + 1e-10) for p in probs if p > 0)

    def _spectral_rolloff(
        self, fft_data: np.ndarray, roll_percent: float = 0.85
    ) -> float:
        cumulative = np.cumsum(fft_data)
        threshold = roll_percent * cumulative[-1]
        rolloff = np.searchsorted(cumulative, threshold)
        return float(rolloff)

    def _mel_filterbank(self, n_fft: int, n_mels: int = 13) -> List[np.ndarray]:
        bands = []
        for i in range(n_mels):
            band = np.zeros(n_fft)
            center = int((i + 1) * n_fft / (n_mels + 1))
            width = max(1, n_fft // (n_mels + 1))
            for j in range(max(0, center - width), min(n_fft, center + width)):
                band[j] = 1.0 - abs(j - center) / width
            bands.append(band)
        return bands

    def _estimate_voicing(self, audio: np.ndarray) -> float:
        """Estimate voicing ratio (periodic vs noise)"""
        if len(audio) < 256:
            return 0.5
        autocorr = np.correlate(audio, audio, mode="full")
        autocorr = autocorr[len(autocorr) // 2 :]
        if len(autocorr) < 2:
            return 0.5
        peak = np.max(autocorr[1:])
        zero_lag = autocorr[0]
        if zero_lag == 0:
            return 0.5
        return float(peak / zero_lag)

    def _estimate_pitch(self, audio: np.ndarray) -> float:
        """Estimate fundamental frequency"""
        if len(audio) < 256:
            return 0.0
        centered = audio - np.mean(audio)
        if not np.any(centered):
            return 0.0
        autocorr = np.correlate(centered, centered, mode="full")
        autocorr = autocorr[len(autocorr) // 2 :]
        if len(autocorr) < 2:
            return 0.0
        min_lag = max(1, int(self.sample_rate / 500))
        max_lag = min(len(autocorr), int(self.sample_rate / 50))
        if max_lag <= min_lag:
            return 0.0
        peak_lag = int(np.argmax(autocorr[min_lag:max_lag])) + min_lag
        return float(self.sample_rate / peak_lag)


class VoiceMemory:
    """Deep voice memory - stores, learns, adapts"""

    def __init__(self):
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        self.feature_extractor = VoiceFeatureExtractor()
        self.profiles: Dict[str, Dict] = {}
        self.sessions: List[Dict] = []
        self.current_user: Optional[str] = None
        self.voice_quality_history: List[float] = []
        self.is_sick_mode = False
        self.sick_threshold = 0.6  # Lower threshold when sick
        self.normal_threshold = 0.75
        self._load()

    def _load(self):
        """Load voice profiles and sessions"""
        try:
            if VOICE_PROFILES_FILE.exists():
                with open(VOICE_PROFILES_FILE, "r") as f:
                    self.profiles = json.load(f)
        except Exception as e:
            print(f"[VOICE_MEMORY] Profile load error: {e}")

        try:
            if VOICE_SESSIONS_FILE.exists():
                with open(VOICE_SESSIONS_FILE, "r") as f:
                    self.sessions = json.load(f)
        except Exception as e:
            print(f"[VOICE_MEMORY] Session load error: {e}")

    def _save(self):
        """Save voice profiles and sessions"""
        try:
            with open(VOICE_PROFILES_FILE, "w") as f:
                json.dump(self.profiles, f, indent=2)
            with open(VOICE_SESSIONS_FILE, "w") as f:
                json.dump(self.sessions[-1000:], f, indent=2)  # Keep last 1000 sessions
        except Exception as e:
            print(f"[VOICE_MEMORY] Save error: {e}")

    def enroll_user(self, user_id: str, audio_samples: List[np.ndarray]) -> bool:
        """Enroll a new user voice profile"""
        if not audio_samples:
            return False

        # Extract features from multiple samples
        all_features = []
        for sample in audio_samples:
            features = self.feature_extractor.extract_features(sample)
            if "feature_vector" in features:
                all_features.append(features["feature_vector"])

        if not all_features:
            return False

        # Compute average feature vector
        avg_features = np.mean(all_features, axis=0).tolist()
        std_features = np.std(all_features, axis=0).tolist()

        self.profiles[user_id] = {
            "user_id": user_id,
            "enrolled_at": datetime.now().isoformat(),
            "avg_features": avg_features,
            "std_features": std_features,
            "sample_count": len(audio_samples),
            "total_sessions": 0,
            "last_heard": datetime.now().isoformat(),
            "voice_quality_scores": [],
            "adaptation_history": [],
            "is_primary": len(self.profiles) == 0,  # First user is primary
        }

        self._save()
        print(f"[VOICE_MEMORY] Enrolled user: {user_id} ({len(audio_samples)} samples)")
        return True

    def verify_speaker(
        self, audio_data: np.ndarray, user_id: Optional[str] = None
    ) -> Tuple[bool, float]:
        """Verify if the speaker matches the enrolled profile"""
        if not self.profiles:
            return False, 0.0

        # Extract features from current audio
        features = self.feature_extractor.extract_features(audio_data)
        if "feature_vector" not in features:
            return False, 0.0

        current_vector = np.array(features["feature_vector"])

        # If user_id specified, verify against that profile
        if user_id and user_id in self.profiles:
            profile = self.profiles[user_id]
            similarity = self._compute_similarity(current_vector, profile)
            self._record_session(user_id, similarity, features)
            return similarity >= self._get_threshold(user_id), similarity

        # Otherwise, find best match among all profiles
        best_user = None
        best_similarity = 0.0

        for uid, profile in self.profiles.items():
            similarity = self._compute_similarity(current_vector, profile)
            if similarity > best_similarity:
                best_similarity = similarity
                best_user = uid

        if best_user:
            self._record_session(best_user, best_similarity, features)
            threshold = self._get_threshold(best_user)
            return best_similarity >= threshold, best_similarity

        return False, 0.0

    def identify_speaker(self, audio_data: np.ndarray) -> Tuple[Optional[str], float]:
        """Identify who is speaking"""
        if not self.profiles:
            return None, 0.0

        features = self.feature_extractor.extract_features(audio_data)
        if "feature_vector" not in features:
            return None, 0.0

        current_vector = np.array(features["feature_vector"])

        best_user = None
        best_similarity = 0.0

        for uid, profile in self.profiles.items():
            similarity = self._compute_similarity(current_vector, profile)
            if similarity > best_similarity:
                best_similarity = similarity
                best_user = uid

        return best_user, best_similarity

    def adapt_to_voice_change(self, user_id: str, audio_data: np.ndarray):
        """Adapt profile when voice changes (sick, tired, etc.)"""
        if user_id not in self.profiles:
            return

        features = self.feature_extractor.extract_features(audio_data)
        if "feature_vector" not in features:
            return

        profile = self.profiles[user_id]
        current_vector = np.array(features["feature_vector"])
        avg_vector = np.array(profile["avg_features"])

        # Check if this is a significant voice change
        similarity = self._compute_similarity(current_vector, profile)

        if similarity < self.normal_threshold:
            # Voice has changed - could be sick, tired, or different conditions
            self.voice_quality_history.append(similarity)

            # Check if this is a pattern (consistently lower quality)
            if len(self.voice_quality_history) >= 3:
                recent_avg = np.mean(self.voice_quality_history[-5:])
                if recent_avg < self.sick_threshold:
                    self.is_sick_mode = True
                    print(f"[VOICE_MEMORY] Sick mode activated for {user_id}")

        # Gradually adapt the profile
        learning_rate = 0.05  # Slow adaptation
        new_avg = (1 - learning_rate) * avg_vector + learning_rate * current_vector
        profile["avg_features"] = new_avg.tolist()
        profile["sample_count"] += 1
        profile["last_heard"] = datetime.now().isoformat()
        profile["total_sessions"] += 1

        # Track voice quality
        if "voice_quality_scores" not in profile:
            profile["voice_quality_scores"] = []
        profile["voice_quality_scores"].append(similarity)

        # Keep only recent scores
        if len(profile["voice_quality_scores"]) > 100:
            profile["voice_quality_scores"] = profile["voice_quality_scores"][-100:]

        profile["adaptation_history"].append(
            {
                "time": datetime.now().isoformat(),
                "similarity": similarity,
                "sick_mode": self.is_sick_mode,
            }
        )

        self._save()

    def _compute_similarity(self, current_vector: np.ndarray, profile: Dict) -> float:
        """Compute similarity between current voice and stored profile"""
        avg_vector = np.array(profile["avg_features"])
        std_vector = np.array(profile.get("std_features", np.ones_like(avg_vector)))

        # Normalize both vectors by the enrolled feature variance.
        std_vector = np.maximum(std_vector, 0.01)  # Avoid division by zero
        normalized_current = current_vector / std_vector
        normalized_avg = avg_vector / std_vector

        # Cosine similarity
        dot_product = np.dot(normalized_current, normalized_avg)
        norm_current = np.linalg.norm(normalized_current)
        norm_avg = np.linalg.norm(normalized_avg)

        if norm_current == 0 or norm_avg == 0:
            cosine_sim = 0.0
        else:
            cosine_sim = (dot_product / (norm_current * norm_avg) + 1.0) / 2.0

        # Also compute Euclidean distance similarity
        euclidean_dist = np.linalg.norm(current_vector - avg_vector)
        max_dist = np.linalg.norm(avg_vector) * 2
        if max_dist == 0:
            euclidean_sim = 1.0 if euclidean_dist == 0 else 0.0
        else:
            euclidean_sim = max(0, 1 - (euclidean_dist / max_dist))

        # Weighted combination
        return float(0.7 * cosine_sim + 0.3 * euclidean_sim)

    def _get_threshold(self, user_id: str) -> float:
        """Get verification threshold (lower when sick)"""
        if self.is_sick_mode:
            return self.sick_threshold
        return self.normal_threshold

    def _record_session(self, user_id: str, similarity: float, features: Dict):
        """Record a voice session"""
        session = {
            "user_id": user_id,
            "timestamp": datetime.now().isoformat(),
            "similarity": similarity,
            "voice_quality": features.get("rms", 0),
            "pitch": features.get("pitch_estimate", 0),
            "energy": features.get("mean_energy", 0),
        }
        self.sessions.append(session)
        if len(self.sessions) > 1000:
            self.sessions = self.sessions[-1000:]

    def get_user_stats(self, user_id: str) -> Dict[str, Any]:
        """Get statistics for a user"""
        if user_id not in self.profiles:
            return {}

        profile = self.profiles[user_id]
        user_sessions = [s for s in self.sessions if s["user_id"] == user_id]

        return {
            "user_id": user_id,
            "enrolled_at": profile.get("enrolled_at"),
            "total_sessions": profile.get("total_sessions", 0),
            "sample_count": profile.get("sample_count", 0),
            "last_heard": profile.get("last_heard"),
            "avg_similarity": np.mean([s["similarity"] for s in user_sessions])
            if user_sessions
            else 0,
            "recent_similarity": np.mean([s["similarity"] for s in user_sessions[-10:]])
            if len(user_sessions) >= 10
            else 0,
            "is_sick_mode": self.is_sick_mode,
            "voice_quality_history": self.voice_quality_history[-10:],
        }

    def reset_sick_mode(self):
        """Reset sick mode when user recovers"""
        self.is_sick_mode = False
        self.voice_quality_history = []
        print("[VOICE_MEMORY] Sick mode deactivated")
        self._save()

    def get_all_users(self) -> List[str]:
        return list(self.profiles.keys())

    def is_enrolled(self, user_id: str) -> bool:
        return user_id in self.profiles
