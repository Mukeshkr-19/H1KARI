# Voice Daemon Integration & Streaming Runtime Architecture

This document describes H1KARI's production streaming-voice runtime adapter (`VoiceStreamingRuntime`) and its integration with the always-on wake-word daemon (`services/hikari_daemon.py`).

---

## Runtime Architecture

The `VoiceStreamingRuntime` (in `core/voice_streaming/runtime.py`) composes five core foundation primitives into a unified, pure in-memory runtime:

1. **`AudioFramePipeline`**: Ingress frame validation, bounded queue handling, sequence gap detection, and privacy-safe operational metrics.
2. **`VADEngineState`**: Deterministic VAD state logic enforcing frame threshold hysteresis, false-start handling, and maximum utterance duration limits.
3. **`EchoPolicyEvaluator`**: Echo cancellation policy evaluation considering capability verification, headphone connection state, and active playback context.
4. **`VoiceStreamStateMachine`**: State transitions enforcing deterministic turn rules, monotonic timestamp nanoseconds, and audit records.
5. **`StreamingTranscriptAccumulator`**: Bounded in-memory interim revisions and immutable final segments.

```
                   ┌─────────────────────────────────────────┐
                   │          VoiceStreamingRuntime          │
                   └────────────────────┬────────────────────┘
                                        │
      ┌─────────────────┬───────────────┴───────────────┬─────────────────┐
      ▼                 ▼                               ▼                 ▼
┌───────────┐    ┌─────────────┐               ┌─────────┐      ┌──────────────────┐
│Frame      │    │VAD State    │               │Echo     │      │VoiceStreamState  │
│Pipeline   │    │Engine       │               │Policy   │      │Machine           │
└───────────┘    └─────────────┘               └─────────┘      └──────────────────┘
```

---

## Daemon Compatibility Path

The wake-word daemon (`services/hikari_daemon.py`) uses a compatibility adapter path around synchronous captured speech:

1. **Module Import Safety**: Importing `services.hikari_daemon` or `core.voice_streaming.runtime` performs **zero audio model loading, zero microphone access, zero file I/O, and zero network calls**.
2. **Lazy Initialization**: `VoiceStreamingRuntime` is instantiated lazily when daemon listening starts (`listen_always()`).
3. **Utterance Adapter**: `process_utterance(text, is_verified_speaker)` evaluates wake phrases, same-utterance wake commands, active turn processing, and silent goodbye commands through the streaming state machine.

---

## Wake-Word Gate

- **Strict Prefix Matching**: H1KARI activates only on exact wake prefixes (`hikari`, `hey hikari`, `okay hikari`, `hi hikari`).
- **No Substring / Fuzzy Matching**: Words containing "hikari" as a substring or partial match (e.g., "heck", "this mentions hikari later") are **rejected and ignored**.
- **Passive Sleeping Invariant**: While sleeping (`WAKE_LISTENING`), ordinary speech without an explicit wake prefix does **not** call `process`, does **not** call `speak`, does **not** log a conversation, and does **not** activate command mode.
- **Same-Utterance Commands**:
  - `"Hikari"` -> Activates and acknowledges with `"Yes?"`.
  - `"Hey Hikari, what time is it?"` -> Activates and processes trailing command `"what time is it?"`.
  - `"what time is it?"` while sleeping -> Ignored silently.

---

## Silent-Goodbye Behavior

When an authenticated user speaks an explicit stop/goodbye phrase (`"bye"`, `"goodbye"`, `"stop"`, `"sleep"`, `"exit"`, etc.) while in active mode:

1. The runtime transitions state back to passive `WAKE_LISTENING`.
2. Active interim and final transcript accumulator state is cleared.
3. The daemon does **not** call `process()`, does **not** speak a farewell via TTS, and does **not** log the goodbye in conversation logs.
4. The daemon loop remains running, listening silently for the next wake word.

---

## Speaker Authentication Boundary

- **Fails Closed**: Speaker verification (`core.speaker_auth.SpeakerAuth`) gates wake activation and active command execution. Missing enrollment or unverified speakers are ignored without activating command mode.
- **Carried Verification Evidence**: `process_utterance(..., is_verified_speaker=True)` carries caller-supplied verification decisions into the runtime state machine.

---

## VAD Flow

- Frame measurements (`VADFrameMeasurement`) are processed by `VADEngineState`.
- `SILENCE` -> `POSSIBLE_SPEECH` requires `speech_start_threshold`.
- `POSSIBLE_SPEECH` -> `CONFIRMED_SPEECH` requires `min_speech_frames`. Dropping below threshold before min frames triggers a **false-start drop** back to `SILENCE`.
- `max_utterance_duration_ms` forces `CONFIRMED_END` to prevent runaway listening.

---

## Interruption Lifecycle

1. **Explicit Stop Command Required**: User barge-in requires verified owner evidence and explicit interruption phrases (`"hikari stop"`, `"hikari done"`, `"stop hikari"`). VAD probability alone does **not** authorize interruption.
2. **Process Ownership**: `_terminate_speech_process(process)` stops **only** the daemon-owned TTS process (`afplay` / `say`) and reaps it deterministically. Unrelated system processes are never touched.
3. **Confirmation Protocol**: Requesting interruption transitions state to `INTERRUPTING`. Moving to `INTERRUPTED` occurs only after confirming physical playback termination (`confirm_interruption()`).

---

## Echo / AEC Fallback

- **Verification Required**: Available AEC capabilities (`native_aec_available`, `software_aec_available`) must be explicitly verified (`native_aec_verified=True`) before full-duplex operation is declared safe.
- **Headphone Mode**: Connected headphones bypass acoustic echo path and grant `full_duplex_safe=True`.
- **Fallback**: Unverified AEC falls back to `HALF_DUPLEX_FALLBACK` (muting microphone capture during playback) or `PLAYBACK_SUPPRESSION` (ducking speaker output by -18 dB during user speech).

---

## Privacy Guarantees

- **No Raw Audio Persistence**: Raw PCM frame payloads are excluded from `__repr__`, events, and logs.
- **Zero Disk Writes**: Runtime state and accumulators operate purely in volatile memory.
- **No Cloud Egress**: Speech recognition and synthesis run entirely local-first.

---

## Shutdown Behavior

Calling `request_shutdown()`:
1. Sets `daemon_running = False` to break the listener loop at its next boundary.
2. Closes `VoiceStreamingRuntime` via `close()`, resetting state to passive `WAKE_LISTENING` and clearing active buffers.

---

## Known Limitations

- **Synchronous Audio Capture**: The current daemon uses synchronous SpeechRecognition audio capture. The streaming runtime provides frame-pipeline and VAD compatibility hooks for future streaming socket/WebRTC adapters.
- **System Speech Dependencies**: Requires macOS `afplay` / `say` or local `pocket-tts` for speech synthesis.

---

## Manual Hardware Testing Checklist (for Mira / Sanjay)

1. **Wake-Word Activation**: Say "Hikari" in a quiet room; verify daemon responds "Yes?".
2. **Same-Utterance Command**: Say "Hey Hikari, what is the weather?"; verify immediate answer without double prompt.
3. **Silent Goodbye**: Say "Goodbye"; verify daemon returns to sleeping mode without speaking a response or writing conversation log entry.
4. **Speaker Lock**: Have an un-enrolled speaker say "Hikari"; verify voice is rejected and daemon remains sleeping.
5. **Barge-In**: During long TTS output, say "Hikari stop"; verify TTS stops immediately and daemon remains active.
