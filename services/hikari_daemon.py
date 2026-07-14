#!/usr/bin/env python3
"""
HIKARI - Always-on wake-word daemon (macOS)

This is the "JARVIS-like" background mode:
- Always listening for wake word ("hikari")
- After activation, listens for commands
- "bye"/"stop"/"goodbye" -> goes silent again (but keeps listening for wake word)
- Speaker verification: only the enrolled speaker can activate/command

Enrollment stores embeddings locally under the private brain legacy-data dir.
"""

from __future__ import print_function
import os
import sys
import time
from pathlib import Path
import subprocess
import signal
import json

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

# Speaker verification (local-first); must run after sys.path includes repo root
try:
    from core.speaker_auth import SpeakerAuth

    SPEAKER_AUTH_AVAILABLE = True
except Exception:
    SPEAKER_AUTH_AVAILABLE = False

from core.daily_logs import maybe_rotate_daily_log
from core.runtime_paths import legacy_data_dir

# Force unbuffered output
sys.stdout = os.fdopen(sys.stdout.fileno(), "w", buffering=1)

WAKE_WORD = "hikari"
STOP_WORDS = [
    "stop listening",
    "exit hikari",
    "goodbye hikari",
    "bye hikari",
    "sleep hikari",
    "stop",
    "bye",
]

# Flag to control daemon exit
daemon_running = True

LEGACY_DATA_DIR = legacy_data_dir()
LEARNING_FILE = LEGACY_DATA_DIR / "learning.json"
VOICE_PRINT_FILE = LEGACY_DATA_DIR / "voiceprint.bin"  # legacy
LEGACY_DATA_DIR.mkdir(parents=True, exist_ok=True)


def log_convo(user: str, hikari: str):
    """Log conversation"""
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


def save_learnings(data):
    with open(LEARNING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


def check_learnings(text):
    data = load_learnings()
    for wrong, correct in data.get("corrections", {}).items():
        if wrong.lower() in text.lower():
            return correct
    return None


def add_learning(wrong, correct):
    data = load_learnings()
    data["corrections"][wrong] = correct
    save_learnings(data)


def enroll_voice():
    """Enroll speaker embedding (recommended)."""
    if not SPEAKER_AUTH_AVAILABLE:
        print("\n❌ Speaker verification not available (missing dependencies).")
        print("   Install: pip install speechbrain torch")
        return False

    auth = SpeakerAuth()
    print("\n🎙️ Voice enrollment (speaker verification)")
    print("Say a short phrase 3 times when prompted (normal speaking voice).")
    print("Tip: do this in a quiet room for best results.\n")

    embeddings = []
    for i in range(3):
        print(f"Sample {i + 1}/3 — speak now...", flush=True)
        try:
            with sr.Microphone() as source:
                r.adjust_for_ambient_noise(source, duration=0.6)
                audio = r.listen(source, timeout=6, phrase_time_limit=4)
            emb = auth.embedding_from_speech_recognition_audio(audio)
            embeddings.append(emb)
            print("✓ captured")
            time.sleep(0.8)
        except Exception as e:
            print(f"Error capturing sample: {e}")
            return False

    try:
        auth.enroll_from_embeddings(embeddings)
        print("\n✅ Voice enrolled! HIKARI will ignore other speakers.\n")
        return True
    except Exception as e:
        print(f"Error saving enrollment: {e}")
        return False


# One SpeakerAuth loads ECAPA once; a new instance per utterance reloads the model and breaks wake responsiveness.
_speaker_auth_cache = None


# State machine for JARVIS-style behavior
class HikariState:
    LISTENING = "listening"  # Waiting for wake word
    ACTIVE = "active"  # Processing commands
    SPEAKING = "speaking"  # Responding to user


hikari_state = HikariState.LISTENING


def _get_speaker_auth():
    global _speaker_auth_cache
    if not SPEAKER_AUTH_AVAILABLE:
        return None
    if _speaker_auth_cache is None:
        _speaker_auth_cache = SpeakerAuth()
    return _speaker_auth_cache


def verify_speaker(audio) -> bool:
    """
    Returns True iff the speaker matches the enrolled voice.
    If no enrollment exists OR verification unavailable, we allow activation (with warning).
    """
    if not SPEAKER_AUTH_AVAILABLE:
        # No speaker-verification available -> behave as "open" mode
        return True

    auth = _get_speaker_auth()
    if auth is None:
        return True
    if not auth.is_enrolled():
        print("⚠️  No enrolled voice yet. Say 'enroll my voice' or run --enroll-voice")
        return True

    try:
        emb = auth.embedding_from_speech_recognition_audio(audio)
        res = auth.verify_embedding(emb)
        if not res.ok:
            print(
                f"❌ Speaker mismatch (score={res.score:.3f}, th={res.threshold:.3f})"
            )
        return res.ok
    except ImportError as e:
        print(f"⚠️  Speaker verification unavailable (missing: {e}). Access denied.")
        return False
    except Exception as e:
        print(f"⚠️  Speaker verification error: {e}. Access denied.")
        return False


sr = None
whisper_model = None
faster_whisper_model = None
np = None

# Try to load faster-whisper first (offline, fast)
try:
    from faster_whisper import WhisperModel
    import numpy as np

    print("[OK] faster-whisper loading...")
    faster_whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
    print("[OK] faster-whisper loaded!")
except Exception as e:
    print(f"[INFO] faster-whisper: {e}")

# Try to load Whisper for better STT
try:
    import whisper
    import numpy as np

    print("[OK] Whisper - loading model...")
    whisper_model = whisper.load_model("base")
    print("[OK] Whisper model loaded!")
except Exception as e:
    print(f"[MISSING] Whisper: {e}")

try:
    import speech_recognition as sr_module

    sr = sr_module
    print("[OK] SpeechRecognition")
except:
    print("[MISSING] SpeechRecognition")


def recognize_audio(audio):
    """Use faster-whisper first (offline), then Google"""
    # Try faster-whisper first (offline, fastest)
    if faster_whisper_model is not None and np is not None:
        try:
            audio_data = (
                np.frombuffer(audio.get_raw_data(), dtype=np.int16).astype(np.float32)
                / 32768.0
            )
            segments, info = faster_whisper_model.transcribe(
                audio_data, language="en", beam_size=1
            )
            text = "".join(seg.text for seg in segments).strip().lower()
            # Only return if we got actual text (not empty)
            if text and len(text) > 2:
                print(f"📝 (faster-whisper) '{text}'", flush=True)
                return text
        except Exception as e:
            pass  # Fall through

    # Fallback to Google (more reliable for wake word)
    for attempt in range(2):
        try:
            text = r.recognize_google(audio, language="en-US").lower().strip()
            if text:
                print(f"📝 (Google) '{text}'", flush=True)
                return text
        except sr.UnknownValueError:
            if attempt == 1:
                break
        except sr.RequestError:
            time.sleep(0.3)
            continue
    return ""


print("=" * 50)

if sr:
    r = sr.Recognizer()
    r.energy_threshold = 200  # Very low to hear quiet speech
    r.dynamic_energy_threshold = True  # Auto-adjust for ambient noise
    r.pause_threshold = 1.5  # Wait longer for you to finish sentence
    r.phrase_time_limit = 10  # Shorter to be more responsive
    r.non_speaking_duration = 0.5


def speak(text):
    """Speak using macOS say command"""
    global hikari_state
    hikari_state = HikariState.SPEAKING
    print(f"🔊 TTS: {text}", flush=True)
    # Use macOS say with faster rate
    subprocess.run(["say", "-r", "200", text], capture_output=True)
    time.sleep(0.3)
    hikari_state = HikariState.ACTIVE


def process(text):
    """Process user input through orchestrator"""
    correction = check_learnings(text)
    if correction:
        return f"Got it! {correction}"

    try:
        from core.orchestrator import get_orchestrator

        orch = get_orchestrator()
        response = orch.process_input(text, source="voice")
        return response
    except Exception as e:
        return f"Oops! {str(e)[:80]}"


def is_stop_command(text: str) -> bool:
    """Check if user wants to go back to listening mode"""
    text_lower = text.lower().strip()
    stop_phrases = [
        "bye",
        "goodbye",
        "exit",
        "stop",
        "go to sleep",
        "sleep",
        "that's all",
        "that's it",
        "nothing else",
        "done",
        "thank you",
        "thanks",
        "okay goodbye",
        "see you later",
    ]
    return any(phrase in text_lower for phrase in stop_phrases)


def _is_wake_phrase(text: str) -> bool:
    """Allow common speech-to-text variants of the wake word."""
    return bool(
        text.startswith("hec")
        or text.startswith("hik")
        or "hect" in text
        or "hikar" in text
    )


def _listen_for_wake_word() -> None:
    global hikari_state

    print("💤 ", end="\r", flush=True)
    with sr.Microphone() as source:
        r.adjust_for_ambient_noise(source, duration=0.5)
        audio = r.listen(source, timeout=5, phrase_time_limit=5)

    text = recognize_audio(audio)
    if not text or not _is_wake_phrase(text):
        return
    if not verify_speaker(audio):
        print("❌ Voice not recognized, ignoring...\n")
        return

    print(f"\n🎉 '{text}' - ACTIVATED!\n")
    hikari_state = HikariState.ACTIVE
    speak("Go ahead!")


def _listen_for_active_command() -> None:
    global hikari_state

    print("👂 ", end="\r", flush=True)
    with sr.Microphone() as source:
        audio = r.listen(source, timeout=8, phrase_time_limit=30)

    if not verify_speaker(audio):
        print("❌ Voice not recognized, ignoring...\n")
        return

    text = recognize_audio(audio)
    if not text:
        return
    print(f"You: {text}")

    if any(phrase in text for phrase in ["that's wrong", "mistake", "incorrect"]):
        speak("What should I have said?")
        return
    if is_stop_command(text):
        speak("Talk to you later!")
        hikari_state = HikariState.LISTENING
        print("💤 Going to sleep... (still listening for 'hikari')\n")
        return

    response = process(text)
    if response:
        print(f"HIKARI: {response}")
        speak(response)
        log_convo(text, response)


def listen_always() -> None:
    """Listen for the wake word, then process verified commands until stopped."""
    if sr is None:
        raise RuntimeError("SpeechRecognition is not installed")

    print("\n" + "=" * 50)
    print("🎯 HIKARI - JARVIS Mode Active")
    print("  • Say 'hikari' to activate (when sleeping)")
    print("  • Say 'bye', 'exit', or 'goodbye' to sleep")
    print("  • Always listening...\n")

    while daemon_running:
        try:
            if hikari_state == HikariState.LISTENING:
                _listen_for_wake_word()
            elif hikari_state == HikariState.ACTIVE:
                _listen_for_active_command()
        except (sr.WaitTimeoutError, sr.UnknownValueError):
            continue
        except OSError as exc:
            print(f"🎤 Mic error: {exc}", flush=True)
            time.sleep(2)
        except Exception as exc:
            print(f"Daemon loop error: {exc}", flush=True)
            time.sleep(1)


def main() -> int:
    global hikari_state

    if sr is None:
        print("\n❌ Install SpeechRecognition before starting the voice daemon.")
        return 1
    if len(sys.argv) > 1 and sys.argv[1] in ["--enroll-voice", "--setup-voice"]:
        return 0 if enroll_voice() else 1

    print(f"\n✅ HIKARI ready! Say '{WAKE_WORD}' to activate")
    if SPEAKER_AUTH_AVAILABLE:
        auth = _get_speaker_auth()
        if auth and auth.is_enrolled():
            print("🔐 Speaker verification enabled.\n")
        else:
            print("⚠️  No enrolled voice; activation is currently open.\n")
    else:
        print("⚠️  Speaker verification unavailable; activation is currently open.\n")

    hikari_state = HikariState.LISTENING
    signal.signal(signal.SIGINT, lambda _s, _f: sys.exit(0))
    listen_always()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
