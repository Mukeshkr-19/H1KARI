# HIKARI Master Integration and Implementation Plan

Status: active implementation authority

Version: 1.0

Updated: 2026-07-13

Repository: H1KARI
Product and runtime name: HIKARI / `hikari`

## 1. Purpose

This file is the canonical execution plan for evolving the existing H1KARI repository into HIKARI: a safe, local-first, accessible AI companion for technical and non-technical users.

It combines the product master plan, the living feature-source tracker, the current repository baseline, and the future-integration backlog into one implementation authority. Detailed research may remain elsewhere, but implementation order, safety boundaries, acceptance gates, and branch discipline are governed here.

## 2. North star

HIKARI helps people learn, create, communicate, understand information, operate technology, and complete everyday tasks through conversation, voice, vision, careful memory, and safe tools.

The product succeeds only when ordinary users can receive useful help without needing to understand models, APIs, terminals, agent frameworks, or repository internals.

## 3. Product definition

HIKARI is:

- one companion experience across text, voice, vision, files, tools, and devices
- local-first, with explicit and replaceable cloud escalation
- provider-independent at the session, task, memory, and tool layers
- governed by a HIKARI-owned safety and permission boundary
- careful about memory provenance, ownership, review, correction, and deletion
- accessible by architecture, not by a late visual pass
- useful to general users while retaining a capable developer mode

HIKARI is not:

- a wholesale fork or merge of unrelated assistant repositories
- an unrestricted autonomous process with direct computer control
- a model-specific wrapper
- a consumer-web subscription bypass or credential scraper
- a medical, emergency, therapy, or safety-critical authority
- a developer-only command line presented as a universal assistant

## 4. Preserved foundations

The migration must preserve these working H1KARI strengths:

- Brain v2 remains the memory authority until a separately approved migration passes equivalent safety and behavior tests.
- Reviewed memories remain distinct from raw evidence and unreviewed candidates.
- Owner, household, guest, child, trusted-helper, and unknown identities remain separable.
- Guest and unknown sessions cannot read or write owner memory by default.
- Public source remains separate from credentials, conversations, voice artifacts, databases, and personal runtime state.
- Existing CLI, server, doctor, memory, daemon, and frontend behavior remains available until replacement acceptance tests pass.
- Existing tests are expanded into characterization and regression coverage rather than discarded.

## 5. Non-negotiable engineering rules

1. One companion orchestrator owns the active interaction and task lifecycle.
2. Voice, memory, identity, files, browser access, and system access are services or policy-governed tools, not competing top-level agents.
3. Specialized workers are used only for bounded expert or parallel work and never bypass central policy.
4. Every side effect is represented as a typed action proposal and evaluated before execution.
5. Models and tools never receive raw long-lived credentials.
6. Unknown actors, tools, folders, domains, and external actions are denied by default.
7. High-risk actions require stronger identity evidence than voice recognition alone.
8. No open-source code, model, prompt, asset, or dataset enters the product without exact provenance and license review.
9. Existing working behavior is wrapped and characterized before it is replaced.
10. Accessibility, privacy, failure behavior, audit events, and rollback are part of each feature's definition of done.
11. The base installation remains small; heavy speech, vision, model, and research capabilities become optional workers or adapters.
12. Repository files, commits, and public artifacts remain project-owned and contain no automation attribution or conversation residue.

## 6. Source-of-truth hierarchy

When planning inputs conflict, use this order:

1. Safety, privacy, identity isolation, license obligations, and explicit owner decisions.
2. This canonical plan.
3. Verified behavior and tests in the current repository.
4. The living feature-source tracker and accepted architecture decisions.
5. External project patterns and assessments.

External project documentation is evidence, not authority. Repository behavior is not automatically desirable merely because it already exists; unsafe behavior must be contained while compatibility is maintained.

## 7. Development and integration workflow

### 7.1 Protected branch model

- `main` is frozen for this program and is not an implementation target.
- `develop` is the only integration branch for this program.
- Every coherent work package starts from the latest verified `develop`.
- Work branches use a focused prefix such as `docs/`, `audit/`, `infra/`, `core/`, `brain/`, `safety/`, `voice/`, `frontend/`, or `integration/`.
- Work branches merge into `develop` only after their required gates pass.
- Nothing merges from `develop` to `main` without a separate, explicit owner decision.
- Only the designated release operator pushes program branches.

### 7.2 Existing branch intake

Existing unmerged fix branches are candidates, not an automatic bundle. For each branch:

1. Record the branch tip and its merge base with `main`/`develop`.
2. Review the complete diff and all callers touched by the change.
3. Check whether later work already supersedes or duplicates it.
4. Rebase or cherry-pick only the minimal valid change into a fresh intake branch.
5. Run focused tests, then the integration gates.
6. Merge the verified intake branch into `develop`.
7. Leave the original branch unchanged unless cleanup is separately requested.

This prevents a large historical branch merge from silently importing stale assumptions or conflicting fixes.

### 7.3 Required change record

Every work package states:

- target user outcome
- non-goals
- affected data and trust boundaries
- action risk classes and permissions
- compatibility behavior
- tests and manual checks
- migration and rollback path
- third-party provenance, when applicable

## 8. Target architecture

```text
Clients
  Desktop | CLI/TUI | Web | Mobile | Messaging | Home voice satellite
      |
Local gateway and event stream
      |
Companion orchestrator
  Task ledger | Planner | Interaction mode | Context | Verifier
      |
HIKARI safety kernel
  Identity | Policy | Consent | Sandbox | Audit | Secrets
      |
Services
  Brain v2 memory | Speech | Vision | Scheduler | Notifications | Sync
      |
Adapters
  Models | Tools | MCP | Browser | OS | Home Assistant | Official CLIs
      |
Execution backends
  Scoped local host | Sandbox | Optional remote worker | Mobile device
```

### 8.1 Stable contracts

The first contracts to stabilize are:

- `CompanionRequest`, `CompanionResponse`
- `TaskPlan`, `TaskStep`, `TaskResult`
- `ActionProposal`, `PolicyDecision`, `ApprovalGrant`
- `ActorIdentity`, `TrustLevel`, `HouseholdRole`
- `MemoryEvidence`, `MemoryCandidate`, `AcceptedMemory`, `MemoryCorrection`
- `ModelRequest`, `ModelCapability`, `ModelUsage`, `ProviderHealth`
- `ToolManifest`, `ToolInvocation`, `ToolResult`, `SideEffectRecord`
- `ChannelEvent`, `ConversationSession`, `NotificationPreference`
- `SpeechTurn`, `TranscriptSegment`, `SpeakerMatch`
- `VisionObservation`, `Confidence`, `SafetyConstraint`

Typed Python contracts come first. A Rust trusted core is an incremental implementation option for stable policy, secrets, audit, supervision, and IPC boundaries; it is not a prerequisite or permission for a rewrite.

## 9. Safety and trust model

### 9.1 Action sequence

```text
intent or workflow
  -> normalized action proposal and declared side effects
  -> actor, device, channel, and active grant resolution
  -> risk and sensitivity classification
  -> policy decision: deny, ask, sandbox, or scoped host access
  -> bounded execution with timeout and resource limits
  -> side-effect and artifact capture
  -> outcome verification
  -> immutable audit event
  -> undo offer when supported
```

### 9.2 Risk classes

| Class | Examples | Default |
|---|---|---|
| R0 Conversation | reasoning, drafting, explanation | allow; no external side effects |
| R1 Read | approved files, public web, selected messages | allow only within an active scope |
| R2 Reversible write | create, draft, move, copy | preview plus approval or precise persistent grant |
| R3 External action | send, publish, invite, notify | explicit target and payload confirmation |
| R4 Destructive/security | delete, system settings, credentials, doors | deny by default; narrow feature-specific flow |
| R5 Financial/safety-critical | payments, emergency, physical navigation | unsupported or strongly restricted initially |

### 9.3 Privacy defaults

- Local-only operation is a supported mode with limitations stated clearly.
- Conversation, file, audio, image, and personal-memory content is not telemetry by default.
- Audio recordings are not retained by default.
- Camera frames are transient unless the user explicitly saves them.
- Memory writes remain visible, attributable, correctable, exportable, and forgettable.
- Remote channels, sync, and proactive behavior are opt-in and revocable.
- Logs and diagnostics redact credentials and private content.

## 10. Accessibility requirements

Accessibility is a blocking release gate for the support level claimed by a feature.

- Visual: screen-reader semantics, keyboard navigation, focus control, scalable text, high contrast, and no color-only meaning.
- Hearing: live captions, transcripts, visual microphone/speaking states, and text alternatives.
- Motor: voice-only paths, large targets, switch-control compatibility, minimal precision, and no unnecessary timers.
- Cognitive: plain language, predictable navigation, one-task/one-step modes, progress, repeat, rephrase, and easy undo.
- Speech: typing and switch input remain equal to voice; slow or non-standard speech is not penalized.
- Care: trusted-helper access is explicit, visible, scoped, logged, and revocable.
- Child: age-appropriate language, restricted integrations, guardian transparency, and no unsupervised purchases or public posting.

Release claims require automated checks plus manual validation with representative users and assistive technologies.

## 11. Canonical delivery roadmap

### Phase A - Baseline control and branch intake

Outcome: `develop` becomes a reproducible, reviewed integration baseline without changing `main`.

Work:

- publish this canonical plan
- inventory existing worktrees and unmerged branches
- classify each branch as integrated, candidate, superseded, conflicting, or rejected
- intake only verified fixes through focused branches
- record baseline test, doctor, Brain v2, privacy, frontend, and dependency results

Exit:

- `develop` is based on current `main`
- every added commit has a reviewed diff and passing relevant tests
- no private data or automation attribution is present
- `main` remains unchanged

### Phase 0 - Foundation and audit

Outcome: the project is governable, reproducible, and ready for safe architectural migration.

Work packages:

- WP-001 component, dependency, model, asset, prompt, and license inventory
- WP-002 governance and public security documents; final project license remains a separate owner decision after audit
- WP-003 characterization coverage for CLI, server, doctor, Brain v2, daemon, voice identity, and frontend API behavior
- WP-004 continuous checks for tests, privacy, secrets, dependencies, frontend, and generated schemas
- WebSocket compatibility changes follow `docs/PROTOCOL_V1.md` and the shared
  `protocol/hikari-v1.json` source of truth
- WP-005 canonical naming and compatibility policy
- WP-006 `HIKARI_HOME` layout, initialization, backup, migration dry run, and rollback
- WP-007 public threat model and central action-policy interface skeleton

Exit:

- fresh initialization works without a required sibling private repository
- current supported behavior remains green
- every shipped dependency and asset has known provenance
- no final license is added before provenance review and owner approval

Completion evidence: `docs/PHASE_0_COMPLETION.md`.

### Phase 1 - Safe companion kernel

Outcome: one resumable companion task can safely read and explain a user-approved document.

Work packages:

- WP-101 typed domain contracts
- WP-102 persistent task ledger with progress, interruption, retry, cancellation, and verification
- WP-103 central policy and audit path for existing file, system, code, browser, and scheduling tools
- WP-104 actor-aware identity and approval grants
- WP-105 Brain v2 service boundary preserving provenance and guest isolation
- WP-106 provider-neutral session and capability router
- WP-107 selected-document read/explain vertical slice
- WP-108 basic accessible desktop/client flow while retaining CLI compatibility

Exit:

- a fresh user can initialize HIKARI without private machine assumptions
- a selected document can be explained in text through an explicit read grant
- every file access is audited
- task state survives provider fallback and client reconnect
- no side-effecting tool can bypass policy

### Phase 2 - Voice companion

Outcome: the Phase 1 document workflow can be completed without a keyboard.

Work:

- push-to-talk first; optional wake word later
- local/cloud STT and TTS adapters
- VAD, turn detection, interruption, repeat, slower, stop, and captions
- speaker identity as optional evidence, never sole high-risk authorization
- visible microphone state and audio retention controls
- clean fallback to text

Exit:

- voice-only document flow works with captions
- unknown speakers cannot access owner memory
- voice failure does not lose the task

Completion evidence: `docs/PHASE_2_COMPLETION.md`.

### Phase 3 - Safe productivity tools

Outcome: approved everyday tasks can be drafted and executed with transparent scopes.

Work:

- browser research, email draft, calendar read/draft, reminders
- MCP and skill-package policy wrapper
- scheduled jobs with ownership, quiet hours, pause, audit, and meaningful-change delivery
- approval scopes for once, session, duration, or precise persistent grants

Exit:

- every external action previews destination and payload
- jobs are visible, pausable, and audited
- no third-party tool exceeds its declared permissions

Completion evidence: `docs/PHASE_3_COMPLETION.md`.

### Phase 4 - Vision and mobile capture

Outcome: users can safely ask for help with an image, screenshot, document, or live camera view.

Work:

- OCR and document/image understanding adapters
- confidence-aware observations and clarification
- obvious, cancellable camera activity
- secure device pairing and cross-device task handoff

Exit:

- no silent capture
- uncertainty is communicated
- mobile cannot broaden desktop permissions

Completion evidence: `docs/PHASE_4_COMPLETION.md`.

### Phase 5 - Learning, care, and accessibility release

Outcome: Teach Me, Guide My Hands, Care, and child experiences pass real accessibility and safety validation.

Exit:

- critical flows pass automated and manual accessibility checks
- representative users complete the declared tasks
- trusted-helper access is visible and revocable

### Phase 6 - Developer and ecosystem expansion

Outcome: advanced repository, home, skill, remote-worker, and evaluation capabilities grow without weakening the core.

Work includes repository intelligence, Git/sandbox workflows, signed skill packages, Home Assistant, optional encrypted sync, optional remote workers, and measured local-model routing.

## 12. Future integrations register

Every entry below is a candidate or study source until it passes Section 13. Names identify upstream ideas; they do not authorize code copying or a dependency.

### 12.1 Runtime, orchestration, and task systems

| Source family | Candidate value | Planned disposition | Earliest phase |
|---|---|---|---|
| Hermes Agent | provider switching, TUI, gateways, scheduling, skills, MCP, bounded workers | study and adapt behind HIKARI contracts | Phase 1 |
| Vellum Assistant | actor identity, credentials isolation, sandbox, channels, onboarding, permission UX | benchmark and selectively port audited patterns | Phase 0 |
| OpenClaw | modular runtime and tool architecture | study-only until interfaces stabilize | Phase 1 |
| OpenJarvis | local-first primitives and routing evaluation | optional adapters and benchmark ideas | Phase 1 |
| DeerFlow | research and workflow patterns | study for bounded research workflow | Phase 3 |
| Ruflo / Claude Flow | coordination and task-graph patterns | study; avoid framework coupling | Phase 6 |
| OpenSwarm | specialist-worker coordination | study only for genuinely parallel expert work | Phase 6 |
| Beads | durable task/dependency tracking | compare with HIKARI task ledger; do not duplicate | Phase 1 |
| OpenSpace | codebase intelligence patterns | study for developer mode | Phase 6 |
| Project delegation pattern | work contracts, verification, merge gates | adopt as repository workflow, not product runtime dependency | Phase A |

### 12.2 Memory and knowledge

| Source family | Candidate value | Planned disposition | Earliest phase |
|---|---|---|---|
| MemPalace | structured memory concepts | compare with Brain v2; preserve Brain v2 authority | Phase 1 |
| LLM Wiki pattern | raw evidence to reviewed human-readable knowledge | continue private wiki artifact with controlled writeback | Phase 1 |
| Graphify | graph extraction and code/knowledge relationships | optional study after retrieval contracts stabilize | Phase 6 |
| Chroma / Qdrant | vector retrieval backends | optional adapter only if measured need exceeds current storage | Phase 1 |
| Time-sense layer | temporal context, staleness, deadlines, meaningful reminders | HIKARI-owned service integrated with tasks and memory | Phase 3 |
| Research papers | scoring, retrieval, memory, safety, accessibility evidence | cite decisions and evaluations; no blind implementation | all phases |

### 12.3 Voice and real-time media

| Source family | Candidate value | Planned disposition | Earliest phase |
|---|---|---|---|
| Pipecat / TEN | real-time transport and turn-taking | benchmark behind `SpeechEngine` interface | Phase 2 |
| openWakeWord | local wake word | optional, after push-to-talk is stable | Phase 2 late |
| Silero VAD | voice activity detection | optional adapter with model/license audit | Phase 2 |
| faster-whisper | local speech recognition | optional worker; not base install | Phase 2 |
| Kokoro TTS | local speech output | optional worker; voice/model license audit required | Phase 2 |
| Omi | wearable/continuous capture and memory ideas | study privacy and consent patterns; no continuous capture by default | Phase 4+ |

### 12.4 Vision, OCR, desktop, and mobile

| Source family | Candidate value | Planned disposition | Earliest phase |
|---|---|---|---|
| Florence-2 / LLaVA / Moondream | local vision capabilities | capability adapters selected by measured hardware fit | Phase 4 |
| PaddleOCR / Tesseract | document and image text extraction | compare accuracy, footprint, language, and license | Phase 4 |
| Tauri | packaged accessible desktop shell | evaluate after stable gateway APIs | Phase 1 |
| egui | native Rust UI option | later study; no duplicate desktop stack | Phase 6 |
| Flutter / React Native / native mobile | camera, voice, notification, handoff client | decide only after desktop contract stabilizes | Phase 4 |

### 12.5 Models and providers

| Source family | Candidate value | Planned disposition | Earliest phase |
|---|---|---|---|
| Ollama | simple local model serving | provider adapter | Phase 1 |
| llama.cpp | efficient local inference | provider adapter | Phase 1 |
| MLX | Apple-local inference | optional platform adapter | Phase 1 |
| vLLM | high-throughput serving | optional remote/self-hosted adapter | Phase 6 |

No provider is the product. Provider changes must not destroy session, task, tool, or memory state.

### 12.6 Tools, browser, home, security, and operations

| Source family | Candidate value | Planned disposition | Earliest phase |
|---|---|---|---|
| MCP | interoperable tools and skills | first-class client only through HIKARI policy wrapper | Phase 3 |
| Browser Use / Playwright | browser research and interaction | adapter with domain, data, and action scopes | Phase 3 |
| Home Assistant | local device ecosystem | official APIs or MCP-like adapter; never reimplement ecosystem | Phase 6 |
| ripgrep / fd / bat | fast file and code exploration | use installed/native tools where appropriate | Phase 1/6 |
| OS keychain / keyring-rs | credential isolation | trusted credential broker candidate | Phase 1 |
| gVisor / Firecracker / containers | stronger execution isolation | select by platform and measured risk; not base complexity by default | Phase 3/6 |
| OpenTelemetry | operational events and metrics | privacy-filtered, content-free telemetry only | Phase 3 |
| OpenAI Evals / Langfuse concepts | evaluation and traces | build provider-neutral, privacy-safe evaluation artifacts | Phase 0/6 |
| Lucide | accessible icon set | audited UI dependency if selected | Phase 1 |
| Docusaurus | documentation site | only when docs volume justifies a site | Phase 6 |
| cargo-dist | native release packaging | after a real Rust/native deliverable exists | Phase 6 |
| Syncthing | user-controlled local sync concepts | study; explicit encryption and conflict model required | Phase 6 |
| public API catalogs | integration discovery | research input only; each API audited independently | Phase 3+ |

## 13. Integration acceptance gate

No future-integration candidate advances from study to implementation until all answers are recorded:

1. Which user outcome does it enable now?
2. Can existing code, the standard library, or a native platform capability already satisfy the need?
3. What exact HIKARI-owned interface isolates it?
4. Is it a dependency, vendored code, isolated port, protocol adapter, study-only source, or rejection?
5. What exact release/commit is evaluated?
6. What licenses apply to code, subdirectories, models, prompts, assets, and datasets?
7. What data leaves the device, and under which user-visible grant?
8. What side effects and risk classes can it produce?
9. What sandbox, resource, timeout, and network limits apply?
10. How is it disabled, replaced, upgraded, and rolled back?
11. What security, privacy, performance, failure, and accessibility tests prove fitness?
12. Who owns vulnerability monitoring and upstream updates?

The decision and provenance are recorded before code is merged.

## 14. Quality gates

### 14.1 Every work branch

- focused tests for changed behavior
- `git diff --check`
- no secrets, private runtime data, databases, generated QA artifacts, or automation attribution
- documentation updated when behavior or contracts change
- frontend release candidates complete `docs/ACCESSIBILITY_CHECKLIST.md`
- dependency and license delta reviewed
- rollback or disable path documented for behavior-changing work

### 14.2 Integration branch

Run the applicable full set before declaring `develop` green:

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python hikari.py --doctor
.venv/bin/python hikari.py --voice-status
.venv/bin/python hikari.py --brain-v2-eval
.venv/bin/python hikari.py --brain-live-qa
.venv/bin/python tests/privacy_scan.py
```

For frontend changes:

```bash
cd hikari-frontend
npm run lint
npm run build
npm audit --audit-level=moderate
```

Also inspect tracked and untracked paths, dependency changes, branch ancestry, and the final diff against the current `develop` baseline.

### 14.3 Release blockers

- known critical security vulnerability
- privacy or identity-boundary failure
- policy bypass for a side effect
- loss or corruption without recovery
- inaccessible critical flow at the claimed support level
- unknown third-party provenance
- install, migration, or rollback failure
- documentation that promises behavior not demonstrated by tests

## 15. Definition of done

A feature is done only when:

- its user story and non-goals are explicit
- data, actors, scopes, side effects, and risk classes are modeled
- accessibility and failure behavior are specified
- unit, integration, permission, privacy, and relevant manual checks pass
- error messages give an understandable next step
- logs, fixtures, and artifacts contain no private data
- performance and offline behavior are understood
- documentation and migration notes match the implementation
- third-party provenance is complete
- the feature can be disabled or rolled back safely

Compilation alone is not completion. A phase completes only when its user-visible exit criteria are demonstrated.

## 16. Product success test

The first product milestone is one excellent vertical experience:

> A new user initializes HIKARI, chooses text or voice, grants access to one local document, receives a clear spoken and written explanation, asks a follow-up that preserves task context, and can inspect or remove any resulting memory—while every access and action remains visible and governed.

Long-term success means a parent, child, person using assistive technology, and developer can use the same product in the mode that works for them, remain in control of their data and actions, and continue working when models or providers change.

## 17. Immediate execution queue

1. ~~Merge this canonical plan into `develop` after document and metadata review.~~
2. ~~Produce the existing-branch intake ledger and classify every unmerged branch.~~
3. ~~Establish and record the verified `develop` baseline.~~
4. ~~Intake the smallest high-confidence safety/runtime fixes through separate branches.~~
5. ~~Publish the WP-001 provenance/component inventory without selecting a final project license.~~
6. ~~Execute the focused WP-001 remediation branches recorded in `docs/PROVENANCE_INVENTORY.md`.~~
7. ~~Execute the prioritized WP-003 gaps in `docs/CHARACTERIZATION_MATRIX.md` before architectural refactors.~~
8. ~~Establish WP-004 continuous checks for the full Python suite, privacy and protocol contracts, locked dependencies, and the frontend release gates.~~
9. ~~Define WP-005 canonical checkout/runtime naming and preserve legacy launcher compatibility.~~
10. ~~Design WP-006 `HIKARI_HOME` around current runtime paths and a reversible migration dry run.~~
11. ~~Define WP-007 action-policy contracts before routing additional tools or integrations.~~

Future work follows this queue unless a verified security or data-loss issue requires immediate priority.
