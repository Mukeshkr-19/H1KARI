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
import re
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from core.speech_adapters import (
    CapturedAudio,
    PocketTTSAdapter,
    prepare_spoken_text,
    SpeechAdapterError,
    build_tts_adapter,
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
from core.voice_config import tts_rate, tts_voice_name

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
    if sr is None or r is None:
        print("\n❌ SpeechRecognition is required for voice enrollment.")
        return False
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
_streaming_runtime = None
_voice_audio_loop = None
_capture_mode = "utterance_only"
_capture_backend = "utterance-only"
_playback_controller = None
_frame_endpoint_gate = None
_barge_endpoint_gate = None
_utterance_seq = 0
_interruption_seq = 0
_time_sense_coordinator = None


def _get_streaming_runtime():
    global _streaming_runtime
    if _streaming_runtime is None:
        from core.voice_streaming.runtime import VoiceStreamingRuntime

        # Native CoreAudio frames use the host monotonic clock. Production must
        # share that clock; the runtime's synthetic default is tests-only.
        _streaming_runtime = VoiceStreamingRuntime(
            "daemon_stream",
            clock=time.monotonic_ns,
        )
        _streaming_runtime.start_wake_listening()
        _resolve_capture_mode(_streaming_runtime)
    return _streaming_runtime


_time_sense_bridge = None


def _get_time_sense_bridge():
    """Advisory Time Sense bridge. Never speaks or schedules."""
    global _time_sense_bridge
    if _time_sense_bridge is None:
        from datetime import datetime, timezone

        from core.time_sense.runtime_bridge import TimeSenseRuntimeBridge

        _time_sense_bridge = TimeSenseRuntimeBridge(lambda: datetime.now(timezone.utc))
    return _time_sense_bridge


def get_timing_advisory_snapshot():
    """Expose content-free timing advisories without initiating speech."""
    bridge = _get_time_sense_bridge()
    return bridge.snapshot()

def _next_utterance_id(prefix: str = "utt") -> str:
    global _utterance_seq
    _utterance_seq += 1
    if _utterance_seq > 1_000_000:
        _utterance_seq = 1
    return f"{prefix}-{_utterance_seq}"


def _selected_capture_backend() -> str:
    """Explicit capture backend selection (distinct from STT --voice-backend)."""
    import os
    configured = os.getenv("HIKARI_VOICE_CAPTURE_BACKEND")
    if configured is None:
        return "auto"
    raw = configured.strip().lower()
    if raw in {"macos-coreaudio", "utterance-only", "utterance_only"}:
        return "macos-coreaudio" if raw == "macos-coreaudio" else "utterance-only"
    raise ValueError("invalid_voice_capture_backend")


def _resolve_capture_mode(runtime) -> str:
    """Capability-derived capture mode. Never assumes frame stream."""
    global _capture_mode, _capture_backend, _voice_audio_loop, _frame_endpoint_gate
    global _barge_endpoint_gate
    from core.voice_streaming.live_audio import (
        AudioInputCapability,
        VoiceAudioLoop,
        try_create_production_frame_source,
        try_create_pyaudio_source,
    )

    try:
        selected_backend = _selected_capture_backend()
    except ValueError:
        _capture_backend = "invalid"
        _capture_mode = "capture_unavailable"
        runtime.set_input_capability(AudioInputCapability.UNAVAILABLE, frame_loop_open=False)
        print("[DAEMON] invalid voice capture backend; voice capture unavailable", flush=True)
        return _capture_mode
    auto_backend = selected_backend == "auto"
    _capture_backend = "macos-coreaudio" if auto_backend else selected_backend
    _voice_audio_loop = None
    _frame_endpoint_gate = None
    _barge_endpoint_gate = None
    _capture_mode = "utterance_only"

    # Legacy PyAudio probe remains unavailable-by-design.
    _ = try_create_pyaudio_source(stream_id=runtime.stream_id)

    if _capture_backend == "macos-coreaudio":
        source = try_create_production_frame_source(
            stream_id=runtime.stream_id,
            capture_backend="macos-coreaudio",
        )
        if source.capability == AudioInputCapability.FRAME_STREAM:
            loop = VoiceAudioLoop(runtime.stream_id, source=source, clock=runtime.now_ns)
            opened = loop.open()
            if opened.accepted:
                _voice_audio_loop = loop
                _capture_mode = "frame_stream"
                runtime.set_input_capability(
                    AudioInputCapability.FRAME_STREAM,
                    frame_loop_open=True,
                )
                try:
                    from core.voice_capture.endpointing import UtteranceEndpointGate
                    from core.voice_capture.vad_backend import create_vad_backend
                    import os

                    model_path = os.getenv("HIKARI_SILERO_VAD_PATH") or None
                    allow_energy = os.getenv("HIKARI_ALLOW_ENERGY_VAD", "0") == "1"
                    backend = create_vad_backend(
                        model_path=model_path,
                        allow_energy_fallback=allow_energy,
                    )
                    if not backend.available:
                        loop.close()
                        _voice_audio_loop = None
                        _capture_mode = "capture_unavailable"
                        runtime.set_input_capability(
                            AudioInputCapability.UNAVAILABLE,
                            frame_loop_open=False,
                        )
                        print("[DAEMON] local VAD unavailable; CoreAudio capture disabled", flush=True)
                        return _capture_mode
                    _frame_endpoint_gate = UtteranceEndpointGate(
                        stream_id=runtime.stream_id,
                        backend=backend,
                    )
                    _barge_endpoint_gate = UtteranceEndpointGate(
                        stream_id=runtime.stream_id,
                        backend=create_vad_backend(
                            model_path=model_path,
                            allow_energy_fallback=allow_energy,
                        ),
                        pre_roll_ms=120.0,
                        hangover_ms=220.0,
                        max_utterance_bytes=128_000,
                    )
                except Exception:
                    _frame_endpoint_gate = None
                    _barge_endpoint_gate = None
                    loop.close()
                    _voice_audio_loop = None
                    _capture_mode = "capture_unavailable"
                    runtime.set_input_capability(
                        AudioInputCapability.UNAVAILABLE,
                        frame_loop_open=False,
                    )
                    print("[DAEMON] CoreAudio endpointing unavailable", flush=True)
                return _capture_mode
            try:
                loop.close()
            except Exception:
                pass
        if auto_backend:
            _capture_backend = "utterance-only"
            _capture_mode = "utterance_only"
            runtime.set_input_capability(
                AudioInputCapability.UTTERANCE_ONLY,
                frame_loop_open=False,
            )
            print("[DAEMON] CoreAudio helper unavailable; using utterance-only capture", flush=True)
            return _capture_mode
        # Explicit selection must never silently claim success via another backend.
        _capture_mode = "capture_unavailable"
        runtime.set_input_capability(AudioInputCapability.UNAVAILABLE, frame_loop_open=False)
        print("[DAEMON] macos-coreaudio unavailable; voice capture will not start", flush=True)
        return _capture_mode

    runtime.set_input_capability(AudioInputCapability.UTTERANCE_ONLY, frame_loop_open=False)
    return _capture_mode


def _get_time_sense_coordinator():
    global _time_sense_coordinator
    if _time_sense_coordinator is None:
        from datetime import datetime, timezone
        from core.time_sense.observation_coordinator import TimeSenseObservationCoordinator

        bridge = _get_time_sense_bridge()
        _time_sense_coordinator = TimeSenseObservationCoordinator(
            lambda: datetime.now(timezone.utc),
            bridge=bridge,
        )
    return _time_sense_coordinator


def get_voice_capture_mode() -> str:
    return _capture_mode


def get_voice_capture_backend() -> str:
    return _capture_backend


def get_timing_coordinator_snapshot():
    """Content-free Time Sense coordinator snapshot (advisory only)."""
    return _get_time_sense_coordinator().content_free_snapshot()



def _sync_hikari_state_from_runtime(runtime) -> None:
    """Derive legacy hikari_state from the single canonical voice runtime."""
    global hikari_state
    if runtime.is_wake_listening:
        hikari_state = HikariState.LISTENING
    elif runtime.is_active_listening or runtime.allows_orchestrator_process():
        hikari_state = HikariState.ACTIVE


# State machine for JARVIS-style behavior (projection of VoiceStreamingRuntime)
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


def verify_speaker(audio, *, announce: bool = True) -> bool:
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
        embeddings = auth.verification_embeddings_from_speech_recognition_audio(audio)
        res = auth.verify_embeddings(embeddings)
        if not res.ok and announce:
            print(
                "❌ Voice not recognized "
                f"(match {res.score:.3f}, required {res.threshold:.3f})"
            )
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
    """Initialize local STT and optional legacy microphone capture independently."""
    global _audio_initialized, sr, stt_adapter, r

    if _audio_initialized:
        return stt_adapter is not None
    _audio_initialized = True

    backend_name = _get_configured_stt_backend()
    try:
        stt_adapter = build_stt_adapter(backend_name)
        print(f"[OK] STT backend: {backend_name}")
    except Exception:
        stt_adapter = None
        print("[DAEMON] configured local STT backend is unavailable", flush=True)

    try:
        import speech_recognition as sr_module

        sr = sr_module
        r = sr.Recognizer()
        r.energy_threshold = 200
        r.dynamic_energy_threshold = True
        r.pause_threshold = 1.1
        r.phrase_time_limit = 10
        r.non_speaking_duration = 0.5
        print("[OK] SpeechRecognition legacy capture")
    except Exception:
        sr = None
        r = None

    return stt_adapter is not None


def recognize_audio(audio, *, short_utterance: bool = False):
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
        short_transcribe = getattr(stt_adapter, "transcribe_short_utterance", None)
        if short_utterance and callable(short_transcribe):
            text = short_transcribe(captured)
        else:
            text = stt_adapter.transcribe(captured)
        if text:
            print("[DAEMON] Recognition succeeded", flush=True)
        return text.lower().strip()
    except SpeechAdapterError:
        print("[DAEMON] Recognition failed; utterance ignored", flush=True)
        return ""
    except Exception:
        print("[DAEMON] Recognition encountered an unexpected error", flush=True)
        return ""


def recognize_interrupt_audio(audio):
    """Transcribe a possible barge-in without wake-word hallucination bias."""

    if stt_adapter is None:
        return ""
    method = getattr(stt_adapter, "transcribe_interrupt_utterance", None)
    if not callable(method):
        return recognize_audio(audio, short_utterance=False)
    try:
        captured = CapturedAudio(
            pcm_bytes=audio.get_raw_data(),
            sample_rate=audio.sample_rate,
            sample_width=audio.sample_width,
            channel_count=1,
        )
        return method(captured).lower().strip()
    except SpeechAdapterError:
        return ""
    except Exception:
        return ""


def _speech_interrupt_mode(text: str) -> str | None:
    """Classify only deliberate stop commands, never transcript fragments."""
    from core.voice_streaming.runtime import speech_interrupt_mode

    mode = speech_interrupt_mode(text)
    if mode is None:
        return None
    # Preserve legacy wake_explicit alias for hikari-prefixed stops.
    normalized = " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())
    if normalized in {"hikari stop", "hikari done", "stop hikari"}:
        return "wake_explicit"
    return mode


def _is_speech_interrupt(text: str) -> bool:
    return _speech_interrupt_mode(text) is not None


def _next_interruption_id() -> str:
    global _interruption_seq
    _interruption_seq += 1
    if _interruption_seq > 1_000_000:
        _interruption_seq = 1
    return f"barge_in_{_interruption_seq}"


def _terminate_speech_process(process) -> None:
    """Stop only the owned speech process and reap it deterministically."""
    try:
        process.terminate()
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)
    except Exception:
        pass


def _request_verified_interruption(*, speech_observed_ns: int) -> str | None:
    """Request, but never confirm, one exactly correlated interruption."""
    runtime = _get_streaming_runtime()
    from core.voice_streaming.interruption_evidence import (
        InterruptionEvidence,
        InterruptionVerificationSource,
    )

    now_ns = runtime.now_ns()
    interruption_id = _next_interruption_id()
    evidence = InterruptionEvidence(
        stream_id=runtime.stream_id,
        interruption_id=interruption_id,
        target_assistant_utterance_id=runtime.interruption_target_id(),
        speaker_verified=True,
        verification_source=InterruptionVerificationSource.SPEAKER_AUTH,
        speech_observed_ns=speech_observed_ns,
        observed_at_ns=now_ns,
        expires_at_ns=now_ns + 2_000_000_000,
    )
    if not runtime.request_interruption(interruption_id, evidence=evidence):
        print("[DAEMON] Barge-in denied by runtime evidence gate", flush=True)
        return None
    return interruption_id


def _wait_for_speech_or_owner_interrupt(process) -> tuple[str, str] | None:
    """Return interrupt mode when verified owner speaks over active speech.

    Returns None if playback finished without interrupt, else stop/cancel/goodbye/
    wake_explicit. Barge-in requires an explicit interruption phrase.
    """
    if sr is None or r is None:
        process.wait()
        return None

    try:
        with sr.Microphone() as source:
            while daemon_running and process.poll() is None:
                try:
                    audio = r.listen(source, timeout=0.35, phrase_time_limit=2)
                except (sr.WaitTimeoutError, sr.UnknownValueError):
                    continue
                text = recognize_interrupt_audio(audio)
                mode = _speech_interrupt_mode(text)
                if mode is None:
                    continue
                if not verify_speaker(audio):
                    continue
                runtime = _get_streaming_runtime()
                now_ns = runtime.now_ns()
                interruption_id = _request_verified_interruption(speech_observed_ns=now_ns)
                if interruption_id is None:
                    return None
                return mode, interruption_id
    except OSError:
        pass

    process.wait()
    return None


def _wait_for_frame_stream_owner_interrupt(process) -> tuple[str, str] | None:
    """Use the already-open CoreAudio stream for barge-in during TTS."""
    loop = _voice_audio_loop
    gate = _barge_endpoint_gate
    runtime = _get_streaming_runtime()
    if loop is None or gate is None:
        process.wait()
        return None
    gate.reset()
    while daemon_running and process.poll() is None:
        pulled = loop.pull()
        if not pulled.accepted or pulled.frame is None:
            if pulled.reason.value in {
                "closed",
                "cancelled",
                "hardware_error",
                "bound_exceeded",
            }:
                break
            continue
        frame = pulled.frame
        runtime.ingest_live_frame(frame)
        tick = gate.process_frame(
            frame.pcm,
            monotonic_ns=frame.monotonic_ns,
            sample_rate=frame.sample_rate,
        )
        if tick.event.value not in {"finalized", "max_duration"} or not tick.utterance_pcm:
            continue
        pcm = tick.utterance_pcm
        utterance_id = _next_utterance_id("barge")
        text = _transcribe_pcm_utterance(pcm, short_utterance=True)
        mode = _speech_interrupt_mode(text)
        if mode is None or not _verify_speaker_pcm(pcm, utterance_id=utterance_id):
            gate.reset()
            continue
        interruption_id = _request_verified_interruption(
            speech_observed_ns=frame.monotonic_ns,
        )
        gate.reset()
        if interruption_id is not None:
            return mode, interruption_id
    process.wait()
    return None


_voice_orchestrator = None
_local_tts_adapter = None


def _start_speech_process(text: str):
    """Start the selected local backend and return process plus cleanup."""

    global _local_tts_adapter
    text = prepare_spoken_text(text)
    if not text:
        raise SpeechAdapterError("no speakable response")
    backend = (os.getenv("HIKARI_TTS_BACKEND") or "macos-say").strip()
    if backend == "pocket-tts":
        temp_dir = None
        try:
            if _local_tts_adapter is None:
                _local_tts_adapter = build_tts_adapter("pocket-tts")
            if not isinstance(_local_tts_adapter, PocketTTSAdapter):
                raise SpeechAdapterError("invalid local speech adapter")
            temp_dir = tempfile.TemporaryDirectory(prefix="hikari-tts-")
            output = os.path.join(temp_dir.name, "speech.wav")
            _local_tts_adapter.render_wav(text, output)
            process = subprocess.Popen(
                [
                    "/usr/bin/afplay",
                    "-r",
                    f"{tts_rate() / 185:.3f}",
                    output,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return process, temp_dir.cleanup
        except Exception:
            if temp_dir is not None:
                temp_dir.cleanup()
            print(
                "[DAEMON] Local neural voice unavailable; using macOS speech",
                flush=True,
            )

    process = subprocess.Popen(
        ["/usr/bin/say", "-v", tts_voice_name(), "-r", str(tts_rate()), text],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return process, lambda: None


def speak(text, *, allow_interrupt: bool = True):
    """Speak locally while accepting verified owner barge-in."""
    global hikari_state, _playback_controller
    from core.voice_capture.playback import PlaybackController

    hikari_state = HikariState.SPEAKING
    runtime = _get_streaming_runtime()
    if not runtime.assistant_speaking_start():
        hikari_state = HikariState.ACTIVE
        return False
    if _playback_controller is None:
        _playback_controller = PlaybackController(now_ns=runtime.now_ns)
    print("[DAEMON] Synthesizing response", flush=True)
    process = None
    cleanup = lambda: None

    class _ProcPlayback:
        def __init__(self, proc):
            self._proc = proc

        def pause(self) -> None:
            # Half-duplex honest path: cancel is the safe pause substitute.
            _terminate_speech_process(self._proc)

        def cancel(self) -> None:
            _terminate_speech_process(self._proc)

        def is_alive(self) -> bool:
            return self._proc.poll() is None

    try:
        process, cleanup = _start_speech_process(text)
        _playback_controller.start(
            playback_id=f"play-{_next_utterance_id('play')}",
            response_id=runtime.interruption_target_id(),
            backend=_ProcPlayback(process),
            started_ns=runtime.now_ns(),
        )
        if allow_interrupt and _capture_mode == "frame_stream":
            pending = _wait_for_frame_stream_owner_interrupt(process)
        elif allow_interrupt:
            pending = _wait_for_speech_or_owner_interrupt(process)
        else:
            process.wait()
            pending = None
        if pending is None:
            time.sleep(0.15)
            runtime.add_assistant_segment(text)
            if not _playback_controller.notify_physically_stopped():
                return False
            return True
        mode, interruption_id = pending
        if not _playback_controller.cancel():
            return False
        if not _playback_controller.notify_physically_stopped():
            return False
        if not runtime.confirm_interruption(interruption_id, is_confirmed=True):
            return False
        print("[DAEMON] Speech interrupted by explicit local command", flush=True)
        if mode == "goodbye":
            runtime.start_wake_listening()
            hikari_state = HikariState.LISTENING
            return False
        runtime.start_active_listening()
        return False
    except Exception:
        if process is not None and process.poll() is None:
            _terminate_speech_process(process)
        _playback_controller.clear()
        runtime.start_active_listening()
        print("[DAEMON] Speech playback failed", flush=True)
        return False
    finally:
        cleanup()
        if hikari_state == HikariState.SPEAKING:
            hikari_state = HikariState.ACTIVE


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
    """Return True only for an explicit command to resume wake-word listening."""
    normalized = " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())
    stop_phrases = {
        "bye",
        "goodbye",
        "good bye",
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
    }
    return normalized in stop_phrases


def _is_wake_phrase(text: str) -> bool:
    """Accept only explicit forms of the HIKARI wake phrase."""
    return _extract_wake_command(text) == ""


def _extract_wake_command(text: str) -> str | None:
    """Return a same-utterance command after an explicit HIKARI wake prefix."""
    from core.voice_streaming.runtime import extract_wake_command

    return extract_wake_command(text)


def _listen_for_wake_word() -> None:
    global hikari_state

    # Legacy loop routing remains fail-closed; canonical runtime gates process().
    if hikari_state != HikariState.LISTENING:
        return
    runtime = _get_streaming_runtime()
    if not runtime.is_wake_listening:
        return
    print("💤 ", end="\r", flush=True)
    with sr.Microphone() as source:
        r.adjust_for_ambient_noise(source, duration=0.5)
        audio = r.listen(source, timeout=5, phrase_time_limit=10)

    text = recognize_audio(audio, short_utterance=True)
    wake_command = _extract_wake_command(text)
    if not text or wake_command is None:
        return
    if not verify_speaker(audio):
        print("❌ Voice not recognized, ignoring...\n")
        return

    # Canonical runtime is the only authority before process().
    res = runtime.process_utterance(
        text,
        is_verified_speaker=True,
        is_short=True,
        utterance_id=_next_utterance_id("wake"),
    )
    _sync_hikari_state_from_runtime(runtime)
    if res.get("action") == "ignore":
        return

    print("\n🎉 ACTIVATED!\n")
    hikari_state = HikariState.ACTIVE

    if res.get("action") == "process_command":
        cmd = res["command"]
        # Execute same-utterance wake command exactly once.
        response = process(cmd)
        if response:
            speak(response)
            log_convo(cmd, response)
        return

    # Do not run the microphone barge-in listener over the acknowledgement;
    # it can consume the first words of the owner's next command.
    speak("Yes?", allow_interrupt=False)


def _listen_for_active_command() -> None:
    global hikari_state

    if hikari_state != HikariState.ACTIVE:
        return
    runtime = _get_streaming_runtime()
    # Align canonical runtime when the owned loop is already ACTIVE.
    if runtime.is_wake_listening:
        runtime.start_active_listening()
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

    # Single canonical gate: runtime decides goodbye vs process vs ignore.
    result = runtime.process_utterance(
        text,
        is_verified_speaker=True,
        utterance_id=_next_utterance_id("active"),
    )
    _sync_hikari_state_from_runtime(runtime)

    if result.get("action") == "silent_goodbye":
        hikari_state = HikariState.LISTENING
        print("💤 Going to sleep... (still listening for 'hikari')\n")
        return

    if result.get("action") != "process_command":
        return

    command = result["command"]
    response = process(command)
    if response:
        speak(response)
        log_convo(command, response)



def _transcribe_pcm_utterance(pcm: bytes, *, short_utterance: bool = False) -> str:
    """Local STT from PCM; never cloud-falls-back silently."""
    global stt_adapter
    try:
        if stt_adapter is None:
            return ""
        captured = CapturedAudio(
            pcm_bytes=pcm,
            sample_rate=16000,
            sample_width=2,
            channel_count=1,
        )
        if short_utterance and hasattr(stt_adapter, "transcribe_short_utterance"):
            result = stt_adapter.transcribe_short_utterance(captured)
        else:
            result = stt_adapter.transcribe(captured)
        return result.lower().strip() if isinstance(result, str) else ""
    except Exception:
        return ""


def _verify_speaker_pcm(pcm: bytes, *, utterance_id: str) -> bool:
    if not SPEAKER_AUTH_AVAILABLE:
        return False
    auth = _get_speaker_auth()
    if auth is None or not auth.is_enrolled():
        return False
    try:
        result = auth.verify_pcm16_mono(pcm, utterance_id=utterance_id)
        if os.getenv("HIKARI_VOICE_DIAGNOSTICS", "0") == "1":
            print(
                "[DAEMON] Speaker diagnostics "
                f"(accepted={result.ok}, score={result.score:.3f}, "
                f"threshold={result.threshold:.3f}, reason={result.reason})",
                flush=True,
            )
        return bool(result.ok)
    except Exception:
        return False


def _listen_frame_stream_cycle() -> None:
    """One wake/active cycle using CoreAudio frames + local VAD endpointing."""
    global hikari_state, daemon_running, _capture_mode
    runtime = _get_streaming_runtime()
    loop = _voice_audio_loop
    gate = _frame_endpoint_gate
    if loop is None:
        return
    if gate is None:
        time.sleep(0.05)
        return

    diagnostics = os.getenv("HIKARI_VOICE_DIAGNOSTICS", "0") == "1"
    diagnostic_frames = 0
    diagnostic_max_probability = 0.0
    deadline = time.monotonic() + 30.0
    while daemon_running and time.monotonic() < deadline:
        pulled = loop.pull()
        if not pulled.accepted or pulled.frame is None:
            if pulled.reason.value in {"closed", "cancelled", "hardware_error"}:
                _capture_mode = "capture_unavailable"
                daemon_running = False
                print("[DAEMON] CoreAudio capture stopped; voice daemon is stopping", flush=True)
                break
            time.sleep(0.01)
            continue
        frame = pulled.frame
        runtime.ingest_live_frame(frame)
        tick = gate.process_frame(
            frame.pcm,
            monotonic_ns=frame.monotonic_ns,
            sample_rate=frame.sample_rate,
        )
        if diagnostics:
            diagnostic_frames += 1
            diagnostic_max_probability = max(
                diagnostic_max_probability,
                float(tick.speech_probability),
            )
            if diagnostic_frames % 100 == 0:
                print(
                    "[DAEMON] Frame diagnostics "
                    f"(frames={diagnostic_frames}, "
                    f"max_vad={diagnostic_max_probability:.3f})",
                    flush=True,
                )
        if tick.event.value not in {"finalized", "max_duration"}:
            continue
        pcm = tick.utterance_pcm
        if not pcm:
            continue
        print(
            f"[DAEMON] Frame endpoint finalized (pcm_bytes={len(pcm)})",
            flush=True,
        )
        utterance_id = _next_utterance_id("frame")
        short = runtime.is_wake_listening
        text = _transcribe_pcm_utterance(pcm, short_utterance=short)
        if not text:
            print("[DAEMON] Frame STT returned no text", flush=True)
            gate.reset()
            continue
        print(f"[DAEMON] Frame STT complete (chars={len(text)})", flush=True)
        if runtime.is_wake_listening:
            command = _extract_wake_command(text)
            print(
                f"[DAEMON] Wake prefix matched={command is not None}",
                flush=True,
            )
            if command is None:
                gate.reset()
                continue
            speaker_ok = _verify_speaker_pcm(pcm, utterance_id=utterance_id)
            print(f"[DAEMON] Speaker verified={speaker_ok}", flush=True)
            if not speaker_ok:
                gate.reset()
                continue
            result = runtime.process_utterance(
                text if command == "" else f"hikari {command}",
                is_verified_speaker=True,
                is_short=True,
                utterance_id=utterance_id,
            )
            _sync_hikari_state_from_runtime(runtime)
            action = result.get("action")
            if action == "process_command":
                response = process(result.get("command") or command)
                if response:
                    speak(response)
            elif action == "acknowledge_wake":
                speak("Yes?", allow_interrupt=False)
            gate.reset()
            return
        speaker_ok = _verify_speaker_pcm(pcm, utterance_id=utterance_id)
        print(f"[DAEMON] Speaker verified={speaker_ok}", flush=True)
        if not speaker_ok:
            gate.reset()
            continue
        result = runtime.process_utterance(
            text,
            is_verified_speaker=True,
            is_short=False,
            utterance_id=utterance_id,
        )
        _sync_hikari_state_from_runtime(runtime)
        action = result.get("action")
        if action == "silent_goodbye":
            gate.reset()
            return
        if action == "process_command":
            response = process(result.get("command") or text)
            if response:
                speak(response)
        gate.reset()
        return


def listen_always() -> None:
    """Listen for the wake word, then process verified commands until stopped."""
    if not _capture_mode.startswith("frame_stream") and (sr is None or r is None):
        raise RuntimeError("SpeechRecognition is not installed")

    runtime = _get_streaming_runtime()
    if _capture_mode == "capture_unavailable":
        raise RuntimeError("voice_capture_unavailable")
    runtime.start_wake_listening()

    print("\n" + "=" * 50)
    print("🎯 HIKARI - JARVIS Mode Active")
    print("  • Say 'hikari' to activate (when sleeping)")
    print("  • Say 'bye', 'exit', or 'goodbye' to sleep")
    print("  • Always listening...")
    print(f"  • Capture mode: {get_voice_capture_mode()}\n")

    while daemon_running:
        try:
            if _capture_mode.startswith("frame_stream") and _voice_audio_loop is not None:
                _listen_frame_stream_cycle()
                continue
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
    global daemon_running, _streaming_runtime, _time_sense_bridge
    global _voice_audio_loop, _time_sense_coordinator, _utterance_seq, _capture_mode
    global _capture_backend, _frame_endpoint_gate, _barge_endpoint_gate
    global _playback_controller, _interruption_seq

    daemon_running = False
    if _voice_audio_loop is not None:
        try:
            _voice_audio_loop.cancel()
            _voice_audio_loop.close()
        except Exception:
            pass
        _voice_audio_loop = None
    if _streaming_runtime is not None:
        try:
            _streaming_runtime.cancel_active()
            _streaming_runtime.close()
        except Exception:
            pass
        _streaming_runtime = None
    if _time_sense_coordinator is not None:
        try:
            _time_sense_coordinator.clear()
        except Exception:
            pass
        _time_sense_coordinator = None
    if _time_sense_bridge is not None:
        try:
            _time_sense_bridge.clear()
        except Exception:
            pass
        _time_sense_bridge = None
    _utterance_seq = 0
    _interruption_seq = 0
    _capture_mode = "utterance_only"
    _capture_backend = "utterance-only"
    _frame_endpoint_gate = None
    _barge_endpoint_gate = None
    if _playback_controller is not None:
        try:
            _playback_controller.clear()
        except Exception:
            pass
        _playback_controller = None


def main() -> int:
    global daemon_running, hikari_state

    # Load private runtime choices only when the daemon is explicitly started.
    # Importing this module remains free of configuration and model side effects.
    try:
        from dotenv import load_dotenv

        private_env_name = "." + "env"
        load_dotenv(os.path.join(_REPO_ROOT, private_env_name), override=False)
    except ImportError:
        pass

    if len(sys.argv) > 1 and sys.argv[1] == "--check-enrollment":
        if not SPEAKER_AUTH_AVAILABLE:
            return 1
        auth = _get_speaker_auth()
        return 0 if auth is not None and auth.is_enrolled() else 1
    _print_banner()
    if not initialize_audio_backends():
        print("\n❌ The configured local STT backend is unavailable.")
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
