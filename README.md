# H1KARI

Local-first personal AI for macOS.

H1KARI is the public project identity for this repository. The runtime assistant and CLI still answer to **HIKARI** / `hikari`. The public tree holds code, tests, docs, scripts, and an optional Next.js frontend. Private runtime state stays outside Git.

---

## Highlights

| Area | What you get |
| --- | --- |
| **Local-first** | Text CLI, local HTTP server, always-on voice wake daemon |
| **Brain v2** | Durable personal facts only after an explicit remember/save; guest sessions stay isolated |
| **Voice** | Owner speaker verification; `stop` / `hikari stop` / goodbye return to wake-only sleep |
| **Tasks** | Productivity and documents stay separate from Brain memory |
| **Privacy** | Secrets, conversation DBs, enrollment artifacts, and live brain data are never committed |

A chat transcript is context, not accepted truth.

---

## Quick start

Python **3.12**. On macOS arm64, install PortAudio and the verified lockfile:

```bash
cd path/to/H1KARI
brew install python@3.12 portaudio
PYTHON312="$(command -v python3.12)"
"$PYTHON312" -m venv .venv
.venv/bin/python -m pip install --upgrade pip wheel setuptools
.venv/bin/python -m pip install -r requirements-dev-macos-arm64-py312.lock
cp .env.example .env
bash scripts/install-hikari-cli.sh
```

Copy the example environment file (command above), then edit the ignored local environment file with at least one provider key such as `GOOGLE_AI_STUDIO_KEY` or `GROQ_API_KEY`. Never commit credentials.

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

### npm shortcuts

```bash
npm start          # text mode
npm run voice      # hikari.py --daemon
npm run daemon     # services/hikari_daemon.py
npm run enroll     # voice enrollment
npm run server     # local server
```

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

## Privacy

Keep outside Git (and never stage):

- local environment files and API keys
- conversation logs and session databases
- Brain v2 / neural SQLite stores and live-brain trees
- voice enrollment and speaker-auth artifacts
- private operating notes

Brain v2 stores reviewed, source-linked memories separately from raw chat evidence and blocks legacy personal-memory fallback during normal chat when authority is enabled. Casual owner statements stay episode-only until you explicitly ask to remember them.

---

## Docs

| Doc | Topic |
| --- | --- |
| `docs/QUICKSTART.md` | Setup and first-run commands |
| `docs/ARCHITECTURE.md` | Layout and operating model |
| `docs/VOICE_COMPANION.md` | Voice behavior and controls |
| `docs/BRAIN_V2.md` | Brain v2 remember / review model |
| `docs/PROVIDER_PROVENANCE.md` | Hosted providers and data flows |
| `docs/LOCAL_ROUTER_GATEWAYS.md` | Optional OmniRoute / 9Router |
| `SECURITY.md` · `CONTRIBUTING.md` · `GOVERNANCE.md` | Project policy |

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
