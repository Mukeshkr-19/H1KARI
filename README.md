<p align="center">
  <img src="hikari-frontend/public/icon-512.png" width="156" alt="H1KARI logo">
</p>

<h1 align="center">H1KARI</h1>

<p align="center">
  <strong>Local-first personal AI for macOS.</strong><br>
  Reviewed memory, owner-verified voice, bounded actions, and an optional web interface.
</p>

<p align="center">
  <a href="https://github.com/Mukeshkr-19/H1KARI/actions/workflows/ci.yml"><img alt="Continuous checks" src="https://github.com/Mukeshkr-19/H1KARI/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="macOS arm64 verified" src="https://img.shields.io/badge/macOS-arm64-111111?logo=apple&logoColor=white">
  <img alt="Privacy local first" src="https://img.shields.io/badge/privacy-local--first-7C3AED">
  <img alt="License pending" src="https://img.shields.io/badge/license-pending-F59E0B">
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#highlights">Highlights</a> ·
  <a href="#voice-companion">Voice</a> ·
  <a href="#brain-v2-memory">Brain v2</a> ·
  <a href="#privacy-and-security">Privacy</a> ·
  <a href="docs/ARCHITECTURE.md">Architecture</a> ·
  <a href="SECURITY.md">Security</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

---

H1KARI is the public project identity for this repository. The runtime assistant
and CLI answer to **HIKARI** / `hikari`. The public tree contains source, tests,
documentation, scripts, and an optional Next.js frontend. Private runtime state
is excluded by repository policy, ignore rules, and CI privacy checks.

---

## Highlights

| Area | What you get |
| --- | --- |
| 🏠 **Local-first** | Text CLI, loopback HTTP server, local runtime data, and optional hosted model routes |
| **Brain v2** | Durable personal facts only after an explicit remember/save; guest sessions stay isolated |
| 🎙️ **Voice** | Wake-word daemon, owner speaker verification, local STT support, interruption, and wake-only sleep |
| ✅ **Bounded actions** | Approval, audit, task, helper, child-mode, and execution boundaries |
| 🧠 **Context** | Graph, episodic, and wiki context remain supplemental to accepted memories |
| 🔐 **Privacy** | Secrets, conversation databases, enrollment artifacts, and live Brain data stay outside the public tree |

> A chat transcript is context, not accepted truth. Durable personal memory
> requires an explicit remember/save request and passes Brain v2 policy.

### Project status

| Component | Status | Notes |
| --- | --- | --- |
| CLI and local server | Available | Python 3.12; loopback-first defaults |
| Brain v2 | Available | Reviewed, source-linked durable memory |
| Voice daemon | Available | Requires microphone access and owner enrollment |
| Frontend | Optional | Next.js interface with separate lint/build gates |
| Hosted providers | Optional | Account access, quotas, model availability, and provider terms apply |
| Packaging/license | Pending | Source tree is public; no project-wide license has been selected |

---

## Quick start

Python **3.12**. On macOS arm64, install PortAudio and the verified lockfile:

```bash
cd path/to/H1KARI
brew install python@3.12 portaudio
PYTHON312="$(command -v python3.12)"
"$PYTHON312" -m venv .venv
.venv/bin/python -m pip install --upgrade pip wheel setuptools
.venv/bin/python -m pip install -r requirements-macos-arm64-py312.lock
cp .env.example .env
bash scripts/install-hikari-cli.sh
```

Copy the example environment file, then configure either a local Ollama model or
an optional hosted provider such as Google or Groq. Hosted keys belong only in
the ignored local environment file. Never commit credentials. See
[provider provenance](docs/PROVIDER_PROVENANCE.md) before sending sensitive text.

### Commands

```bash
hikari --help
hikari --doctor
hikari --text
hikari --new
hikari --sessions
hikari --server --host 127.0.0.1 --port 9876
```

Repo-local equivalents:

```bash
.venv/bin/python hikari.py --help
.venv/bin/python hikari.py --doctor
.venv/bin/python hikari.py --text
.venv/bin/python hikari.py --server --host 127.0.0.1 --port 9876
.venv/bin/python hikari.py --enroll-voice
.venv/bin/python hikari.py --daemon
```

`hikari --text` resumes the latest local-owner chat by default. Use `hikari --new` for a clean text session. The voice daemon starts its own conversation session so voice turns do not inherit an unrelated text thread; Brain v2 remains the authority for personal facts.

For contributor and test dependencies, install the developer lock separately:

```bash
.venv/bin/python -m pip install -r requirements-dev-macos-arm64-py312.lock
```

### npm shortcuts

```bash
npm start          # text mode
npm run voice      # hikari.py --daemon
npm run daemon     # services/hikari_daemon.py
npm run enroll     # voice enrollment
npm run server     # local server
```

---

## Voice companion

```bash
.venv/bin/python hikari.py --enroll-voice
.venv/bin/python hikari.py --daemon
```

When sleeping, HIKARI listens for the wake word but does not send ordinary room
speech to the orchestrator. After activation, `bye`, `goodbye`, or an inactivity
timeout returns it to wake-only sleep. `stop` interrupts supported playback.
Behavior depends on microphone permissions, the configured capture backend, and
real-device acoustic conditions. See the [voice guide](docs/VOICE_COMPANION.md)
and [real-device acceptance checklist](docs/VOICE_REAL_DEVICE_ACCEPTANCE.md).

---

## Brain v2 memory

- “Remember this: my favorite color is amber.” saves an eligible owner fact.
- “Remember this in my brain” can save the immediately preceding eligible fact.
- Direct family-name questions return only the reviewed saved name.
- Casual statements remain episode evidence rather than silently becoming truth.
- Pending, rejected, retired, and superseded records cannot answer as accepted facts.

Read [Brain v2](docs/BRAIN_V2.md), the
[memory repair guide](docs/MEMORY_REPAIR.md), and the
[retrieval context design](docs/BRAIN_RETRIEVAL_CONTEXT_INTEGRATION.md).

---

## Repository layout

```text
H1KARI/
├── agents/             Agent implementations
├── bin/                Launchers
├── core/               Orchestrator, Brain v2, voice, router, policy
├── docs/               Public project docs
├── hikari-frontend/    Optional Next.js frontend
├── scripts/            Install and doctor helpers
├── security/           Auth helpers
├── services/           Voice daemon and tray entrypoints
├── skills/             Built-in skills
├── tests/              Pytest suite
├── hikari.py           Main CLI entrypoint
└── README.md
```

---

## Privacy and security

Keep outside Git (and never stage):

- local environment files and API keys
- conversation logs and session databases
- Brain v2 / neural SQLite stores and live-brain trees
- voice enrollment and speaker-auth artifacts
- private operating notes

Brain v2 stores reviewed, source-linked memories separately from raw chat evidence and blocks legacy personal-memory fallback during normal chat when authority is enabled. Casual owner statements stay episode-only until you explicitly ask to remember them.

Security issues should follow the private reporting instructions in
[SECURITY.md](SECURITY.md). Provider behavior, retention, and disable paths are
documented in [PROVIDER_PROVENANCE.md](docs/PROVIDER_PROVENANCE.md).

---

## Docs

| Doc | Topic |
| --- | --- |
| [Quick start](docs/QUICKSTART.md) | Setup and first-run commands |
| [Architecture](docs/ARCHITECTURE.md) | Layout and operating model |
| [Voice companion](docs/VOICE_COMPANION.md) | Voice behavior and controls |
| [Brain v2](docs/BRAIN_V2.md) | Remember, review, retrieval, and authority model |
| [Provider provenance](docs/PROVIDER_PROVENANCE.md) | Hosted providers and data flows |
| [Local router gateways](docs/LOCAL_ROUTER_GATEWAYS.md) | Optional OmniRoute and 9Router integration |
| [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Governance](GOVERNANCE.md) | Project policy |
| [Third-party notices](THIRD_PARTY_NOTICES.md) | Dependency, model, asset, and adapted-code notices |

---

## Verify

```bash
hikari --help
hikari --doctor
printf 'status\nexit\n' | .venv/bin/python hikari.py --text
.venv/bin/python -m pytest tests -q
```

Full pre-push check:

```bash
.venv/bin/python hikari.py --doctor-full
```

Privacy scan before a public push:

```bash
.venv/bin/python -m pytest tests/test_privacy_terms.py -q
```

---

## Release and licensing

H1KARI is under active development. The repository does not currently declare a
project-wide open-source license. Review [GOVERNANCE.md](GOVERNANCE.md) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before redistribution,
packaging, or commercial use. A future license decision must remain compatible
with every included dependency, model, asset, and adapted source component.

Contributions are welcome through [CONTRIBUTING.md](CONTRIBUTING.md). Please run
the doctor, Python suite, frontend checks when applicable, and privacy scan
before opening a pull request.
