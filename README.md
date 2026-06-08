# H1KARI v2.0 - Personal AI Assistant

H1KARI is a local-first personal AI assistant for macOS. The assistant and CLI still answer to HIKARI / `hikari`; the repo uses H1KARI as its clean public project identity. The public repo contains code, tests, docs, scripts, and the optional Next.js frontend. Private runtime state lives outside Git in a sibling private data directory (not committed).

## What Works Now

- Python CLI entrypoint: `hikari.py`
- Text mode: `python hikari.py --text`
- Server mode: `python hikari.py --server --host 127.0.0.1 --port 9876`
- HTTP routes: `/api/status`, `/connect`, `/qr`
- Multi-agent routing: voice, research, files, system, code, memory
- Neural memory bridge connected through the live brain directory (not committed)
- Next.js frontend builds and lints
- Tests pass with Python 3.12

## Public Repo Layout

```text
H1KARI/
├── agents/             # Agent implementations
├── bin/                # Launchers, including bin/Hikari
├── config/             # Provider configuration
├── core/               # Orchestrator, server, memory, voice, integrations
├── docs/               # Public project docs
├── hikari-frontend/    # Optional Next.js frontend
├── scripts/            # Install/uninstall helper scripts
├── security/           # Authentication helpers
├── services/           # Daemon/tray/always-on service entrypoints
├── skills/             # Built-in skill system
├── tests/              # Pytest suite
├── .env.example        # Placeholder environment template
├── .gitignore
├── AGENTS.md           # Agent-facing repo context
├── README.md
├── hikari.py           # Main CLI/server entrypoint
├── install.sh
├── package.json        # npm shortcuts for Python commands
├── requirements-dev.txt
└── requirements.txt
```

## Public Docs

- `docs/QUICKSTART.md` - setup and first-run commands.
- `docs/ARCHITECTURE.md` - current repo layout, commands, and operating model.
- `docs/NEURAL_MEMORY_ACCEPTANCE.md` - neural memory acceptance criteria.

## Private Local Layout

These are intentionally not in GitHub:

```text
<private-data-directory>/
├── docs/               # Private operating guide, roadmap, recovery notes
├── live-brain/         # Live SQLite neural brain
├── monthly-backups/    # Scheduled brain backups
├── brain-backups/      # Older/manual backups
├── legacy-data/        # Legacy local runtime data
└── scripts/            # Private backup scripts
```

Compatibility brain path (local machine only):

```text
<live-brain-directory> -> <private-data-directory>/live-brain
```

## Setup

Use Python 3.12. Python 3.14 has caused native dependency install failures for this project.

```bash
cd path/to/H1KARI
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip wheel setuptools
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
cp .env.example local-environment-file
bash scripts/install-hikari-cli.sh
```

Edit your local environment file (from `.env.example`) and add at least one provider key, for example `GOOGLE_AI_STUDIO_KEY` or `GROQ_API_KEY`.

## Run

After CLI install, `hikari` and `Hikari` work from any terminal folder:

```bash
hikari --help
hikari --doctor
hikari --text
hikari --server --host 127.0.0.1 --port 9876
hikari --text --verbose
```

Repo-local commands still work too:

```bash
cd path/to/H1KARI

# CLI help
.venv/bin/python hikari.py --help

# Quick health/status check
.venv/bin/python hikari.py --doctor

# Full pre-push health check
.venv/bin/python hikari.py --doctor-full

# Text mode
.venv/bin/python hikari.py --text

# Server mode
.venv/bin/python hikari.py --server --host 127.0.0.1 --port 9876

# Simple always-listening daemon
.venv/bin/python hikari.py --daemon

# Speaker-locked daemon enrollment and run
.venv/bin/python services/hikari_daemon.py --enroll-voice
.venv/bin/python services/hikari_daemon.py
```

CLI install/uninstall:

```bash
bash scripts/install-hikari-cli.sh
bash scripts/uninstall-hikari-cli.sh
# or
.venv/bin/python hikari.py --install-cli
.venv/bin/python hikari.py --uninstall-cli
```

Phone/server URLs when server mode is running:

```text
http://127.0.0.1:9876/api/status
http://127.0.0.1:9876/connect
http://127.0.0.1:9876/qr
```

## Frontend

```bash
cd path/to/H1KARI/hikari-frontend
npm run lint
npm run build
```

The frontend must not depend on remote Google Fonts during build. Keep fonts local or use system fonts.

## Verification Before Push

```bash
cd path/to/H1KARI

git status --short --branch
hikari --help
hikari --doctor
printf 'status\nexit\n' | .venv/bin/python hikari.py --text
.venv/bin/python -m pytest tests -q
cd hikari-frontend && npm run lint && npm run build
```

Full doctor/status check:

```bash
.venv/bin/python hikari.py --doctor-full
# or
npm run doctor:full
# or
bash scripts/doctor.sh --full
```

Quick doctor checks repo layout, Git cleanliness, Python version, private brain paths,
public Git privacy, duplicate tracked content, and frontend dependency presence.
Full doctor additionally runs CLI help, text status, Python tests, frontend lint,
and frontend build.

Normal CLI chat is quiet by default. Use `--verbose` when you want internal
initialization, routing, scheduler, memory, and provider logs.

Private-file scan before any public push:

```bash
.venv/bin/python -m pytest tests/test_privacy_terms.py -q
```

That test must pass (zero denylist hits in tracked and untracked public source).

## Never Push

- local environment files with real API keys
- `data/`
- `logs/`
- live brain directories and symlinks
- private data directories
- `.venv/`
- `.idea/`
- `*.db`, `*.sqlite`, `*.sqlite3`
- voice auth, voice prints, raw runtime memory, private roadmap, recovery ledger, or operating notes

## Current Near-Term Priorities

1. Keep the current baseline stable.
2. Keep public code in GitHub and private memory/docs outside GitHub.
3. Make voice mode reliable and easier to test.
4. Improve neural memory quality with backup-first cleanup tools.
5. Add Obsidian as an export/readable layer, not as the source of truth.
