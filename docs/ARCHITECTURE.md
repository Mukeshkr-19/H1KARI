# H1KARI Architecture Notes

This file describes the current repository layout and operating model. It is public project-facing documentation; private recovery notes live in a private docs tree outside Git.

## Vision

H1KARI is a local-first personal AI assistant for macOS with:

- text and server modes through `hikari.py`
- voice/daemon services under `services/`
- multi-agent task routing under `agents/`
- core intelligence, memory, server, and integrations under `core/`
- optional phone/browser frontend under `hikari-frontend/`
- private live neural brain outside Git

## Current Architecture

```text
H1KARI/
├── agents/                  # Voice, research, files, system, code, memory agents
├── bin/                     # Launchers
├── core/
│   ├── orchestrator.py      # Central coordinator
│   ├── router.py            # Multi-provider AI routing
│   ├── server.py            # HTTP/WebSocket server: /api/status, /connect, /qr
│   ├── voice.py             # Speech I/O helpers
│   ├── memory.py            # JSON memory fallback
│   ├── brain_v2/            # Omi-inspired episode pipeline (see docs/BRAIN_V2.md)
│   ├── brain.py             # HikariBrain facade + layered context packet
│   ├── runtime_paths.py     # Private runtime paths; no local state in public repo
│   ├── neural_memory/       # SQLite graph memory subsystem
│   ├── neural_memory_bridge.py
│   ├── personality.py
│   ├── mac_control.py
│   └── smart_home.py
├── docs/                    # Public docs
├── hikari-frontend/         # Next.js frontend
├── scripts/                 # Login-agent helpers
├── security/                # Auth helpers
├── services/                # Daemon/tray/always-on entrypoints
├── skills/                  # Skill system
├── tests/                   # Pytest suite
├── hikari.py                # Main entrypoint
├── install.sh
├── package.json
├── requirements-dev.txt
└── requirements.txt
```

## Private Runtime Architecture

Private files are not part of the public repo:

```text
<private-data-directory>/
├── docs/
├── live-brain/
├── monthly-backups/
├── brain-backups/
├── legacy-data/
└── scripts/
```

The live brain is reached through a local live-brain directory (often a symlink) pointing at:

```text
<private-data-directory>/live-brain
```

## Current Commands

```bash
cd path/to/H1KARI

.venv/bin/python hikari.py --help
.venv/bin/python hikari.py --doctor
.venv/bin/python hikari.py --doctor-full
.venv/bin/python hikari.py --install-cli
.venv/bin/python hikari.py --uninstall-cli
.venv/bin/python hikari.py --text
.venv/bin/python hikari.py --server --host 127.0.0.1 --port 9876
.venv/bin/python hikari.py --daemon
.venv/bin/python hikari.py --tray
.venv/bin/python hikari.py --install
```

After CLI install, these global shell commands work from any directory:

```bash
hikari --doctor
Hikari --doctor
hikari --text --verbose
```

Speaker-locked daemon:

```bash
.venv/bin/python services/hikari_daemon.py --enroll-voice
.venv/bin/python services/hikari_daemon.py
```

Frontend:

```bash
cd hikari-frontend
npm run lint
npm run build
```

## Known Stable Baseline

- `pytest tests -q` passes.
- `hikari.py --help` and `hikari --help` work.
- `hikari.py --doctor` and `hikari --doctor` work.
- On a source-only H1KARI clone, doctor may warn about optional private data, Brain v2 episode DB, or frontend `node_modules`.
- text mode status works.
- server `/api/status` works.
- frontend lint/build passes when dependencies are installed.
- neural memory connects after private live-brain is configured.

## Important Caution

Do not move private brain/docs into Git. Do not clean neural-memory data without a backup first. Do not add commands to docs unless they have been verified.
