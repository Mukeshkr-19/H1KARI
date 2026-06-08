# H1KARI Quick Start

This guide matches the current repo layout as of May 19, 2026.

## 1. Go To The Repo

```bash
cd path/to/H1KARI
```

## 2. Use Python 3.12

The project should use Python 3.12. Avoid creating the venv with Python 3.14.

```bash
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip wheel setuptools
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
bash scripts/install-hikari-cli.sh
```

## 3. Configure API Keys

```bash
cp .env.example your-local-env-file
```

Edit your local environment file and add at least one key:

```text
GOOGLE_AI_STUDIO_KEY=your-key-here
GROQ_API_KEY=your-key-here
```

Keep real keys local. Never commit environment files with secrets.

## 4. Run HIKARI

After CLI install, this works from any terminal folder:

```bash
hikari --help
hikari --doctor
hikari --text
hikari --server --host 127.0.0.1 --port 9876
hikari --text --verbose
```

Repo-local commands still work:

```bash
# See all supported options
.venv/bin/python hikari.py --help

# Quick health/status check
.venv/bin/python hikari.py --doctor

# Full pre-push health check
.venv/bin/python hikari.py --doctor-full

# Same checks through the helper script
bash scripts/doctor.sh
bash scripts/doctor.sh --full

# Install or remove global hikari/Hikari shell commands
bash scripts/install-hikari-cli.sh
bash scripts/uninstall-hikari-cli.sh

# Text mode, safest first test
.venv/bin/python hikari.py --text

# Server mode for phone/browser connection
.venv/bin/python hikari.py --server --host 127.0.0.1 --port 9876

# Simple always-listening daemon
.venv/bin/python hikari.py --daemon
```

There is currently no `hikari.py --voice` option. Use `--daemon` for the simple voice service, or use `services/hikari_daemon.py` for the speaker-locked daemon.
Normal text mode hides internal startup and routing logs. Add `--verbose` only when debugging.

## 5. Speaker-Locked Voice Daemon

```bash
.venv/bin/python services/hikari_daemon.py --enroll-voice
.venv/bin/python services/hikari_daemon.py
```

Speaker enrollment stores local voice-auth data under ignored runtime paths. Do not push it.

## 6. Phone Connection

Start server mode, then open:

```text
http://127.0.0.1:9876/connect
http://127.0.0.1:9876/qr
http://127.0.0.1:9876/api/status
```

For another device on the same WiFi, replace `127.0.0.1` with the Mac's LAN IP.

## 7. Frontend Check

```bash
cd path/to/H1KARI/hikari-frontend
npm run lint
npm run build
```

## 8. Health Check Before Work

```bash
cd path/to/H1KARI
git status --short --branch
hikari --help
hikari --doctor
printf 'status\nexit\n' | .venv/bin/python hikari.py --text
.venv/bin/python -m pytest tests -q
```

Expected baseline:

- Git branch tracks `mukeshkr19/main`.
- CLI help works.
- Doctor quick check works.
- Text `status` works.
- Neural memory connects when run outside a restricted sandbox.
- Tests pass.
- Frontend lint/build pass.

## 9. Private Data Rule

Public repo is source code only. Private runtime state lives in a sibling private data directory (not committed).

Live brain: neural SQLite under `<private-data-directory>/live-brain/` (not committed).

Optional compatibility symlink: live-brain directory on your machine pointing at the private live-brain tree.

## 10. Start Here Tomorrow

1. Run the health check.
2. Fix docs or command drift before feature work.
3. Improve one layer at a time: CLI, server, UI, voice, neural memory.
4. Back up the brain before any memory cleanup.
