# Streaming Voice Pipeline Foundations

HIKARI-native, pure foundations for streaming voice. This package does **not**
claim live VAD, live AEC, daemon integration, or production full-duplex audio.

Package: `core/streaming_voice/`

## Guarantees

- Injected monotonic clock only (no wall-clock side effects).
- Imports and constructors perform no filesystem, microphone, network, model,
  process, or database I/O.
- No raw audio persistence; frames and segments are metadata/text only.
- Fail-closed reason codes; content-free `repr` surfaces.
- Clocks, IDs, models, audio backends, transports, persistence, and executors
  remain Mira-owned injection points.

## Transcript segments

`TranscriptSegment` is frozen with:

- `segment_id`, `utterance_id`, `session_id`
- `SpeakerCategory`
- monotonic `start_mono` / `end_mono` (`end >= start`, finite, non-negative)
- `SegmentStatus` partial/final
- bounded `ConfidenceCategory` and bounded text
- stable `sequence` ordering via `SegmentLedger` (duplicate/replay/out-of-order rejection)

## VAD state machine

States: `IDLE` → `POSSIBLE_SPEECH` → `SPEAKING` → `ENDING` → `COMPLETE` /
`CANCELLED`.

`VadStateMachine` supports debounce, minimum speech duration, silence end-of-turn
timeout, maximum utterance duration, bounded pre-roll metadata, stale/out-of-order
frame rejection, and `cancel()` from any active state.
The machine binds to the first frame's session, rejects cross-session frames,
and fails closed when replay history reaches its hard bound.

## Full-duplex turn state

States: `SLEEPING`, `WAKE_CANDIDATE`, `LISTENING`, `USER_SPEAKING`,
`ASSISTANT_THINKING`, `ASSISTANT_SPEAKING`, `INTERRUPTED`, `DRAINING`, `CLOSED`.

Exact transition graph is exported by `transition_table()`. Stale utterance /
response correlation is rejected.
Interruption timestamps are checked against the injected clock; future and stale
events cannot cancel active speech.

## Wake / sleep authority

- Sleeping audio may only create a bounded `WAKE_CANDIDATE`.
- Ordinary speech while sleeping cannot reach orchestration.
- Wake requires caller-supplied wake **and** speaker-policy evidence.
- `goodbye()` returns to `SLEEPING`; wake detector remains available while
  responses stay suppressed until another valid wake.
- Wake grants no tool or memory authority.

## Interruption / barge-in

`BargeInController` correlates interruptions to the active assistant utterance,
rejects stale/duplicate/noise events, cancels only the active utterance, and
completes through bounded drain.

## AEC capability contract

`AecNegotiator` models `AVAILABLE` / `DEGRADED` / `UNAVAILABLE` / `FAILED`.
Unavailable or degraded AEC falls back to half duplex. The contract never falsely
claims echo cancellation is active (requires `AVAILABLE` + negotiated).

## Buffering / backpressure

`BoundedVoiceBuffer` enforces hard frame/byte/time caps with deterministic
drop-oldest policy, dropped-frame counters, and content-free latency summaries.

## Mira integration points (not wired here)

- Inject audio backend and monotonic clock into `VadStateMachine` / `TurnStateMachine`.
- Supply `WakeEvidence` from existing wake-word + speaker-verification modules.
- Map turn transitions into conversation session and orchestrator gates.
- Negotiate AEC with the platform audio stack before enabling full duplex.
