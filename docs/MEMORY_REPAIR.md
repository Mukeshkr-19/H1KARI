# Brain v2 Memory Repair (Owner Workflow)

Accepted Brain v2 memories can be corrected without hard-deleting rows or transcript evidence.

## Principles

- **Preview first** — use `--repair-preview` before any apply on the live database.
- **Backup first** — copy the private brain directory before `--confirm-repair`.
- **Source-linked provenance** — supersede keeps predecessor segment ids in metadata; retire preserves the row and audit trail.
- **Separate from neural repair** — legacy neural SQLite repair apply is still read-only/plan-only in this release.

## Read-only inspection

```bash
.venv/bin/python hikari.py --brain-v2-memories
.venv/bin/python hikari.py --brain-v2-repair-show <memory_id>
.venv/bin/python hikari.py --brain-v2-memory-history <memory_id>
```

## Repair preview (no writes)

```bash
.venv/bin/python hikari.py --brain-v2-retire <memory_id> --repair-preview
.venv/bin/python hikari.py --brain-v2-supersede <memory_id> \
  --brain-v2-statement "Owner A lives in City B." --repair-preview
.venv/bin/python hikari.py --brain-v2-edit-metadata <memory_id> \
  --brain-v2-memory-type location --repair-preview
```

## Apply on live private brain (confirmation required)

On the default home episodes database, apply requires an exact token:

| Action | Token |
|--------|-------|
| Retire | `RETIRE` |
| Supersede (statement change) | `SUPERSEDE` |
| Edit metadata/type only | `EDIT` |

```bash
# After backup:
.venv/bin/python hikari.py --brain-v2-retire <memory_id> --confirm-repair RETIRE
.venv/bin/python hikari.py --brain-v2-supersede <memory_id> \
  --brain-v2-statement "Owner A lives in City B." --confirm-repair SUPERSEDE
.venv/bin/python hikari.py --brain-v2-edit-metadata <memory_id> \
  --brain-v2-memory-type location --confirm-repair EDIT
```

Isolated test databases (`HIKARI_BRAIN_V2_EPISODES_DB` set to a temp path) skip live confirmation.

## Statement vs metadata

- **Wrong fact text** → `--brain-v2-supersede` (creates a new active row, links provenance).
- **Wrong type/tag only** → `--brain-v2-edit-metadata` (statement unchanged).
- **Remove from recall** → `--brain-v2-retire`.

## After repair

```bash
.venv/bin/python hikari.py --brain-v2-memory-history <memory_id>
.venv/bin/python hikari.py --brain-v2-eval
.venv/bin/python scripts/brain_live_qa.py
```

Do not paste private memory output into public chats or commits.
