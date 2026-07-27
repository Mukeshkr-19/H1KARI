# Omi-Derived Voice Pipeline

Status: implementation record for HIKARI macOS CoreAudio capture / VAD endpointing

Reviewed upstream: [BasedHardware/omi](https://github.com/BasedHardware/omi) commit
`571c5d849fabb0b9e938129161ea5bb24a8e50fe` (MIT License).

HIKARI remains the only assistant, Brain, policy authority, and orchestrator.
Omi-derived code is limited to audio capture, VAD/endpointing patterns, and
streaming framing. No Omi memory, tasks, apps, cloud backend, Firebase,
Pinecone, Redis, LangChain, personas, or second-brain behavior is imported or
run.

## Exact upstream files inspected

- `desktop/macos/Desktop/Sources/AudioCaptureService.swift`
- `backend/utils/stt/vad_gate.py`
- `backend/utils/stt/vad.py`
- `backend/utils/listen_audio.py`
- `backend/routers/listen/contracts.py`
- `backend/routers/listen/receiver.py`
- `backend/routers/listen/runtime.py`

## Patterns adapted (not copied wholesale)

| Pattern | HIKARI location | Notes |
|---|---|---|
| CoreAudio IOProc capture + Float32→PCM16 clamp | `native/macos_audio_capture/.../CoreAudioCapture.swift`, `PCMConversion.swift` | 16 kHz mono PCM16 |
| Resample frame-capacity guard | `PCMConversion.resampledFrameCapacity` | Rejects zero/non-finite rates |
| Silent-mic consecutive-window watchdog | `SilentMicWatchdog.swift` | Bluetooth-biased recovery suggestion |
| VAD gate state / pre-roll / hangover architecture | `core/voice_capture/endpointing.py` | Hangover shortened for assistant use (~450 ms, not Omi meeting 4s) |
| Silero ONNX window/context constants | `core/voice_capture/vad_backend.py` | Uses the pinned `faster-whisper==1.2.1` dependency asset when installed; an explicit reviewed path can override it |

## Intentionally not imported

Omi cloud STT, Deepgram/Soniox requirements, hosted VAD fallbacks, Redis,
Firebase, Pinecone, LangChain, personas, memory/task apps, and any parallel
assistant runtime.

## License obligations

Omi is MIT. Required copyright and permission notice is reproduced in
`THIRD_PARTY_NOTICES.md`. File-level attribution appears on adapted Swift/Python
modules. No AI-tool or co-author metadata stamps are added.
This document does not claim Omi endorsement.

## HIKARI authority boundary

- `VoiceStreamingRuntime` is the sole wake/sleep/interruption state authority.
- Partial transcripts never execute commands.
- Speaker verification remains mandatory for owner wake and interruption.
- `InterruptionEvidence` must be exact; caller `is_authenticated=True` is ignored.
- Automatic production selection prefers the reviewed CoreAudio helper when it
  is built and probe-ready. The login agent pins that backend explicitly so a
  missing helper fails visibly rather than silently changing capture authority.

## IPC framing protocol

Fixed 48-byte little-endian header + bounded payload (max 65536):

`magic="HIKA" | version=u16 | type=u16 | sequence=u64 | monotonic_ns=u64 |
sample_rate=u32 | channels=u16 | sample_width=u16 | payload_len=u32 |
reserved0..2=u32`

Message types: ready=1, pcm=2, error=3, end=4, cancelAck=5.

Stderr is content-free diagnostics only. Corrupt framing fails closed.

## Privacy

No raw audio, transcripts, embeddings, or personal identifiers in reprs, logs,
exceptions, metrics, or protocol error frames. Helper never records to disk.
Probe mode (`--help` / no `--capture`) does not open the microphone.

## Build

```bash
./scripts/build_macos_audio_capture.sh
```

Requires Swift 5.9+, macOS 13+ deployment target and a compatible installed
macOS SDK. The build script uses a local module cache and runs the release
self-test before accepting the helper. Output:
`native/macos_audio_capture/.build/release/hikari-macos-audio-capture`.

## Selection and fallback

With no capture variable, foreground startup uses `auto`: CoreAudio when the
reviewed helper is probe-ready, otherwise the legacy utterance-only path. The
login-agent installer builds the helper and writes
`HIKARI_VOICE_CAPTURE_BACKEND=macos-coreaudio`; explicit selection fails closed
if the helper or local VAD is unavailable. STT selection remains
`--voice-backend` (`faster-whisper` by default for the daemon).

## Known limitations

- The bounded capture diagnostic passed the current Mac input through CoreAudio,
  the installed Silero asset, endpointing and local faster-whisper on 2026-07-26;
  the remaining daemon/barge-in/device matrix is tracked in
  `docs/VOICE_REAL_DEVICE_ACCEPTANCE.md`.
- If neither the pinned dependency asset nor an explicit Silero path is
  available, production CoreAudio mode fails closed. Energy fallback is an
  explicit degraded/test option and is never labeled as Silero.
- Full-duplex AEC is not claimed; barge-in uses pause-on-speech / interrupt
  phrases with evidence gates.
- Faster-whisper partial streaming is capability-flagged off by default.
