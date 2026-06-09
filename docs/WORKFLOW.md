# H1KARI Contributor Workflow

Public repo: [Mukeshkr-19/H1KARI](https://github.com/Mukeshkr-19/H1KARI)

Runtime CLI compatibility: `hikari`, `Hikari`, `hikari.py`.

## Tool roles

| Tool | Role | Git commit/push |
|---|---|---|
| Cursor | Implementation, focused tests | No |
| Agy | Behavioral QA, scripted terminal chat | No |
| Gemini CLI | Architecture and privacy audit | No |
| Hermes (reference) | Pattern comparison reports | No |
| Codex/sia | Final review, branch, gates, commit, push | **Yes (only)** |

Living feature tracker (local, not always in repo):

`../future-integrations/HIKARI_FEATURE_SOURCES_TRACKER.md`

## Branch discipline

- Never commit directly to `main`.
- One narrow slice per branch, for example:
  - `brain/<topic>`
  - `docs/<topic>`
  - `infra/<topic>`
  - `voice/<topic>`
- Codex creates the branch, runs gates, commits, pushes, then merges when green.

## Required gates before merge

From repo root with project venv:

```bash
git diff --check
.venv/bin/python -m pytest tests -q
.venv/bin/python hikari.py --doctor
.venv/bin/python hikari.py --brain-v2-eval
.venv/bin/python scripts/brain_live_qa.py   # brain/memory behavior changes
.venv/bin/python tests/privacy_scan.py
```

## Privacy rules

- No real owner names, cities, schools, or emails in tracked files.
- Use fake fixtures: Owner A, Person B, City A, School A, Topic A, Guest B.
- Never commit: `.env*`, `*.db`, live-brain paths, private data trees, API keys.

## Task scheduling env vars

- `HIKARI_TASKS_DB` — override task SQLite path (tests use isolated temp files).
- `HIKARI_ENABLE_TASK_SCHEDULER=1` — opt in to macOS Reminders via osascript.
- `HIKARI_DISABLE_OSASCRIPT=1` — disable macOS osascript side effects (tests/live QA).

## Known non-blocking test noise

Pytest currently reports dependency deprecation warnings from the voice/browser stack
(`aifc`, `audioop`, and `websockets.legacy`). These do not affect the Brain v2 or
task-boundary gates, but they should be handled before a Python 3.13 runtime move.

## Definition of done

- Scope is narrow and named.
- All gates pass locally.
- Handoff report lists files changed, verification output, and risks.
- Only Codex performs `git commit` and `git push`.
