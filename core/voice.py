"""
HIKARI v2.0 - Voice I/O System

Uses bounded speech adapters so that backend selection follows the runtime
configuration and local backends never silently fall back to cloud services.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from core.speech_adapters import (
    CapturedAudio,
    SpeechAdapterError,
    SpeechBackendUnavailable,
    build_stt_adapter,
    build_tts_adapter,
)

# Fix SSL certificate issue on macOS
try:
    import certifi

    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
except ImportError:
    pass

try:
    import speech_recognition as sr

    SR_AVAILABLE = True
except ImportError:
    SR_AVAILABLE = False

try:
    import pyaudio

    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False

try:
    import numpy as np

    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

load_dotenv()


def _get_configured_stt_backend() -> str:
    """Return the STT backend name from the runtime configuration.

    Falls back to ``openai-whisper`` when no runtime configuration exists so
    that the interactive text/daemon paths remain usable.
    """
    try:
        from core.runtime_setup import get_voice_backend_name

        backend = get_voice_backend_name()
        if backend:
            return backend
    except Exception:
        pass
    return "openai-whisper"


def _get_configured_tts_backend() -> str:
    """Return the TTS backend name from the runtime configuration.

    Defaults to ``macos-say``.  The adapter's ``is_available`` method reports
    whether the platform actually supports it.
    """
    try:
        from core.runtime_setup import get_tts_backend_name

        backend = get_tts_backend_name()
        if backend:
            return backend
    except Exception:
        pass
    return "macos-say"


class VoiceSystem:
    """Handles all voice I/O operations using bounded speech adapters."""

    def __init__(self, backend: Optional[str] = None):
        self.recognizer = sr.Recognizer() if SR_AVAILABLE else None
        self.is_listening = False
        self._audio = None
        self._warmup_done = False
        self._mic_index = 0
        self._backend_name = backend or _get_configured_stt_backend()
        self._stt = build_stt_adapter(self._backend_name)
        self._tts = build_tts_adapter(_get_configured_tts_backend())

        if self.recognizer:
            self.recognizer.energy_threshold = 4000
            self.recognizer.dynamic_energy_threshold = True
            self.recognizer.pause_threshold = 0.8

    def _find_best_mic(self):
        """Find the best microphone (prefer built-in)"""
        if not PYAUDIO_AVAILABLE:
            return 0
        try:
            p = pyaudio.PyAudio()
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info["maxInputChannels"] > 0:
                    name = info["name"].lower()
                    if "macbook" in name or "built-in" in name or "internal" in name:
                        p.terminate()
                        return i
            p.terminate()
        except Exception:
            pass
        return 0

    def warmup(self):
        """Warm up microphone"""
        if not SR_AVAILABLE or not PYAUDIO_AVAILABLE:
            return
        try:
            self._audio = pyaudio.PyAudio()
            self._mic_index = self._find_best_mic()
            with sr.Microphone(device_index=self._mic_index) as source:
                print(
                    "[VOICE] Adjusting for ambient noise... (speak normally for 1 second)"
                )
                self.recognizer.adjust_for_ambient_noise(source, duration=1)
                print(
                    f"[VOICE] Energy threshold: {self.recognizer.energy_threshold:.0f}"
                )
            self._warmup_done = True

            # Pre-load the local model in background only if that is the selected backend.
            if self._stt.audio_egress is False and self._stt.is_available():
                threading.Thread(target=self._stt.prepare, daemon=True).start()

            print("[VOICE] Mic ready")
        except Exception:
            print("[VOICE] Mic warmup failed")

    def _audio_to_captured(self, audio) -> CapturedAudio:
        """Convert a speech_recognition AudioData to a CapturedAudio value."""
        return CapturedAudio(
            pcm_bytes=audio.get_raw_data(),
            sample_rate=audio.sample_rate,
            sample_width=audio.sample_width,
            channel_count=1,
        )

    def listen(self, timeout: int = 10, phrase_time_limit: int = 15) -> Optional[str]:
        """Listen for speech and return recognized text using the configured adapter."""
        if not SR_AVAILABLE:
            print("[VOICE] SpeechRecognition not available")
            return None

        try:
            with sr.Microphone(device_index=self._mic_index) as source:
                if not self._warmup_done:
                    self.recognizer.adjust_for_ambient_noise(source, duration=1)
                print("[VOICE] Listening... (speak now)")
                audio = self.recognizer.listen(
                    source, timeout=timeout, phrase_time_limit=phrase_time_limit
                )

            captured = self._audio_to_captured(audio)
            text = self._stt.transcribe(captured)
            print(f"[VOICE] Recognition succeeded ({self._backend_name})")
            return text
        except sr.WaitTimeoutError:
            print("[VOICE] No speech detected")
            return None
        except SpeechAdapterError:
            print("[VOICE] Recognition failed; falling back to text")
            return None
        except Exception:
            print("[VOICE] Recognition encountered an unexpected error")
            return None

    def speak(self, text: str):
        """Text-to-speech using the configured TTS adapter."""
        try:
            clean_text = re.sub(r"[^\w\s:,.!?']", "", text)
            print("[TTS] Synthesizing response")
            self._tts.synthesize(clean_text)
        except SpeechBackendUnavailable:
            # TTS unavailable is not fatal; callers can fall back to text.
            pass
        except SpeechAdapterError:
            print("[TTS] Synthesis failed")
        except Exception:
            print("[TTS] Synthesis encountered an unexpected error")

    def get_status(self) -> dict:
        return {
            "listening": self.is_listening,
            "warmup_done": self._warmup_done,
            "backend": self._backend_name,
            "stt_available": self._stt.is_available(),
            "tts_available": self._tts.is_available(),
            "speech_recognition": SR_AVAILABLE,
            "pyaudio": PYAUDIO_AVAILABLE,
        }


class ClapDetector:
    """Detects clap patterns for silent activation"""

    def __init__(self, clap_count: int = 2, threshold: int = 3000):
        self.clap_count = clap_count
        self.threshold = threshold
        self._running = False
        self._callback = None

    def start(self, callback):
        if not PYAUDIO_AVAILABLE or not NUMPY_AVAILABLE:
            return
        self._callback = callback
        self._running = True
        threading.Thread(target=self._detect_loop, daemon=True).start()

    def stop(self):
        self._running = False

    def _detect_loop(self):
        try:
            p = pyaudio.PyAudio()
            stream = p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=1024,
            )
            clap_times = []
            while self._running:
                data = stream.read(1024, exception_on_overflow=False)
                audio_data = np.frombuffer(data, dtype=np.int16)
                amplitude = np.max(np.abs(audio_data))
                if amplitude > self.threshold:
                    now = time.time()
                    clap_times = [t for t in clap_times if now - t < 2.0]
                    clap_times.append(now)
                    if (
                        len(clap_times) >= self.clap_count
                        and clap_times[-1] - clap_times[0] < 2.0
                    ):
                        if self._callback:
                            self._callback()
                        clap_times = []
                else:
                    clap_times = [t for t in clap_times if time.time() - t < 2.0]
            stream.stop_stream()
            stream.close()
            p.terminate()
        except Exception as e:
            print(f"[CLAP] Error: {e}")
