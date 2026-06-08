#!/usr/bin/env python3
"""
HIKARI - Always-on wake-word daemon using openwakeword
Based on Omi's approach for efficient always-on listening

This runs continuously without needing manual start:
- Uses openwakeword for efficient wake word detection
- After activation, listens for commands until stop word
- "bye" -> goes back to listening (keeps listening!)
"""

from __future__ import print_function
import os
import sys
import time
from pathlib import Path
import subprocess
import signal
import json
import threading
import numpy as np

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from core.daily_logs import maybe_rotate_daily_log
from core.runtime_paths import legacy_data_dir

sys.stdout = os.fdopen(sys.stdout.fileno(), "w", buffering=1)

WAKE_WORD = "hikari"
LEGACY_DATA_DIR = legacy_data_dir()
LEARNING_FILE = LEGACY_DATA_DIR / "learning.json"
LEGACY_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Global state
hikari_state = "LISTENING"  # LISTENING, ACTIVE, SPEAKING
faster_whisper_model = None
owr_model = None


def log_convo(user: str, hikari: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_path = maybe_rotate_daily_log(Path(_REPO_ROOT), "conversations.log")
    with open(log_path, "a") as f:
        f.write(f"[{timestamp}] YOU: {user}\n")
        if hikari:
            f.write(f"[{timestamp}] HIKARI: {hikari}\n")
        f.write("\n")


def load_learnings():
    try:
        with open(LEARNING_FILE, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return {"corrections": {}, "remember": []}


def check_learnings(text):
    data = load_learnings()
    for wrong, correct in data.get("corrections", {}).items():
        if wrong.lower() in text.lower():
            return correct
    return None


def speak(text):
    global hikari_state
    hikari_state = "SPEAKING"
    print(f"🔊 {text}", flush=True)
    subprocess.run(["say", "-r", "200", str(text)], capture_output=True)
    time.sleep(0.3)
    hikari_state = "ACTIVE"


def process(text):
    correction = check_learnings(text)
    if correction:
        return f"Got it! {correction}"
    try:
        from core.orchestrator import get_orchestrator

        orch = get_orchestrator()
        return orch.process_input(text, source="voice")
    except Exception as e:
        return f"Oops! {str(e)[:80]}"


def is_stop_command(text: str) -> bool:
    text_lower = text.lower().strip()
    stop_phrases = [
        "bye",
        "goodbye",
        "exit",
        "stop",
        "sleep",
        "done",
        "thanks",
        "thank you",
    ]
    return any(p in text_lower for p in stop_phrases)


def is_wake_word(text: str) -> bool:
    text_lower = text.lower().strip()
    wake_variants = ["hikari", "hector", "hickory", "icki", "hec", "hik"]
    return any(text_lower.startswith(v) or v in text_lower for v in wake_variants)


def recognize_speech(audio_data, sr_recognizer):
    """Use faster-whisper for offline STT"""
    global faster_whisper_model
    if faster_whisper_model is not None:
        try:
            segments, info = faster_whisper_model.transcribe(
                audio_data, language="en", beam_size=1
            )
            text = "".join(seg.text for seg in segments).strip().lower()
            if text and len(text) > 2:
                print(f"📝 (faster-whisper) '{text}'", flush=True)
                return text
        except Exception as e:
            print(f"STT error: {e}", flush=True)

    # Fallback to Google
    try:
        import speech_recognition as sr

        audio = sr.AudioData(audio_data.tobytes(), 16000, 2)
        text = sr_recognizer.recognize_google(audio, language="en-US").lower().strip()
        if text:
            print(f"📝 (Google) '{text}'", flush=True)
            return text
    except Exception:
        pass
    return ""


def audio_callback(recognizer, audio):
    """Called when audio is detected by openwakeword"""
    global hikari_state
    try:
        audio_data = np.frombuffer(audio.get_raw_data(convert_width=2), dtype=np.int16)
        audio_float = audio_data.astype(np.float32) / 32768.0

        # Check for wake word
        predictions = owr_model.predict(audio_float)

        wake_score = predictions.get("hikari", 0)
        if wake_score and wake_score > 0.3:
            print(f"\n🎉 WAKE! (score={wake_score:.2f})", flush=True)
            if hikari_state == "LISTENING":
                activate()
    except Exception as e:
        pass  # Ignore errors in wake detection


def activate():
    global hikari_state
    hikari_state = "ACTIVE"
    speak("Go ahead!")


def run_daemon():
    global hikari_state, faster_whisper_model, owr_model

    print("=" * 50)
    print("🎯 HIKARI - Always-On Mode")
    print("  Say 'hikari' to activate")
    print("  Say 'bye' to sleep (keeps listening)")
    print("=" * 50)

    # Load SpeechRecognition
    try:
        import speech_recognition as sr

        sr_recognizer = sr.Recognizer()
        sr_recognizer.energy_threshold = 300
        sr_recognizer.dynamic_energy_threshold = True
        print("[OK] SpeechRecognition")
    except Exception as e:
        print(f"[ERROR] SpeechRecognition: {e}")
        return

    # Load faster-whisper
    try:
        from faster_whisper import WhisperModel

        print("[OK] Loading faster-whisper...")
        faster_whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        print("[OK] faster-whisper ready!")
    except Exception as e:
        print(f"[INFO] faster-whisper: {e}")

    # Load openwakeword for efficient wake word detection
    try:
        import openwakeword
        from openwakeword.utils import AudioBuffer

        print("[OK] Loading openwakeword...")

        # Load custom "hikari" model
        owr_model = openwakeword.OpenWakeWord(
            model_path=os.path.join(_REPO_ROOT, "models", "hikari.tflite"),
            inference_framework="tflite",
        )

        # Or use default models
        if not owr_model.models:
            owr_model = openwakeword.OpenWakeWord()

        print("[OK] openwakeword ready!")
    except Exception as e:
        print(f"[INFO] openwakeword: {e}")
        print("→ Falling back to speech_recognition timeout method")
        owr_model = None

    # Use speech_recognition's listen_in_background for always-on listening
    mic = sr.Microphone()

    if owr_model:
        # Use openwakeword callback
        with mic as source:
            sr.Recognizer().adjust_for_ambient_noise(source, duration=1)

        stop_listening = sr.Recognizer().listen_in_background(mic, audio_callback)
        print("\n🎤 Listening for 'hikari'...\n")
    else:
        # Fallback: use timeout-based listening loop
        print("\n🎤 Using timeout-based listening...\n")

    hikari_state = "LISTENING"
    last_activation = 0

    while True:
        try:
            if hikari_state == "LISTENING":
                if not owr_model:
                    # Fallback timeout method
                    try:
                        with sr.Microphone() as source:
                            audio = sr.Recognizer().listen(
                                source, timeout=3, phrase_time_limit=5
                            )
                        text = recognize_speech(
                            np.frombuffer(audio.get_raw_data(), dtype=np.int16),
                            sr.Recognizer(),
                        )
                        if text and is_wake_word(text):
                            print(f"\n🎉 '{text}' - ACTIVATED!\n", flush=True)
                            activate()
                    except Exception:
                        pass

                # Show listening indicator
                print("💤 ", end="\r", flush=True)
                time.sleep(0.5)

            elif hikari_state == "ACTIVE":
                print("👂 ", end="\r", flush=True)
                try:
                    with sr.Microphone() as source:
                        audio = sr.Recognizer().listen(
                            source, timeout=8, phrase_time_limit=30
                        )

                    audio_data = np.frombuffer(audio.get_raw_data(), dtype=np.int16)
                    text = recognize_speech(audio_data, sr.Recognizer())

                    if not text:
                        continue

                    print(f"You: {text}")

                    if is_stop_command(text):
                        speak("Talk to you later!")
                        print("💤 Going to sleep... (still listening)\n")
                        hikari_state = "LISTENING"
                        continue

                    response = process(text)
                    if response:
                        print(f"HIKARI: {response}")
                        speak(response)
                        log_convo(text, response)

                except Exception:
                    pass

        except KeyboardInterrupt:
            print("\n\nBye!")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(1)


def main():
    def signal_handler(s, f):
        print("\n\nBye!")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    print(f"\n✅ HIKARI ready! Say '{WAKE_WORD}' to activate")

    try:
        run_daemon()
    except KeyboardInterrupt:
        print("\n\nBye!")
        sys.exit(0)


if __name__ == "__main__":
    main()
