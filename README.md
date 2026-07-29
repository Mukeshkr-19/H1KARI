<p align="center">
  <img src="hikari-frontend/public/icon-512.png" width="152" alt="H1KARI project icon">
</p>

<h1 align="center">H1KARI</h1>

<p align="center">
  <strong>A local-first personal AI assistant for macOS.</strong><br>
  Reviewed memory, owner-verified voice, bounded actions, and an optional command-center UI.
</p>

<p align="center">
  <a href="https://github.com/Mukeshkr-19/H1KARI/actions/workflows/ci.yml"><img alt="Continuous checks" src="https://github.com/Mukeshkr-19/H1KARI/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Release 2.0.0" src="https://img.shields.io/badge/release-2.0.0-7C3AED">
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="macOS arm64" src="https://img.shields.io/badge/macOS-arm64-111111?logo=apple&logoColor=white">
  <img alt="Local-first privacy" src="https://img.shields.io/badge/privacy-local--first-0F766E">
  <a href="#license-and-redistribution"><img alt="License information" src="https://img.shields.io/badge/license-see%20terms-F59E0B"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#what-h1kari-can-do">Capabilities</a> ·
  <a href="#voice-companion">Voice</a> ·
  <a href="#brain-v2-memory">Brain v2</a> ·
  <a href="#privacy-and-security">Privacy</a> ·
  <a href="docs/ARCHITECTURE.md">Architecture</a> ·
  <a href="docs/QUICKSTART.md">Full guide</a>
</p>

---

H1KARI is the public project identity; the assistant and command-line interface
answer to **HIKARI** / `hikari`. The public repository contains the application,
tests, documentation, scripts, protocol, and optional Next.js frontend. API
keys, conversations, Brain databases, voice enrollment, and other private
runtime state remain outside the repository.

> A transcript is context, not accepted truth. Personal facts become durable
> only through Brain v2 policy, provenance, and an eligible owner remember/save
> request.

## What H1KARI can do

| Area | Current capability |
| --- | --- |
| **Conversation** | Local text CLI, persistent private sessions, loopback server, and provider routing |
| **Brain v2** | Explicit durable memory, reviewed authority, family/profile recall, conflict handling, consolidation, and supplemental graph/episodic/wiki context |
| **Voice** | Always-listening wake detection, owner speaker verification, wake/sleep/goodbye gating, local transcription options, speech interruption, and local TTS |
| **Actions and tasks** | Policy checks, approval, audit, reminders, scheduled jobs, quiet hours, delivery lifecycle, and bounded execution |
| **Phase 5 safety** | Teach Me, Guide My Hands, Care, child mode, trusted-helper grants, strict sessions, protocol frames, and accessible UI state |
| **Phase 6 engineering** | Bounded reason/act/observe/correct loop, repository intelligence, remote-result evidence, skill review contracts, command center, and disabled-by-default adapters |
| **Vision and handoff** | OCR, camera/file capture contracts, uncertainty handling, secure pairing, and cross-device handoff |
| **Frontend** | Optional Next.js command center with protocol validation, status, approvals, and responsive accessible controls |

### Release scope

The safe Phase 1–6 engineering baseline is implemented and covered by automated
tests. External deployments remain intentionally opt-in: Home Assistant,
encrypted sync, remote workers, reviewed skill installation, and measured model
routing stay disabled until configured and accepted in their real environment.
Live full-duplex voice also depends on microphone hardware, platform acoustic
echo cancellation, playback-stop signaling, and device-specific validation.
These boundaries prevent an optional integration from silently gaining memory,
task, tool, or owner authority.

## Architecture at a glance

```text
CLI / Voice / Web UI / Paired device
                  │
        protocol + identity boundary
                  │
             orchestrator
       ┌──────────┼──────────┐
       │          │          │
   Brain v2   action policy  task ledger
       │       approval/audit     │
       └──────────┼───────────────┘
                  │
       bounded, injected adapters
```

Model, planner, remote-worker, skill, and UI output is treated as data—not
authorization. See the [architecture](docs/ARCHITECTURE.md) and
[threat model](docs/THREAT_MODEL.md) for the full trust boundary.

## Quick start

### 1. Clone and prepare macOS

The reproducible release target is **macOS arm64 with Python 3.12**. PortAudio
is required by the supported audio dependency set.

```bash
git clone https://github.com/Mukeshkr-19/H1KARI.git
cd H1KARI
brew install python@3.12 portaudio
PYTHON312="$(command -v python3.12)"
test -n "$PYTHON312" || { echo "Python 3.12 was not found" >&2; exit 1; }
"$PYTHON312" -m venv .venv
.venv/bin/python -m pip install --upgrade pip wheel setuptools
.venv/bin/python -m pip install -r requirements-macos-arm64-py312.lock
cp .env.example .env
bash scripts/install-hikari-cli.sh
```

Other platforms can use `requirements.txt` after installing their platform's
PortAudio package, but they are not represented by the reproducible macOS lock.

### 2. Configure one model route

Edit the ignored local environment file and configure at least one route. Supported
configuration groups include local OmniRoute/9Router gateways and optional
Google, Groq, OpenRouter, Cerebras, NVIDIA, and Cohere providers. Exact model
availability, quotas, retention, and terms belong to each provider and may
change independently of H1KARI.

For a local Ollama-compatible path or hosted-provider details, start with
[provider provenance](docs/PROVIDER_PROVENANCE.md) and
[local router gateways](docs/LOCAL_ROUTER_GATEWAYS.md). Never commit credentials.

### 3. Start HIKARI

```bash
hikari --help
hikari --doctor
hikari --text
```

Useful session and server commands:

```bash
hikari --new
hikari --sessions
hikari --server --host 127.0.0.1 --port 9876
```

Repo-local equivalents are always available:

```bash
.venv/bin/python hikari.py --help
.venv/bin/python hikari.py --doctor
.venv/bin/python hikari.py --text
.venv/bin/python hikari.py --server --host 127.0.0.1 --port 9876
```

`hikari --text` resumes the latest local-owner chat. Use `hikari --new` for a
clean session. Conversations remain private runtime data and do not
automatically become accepted Brain memory.

For contributor/test dependencies:

```bash
.venv/bin/python -m pip install -r requirements-dev-macos-arm64-py312.lock
```

## Voice companion

Enroll the owner once, then start the daemon:

```bash
hikari --enroll-voice
hikari --daemon
```

While sleeping, HIKARI listens for wake evidence but ordinary room speech does
not reach the orchestrator. Say **HIKARI** to wake it. After activation, say
`bye` or `goodbye`, or wait for the inactivity timeout, to return to wake-only
sleep. `stop` and reviewed wake-prefixed stop phrases interrupt supported
playback. Unknown or unverified speakers remain guests and cannot read owner
memory.

The default local speech path uses macOS `say`; Pocket TTS is an optional local
backend. Real-device behavior depends on microphone permission, capture backend,
room acoustics, and speaker enrollment. Follow the
[voice guide](docs/VOICE_COMPANION.md) and
[real-device checklist](docs/VOICE_REAL_DEVICE_ACCEPTANCE.md).

## Brain v2 memory

Examples:

```text
Remember this: my favorite color is amber.
Remember this in my brain.
Save this as a memory: my sister's name is Madhumitha.
```

Eligible owner facts are normalized, source-linked, and stored through Brain v2
policy. Direct family-name recall returns the reviewed name without adding a
sentence wrapper. Casual statements remain episode evidence unless policy
explicitly promotes them. Pending, rejected, retired, and superseded records
cannot answer as accepted facts, and guest sessions cannot write into or read
the owner's Brain.

Read [Brain v2](docs/BRAIN_V2.md), the
[memory repair guide](docs/MEMORY_REPAIR.md), and the
[retrieval integration design](docs/BRAIN_RETRIEVAL_CONTEXT_INTEGRATION.md).

## Optional frontend

```bash
cd hikari-frontend
npm ci
npm run lint
npm run build
```

Start the Python server separately, then use the frontend workflow described in
the [quick-start guide](docs/QUICKSTART.md). Browser clients never get to assert
their own owner identity; pairing and server-derived actor context remain the
authority boundary.

## Repository layout

```text
H1KARI/
├── agents/             Assistant-facing agent adapters
├── bin/                Launchers
├── core/               Brain, orchestration, voice, policy, tasks, Phase 1–6
├── docs/               Architecture, operation, security, and acceptance docs
├── hikari-frontend/    Optional Next.js command center
├── native/             Reviewed native platform components
├── protocol/           Versioned wire-protocol schema
├── scripts/            Install, doctor, privacy, and release helpers
├── security/           Authentication and security helpers
├── services/           Daemon and tray entry points
├── skills/             Built-in reviewed skills
├── tests/              Python test suite
└── hikari.py           Main CLI/server entry point
```

## Privacy and security

Never add these to Git:

- API keys or populated environment files
- conversation logs or session databases
- Brain v2/neural SQLite stores or live-brain directories
- voice enrollment, speaker embeddings, or recordings
- private operating notes, backups, or runtime caches

Runtime data belongs under the private `HIKARI_HOME` boundary. Public CI checks
for secrets, private paths, protocol drift, dependency drift, and tracked
runtime artifacts. Security reports must use GitHub's private advisory flow;
see [SECURITY.md](SECURITY.md). Provider egress and disable paths are documented
in [PROVIDER_PROVENANCE.md](docs/PROVIDER_PROVENANCE.md).

## Verification

Basic health check:

```bash
hikari --help
hikari --doctor
printf 'status\nexit\n' | .venv/bin/python hikari.py --text
```

Full contributor gate:

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python hikari.py --doctor-full
cd hikari-frontend
npm run lint
npm run build
```

The public `main` and `develop` branches run the same macOS Python, privacy,
protocol, frontend audit, lint, unit, and production-build workflow. The current
`main` workflow is visible in [GitHub Actions](https://github.com/Mukeshkr-19/H1KARI/actions/workflows/ci.yml).

## Documentation

| Guide | Purpose |
| --- | --- |
| [Quick start](docs/QUICKSTART.md) | Detailed installation, configuration, voice, and first-run flow |
| [Architecture](docs/ARCHITECTURE.md) | Components, data flow, and operating model |
| [Brain v2](docs/BRAIN_V2.md) | Memory authority, remember/save, review, retrieval, and repair |
| [Voice companion](docs/VOICE_COMPANION.md) | Wake, speaker verification, sleep, interruption, and TTS |
| [Phase 5 manual guide](docs/PHASE_5_MANUAL_TEST_GUIDE.md) | Teach/Guide/Care/child/helper acceptance steps |
| [Phase 6 completion](docs/PHASE_6_COMPLETION.md) | Bounded-agent, command-center, and adapter evidence |
| [Provider provenance](docs/PROVIDER_PROVENANCE.md) | Provider data flow, retention, credentials, and disable paths |
| [Security](SECURITY.md) | Supported branches, reporting, and security boundaries |
| [Contributing](CONTRIBUTING.md) | Change process and required checks |
| [Third-party notices](THIRD_PARTY_NOTICES.md) | Dependencies, models, assets, and adapted-source obligations |

## License and redistribution

The repository is source-visible but does not yet grant a project-wide
open-source license. Third-party and adapted portions retain their own terms;
in particular, the adapted task-planner notice includes non-commercial limits.
Do not redistribute, package, or use H1KARI commercially until the repository
owner selects a compatible project license and the distribution notice bundle
is complete. See [GOVERNANCE.md](GOVERNANCE.md) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Contributing

Focused contributions are welcome through `develop`. Preserve local-first
privacy, owner-memory isolation, approval/audit boundaries, and provenance.
Run the applicable checks above before opening a pull request, and use synthetic
data in tests and documentation. See [CONTRIBUTING.md](CONTRIBUTING.md) and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

---

<p align="center">
  <strong>H1KARI keeps memory reviewed, identity server-derived, and actions bounded.</strong>
</p>
