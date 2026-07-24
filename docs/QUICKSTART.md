# H1KARI Quick Start

This guide matches the current repo layout as of May 19, 2026.

## 1. Go To The Repo

```bash
cd path/to/H1KARI
```

## 2. Install PortAudio And Use Python 3.12

The macOS arm64 lock is verified only with Python 3.12. PyAudio requires the
native PortAudio library, so install both prerequisites with Homebrew. Discover
the interpreter through `PATH` instead of assuming a fixed installation path.

```bash
brew install python@3.12 portaudio
PYTHON312="$(command -v python3.12)"
if [ -z "$PYTHON312" ]; then echo "Python 3.12 was not found on PATH." >&2; exit 1; fi
"$PYTHON312" --version
"$PYTHON312" -m venv .venv
.venv/bin/python -m pip install --upgrade pip wheel setuptools
.venv/bin/python -m pip install -r requirements-dev-macos-arm64-py312.lock
bash scripts/install-hikari-cli.sh
```

Confirm that the discovered interpreter reports Python 3.12 before creating the
environment. The repository `install.sh` script similarly discovers `python3`
from `PATH` and uses the verified lock only for Python 3.12 on macOS arm64.
Other platforms must install PortAudio through their package manager, then use
`requirements.txt` plus `requirements-dev.txt` until their own clean-environment
lock is published.

## 3. Configure API Keys

```bash
cp .env.example .env
```

Edit the ignored local `.env` file and add at least one key. Runtime
`load_dotenv()` calls load this filename automatically:

```text
GOOGLE_AI_STUDIO_KEY=your-key-here
GROQ_API_KEY=your-key-here
```

This file is ignored by Git. Keep it local and never commit credentials.

## 4. Run HIKARI

After CLI install, this works from any terminal folder:

```bash
hikari --help
hikari --doctor
hikari --text
hikari --new
hikari --sessions
hikari --session chat_0123456789abcdef01234567
hikari --server --host 127.0.0.1 --port 9876
hikari --text --verbose
```

`hikari` resumes the latest active local-owner conversation by default.
Use `hikari --new` for a clean chat, `hikari --sessions` to list saved chats,
or `hikari --session SESSION_ID` to resume one explicitly. Text and foreground
voice use the same selected session. Chat transcripts are private runtime data,
never repository files, and do not become Brain v2 authority automatically.

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

# Owner-locked wake-word daemon
.venv/bin/python hikari.py --enroll-voice
.venv/bin/python hikari.py --daemon
```

`hikari.py --voice` provides explicit foreground voice input. `--daemon` waits
for the wake word `HIKARI` and refuses to start until the local owner voice is
enrolled. Enrollment stores only a private local speaker embedding; raw
enrollment audio is discarded.
Normal text mode hides internal startup and routing logs. Add `--verbose` only when debugging.

## 5. Speaker-Locked Voice Daemon

```bash
.venv/bin/python hikari.py --enroll-voice
.venv/bin/python hikari.py --daemon
```

Speaker enrollment stores local voice-auth data under ignored runtime paths. Do not push it.

The daemon uses the local US English Samantha voice at a comfortable 185 words per minute
by default, with no model-generation delay.
You can choose any bounded rate from 120 through 220 in the ignored private
environment file:

```text
HIKARI_TTS_RATE=185
```

macOS `say` remains the zero-download fallback. For the optional free, local
neural voice, install the isolated voice extra once and select Pocket TTS:

```bash
.venv/bin/python -m pip install -r requirements-voice-local.txt
```

```text
HIKARI_TTS_BACKEND=pocket-tts
HIKARI_TTS_VOICE=alba
```

Pocket TTS loads lazily on the first spoken response and may download its model
weights on that first use. Model weights stay in the user's runtime cache and
are not repository files. If Pocket TTS is unavailable, the daemon falls back
to local macOS speech. Owner speaker enrollment is authentication data; it is
never reused as an assistant voice-cloning sample.

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

- Git status is clean (or only your intentional local edits).
- CLI help works (`hikari --help`).
- Doctor quick check works (`hikari --doctor`).
- On a source-only H1KARI clone, doctor may warn about optional private data, brain symlink, Brain v2 episode DB (before first chat), and frontend `node_modules` — that is normal.
- Text `status` works.
- Tests pass.
- Frontend lint/build pass when `hikari-frontend/node_modules` is installed.

## 9. Private Data Rule

Public repo is source code only. Private runtime state lives in a sibling private data directory (not committed).

Live brain: neural SQLite under `<private-data-directory>/live-brain/` (not committed).

Optional compatibility symlink: live-brain directory on your machine pointing at the private live-brain tree.

## 10. Start Here Tomorrow

1. Run the health check.
2. Fix docs or command drift before feature work.
3. Improve one layer at a time: CLI, server, UI, voice, neural memory.
4. Back up the brain before any memory cleanup.
