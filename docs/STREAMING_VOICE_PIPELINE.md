# Streaming Voice Pipeline

## Canonical authority

`core.voice_streaming.runtime.VoiceStreamingRuntime` is the only production
wake/sleep/turn authority. `core.streaming_voice` is a compatibility/policy
facade. The daemon may call `process()` only when
`process_utterance(...).action == "process_command"`.

## Live audio frames

`core.voice_streaming.live_audio` defines injected frame-source contracts:

- `AudioInputCapability`: unavailable | utterance_only | frame_stream
- `LiveAudioFrame` / `AudioFrameSource` / `VoiceAudioLoop`

Importing the module never opens a microphone. Default construction is
unavailable. Optional PyAudio probing stays unavailable; package presence is
not hardware evidence. Production capture mode remains `utterance_only` unless
an injected frame source is opened and driven by a real frame loop via
`set_input_capability(..., frame_loop_open=True)`.

### Replay guarantee

Seen frame IDs are tracked in a bounded deque+set. When capacity is reached,
further frames are rejected with `BOUND_EXCEEDED` until the loop is
reset/closed for a new stream/session. IDs are never silently evicted
mid-session, so an old frame cannot be replayed within the active security
window.

### Staleness

Every frame — including the first — is checked against the injected `now`.
Stale and future frames are rejected without advancing canonical sequence
state. Duplicate, out-of-order, and cross-stream failures remain distinct.

## Frame → VAD

`ingest_live_frame` feeds the canonical pipeline + VAD. VAD advises boundaries
only and never calls the orchestrator. Sleeping speech can produce VAD evidence
but cannot process commands. During assistant playback, frames may update
barge-in observation only and never orchestrate.

## Barge-in evidence

`request_interruption` never trusts caller booleans such as `is_authenticated`.
Authorization requires an immutable `InterruptionEvidence` bound to stream,
interruption id, target utterance/response, verified speaker, verification
source, speech/observation timestamps, and expiry. Speech must be observed
**after** assistant playback began. Prior command VAD/`_last_vad_speech_ns`
cannot authorize barge-in. Frame-stream mode additionally requires fresh
post-playback barge VAD. Utterance-only mode requires the evidence object and
does not invent frame-level VAD.

### Assistant playback correlation

Facade code must not assign canonical private fields. Use:

- `VoiceStreamingRuntime.bind_assistant_playback(utterance_id, response_id)`
- `VoiceStreamingRuntime.clear_assistant_playback(expected_response_id=...)`

Binding is allowed only in thinking/speaking, is idempotent for an exact match,
rejects conflicts, and never changes wake/sleep authority.
`interruption_target_id()` remains the authoritative interruption target.

### Compatibility facade (`TurnStateMachine.interrupt`)

The facade accepts an `InterruptionEvent` plus required `InterruptionEvidence`.
Missing evidence or `is_authenticated=...` alone is denied with no drain.
Evidence must correlate to the event (interruption id, session/stream,
observation timestamp) and the active assistant utterance/response. The
facade forwards the exact evidence to `VoiceStreamingRuntime` and sets
draining only after the runtime accepts the request. Physical playback stop
is confirmed only via `notify_playback_stopped` before `finish_drain`.

## AEC / duplex honesty

Default AEC is unavailable → half duplex. Full duplex requires injected
`PlatformAecEvidence` that is available, enabled, verified, and bound to the
active stream/device with a fresh timestamp. AEC loss or capability downgrade
returns to half duplex immediately. No fake DSP flags.

## Privacy

`LiveAudioFrame.__repr__` and runtime events omit transcript text, speaker
identifiers, request identifiers, raw device identifiers, and sensitive
correlation values. Events use booleans, bounded counts, and stable
classifications.

## Daemon

Capability-derived capture mode defaults to utterance-only SpeechRecognition.
Exactly one capture path; exactly one `process()` per authorized utterance.
Shutdown cancels audio/VAD/transcript/timing state and resets utterance IDs.
