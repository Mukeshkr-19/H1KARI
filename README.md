# H1KARI

H1KARI is a local-first personal AI assistant for macOS. The runtime assistant and CLI still answer to HIKARI / `hikari`; this repository uses H1KARI as the public project identity. The public repo holds code, tests, docs, scripts, and an optional Next.js frontend. Private runtime state stays outside Git.

## What it is

- **Local-first assistant** — text CLI, local HTTP server, and an always-on voice wake daemon.
- **Brain v2** — reviewed, source-linked personal memory is the authority for durable personal facts. Guest sessions stay isolated from owner memory.
- **Voice** — wake-word daemon with speaker verification; stop/goodbye phrases return to wake-only listening.
- **Tasks** — productivity and document workflows are separate from Brain memory. A chat transcript is context, not accepted truth.
- **Privacy** — secrets, conversation DBs, voice enrollment artifacts, and live brain data are not committed.

## Quick start

Use Python 3.12. On macOS arm64, install PortAudio and the verified lock:

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

Edit the ignored local `.env` with at least one provider key (for example `GOOGLE_AI_STUDIO_KEY` or `GROQ_API_KEY`). Never commit credentials.

### Run

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

`hikari --text` resumes the latest local-owner chat by default. Use `hikari --new` for a clean text session. The voice daemon starts its own conversation session so voice turns do not inherit an unrelated prior text thread; Brain v2 remains the authority for personal facts.

### npm shortcuts

```bash
npm start          # text mode
npm run voice      # hikari.py --daemon
npm run daemon     # services/hikari_daemon.py
npm run enroll     # voice enrollment
npm run server     # local server
```

## Layout

```text
H1KARI/
├── agents/             # Agent implementations
├── bin/                # Launchers
├── core/               # Orchestrator, Brain v2, voice, router, policy
├── docs/               # Public project docs
├── hikari-frontend/    # Optional Next.js frontend
├── scripts/            # Install and doctor helpers
├── security/           # Auth helpers
├── services/           # Voice daemon and tray entrypoints
├── skills/             # Built-in skills
├── tests/              # Pytest suite
├── hikari.py           # Main CLI entrypoint
└── README.md
```

## Privacy

Keep outside Git (and never stage):

- local `.env` and API keys
- conversation logs and session databases
- Brain v2 / neural SQLite stores and live-brain trees
- voice enrollment and speaker-auth artifacts
- private operating notes

Brain v2 stores reviewed, source-linked memories separately from raw chat evidence and blocks legacy personal-memory fallback during normal chat when authority is enabled.

## Docs

- `docs/QUICKSTART.md` — setup and first-run commands
- `docs/ARCHITECTURE.md` — layout and operating model
- `docs/VOICE_COMPANION.md` — voice behavior and controls
- `docs/PROVIDER_PROVENANCE.md` — hosted providers and data flows
- `docs/LOCAL_ROUTER_GATEWAYS.md` — optional OmniRoute / 9Router
- `SECURITY.md`, `CONTRIBUTING.md`, `GOVERNANCE.md`

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
