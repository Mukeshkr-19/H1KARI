# Compiled Private Wiki Writeback

H1KARI compiles active accepted Brain v2 memories into private markdown pages for easy reading and offline backup.

## Principles

- **Durable memories only** — The compiled wiki represents long-term structured facts only. Casual chat, pending candidates, rejected candidates, retired memories, superseded memories, task lists, and temporary session context (such as trip location details like *"I am in City B now"*) are strictly excluded.
- **Reference only** — The wiki pages are a read-only Compiled Support representation. The **active accepted Brain v2 memories** in the episodes database remain the sole truth authority for H1KARI’s cognitive and retrieval systems. The wiki does not act as a source of truth for the agent.
- **Strictly private** — Wiki markdown files are written strictly to the private brain directory (never inside the public repository source tree).
- **Owner-first privacy** — Guest speaker statements and temporary interlude memories are strictly filtered out to prevent data pollution and ensure privacy isolation for the owner.

## CLI Usage

### Read-only Preview (No File Writes)
To print what would be written to the wiki without creating any files:
```bash
.venv/bin/python hikari.py --brain-v2-wiki-preview
```

### Apply Compiled Writeback
To compile active memories and write them atomically to the private wiki directory:
```bash
.venv/bin/python hikari.py --brain-v2-wiki-writeback
```

## Compilation Directory
- **Default:** the default home episodes wiki folder (or the configured `brain_dir() / "wiki"` path).
- **Override (for testing):** Set the `HIKARI_WIKI_DIR` environment variable to write compiled pages to a custom location.

## Generated Pages

- `profile.md` — Core user identity facts.
- `education.md` — User and partner academic histories.
- `locations.md` — Durable home location facts (excluding transient session context).
- `relationships.md` — Friends, family, and household relationship connections.
- `preferences.md` — Personal preferences and dislikes.
- `plans.md` — Compiled active plans and events (written **only** if active plan memories exist in the database).

## Atomic Write and Cleanup
Wiki writeback is executed atomically (via temporary `.tmp` write and POSIX replacement) to prevent file corruption. Standard wiki pages are kept as empty templates when the compiled profile has at least one active accepted memory. Optional pages, such as `plans.md`, are removed when they no longer have active accepted memories so stale entries do not linger.
