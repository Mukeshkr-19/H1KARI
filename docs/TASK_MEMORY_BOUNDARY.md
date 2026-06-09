# Task vs Brain v2 Memory Boundary

HIKARI keeps **tasks** separate from **Brain v2 semantic personal memory**.

## Brain v2 semantic memory

Use for durable owner facts that should be recalled later:

- identity (legal and preferred name)
- stable home location
- education, preferences, relationships
- future plans the owner wants remembered
- session-only current location (working context)

Stored through episodes → candidates → review → accepted memories.

## Task intents (not semantic memory)

Use for actions and requests that should **not** become personal facts:

- `remind me to …`
- `schedule …`
- `open …` / `run …` panel or app actions
- `write code for …`

These are routed to the task intent service (`core/tasks/`) and receive honest UX:
scheduling is **not wired up yet**, and the utterance is **not** stored as Brain v2 memory.

## Design rules

1. Never store reminder/scheduling phrasing as accepted Brain v2 facts.
2. Never claim a reminder was created until a real scheduler exists.
3. Task records are intents only (`TaskStatus.NOT_SCHEDULED`).
4. Plans with explicit future intent (`Remember this: I will meet Person C tomorrow`) remain Brain v2 plan memories.

## Modules

| Module | Role |
|---|---|
| `core/brain_statements.py` | Detect task vs declarative memory phrasing |
| `core/tasks/schemas.py` | `TaskRecord`, `TaskIntent`, `TaskStatus` |
| `core/tasks/store.py` | `TaskStore` interface + in-memory stub |
| `core/tasks/service.py` | Record task intents without Brain v2 writes |
| `core/orchestrator.py` | Route task phrasing before consolidation |

## Persistence

- Task intents are stored in a separate SQLite database under the private brain runtime area.
- Override path for tests or custom installs with `HIKARI_TASKS_DB`.
- List recent intents: `hikari.py --tasks-list` (read-only; does not create a missing DB).
- Use `hikari.py --tasks-list --all` to include every speaker/session scope.
- Records include `speaker_label`, `session_id`, and `source` (owner vs guest).

## Scheduling (opt-in)

- macOS Reminders scheduling is **disabled by default**.
- Set `HIKARI_ENABLE_TASK_SCHEDULER=1`, record a reminder task, then say `schedule that reminder`.
- Status becomes `SCHEDULED` only after osascript succeeds; failures become `SCHEDULE_FAILED`.
- Tasks remain outside Brain v2 memory even when scheduled.

## Future work

- Scheduler integration (macOS reminders/calendar) with explicit user confirmation
- Command center UI for open tasks
