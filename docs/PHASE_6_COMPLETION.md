# Phase 6 Completion Evidence

## Outcome

Phase 6's safe engineering baseline is complete. HIKARI now contains:

- a bounded reason → authorize → act → observe → correct kernel
- replay-safe remote-worker evidence contracts
- bounded read-only repository intelligence
- pure Git and sandbox policy planners
- reviewed signed-skill lifecycle contracts
- Home Assistant proposal and confirmation contracts
- encrypted-sync conflict planning
- measured model-routing evaluation
- an inert `Phase6Runtime` facade attached to `HIKARI_Orchestrator`
- pure streaming-voice/VAD/turn-taking and conversational Time Sense foundations
- strict Phase 6 command-center transport, reducer, and accessible UI components
- disabled-by-default injected adapter scaffolding with adversarial authority tests
- a server-wired, fail-closed `Phase6Subsystem` for command-center status,
  bounded agent runs, exact Home Assistant confirmation, and proposal cancellation
- disabled-by-default live backend implementations for Home Assistant, encrypted
  sync state, remote-worker state, skill archive inspection, and measured routing

The runtime defaults are deliberately restrictive. Starting HIKARI does not
enable agent execution, remote workers, skill installation, Home Assistant
transport, encrypted-sync transport, or model-router mutation. Those deployment
adapters require separate explicit configuration, injected authority boundaries,
and environment-specific acceptance testing.

## Authority boundary

- Planner, model, remote-worker, and generated-code output grants no authority.
- Every bounded action requires immediate policy authorization and audit.
- Approval references are exact, scoped, expiring, and one-time.
- Repository indexing is explicit, root-contained, bounded, and read-only.
- Git and sandbox APIs return decisions only; they execute nothing.
- Skill proposals cannot self-approve or install themselves.
- Home Assistant state changes require an exact owner confirmation and currently
  reject payload-bearing service calls without reviewed schemas.
- Sync planning handles opaque ciphertext descriptors only and never silently
  resolves authority conflicts.
- Model recommendations cannot weaken privacy, safety, resource, or freshness
  gates and do not mutate the live router.

## Automated evidence

Verified on Python 3.12:

- Full backend run: `5272 passed, 1 skipped`, plus `5` subtests; the sole
  sandbox-denied loopback socket case passed separately with local bind permission
- Home Assistant deadline and adapter regression suite: `72 passed`
- Frontend unit suite: `239 passed`; Phase 5 pretest: `7 passed`; Phase 6
  protocol/reducer suite: `13 passed`
- Frontend lint: clean
- Frontend production build: passed
- Python compileall: passed
- public privacy scan: passed
- public artifact/attribution scan: passed
- `git diff --check`: clean
- HIKARI doctor: passed with only the expected dirty-worktree warning before
  the release commit

## Remaining human and optional deployment evidence

- Phase 5 representative-user and manual accessibility checks remain human
  release evidence and are not claimed by automated tests.
- Live Home Assistant, encrypted sync, remote workers, skill installation, and
  model-router switching are optional deployments, not silently enabled product
  behavior. Each needs its own configured integration test before activation.
- Long-duration voice/full-duplex and background-daemon testing remains part of
  the final device-specific manual checklist.
- Canonical streaming-voice wake/sleep/goodbye/interruption authority is wired
  into the daemon, but a production live-frame microphone source, verified
  platform AEC, hardware playback-stop signaling, and robust real-device
  full-duplex behavior are not claimed.
- The command center is registered in the WebSocket server and mounted in the
  frontend. Its live integrations remain unavailable unless an explicitly
  configured, policy-governed subsystem and adapter are injected.
