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

Natural-language task utterances are routed to the legacy task-intent service
(`core/tasks/`) and are not stored as Brain v2 memory. That chat path remains
separate from the Phase 3 Files-tab productivity flow, where an explicitly
prepared reminder or read-only scheduled job can be previewed and confirmed.

## Design rules

1. Never store reminder/scheduling phrasing as accepted Brain v2 facts.
2. Never claim a reminder was created until the confirmed productivity adapter
   returns success.
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

- The legacy chat-task macOS Reminders scheduler is **disabled by default**.
- Set `HIKARI_ENABLE_TASK_SCHEDULER=1`, record a reminder task, then say `schedule that reminder`.
- Status becomes `SCHEDULED` only after osascript succeeds; failures become `SCHEDULE_FAILED`.
- Tasks remain outside Brain v2 memory even when scheduled.
- The Phase 3 Files-tab flow is separate: reminder creation requires a frozen
  preview and explicit approval, while one-shot scheduling accepts only browser
  research and calendar reads.

## Future work

- Unifying natural-language task intents with the Phase 3 confirmation flow
- Command center UI for open tasks
