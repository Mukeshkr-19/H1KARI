# Streaming Voice Pipeline Architecture

This document describes H1KARI's deterministic streaming voice contracts, state machine, and transcript lifecycle primitives.

---

## State Diagram

```
                       ┌───────────────┐
                       │     IDLE      │
                       └───────┬───────┘
                               │
               start_wake_listening / start_active_listening
                               │
           ┌───────────────────┴───────────────────┐
           ▼                                       ▼
 ┌───────────────────┐                 ┌───────────────────┐
 │  WAKE_LISTENING   ├─(verified wake)─►  ACTIVE_LISTENING   │
 └───────────────────┘                 └─────────┬─────────┘
   (Passive Mode:                                │ (Active Mode:
    Ignores ordinary                               VAD speech /
    commands)                                      interim transcript)
                                                 │
                                                 ▼
                                       ┌───────────────────┐
                                       │   USER_SPEAKING   │
                                       └─────────┬─────────┘
                                                 │ (VAD speech end)
                                                 ▼
                                       ┌───────────────────┐
                                       │FINALIZING_USER_TURN│
                                       └─────────┬─────────┘
                                                 │ (Final transcript)
                                                 ▼
                                       ┌───────────────────┐
                                       │     THINKING      │
                                       └─────────┬─────────┘
                                                 │ (Assistant speaking)
                                                 ▼
                                       ┌───────────────────┐
                                       │ASSISTANT_SPEAKING ◄──┐ (Interruption failed)
                                       └─────────┬─────────┘  │
                                                 │            │
                         (Auth Interruption Req) │            │
                                                 ▼            │
                                       ┌───────────────────┐  │
                                       │   INTERRUPTING    ├──┘
                                       └─────────┬─────────┘
                                                 │ (Interruption confirmed)
                                                 ▼
                                       ┌───────────────────┐
                                       │    INTERRUPTED    │
                                       └───────────────────┘
```

---

## Passive Wake vs Active Listening

H1KARI enforces a strict distinction between **passive wake-listening** (`WAKE_LISTENING`) and **active command-listening** (`ACTIVE_LISTENING`):

1. **`WAKE_LISTENING` (Passive Mode)**:
   - The system listens strictly for a caller-supplied `VerifiedWakeEvent`.
   - Ordinary VAD speech, interim transcripts, or command events are **fails-closed rejected** and ignored while in this state.
   - Transition to `ACTIVE_LISTENING` requires an explicit, verified wake event (`is_verified=True`).

2. **`ACTIVE_LISTENING` (Active Command Mode)**:
   - The system is armed to process user speech, interim transcripts, and final turn commands.
   - VAD speech events transition the state to `USER_SPEAKING`.
   - Silent goodbye or sleep commands return the state silently to `WAKE_LISTENING`.

---

## Transcript Lifecycle

The streaming transcript pipeline manages interim revisions and immutable final segments:

- **Interim Revisions**: `InterimTranscript` events update the current live transcript (`current_interim`) as the user or assistant is actively speaking. Revisions must have monotonic timestamps `>=` prior events and match the current session ID.
- **Final Segments**: `FinalTranscript` events represent completed, immutable turns.
  - Final transcripts require valid non-empty text, non-negative monotonic timestamps, `end_monotonic_ns >= start_monotonic_ns`, and bounded confidence (`0.0 <= confidence <= 1.0`).
  - Once added to `StreamingTranscriptAccumulator`, final segments are frozen and immutable.
- **Role Separation**: User (`role="user"`) and assistant (`role="assistant"`) segments are strictly separated. Assistant output cannot be mutated or converted into owner-authored facts.
- **Session Isolation**: Transcripts with mismatched `stream_id` are rejected immediately.

---

## VAD Boundary

Voice Activity Detection (VAD) is treated as an external signal provider:
- `VADEvent` carries `is_speech`, `confidence`, `monotonic_ns`, and `speech_duration_ms`.
- VAD capability status (`VADCapability`) explicitly exposes whether VAD is enabled, available, and its operational algorithm.
- If VAD is marked unavailable (`available=False`), accessibility indicators surface an explicit warning while preserving system stability.

---

## AEC Boundary

Acoustic Echo Cancellation (AEC) isolates speaker output from microphone input:
- `AECCapability` explicitly reports hardware or software AEC status (`enabled`, `available`, `is_hardware`, `status_reason`).
- AEC status is exposed in `AccessibilityState` announcements to inform users when echo cancellation is degraded or unavailable.

---

## Barge-in Semantics

User interruption (barge-in) during assistant playback (`ASSISTANT_SPEAKING`) follows a two-phase protocol:

1. **Interruption Request**:
   - Requires an explicit, authenticated `InterruptionRequest` (`is_authenticated=True`).
   - Ambient noise or unauthenticated speech events **cannot** grant interruption authority and are rejected.
   - On valid request, state transitions to `INTERRUPTING`. The system does **not** claim physical playback has stopped at this stage.

2. **Interruption Confirmation**:
   - `InterruptionConfirmation` reports physical audio playback termination from the audio output subsystem.
   - On confirmation (`is_confirmed=True`), state transitions to `INTERRUPTED`. If unconfirmed or failed, state reverts to `ASSISTANT_SPEAKING`.

---

## Authentication Boundary

- Speaker authentication decisions (`AuthDecision`) are **carried, not performed** by the streaming state machine.
- Verification occurs in upstream speaker auth subsystems (`core.speaker_auth`).
- Wake activation and barge-in transitions check caller-supplied authentication decisions without performing biometric computation internally.

---

## Privacy

- **No Raw Audio Persistence**: Contracts and state machines store timestamps, text transcripts, and metadata only. No raw PCM, audio buffers, byte arrays, or voiceprints are persisted.
- **Session Reset**: Calling `reset()` clears all volatile in-memory interim and final transcript segments.
- **Bounded In-Memory Stores**: Transcript accumulators and transition histories enforce bounded list limits (`max_segments`, `max_history`) to prevent unbounded memory growth.

---

## Accessibility

The state machine exposes `AccessibilityState` views for UI rendering and screen readers:
- **Visual Indicators**: Map state to standard UI indicator states (`idle`, `listening`, `thinking`, `speaking`, `interrupted`, `error`).
- **Live Captions**: Exposes active interim or recent final text.
- **Screen Reader Announcements**: Provides explicit status phrases (e.g., `"Listening for wake word"`, `"Assistant thinking..."`, `"Assistant playback interrupted"`).
- **Fallback & Control**: Exposes `non_audio_fallback`, `manual_stop_available`, and `reduced_motion` preferences.

---

## Future Adapter Plan

Future streaming audio adapters (e.g., PyAudio, WebRTC, WebSocket audio streams) will integrate with this foundation by:
1. Translating hardware/socket audio frames into `VADEvent` and `InterimTranscript` events.
2. Feeding caller-verified wake events (`VerifiedWakeEvent`) from upstream wake-word engines.
3. Subscribing to `AccessibilityState` updates for visual companion overlay sync.
4. Handling `InterruptionRequest` events by stopping audio output hardware and returning `InterruptionConfirmation`.

---

## Non-Goals

- **No Hardware Access**: This module contains no direct PyAudio, PortAudio, or device driver code.
- **No Async Runtime Loop**: State transitions are purely synchronous, deterministic, and driven by caller events.
- **No Wall-Clock Time Dependency**: All temporal logic relies strictly on monotonic nanosecond timestamps passed in events.
