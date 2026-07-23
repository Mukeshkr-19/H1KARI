#!/usr/bin/env python3
"""
HIKARI - Always-on wake-word daemon (macOS)

This is the "JARVIS-like" background mode:
- Always listening for wake word ("hikari")
- After activation, listens for commands
- "bye"/"stop"/"goodbye" -> goes silent again (but keeps listening for wake word)
- Speaker verification: only the enrolled speaker can activate/command

Enrollment stores embeddings locally under the private brain legacy-data dir.
The daemon fails closed until an owner voice has been enrolled.
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

from core.speech_adapters import (
    CapturedAudio,
    SpeechAdapterError,
    build_stt_adapter,
)

# Speaker verification (local-first); must run after sys.path includes repo root
try:
    from core.speaker_auth import SpeakerAuth

    SPEAKER_AUTH_AVAILABLE = True
except Exception:
    SPEAKER_AUTH_AVAILABLE = False

from core.daily_logs import maybe_rotate_daily_log
from core.runtime_paths import legacy_data_dir

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


def _print_banner() -> None:
    print(
        """
==================================================
HIKARI - Always-on Voice Daemon
==================================================
""".strip()
    )


def log_convo(_user: str, hikari: str):
    """Log structural completion only; never persist voice content."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_path = maybe_rotate_daily_log(Path(_REPO_ROOT), "conversations.log")
    with open(log_path, "a") as f:
        outcome = "response" if hikari else "no_response"
        f.write(f"[{timestamp}] voice_turn={outcome}\n")


def load_learnings():
    try:
        with open(LEARNING_FILE, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return {"corrections": {}, "remember": []}


def save_learnings(data):
    LEGACY_DATA_DIR.mkdir(parents=True, exist_ok=True)
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
    if not auth.available():
        print("\n❌ Speaker verification model could not be loaded.")
        print("   Check your connection once, then retry: hikari --enroll-voice")
        return False
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
        except Exception:
            print("Error capturing enrollment sample")
            return False

    try:
        auth.enroll_from_embeddings(embeddings)
        print("\n✅ Voice enrolled! HIKARI will ignore other speakers.\n")
        return True
    except Exception:
        print("Error saving enrollment")
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
    Missing enrollment or unavailable verification always fails closed.
    """
    if not SPEAKER_AUTH_AVAILABLE:
        return False

    auth = _get_speaker_auth()
    if auth is None:
        return False
    if not auth.is_enrolled():
        print("⚠️  Owner voice is not enrolled. Run: hikari --enroll-voice")
        return False

    try:
        emb = auth.embedding_from_speech_recognition_audio(audio)
        res = auth.verify_embedding(emb)
        if not res.ok:
            print("❌ Voice not recognized")
        return res.ok
    except ImportError:
        print("⚠️  Speaker verification unavailable. Access denied.")
        return False
    except Exception:
        print("⚠️  Speaker verification error. Access denied.")
        return False


sr = None
stt_adapter = None
r = None
_audio_initialized = False


def _get_configured_stt_backend() -> str:
    """Return the STT backend name from runtime configuration.

    The wake daemon defaults to the local faster-whisper backend.  Cloud STT
    is only used when the user has explicitly selected it.
    """
    try:
        from core.runtime_setup import get_voice_backend_name

        backend = get_voice_backend_name()
        if backend:
            return backend
    except Exception:
        pass
    return "faster-whisper"


def initialize_audio_backends() -> bool:
    """Initialize speech dependencies once, when the daemon actually starts."""
    global _audio_initialized, sr, stt_adapter, r

    if _audio_initialized:
        return sr is not None
    _audio_initialized = True

    try:
        import speech_recognition as sr_module

        sr = sr_module
        r = sr.Recognizer()
        r.energy_threshold = 200
        r.dynamic_energy_threshold = True
        r.pause_threshold = 1.5
        r.phrase_time_limit = 10
        r.non_speaking_duration = 0.5
        backend_name = _get_configured_stt_backend()
        stt_adapter = build_stt_adapter(backend_name)
        print("[OK] SpeechRecognition")
        print(f"[OK] STT backend: {backend_name}")
    except Exception:
        sr = None
        r = None

    return sr is not None


def recognize_audio(audio):
    """Transcribe captured audio through the bounded adapter boundary."""
    if stt_adapter is None:
        return ""

    try:
        captured = CapturedAudio(
            pcm_bytes=audio.get_raw_data(),
            sample_rate=audio.sample_rate,
            sample_width=audio.sample_width,
            channel_count=1,
        )
        text = stt_adapter.transcribe(captured)
        if text:
            print("[DAEMON] Recognition succeeded", flush=True)
        return text.lower().strip()
    except SpeechAdapterError:
        print("[DAEMON] Recognition failed; falling back to text", flush=True)
        return ""
    except Exception:
        print("[DAEMON] Recognition encountered an unexpected error", flush=True)
        return ""


def speak(text):
    """Speak using macOS say command"""
    global hikari_state
    hikari_state = HikariState.SPEAKING
    print("[DAEMON] Synthesizing response", flush=True)
    # Use macOS say with faster rate
    subprocess.run(["say", "-r", "200", text], capture_output=True)
    time.sleep(0.3)
    hikari_state = HikariState.ACTIVE


_voice_orchestrator = None


def _get_voice_orchestrator():
    """Return the shared orchestrator, bound to the latest private owner chat."""
    global _voice_orchestrator
    if _voice_orchestrator is not None:
        return _voice_orchestrator

    from core.conversation_sessions import create_conversation_session_store
    from core.orchestrator import get_orchestrator

    orchestrator = get_orchestrator()
    store = create_conversation_session_store()
    record = store.latest(owner_id="local-owner")
    if record is None:
        record = store.create(owner_id="local-owner")
    orchestrator.configure_conversation_session(store, record.session_id)
    _voice_orchestrator = orchestrator
    return orchestrator


def process(text):
    """Process user input through orchestrator"""
    correction = check_learnings(text)
    if correction:
        return f"Got it! {correction}"

    try:
        orch = _get_voice_orchestrator()
        response = orch.process_input(text, source="voice")
        return response
    except Exception:
        return "The request could not be completed. Please use text input or try again."


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
    """Accept only explicit forms of the HIKARI wake phrase."""
    normalized = " ".join(text.casefold().split())
    return normalized in {"hikari", "hey hikari", "okay hikari", "hi hikari"}


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

    print("\n🎉 ACTIVATED!\n")
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
        speak(response)
        log_convo(text, response)


def listen_always() -> None:
    """Listen for the wake word, then process verified commands until stopped."""
    if sr is None or r is None:
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
        except OSError:
            print("🎤 Microphone error", flush=True)
            time.sleep(2)
        except Exception:
            print("Daemon loop error", flush=True)
            time.sleep(1)


def request_shutdown(_signum=None, _frame=None) -> None:
    """Ask the owned listener loop to stop at its next boundary."""
    global daemon_running

    daemon_running = False


def main() -> int:
    global daemon_running, hikari_state

    _print_banner()
    if len(sys.argv) > 1 and sys.argv[1] == "--check-enrollment":
        if not SPEAKER_AUTH_AVAILABLE:
            return 1
        auth = _get_speaker_auth()
        return 0 if auth is not None and auth.is_enrolled() else 1
    if not initialize_audio_backends():
        print("\n❌ Install SpeechRecognition before starting the voice daemon.")
        return 1
    if len(sys.argv) > 1 and sys.argv[1] in ["--enroll-voice", "--setup-voice"]:
        return 0 if enroll_voice() else 1

    print(f"\n✅ HIKARI ready! Say '{WAKE_WORD}' to activate")
    if not SPEAKER_AUTH_AVAILABLE:
        print("❌ Speaker verification is unavailable. Voice mode will not start.")
        return 1
    auth = _get_speaker_auth()
    if auth is None or not auth.is_enrolled():
        print("❌ Owner voice is not enrolled. Run: hikari --enroll-voice")
        return 2
    if not auth.available():
        print("❌ Speaker verification model is unavailable. Voice mode will not start.")
        return 1
    print("🔐 Owner speaker verification enabled.\n")

    daemon_running = True
    hikari_state = HikariState.LISTENING
    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)
    listen_always()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
