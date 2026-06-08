# H1KARI - Agent Context

## Project Overview

H1KARI is a local-first personal AI assistant with Python backend services, multi-agent routing, neural memory, voice services, and an optional Next.js frontend. The runtime assistant and CLI remain compatible with HIKARI / `hikari`.

## Current Paths

- Repo root: your local clone of this repository
- Private local data: sibling private data directory (not committed)
- Live brain: private neural SQLite under the live-brain tree (not committed)
- Brain symlink: optional live brain directory symlink on your machine (not committed)
- GitHub remote to use: `h1kari` -> `https://github.com/Mukeshkr-19/H1KARI.git`

## Public Repo Structure

- `hikari.py` - main CLI/server entrypoint
- `agents/` - agent implementations: code, files, memory, research, system, voice
- `core/` - orchestrator, server, router, memory, voice, Mac/browser integrations
- `core/neural_memory/` - SQLite neural-memory subsystem
- `security/` - codename/auth helpers
- `services/` - daemon/tray/always-on service entrypoints
- `skills/` - built-in skill registry and skills
- `hikari-frontend/` - optional Next.js frontend
- `scripts/` and `bin/` - helper scripts and launchers
- `tests/` - pytest suite
- `docs/` - public docs

## Private Data Rules

Never commit or stage:

- local environment files with real API keys
- `data/`
- `logs/`
- live brain directories and symlinks
- private data directories
- `.venv/`
- `.idea/`
- `*.db`, `*.sqlite`, `*.sqlite3`
- voice auth, voice prints, private operating guide, private roadmap, recovery ledger, or work history

## Current Working Commands

Run from repo root:

```bash
.venv/bin/python hikari.py --help
printf 'status\nexit\n' | .venv/bin/python hikari.py --text
.venv/bin/python hikari.py --server --host 127.0.0.1 --port 9876
.venv/bin/python -m pytest tests -q
```

Frontend:

```bash
cd hikari-frontend
npm run lint
npm run build
```

Voice services:

```bash
.venv/bin/python hikari.py --daemon
.venv/bin/python services/hikari_daemon.py --enroll-voice
.venv/bin/python services/hikari_daemon.py
```

## Work Rules

- Verify before claiming fixed.
- Keep changes focused.
- Treat docs as executable promises: if a command is listed, it should work.
- Back up the live brain before any memory cleanup.
- Do not force-push or rewrite history unless the project owner explicitly approves it.
- Use `h1kari/main` as the public delivery branch.
