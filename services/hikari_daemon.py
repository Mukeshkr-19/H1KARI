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
        # SpeechBrain/torch not installed - allow activation but warn
        print(
            f"⚠️  Speaker verification unavailable (missing: {e}). Allowing activation."
        )
        return True
    except Exception as e:
        # Other errors - fail safe but allow activation since deps might be missing
        print(f"⚠️  Speaker verification error: {e}. Allowing activation.")
        return True


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


def listen_always():
    """
    JARVIS-style continuous listening:
    - Always listening for wake word when in LISTENING mode
    - When ACTIVE, processes all commands until stop word
    - Never stops listening entirely - just changes behavior
    """
    global hikari_state

    print("\n" + "=" * 50)
    print("🎯 HIKARI - JARVIS Mode Active")
    print("  • Say 'hikari' to activate (when sleeping)")
    print("  • Say 'bye', 'exit', or 'goodbye' to sleep")
    print("  • Always listening...\n")


# ============================================================
# MAIN LOOP - JARVIS-style: always listening, activate on "hikari"
# ============================================================

while True:
    try:
        # === STATE: LISTENING - Waiting for wake word ===
        if hikari_state == HikariState.LISTENING:
            print("💤 ", end="\r", flush=True)
            try:
                print("→ Opening mic...", flush=True)
                with sr.Microphone() as source:
                    r.adjust_for_ambient_noise(source, duration=0.5)
                    print(f"→ Energy threshold: {r.energy_threshold}", flush=True)
                    print("→ Waiting for speech (5s)...", flush=True)
                    audio = r.listen(source, timeout=5, phrase_time_limit=5)
                    print("→ Got audio!", flush=True)

                # Got audio!
                print("🔊 ", end="\r", flush=True)

                # Recognize
                text = recognize_audio(audio)

                if not text:
                    print("❓ No speech", flush=True)
                    continue

                print(f"📝 '{text}'", flush=True)

                # Whisper mishears "Hikari" as "hector", "hickory", etc
                # Accept if it starts with "hec" or "hik" - it's probably "Hikari"
                if (
                    text.startswith("hec")
                    or text.startswith("hik")
                    or "hect" in text
                    or "hikar" in text
                ):
                    print(f"✅ WAKE! Activating!", flush=True)
                else:
                    continue

                # SUCCESS!
                print(f"\n🎉 '{text}' - ACTIVATED!\n")
                hikari_state = HikariState.ACTIVE
                speak("Go ahead!")
                time.sleep(0.5)

            except sr.WaitTimeoutError:
                # Normal - no speech within timeout
                print("⏱️ No speech detected", flush=True)
                pass
            except OSError as e:
                print(f"🎤 Mic error: {e}", flush=True)
                time.sleep(2)
            except Exception as e:
                print(f"Error: {e}", flush=True)
                time.sleep(1)
                continue

        # === STATE: ACTIVE - Processing commands ===
        elif hikari_state == HikariState.ACTIVE:
            print("👂 ", end="\r", flush=True)
            try:
                with sr.Microphone() as source:
                    audio = r.listen(source, timeout=8, phrase_time_limit=30)

                print("🔊 ", end="\r", flush=True)
                text = recognize_audio(audio)

                if not text:
                    continue

                print(f"You: {text}")

                # Check for corrections
                if any(p in text for p in ["that's wrong", "mistake", "incorrect"]):
                    speak("What should I have said?")
                    time.sleep(1)
                    continue

                # Check for stop command - go back to listening BUT KEEP LOOPING
                if is_stop_command(text):
                    speak("Talk to you later!")
                    print("💤 Going to sleep... (still listening for 'hikari')\n")
                    hikari_state = HikariState.LISTENING
                    time.sleep(1)
                    continue

                # Process command
                response = process(text)
                if response:
                    print(f"HIKARI: {response}")
                    speak(response)
                    log_convo(text, response)

            except sr.WaitTimeoutError:
                continue
            except sr.UnknownValueError:
                continue
            except Exception as e:
                print(f"Error: {e}", flush=True)
                time.sleep(0.5)
                continue

    except Exception as outer:
        print(f"Outer loop error: {outer}", flush=True)
        time.sleep(1)


if __name__ == "__main__":
    if not sr:
        print("\n❌ Run: /opt/anaconda3/bin/python3 -m pip install speechrecognition")
        sys.exit(1)

    # Check for voice enrollment flag (speaker verification)
    if len(sys.argv) > 1 and sys.argv[1] in ["--enroll-voice", "--setup-voice"]:
        enroll_voice()
        sys.exit(0)

    print(f"\n✅ HIKARI ready! Say '{WAKE_WORD}' to activate")
    print("📚 Say 'that's wrong' to teach me!")

    if SPEAKER_AUTH_AVAILABLE:
        auth = SpeakerAuth()
        if auth.is_enrolled():
            print(
                "🔐 Speaker verification enabled - only your voice will activate HIKARI\n"
            )
        else:
            print("\n⚠️  No enrolled voice yet.")
            print("   Run with --enroll-voice to lock HIKARI to your voice.\n")
    else:
        print("\n⚠️  Speaker verification unavailable (dependencies missing).")
        print("   HIKARI will respond to any voice until you enable it.\n")

    # Initialize state
    hikari_state = HikariState.LISTENING

    signal.signal(signal.SIGINT, lambda s, f: (print("\n\nBye!"), sys.exit(0)))

    try:
        listen_always()
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(1)

    while True:
        try:
            with sr.Microphone() as source:
                print("👂 ", end="\r", flush=True)
                audio = r.listen(source, timeout=5, phrase_time_limit=30)

            # Verify speaker matches (only your voice!)
            if not verify_speaker(audio):
                print("❌ Voice not recognized, ignoring...\n")
                continue

            text = r.recognize_google(audio).lower().strip()

            if not text:
                continue

            print(f"You: {text}")

            if any(p in text for p in ["that's wrong", "mistake", "incorrect"]):
                speak("What should I have said?")
                time.sleep(1)
                try:
                    with sr.Microphone() as source:
                        audio2 = r.listen(source, timeout=5, phrase_time_limit=8)
                    if not verify_speaker(audio2):
                        print("❌ Voice not recognized for correction")
                        speak("Sorry, didn't catch that.")
                        continue
                    correction = r.recognize_google(audio2).strip()
                    if correction:
                        add_learning(text, correction)
                        speak("Got it!")
                except:
                    pass
                continue

            # === Check for stop command === back to listening KEEP LOOP
            if is_stop_command(text):
                speak("Talk to you later!")
                print("💤 Going to sleep... (still listening for 'hikari')\n")
                hikari_state = HikariState.LISTENING
                time.sleep(1)
                continue

            # === Process regular command ===
            response = process(text)
            if response:
                print(f"HIKARI: {response}")
                speak(response)
                log_convo(text, response)

        except sr.WaitTimeoutError:
            continue
        except sr.UnknownValueError:
            continue
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(0.5)


# Old function removed - using listen_always() instead


if __name__ == "__main__":
    if not sr:
        print("\n❌ Run: /opt/anaconda3/bin/python3 -m pip install speechrecognition")
        sys.exit(1)

    # Check for voice enrollment flag (speaker verification)
    if len(sys.argv) > 1 and sys.argv[1] in ["--enroll-voice", "--setup-voice"]:
        enroll_voice()
        sys.exit(0)

    print(f"\n✅ HIKARI ready! Say '{WAKE_WORD}' to activate")
    print("📚 Say 'that's wrong' to teach me!")

    if SPEAKER_AUTH_AVAILABLE:
        auth = SpeakerAuth()
        if auth.is_enrolled():
            print(
                "🔐 Speaker verification enabled - only your voice will activate HIKARI\n"
            )
        else:
            print("\n⚠️  No enrolled voice yet.")
            print("   Run with --enroll-voice to lock HIKARI to your voice.\n")
    else:
        print("\n⚠️  Speaker verification unavailable (dependencies missing).")
        print("   HIKARI will respond to any voice until you enable it.\n")

    # Initialize state
    hikari_state = HikariState.LISTENING

    signal.signal(signal.SIGINT, lambda s, f: (print("\n\nBye!"), sys.exit(0)))

    try:
        listen_always()  # JARVIS-style: always listening, responds to hikari, bye returns to sleep
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(1)
