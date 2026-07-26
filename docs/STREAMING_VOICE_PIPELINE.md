# Streaming Voice Pipeline

## Canonical runtime

**Production authority:** `core.voice_streaming.runtime.VoiceStreamingRuntime`

This is the daemon-facing runtime already used by `services/hikari_daemon.py`.
It owns wake/sleep/turn gating via `VoiceStreamStateMachine` before any
`process()` / orchestrator call.

**Compatibility facade:** `core.streaming_voice.TurnStateMachine`

Bounded contracts (AEC negotiation, barge-in correlation, backpressure,
metadata VAD, transcript segments) live under `core/streaming_voice/`.
Turn/wake APIs there **delegate** to `VoiceStreamingRuntime` and must not keep
independent mutable wake/sleep authority.

There is no third voice runtime.

## Guarantees

- While sleeping / wake-listening, ordinary speech never reaches the orchestrator.
- Wake requires verified wake evidence and speaker verification at the daemon boundary.
- Same-utterance `Hikari, <command>` executes once after wake + speaker checks.
- Exact sleep phrases return to wake-listening without `process()` or `speak()`.
- Wake grants no tool, memory, or action authority.
- No raw audio in reprs, metrics summaries, or protocol-facing state.
- Missing/unverified AEC selects honest half duplex; never claims active AEC
  without negotiated evidence.
- Cancellation clears transcript, interruption, and response correlation state.

## VAD / AEC truthfulness

SpeechRecognition in the daemon provides complete utterances, not live
frame-level VAD. Frame/VAD engines in `voice_streaming` are deterministic
contracts for injected measurements — not a claim of production mic VAD.
AEC modes are capability contracts / echo policy only — not live DSP.

## Daemon gating flow

1. Capture + STT (daemon)
2. Wake extract / speaker verify (daemon)
3. `VoiceStreamingRuntime.process_utterance(...)` (canonical gate)
4. Only `action == process_command` may call `process()`
5. `silent_goodbye` returns to wake-listening without orchestrator or speech

## Mira-owned next steps

- Platform AEC verification hooks into `report_aec_status` / `EchoCapability`
- Optional frame-level VAD backend injection (honest availability flags)
- Frontend accessibility binding to `get_accessibility_state()`
