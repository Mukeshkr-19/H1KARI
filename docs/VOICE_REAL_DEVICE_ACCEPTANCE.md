# Voice Real-Device Acceptance Checklist

Do not pre-mark rows as passed. Sanjay/Mira fill results after manual runs.

## Evidence already collected (2026-07-26)

The bounded diagnostic used the current macOS input device and wrote no audio or
transcript artifact. It observed 738 frames, zero terminal errors, four speech
starts/finalized utterances, installed Silero available with peak probability
0.963, and local faster-whisper detected the exact wake token at index 0. Native
release self-test and probe also passed. This is evidence for the capture/VAD/STT
chain only; it does not pre-mark the speaker-auth, daemon, interruption,
Bluetooth, restart, or soak rows below.

A foreground daemon run then exposed and repaired a production clock mismatch:
the helper used host monotonic timestamps while the daemon runtime used its
synthetic test clock, causing every real frame to fail as future-dated before
VAD. With `time.monotonic_ns` injected, the daemon received continuous frames,
finalized utterances, transcribed locally, and matched an exact wake token. The
existing enrolled speaker profile rejected that wake segment, so HIKARI correctly
remained silent. Full owner wake, barge-in, goodbye, restart, device switching,
and soak acceptance therefore remain open; repeat them in a quiet room and
re-enroll deliberately if the current owner profile still rejects clean samples.

Build helper first:

```bash
./scripts/build_macos_audio_capture.sh
export HIKARI_VOICE_CAPTURE_BACKEND=macos-coreaudio
.venv/bin/python hikari.py --voice-status
.venv/bin/python hikari.py --voice-capture-test
```

| # | Test | Date | Device | Pass/Fail | Observed latency | Notes | Evidence artifact |
|---|---|---|---|---|---|---|---|
| 1 | Built-in Mac microphone | | | | | | |
| 2 | Explicit microphone permission denial | | | | | | |
| 3 | Permission restoration | | | | | | |
| 4 | Say "Hikari" from sleeping ten times (normal volume) | | | | | | |
| 5 | Ten correct wakes without duplicate acknowledgement | | | | | | |
| 6 | Ordinary sentences without wake → zero replies | | | | | | |
| 7 | "Hikari, what time is it?" same-utterance once | | | | | | |
| 8 | Bare wake then separate command once | | | | | | |
| 9 | Stop speaking → endpoint latency | | | | | | |
| 10 | "stop" while HIKARI speaking | | | | | | |
| 11 | "be quiet" while speaking | | | | | | |
| 12 | "goodbye" while speaking | | | | | | |
| 13 | Silence after goodbye | | | | | | |
| 14 | Wake again after goodbye | | | | | | |
| 15 | Switch microphone during runtime | | | | | | |
| 16 | Connect/disconnect Bluetooth headphones | | | | | | |
| 17 | Bluetooth output + built-in mic fallback | | | | | | |
| 18 | Silent/dead device simulation | | | | | | |
| 19 | Daemon restart + orphan-process check | | | | | | |
| 20 | Ten-minute continuous session | | | | | | |
| 21 | One-hour soak | | | | | | |
| 22 | CPU and memory observation | | | | | | |
| 23 | Queue/backpressure behavior | | | | | | |
| 24 | Unplug/replug device | | | | | | |
| 25 | Low-confidence/noisy-room clarification | | | | | | |
| 26 | Non-owner wake rejection | | | | | | |
| 27 | No transcript/audio files created | | | | | | |
| 28 | No private content in logs | | | | | | |
| 29 | Offline local STT behavior | | | | | | |
| 30 | Explicit cloud-disabled confirmation | | | | | | |
