# Voice Streaming Adapters & Runtime Foundations

This document describes H1KARI's runtime-neutral voice streaming infrastructure for audio frame pipelines, Voice Activity Detection (VAD) state machines, and Acoustic Echo Cancellation (AEC) policy evaluation.

---

## Frame Lifecycle

Audio frames move through a bounded, deterministic pipeline (`AudioFramePipeline`) enforcing strict validation, metadata tracking, and privacy bounds:

1. **Ingress & Metadata Validation**:
   - Frames are wrapped in immutable `AudioFrameMetadata` specifying `stream_id`, `sequence_id`, `monotonic_ns`, `sample_rate`, `channels`, `sample_width`, `duration_ms`, `payload_bytes`, and `is_end_of_stream`.
   - Sample rates, channel counts, sample widths, payload sizes, and frame durations are validated against strict allowed sets.
2. **Discontinuity & Rejection Handling**:
   - Out-of-order sequence IDs (`sequence_id <= last_sequence_id`) or backward timestamps (`monotonic_ns < last_monotonic_ns`) are rejected immediately.
   - Sequence gaps or timestamp jumps emit a `FrameDiscontinuityEvent` without dropping valid subsequent frames.
3. **Queue & Overflow Control**:
   - The queue operates under a configured `max_queue_size` with configurable `FrameOverflowMode`:
     - `DROP_OLDEST`: Drops the oldest pending frame to accommodate new ingress.
     - `DROP_NEWEST`: Drops the incoming frame when the queue is full.
     - `FAIL_CLOSED`: Rejects new frames and flags pipeline overflow error.
4. **Egress & Closure**:
   - Popping a frame updates privacy-safe `FramePipelineMetrics` (total duration, bytes processed, frames dropped).
   - Receiving `is_end_of_stream=True` closes the pipeline and prevents further frame insertion.

---

## VAD Transition Table

The VAD engine (`VADEngineState`) enforces deterministic hysteresis and consecutive frame thresholds:

| Current State | Condition / Measurement Event | Next State | Trigger / Reason |
|---|---|---|---|
| `SILENCE` | `prob >= speech_start_threshold` | `POSSIBLE_SPEECH` / `CONFIRMED_SPEECH` | Above start threshold |
| `SILENCE` | `prob < speech_start_threshold` | `SILENCE` | Remains silent |
| `POSSIBLE_SPEECH` | `prob >= speech_start_threshold` (consecutive frames >= `min_speech_frames`) | `CONFIRMED_SPEECH` | Min speech frames reached |
| `POSSIBLE_SPEECH` | `prob < speech_start_threshold` (before `min_speech_frames`) | `SILENCE` | **False-start handling** |
| `CONFIRMED_SPEECH` | `prob < speech_stop_threshold` | `POSSIBLE_END` | Below stop threshold |
| `CONFIRMED_SPEECH` | `assistant_speaking=True` & `prob >= interruption_threshold` (consecutive frames >= `min_interruption_frames`) | `INTERRUPTION_CANDIDATE` | Interruption threshold reached |
| `CONFIRMED_SPEECH` | `utterance_duration >= max_utterance_duration_ms` | `CONFIRMED_END` | Max duration limit reached |
| `POSSIBLE_END` | `prob < speech_stop_threshold` (consecutive frames >= `min_silence_frames`) | `CONFIRMED_END` | Min silence frames reached |
| `POSSIBLE_END` | `prob >= speech_stop_threshold` | `CONFIRMED_SPEECH` | Speech resumed |
| `INTERRUPTION_CANDIDATE` | `prob < speech_stop_threshold` | `POSSIBLE_END` | Speech stopped during interruption |
| `INTERRUPTION_CANDIDATE` | speech continues | `CONFIRMED_SPEECH` | Interruption candidate continues as speech |
| `CONFIRMED_END` | `prob >= speech_start_threshold` | `POSSIBLE_SPEECH` / `CONFIRMED_SPEECH` | New utterance started |
| Any | `close()` | `CLOSED` | Pipeline closed |

---

## Echo / AEC Trust Model

Full-duplex audio operation (simultaneous playback and microphone capture) requires verified acoustic echo cancellation. H1KARI enforces a strict trust model for AEC capabilities:

1. **Verification Requirement**:
   - Availability alone is **insufficient**. Capability flags must explicitly confirm verification (`native_aec_verified=True` or `software_aec_verified=True`).
   - Unverified AEC is denied full-duplex operation to prevent acoustic feedback loops.
   - High residual echo confidence during simultaneous playback and capture
     overrides a verified AEC claim and selects playback suppression.
2. **Supported Modes**:
   - `HEADPHONES_ACTIVE`: Headphones connected. Eliminates acoustic echo path; `full_duplex_safe=True`.
   - `NATIVE_AEC_ACTIVE`: Hardware or OS-level AEC verified; `full_duplex_safe=True`.
   - `SOFTWARE_AEC_ACTIVE`: Software DSP AEC verified; `full_duplex_safe=True`.
   - `HALF_DUPLEX_FALLBACK`: Unverified AEC fallback mode; `full_duplex_safe=False`.
   - `PLAYBACK_SUPPRESSION`: Unverified AEC fallback ducking/suppressing playback volume during user speech.
   - `UNSUPPORTED_FAIL_CLOSED`: Fallback disallowed; input and output disabled.

---

## Fallback Selection

When verified AEC and headphones are unavailable (`full_duplex_safe=False`), `EchoPolicyEvaluator` selects safe fallback actions based on activity:

- **Playback Active & User Silent**: Mutes microphone input capture (`mute_input=True`) to prevent speaker playback from feeding into the microphone.
- **Playback Active & User Speaking / Interruption Requested**: Suppresses/ducks assistant playback volume (`suppress_output=True`, `attenuation_db=-18.0`) to grant priority to user turn.
- **Playback Inactive**: Half-duplex ready state with input unmuted.
- **Fallback Disabled**: Evaluates to `UNSUPPORTED_FAIL_CLOSED`, muting input and suppressing output.

---

## Privacy Guarantees

1. **Zero Raw Audio Leakage**:
   - `AudioFrame.__repr__` explicitly excludes raw payload bytes (`repr=False`).
   - Exceptions, events (`FrameDiscontinuityEvent`, `VADStateTransitionEvent`), and metrics (`FramePipelineMetrics`) record sequence IDs, counts, durations, and byte counts only. Raw PCM bytes never enter strings or logs.
2. **Zero In-Memory Persistence**:
   - Pipeline queues and bounded VAD histories operate strictly in volatile memory. Calling `reset()` clears all pending frames, histories, and metrics.
3. **No File/Database Writes**:
   - Frame processing and policy evaluation perform zero disk, database, socket, or external subprocess I/O.

---

## Limitations

- **Pure In-Memory Infrastructure**: Implements frame queues, VAD state transitions, and AEC policy evaluation without binding to hardware device drivers (PortAudio/PyAudio).
- **Measurement-Driven VAD**: Requires callers to supply frame probability/energy measurements (`VADFrameMeasurement`); contains no internal neural ML weights.

---

## Future Mira-Owned Adapter Integration into the Daemon

Future integration with `services/hikari_daemon.py` and PyAudio/WebRTC streaming adapters will proceed as follows:

1. **Ingress Adapter**: Wrap raw microphone stream callbacks into `AudioFrame` objects and push them to `AudioFramePipeline`.
2. **VAD Adapter**: Pass frame measurements into `VADEngineState` and emit state transitions to `VoiceStreamStateMachine`.
3. **AEC Adapter**: Inspect system hardware (headphone jack, OS AEC capabilities) to construct `EchoCapability` and apply `EchoPolicyDecision` instructions (muting input or ducking output).
4. **Daemon Wiring**: Connect pipeline metrics and state transitions to the daemon's voice status monitoring without persisting raw audio.


## macOS CoreAudio capture

Use `HIKARI_VOICE_CAPTURE_BACKEND=macos-coreaudio` or `--voice-capture-backend macos-coreaudio` to require the reviewed helper. Unconfigured foreground startup uses `auto`; the login agent builds and pins CoreAudio explicitly. See `docs/OMI_DERIVED_VOICE_PIPELINE.md`.
