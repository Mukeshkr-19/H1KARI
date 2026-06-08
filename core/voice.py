"""
HIKARI v2.0 - Voice I/O System
Uses Whisper for local speech recognition (works offline, any accent)
Fallback to Google Speech Recognition
"""

import os
import re
import sys
import time
import json
import wave
import threading
import tempfile
from typing import Optional, Callable
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

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

try:
    import whisper

    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False

load_dotenv()


class VoiceSystem:
    """Handles all voice I/O operations with Whisper as primary"""

    def __init__(self):
        self.recognizer = sr.Recognizer() if SR_AVAILABLE else None
        self.is_listening = False
        self._audio = None
        self._warmup_done = False
        self._mic_index = 0
        self._whisper_model = None
        self._use_whisper = True  # Use Whisper by default

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

    def _load_whisper(self):
        """Load Whisper model (lazy loading)"""
        if self._whisper_model is None and WHISPER_AVAILABLE:
            print("[VOICE] Loading Whisper model (first time takes ~30 seconds)...")
            try:
                self._whisper_model = whisper.load_model("base")
                print("[VOICE] Whisper model loaded successfully")
            except Exception as e:
                print(f"[VOICE] Whisper load failed: {e}")
                self._use_whisper = False

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

            # Pre-load Whisper in background
            if WHISPER_AVAILABLE:
                threading.Thread(target=self._load_whisper, daemon=True).start()

            print("[VOICE] Mic ready")
        except Exception as e:
            print(f"[VOICE] Mic warmup failed: {e}")

    def listen(self, timeout: int = 10, phrase_time_limit: int = 15) -> Optional[str]:
        """Listen for speech and return recognized text using Whisper"""
        if not SR_AVAILABLE:
            print("[VOICE] SpeechRecognition not available")
            return None

        # Load Whisper if not loaded
        if self._use_whisper and self._whisper_model is None:
            self._load_whisper()

        try:
            with sr.Microphone(device_index=self._mic_index) as source:
                if not self._warmup_done:
                    self.recognizer.adjust_for_ambient_noise(source, duration=1)
                print("[VOICE] Listening... (speak now)")
                audio = self.recognizer.listen(
                    source, timeout=timeout, phrase_time_limit=phrase_time_limit
                )

            # Try Whisper first (local, works with any accent)
            if self._use_whisper and self._whisper_model:
                try:
                    text = self._recognize_with_whisper(audio)
                    if text:
                        print(f"[VOICE] Recognized (Whisper): {text}")
                        return text
                except Exception as e:
                    print(f"[VOICE] Whisper error: {e}")

            # Fallback to Google
            try:
                text = self.recognizer.recognize_google(audio)
                print(f"[VOICE] Recognized (Google): {text}")
                return text
            except sr.UnknownValueError:
                print("[VOICE] Could not understand - try speaking closer")
                return None
            except sr.RequestError as e:
                print(f"[VOICE] Google API error: {e}")
                return None

        except sr.WaitTimeoutError:
            print("[VOICE] No speech detected")
            return None
        except Exception as e:
            print(f"[VOICE] Error: {e}")
            return None

    def _recognize_with_whisper(self, audio) -> Optional[str]:
        """Recognize speech using local Whisper model"""
        try:
            # Convert audio to numpy array
            audio_data = (
                np.frombuffer(audio.get_raw_data(), dtype=np.int16).astype(np.float32)
                / 32768.0
            )
            result = self._whisper_model.transcribe(
                audio_data, language="en", fp16=False
            )
            text = result["text"].strip()
            return text if text else None
        except Exception as e:
            print(f"[VOICE] Whisper recognition failed: {e}")
            return None

    def speak(self, text: str):
        """Text-to-speech using macOS say command"""
        try:
            clean_text = re.sub(r"[^\w\s:,.!?']", "", text)
            print(f"[TTS] {clean_text}")
            if sys.platform == "darwin":
                os.system(f'say "{clean_text}"')
            elif sys.platform.startswith("linux"):
                os.system(f'espeak "{clean_text}"')
            else:
                print(f"[TTS] {clean_text}")
        except Exception as e:
            print(f"[TTS Error] {e}")

    def get_status(self) -> dict:
        return {
            "listening": self.is_listening,
            "warmup_done": self._warmup_done,
            "whisper_available": WHISPER_AVAILABLE,
            "whisper_loaded": self._whisper_model is not None,
            "speech_recognition": SR_AVAILABLE,
            "pyaudio": PYAUDIO_AVAILABLE,
        }


class ClapDetector:
    """Detects clap patterns for silent activation"""

    def __init__(self, clap_count: int = 2, threshold: int = 3000):
        self.clap_count = clap_count
        self.threshold = threshold
        self._running = False
        self._callback: Optional[Callable] = None

    def start(self, callback: Callable):
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
