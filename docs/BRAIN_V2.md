# HIKARI Brain v2

Brain v2 repairs HIKARI memory by separating **raw conversation evidence** from **reviewed durable memory**, following the Omi-inspired pipeline in the feature tracker.

## Storage (local, private)

| Item | Location |
|------|----------|
| Episode DB | live brain directory — Brain v2 episodes database |
| Neural graph (existing) | live brain directory — neural memory database |
| Legacy JSON compatibility | live brain directory — legacy data folder |

Nothing under Brain v2 storage is committed to the public repo. Runtime paths resolve through the private brain symlink on your machine.
Legacy JSON modules also write beside the private brain now, not to the public repo `data/` folder. Override with `HIKARI_LEGACY_DATA_DIR` for tests or temporary runs.

Disable Brain v2: `HIKARI_DISABLE_BRAIN_V2=1`

## Data flow

```text
chat/voice turn
  → TranscriptSegment (raw, verbatim)
  → (every 8 turns or on exit) consolidation
  → StructuredEpisode (summary only)
  → MemoryCandidate (pending, scored + duplicate-marked)
  → manual review (CLI)
  → SourceLinkedMemory (accepted, with segment ids)
  → optional promote → neural SQLite (learn_from_text)
  → BrainV2Retrieval → orchestrator context packet
```

## Personal recall authority (Brain v2 enabled)

When Brain v2 is enabled, **legacy neural personal facts do not answer** identity, profile, home/current location, family, education, preferences, plans, or personal HIKARI decisions.

| Source | May answer personal recall? |
|--------|----------------------------|
| Accepted active Brain v2 memories | Yes |
| Session working memory (current-session context) | Yes |
| Legacy neural SQLite personal rows | **No** (quarantined; preserved for private migration) |
| Non-personal / procedural neural paths | Unchanged where already used |

If Brain v2 has no reviewed answer, HIKARI returns: `I do not have a reviewed memory for that yet.` — it does **not** fall through to stale neural personal truth.

When Brain v2 policy is enabled, `HikariBrain.answer()` is **never** called (categorical quarantine, not phrase-by-phrase routing). Questions seeking a fact about the user or household (identity, birthday, work, school, birthplace, siblings, location, plans, preferences, family) route to Brain v2 reviewed recall or the honest no-reviewed message — **never** general AI. Non-personal queries may still use general AI without legacy personal prompt context.

Legacy `user_profile.json` names are not loaded into `SpeakerContext` primary user (only `HIKARI_PRIMARY_USER` env). `PersonalityEngine` quarantines on-disk personal `user_prefs` in Brain v2 mode: style traits adapt in memory, but personal JSON fields on disk are not updated until explicit legacy mode returns.

### Session and guest speaker boundaries (fail closed)

When a temporary session speaker or guest is active, personal recall and memory-summary commands **fail closed**: they do not read the household owner's reviewed Brain v2 memories, legacy neural answers, or JSON conversation logs. The orchestrator returns a guest-scoped no-memory reply instead. Session intros (`I am … talking to you now`, `this is …`, `… here`) mark `session_speaker_mode` when no primary user is configured; with a configured primary user, a different `current_speaker` is treated as a guest.

`SpeakerContext` clears `last_contact_kind` and `last_family_relation` on guest reset, return-to-owner, fresh Brain v2 session start, and new session-speaker intros so stale family/partner slots cannot leak into prompt context or partner-name normalization.

Verified by `tests/test_brain_v2_orchestrator.py::test_guest_family_question_does_not_use_owner_memory`, `::test_guest_profile_command_blocks_owner_reviewed_facts`, `::test_speaker_reset_clears_stale_contact_context`, and `::test_new_session_clears_guest_and_working_context`.

`HIKARI_BRAIN_V2_UNSAFE_NEURAL_PROFILE_SUPPLEMENT` is **not shipped**: setting it blocks `READY` and does not inject legacy neural prompt lines in authoritative mode.

## Candidate lifecycle

```text
user segment (verbatim)
  → consolidation extracts MemoryCandidate (user segments only by default)
  → quality gate: keep | weak | reject_from_queue
  → reject_from_queue: not stored (never enters pending queue)
  → weak: stored with lowered confidence/salience; visible in pending
  → keep: normal pending candidate
  → scoring + duplicate metadata (candidates never deleted for dupes)
  → manual accept / reject
  → accepted SourceLinkedMemory (segment ids preserved)
  → optional neural promote (--brain-v2-accept with --confirm-promote PROMOTE only)
```

## Quality labels (`extraction_policy_version: v2`)

| Label | Meaning |
|-------|---------|
| `keep` | Durable-looking fact (identity, family, preference, location, HIKARI decision, explicit remember, etc.) |
| `weak` | Borderline declarative; shown in pending but ranked lower |
| `reject_from_queue` | Filler, questions, commands, vague text, assistant-only facts — **not stored** |

Metadata on each candidate: `quality_label`, `quality_reasons`, `extraction_policy_version`.

Rejected patterns include: assistant filler (“okay”, “got it”), vague statements, questions, command-only text, chat control (“exit”, “bye”), very short text without entities, and assistant responses stored as user facts unless procedural.

## What is automatic vs manual

| Step | Automatic? |
|------|------------|
| Record transcript segments | Yes (orchestrator when Brain v2 enabled) |
| Consolidate to structured episode + candidates | Yes (periodic + on exit; not every turn) |
| Quality filter + score + duplicate marking | Yes (metadata only; no deletes) |
| Accept / reject candidates | **Manual** (CLI) by default |
| Clear owner self-disclosures (identity, home, own education, preference, plan, decision, direct family relation) | **Automatic** when non-conflicting (`owner_self_disclosure_v1`) |
| Temporary current location (`I am in …`) | **Session working memory** (no review queue) |
| Promote to neural memory | **Manual + explicit** — only with `--brain-v2-accept <id> --confirm-promote PROMOTE` (exact, case-sensitive). All other accept paths stay Brain v2-only. |
| Retrieval in prompts | Accepted memories for semantic layer; pending/rejected never semantic truth |

## Review CLI

```bash
.venv/bin/python hikari.py --brain-v2-status
.venv/bin/python hikari.py --brain-v2-pending
.venv/bin/python hikari.py --brain-v2-show <candidate_id>
.venv/bin/python hikari.py --brain-v2-accept-no-promote <candidate_id>   # safe default
.venv/bin/python hikari.py --brain-v2-accept <candidate_id>   # Brain v2 only (no promote)
.venv/bin/python hikari.py --brain-v2-accept <candidate_id> --confirm-promote PROMOTE   # also promotes
.venv/bin/python hikari.py --brain-v2-reject <candidate_id>
.venv/bin/python hikari.py --brain-v2-memories
.venv/bin/python hikari.py --brain-v2-retag-accepted   # optional metadata refresh
.venv/bin/python hikari.py --brain-v2-retire <memory_id>
.venv/bin/python hikari.py --brain-v2-supersede <memory_id> --brain-v2-statement "<corrected statement>"
.venv/bin/python hikari.py --brain-v2-edit-metadata <memory_id> [--brain-v2-memory-type <type>]
.venv/bin/python hikari.py --brain-v2-memory-history <memory_id>
.venv/bin/python hikari.py --brain-v2-reconcile-status
.venv/bin/python hikari.py --brain-v2-repair-plan
.venv/bin/python hikari.py --brain-v2-readiness
.venv/bin/python hikari.py --brain-v2-conflicts
.venv/bin/python hikari.py --brain-v2-live-qa-checklist
```

Also included in `hikari.py --memory-status` and `hikari.py --doctor`.

### Accepted-memory correction (retire / supersede)

Reviewed memories use metadata lifecycle status (`active`, `retired`, `superseded`). Corrections never hard-delete rows or evidence segments.

| Command | Effect |
|---------|--------|
| `--brain-v2-retire` | Marks memory retired; excluded from current recall; audit preserved |
| `--brain-v2-supersede` | Retires old row, creates new active replacement linked to original evidence |
| `--brain-v2-edit-metadata` | Safe metadata/type edits only (statement unchanged) |
| `--brain-v2-memory-history` | Shows supersession chain and audit entries |

Retired and superseded memories are not returned as current semantic truth. Pending/rejected candidates remain non-truth.

### Legacy neural reconciliation and repair

Brain v2 **accepted truth** and legacy **neural SQLite** can disagree until operator cleanup completes.

| Command | Output | Mutates live DB? |
|---------|--------|------------------|
| `--brain-v2-reconcile-status` | Redacted findings + opaque `neural_target` ids (scans all active neural rows) | No (read-only) |
| `--brain-v2-repair-plan` | Plan items with category, action, `neural_target`, status `not_applied` | No |
| `--brain-v2-readiness` | `READY` / `NOT READY` + counts/categories only | No |
| `--brain-v2-conflicts` | Conflict categories + `[redacted]` placeholders | No |

**Legacy neural repair apply is not implemented in this release.** `apply_neural_repair_item()` and `apply_repair_item()` always raise `NotImplementedError`. Use read-only `--brain-v2-reconcile-status` and `--brain-v2-repair-plan` only. Runtime personal recall correctness comes from Brain v2 authority plus legacy quarantine, not from live repair apply.

Copy-only repair mutation helpers live under `tests/support/legacy_neural_repair_apply.py` for isolated temp-DB tests only; they are not importable production entrypoints and have no CLI apply command.

### Fail-closed coordinator degradation

When Brain v2 policy is enabled (`HIKARI_DISABLE_BRAIN_V2` unset), normal chat/orchestrator startup **does not** call `init_neural_memory()`, initialize legacy neural storage, or create the default private-brain neural filesystem tree. `BrainV2Coordinator` runs with `neural_bridge=None`. Legacy neural access is limited to explicit read-only reconcile/conflict/report CLI paths and optional `PROMOTE` flows when intentionally invoked. Verified by `tests/test_brain_v2_live_isolation.py::test_brain_v2_on_orchestrator_skips_neural_init`.

When Brain v2 policy is enabled but `BrainV2Coordinator` initialization fails, personal recall and `Remember this:` must **not** fall through to legacy neural answers or writes. The orchestrator returns the canonical unavailable message (`Brain v2 is temporarily unavailable…`) or the no-reviewed-memory message. Commands such as `what do you remember?` and `what have we talked about?` use reviewed Brain v2/session-safe summaries only — never the legacy JSON conversation log.

Brain v2 row corrections remain `--brain-v2-retire` / `--brain-v2-supersede`. Neural promotion still requires `PROMOTE` exactly.

Normal chat and orchestrator runtime use `allow_neural_conflict_reads=False`, so personal recall **does not** read the legacy neural database to detect conflicts. When no reviewed Brain v2 memory covers a category, recall returns the honest no-reviewed-memory (or unavailable) response — not a conflict-review message derived from legacy rows. Reviewed Brain v2 memories remain authoritative when present. Legacy conflict inspection is read-only and explicit via `--brain-v2-conflicts`, `--brain-v2-readiness`, and `--brain-v2-reconcile-status` only.

### Private owner workflow (local only)

1. Backup the private brain directory (operator script outside this repo).
2. Run redacted commands: `--brain-v2-readiness`, `--brain-v2-reconcile-status`, `--brain-v2-repair-plan`, `--brain-v2-conflicts`.
3. Privately review any guarded queue IDs with `--brain-v2-pending` / `--brain-v2-show` — **PRIVATE LOCAL OUTPUT - DO NOT COMMIT OR SHARE**. Clear direct owner self-disclosures (such as identity, stable location, education, and preferences) are accepted into Brain v2 automatically when they do not conflict with existing reviewed truth.
4. Accept or reject guarded candidates; use `--brain-v2-retire` / `--brain-v2-supersede` on conflicting Brain v2 memories only after review.
5. Repair apply is not available in this release; optional private cleanup uses read-only plan output only.
6. Re-run `--brain-v2-readiness` until `READY` before declaring Brain phase complete.

`READY` means Brain v2 is authoritative for personal recall, there are zero guarded pending candidates, and legacy personal recall is **quarantined**. Legacy archival findings may still be reported for optional private migration; they do not block runtime readiness once the legacy answer/prompt/write paths are quarantined. It does **not** mean the legacy neural database was fully cleaned.

Readiness reports:

- `brain_v2_policy=enabled|disabled`
- `brain_v2_runtime=available|degraded_unavailable|disabled` (env flag alone does not imply runtime is usable)
- `legacy_personal_recall_authority=quarantined|degraded_runtime_unavailable|not_quarantined`
- `legacy_personal_answer_path|legacy_personal_prompt_path|legacy_personal_write_path=quarantined|degraded|not_quarantined`
- `unsafe_override_active=false` required for `READY` (`HIKARI_BRAIN_V2_UNSAFE_NEURAL_PROFILE_SUPPLEMENT` must be unset)
- `personal_factual_general_ai_fallback=blocked` when policy is enabled (missing reviewed facts cannot fall through to router/AI)
- `legacy_rows_preserved_for_private_migration=<count>` (count only; no row content)
- `archival_legacy_findings` when recognized stale/misattribution categories remain in the quarantined legacy archive

Unrecognized legacy rows may remain preserved after quarantine; they do not block READY by count alone. Copy-only repair is optional private migration tooling, not required for runtime correctness.

Never paste private live CLI output into Git, issues, or public docs.

### Private migration workflow (generic)

1. Review guarded pending Brain v2 candidates locally (`--brain-v2-pending`, `--brain-v2-show`).
2. Clear non-conflicting owner self-disclosures are learned directly into Brain v2 without neural promotion; review is reserved for conflicts or more sensitive/ambiguous facts.
3. Re-state any missing durable owner facts explicitly in chat; Brain v2 should use accepted safe facts immediately.
4. Legacy neural DB stays preserved read-only for history; it is not authoritative for answers.
5. Use redacted `--brain-v2-reconcile-status` / `--brain-v2-repair-plan` only for optional private cleanup on **copied** DBs — never required for quarantine.
6. Do not place real statements, names, or locations in public repo files, tests, or reports.

### Private live QA

Run `hikari.py --brain-v2-live-qa-checklist` locally for operator steps. Safe commands: `--brain-v2-status`, `--brain-v2-reconcile-status`, `--brain-v2-repair-plan`, `--brain-v2-readiness`, `--brain-v2-conflicts`, `--brain-v2-eval` (synthetic fixtures only). **Private content output** (do not paste publicly): `--brain-v2-pending`, `--brain-v2-memories`, `--brain-v2-show`, `--brain-v2-memory-history`. Re-run `--brain-v2-status` before/after maintenance; do not commit live transcripts or memory statements.

### Test isolation

Suite-wide `tests/conftest.py` pins explicit temporary `HIKARI_BRAIN_V2_EPISODES_DB`, `HIKARI_NEURAL_MEMORY_DB`, and `HIKARI_LEGACY_DATA_DIR` paths before any brain modules load, so default pytest does not read or write your live brain directory. Per-test `monkeypatch.setenv` overrides still work.

Fake `HOME` directories are used only in dedicated isolation tests (for example `tests/test_brain_v2_live_isolation.py`) that prove resolved DB paths and orchestrator startup never touch a live brain sentinel under `HOME`. The shared conftest does not override `HOME`.

Verified by `tests/test_brain_v2_live_isolation.py::test_suite_uses_isolated_brain_v2_episodes_db` and `::test_suite_uses_isolated_neural_db_path`.

`HIKARI_BRAIN_V2_CONFLICTS_PRIVATE=1` enables read-only private statement display in `--brain-v2-conflicts` (local operator use only; never in CI/public tests).

Candidate ids support unique prefixes (first 8 characters) for `--brain-v2-show`, accept, and reject.

### Neural promotion safety

**Safe default: no neural promotion.** These paths accept into Brain v2 source-linked memory only and never call the neural promoter:

- `--brain-v2-accept-no-promote <candidate_id>` (explicit safe flag)
- `--brain-v2-accept <candidate_id>` **without** `--confirm-promote`

**Promotion requires an explicit token.** To also write the statement to the live neural memory database, pass `--confirm-promote PROMOTE` with `--brain-v2-accept`. The token must be exactly `PROMOTE` (case-sensitive). Wrong or missing tokens reject promotion; `--brain-v2-accept` without the token still accepts into Brain v2 only.

`--confirm-promote` cannot be combined with `--brain-v2-accept-no-promote`.

### Safe default workflow

Prefer **accept without neural promotion** until you have verified the statement and source segments:

```bash
.venv/bin/python hikari.py --brain-v2-accept-no-promote <candidate_id>
# or equivalently:
.venv/bin/python hikari.py --brain-v2-accept <candidate_id>
```

Use promotion only when you intentionally want the statement written to the **live neural memory database** via `DurableMemoryPromoter`:

```bash
.venv/bin/python hikari.py --brain-v2-accept <candidate_id> --confirm-promote PROMOTE
```

That path is harder to unwind than Brain v2 source-linked memory alone.

### Suggested review workflow

1. Chat normally (or run smoke tests).
2. `hikari.py --brain-v2-pending` — shows id prefix, `rank_score`, `quality`, type, duplicate markers, short statement.
3. `hikari.py --brain-v2-show <id>` — statement, quality reasons, rank score, verbatim source segment(s), duplicate metadata, accept command hints.
4. `hikari.py --brain-v2-accept-no-promote <id>` (safe) or `--brain-v2-reject <id>`.
5. Optional: `hikari.py --brain-v2-accept <id> --confirm-promote PROMOTE` when neural promotion is intended.
6. `hikari.py --brain-v2-memories` — confirm accepted, source-linked rows.
7. `hikari.py --brain-v2-status` — counts and DB health.

Duplicate accepts merge into one source-linked memory (extra segment ids appended; candidates marked `merged_into_existing`). Duplicate pending candidates stay in the queue but rank lower (`duplicate_of`, `duplicate_of_existing_memory`).

## Guided review (`--brain-v2-review`)

Interactive walkthrough for pending candidates in **rank order** (highest `rank_score` first). For each candidate it shows the statement, quality label/reasons, duplicate markers, and verbatim source segment(s), then prompts for an action:

| Key | Effect |
|-----|--------|
| `[a]` accept (no promote) | Same as `--brain-v2-accept-no-promote <id>` — safe default |
| `[p]` promote | Prompts you to type `PROMOTE` (exact, case-sensitive); only then accepts and promotes to live neural memory |
| `[r]` reject | Same as `--brain-v2-reject <id>` |
| `[s]` skip | Leave pending; move to next candidate |
| `[q]` quit | Exit the session |

If you choose `[p]` but type anything other than `PROMOTE`, promotion is cancelled and the candidate stays pending.

```bash
.venv/bin/python hikari.py --brain-v2-review
```

Implementation lives in `core/brain_v2/cli.py` (`cmd_review`, `confirm_promote`, `REVIEW_ACTIONS_HELP`).

Use guided review when you have several pending items and want evidence-first decisions without copying candidate ids manually.

## Memory eval (`--brain-v2-eval`)

Repeatable smoke checks using **fake fixtures only** (no live private names or facts). Runs scripted scenarios against a temporary Brain v2 store and reports pass/fail for:

- profile summary recall (`who am I?`, `what do you know about me?`)
- stable location vs current temporary location
- plan / event recall (meeting place, date)
- education / relationship recall
- guest speaker boundary (guest intros must not overwrite owner memory)
- research gating (personal recall must not route to external research)

```bash
.venv/bin/python hikari.py --brain-v2-eval
```

Prints a pass/fail table only. Clears configured neural DB env vars for the run, uses an isolated temp episodes database with synthetic fixtures only, and does not read conflict data from any configured or default neural path. Use after Brain v2 changes to confirm recall behavior before manual review on a live session.

## Conflict scanner (`--brain-v2-conflicts`)

Compares **reviewed** Brain v2 source-linked memories against neural profile lines when a safe read-only neural summary path exists. Today the CLI is **Brain v2-only**: it does not initialize neural memory or open the live neural database.

```bash
.venv/bin/python hikari.py --brain-v2-conflicts
```

When neural summary is unavailable, output includes:

`Neural summary unavailable; checked accepted Brain v2 memories only.`

Default output is **redacted** (conflict category and action only; `[redacted]` placeholders). It does not print reviewed or neural statement text. For local private review only, set `HIKARI_BRAIN_V2_CONFLICTS_PRIVATE=1` before running the command. Read-only scan — no auto-delete or promotion.

Run after accepting memories that supersede older neural promotions, or when recall still feels “wrong” despite reviewed Brain v2 answers winning in chat.

## Privacy rule (committed code and docs)

**Never commit real private names, places, or household facts** to the public repo. Tests, docs, and example strings must use **fake fixtures only** (e.g. City A, Jamie, Restaurant A, School A).

The public-source privacy scanner (`tests/privacy_scan.py`, enforced by `tests/test_privacy_terms.py`) uses a **generic denylist only** — never encoded private personal names or places. It scans **tracked and untracked** (non-ignored) public text under `core/`, `tests/`, `docs/`, `services/`, `security/`, `skills/`, `agents/`, `bin/`, `config/`, `scripts/`, `hikari-frontend/`, and selected root files. Categories include:

- private path markers (private data directory name, live brain directory markers, macOS home paths in docs)
- runtime database filenames (live neural DB name, Brain v2 episode DB name in public text)
- credentials (environment secret files, credentials JSON filenames, API key patterns)
- API secret token shapes (provider key prefixes and assignment patterns)

Violations report `file:line: rule_id (category) — [REDACTED] snippet` without echoing secrets. Scanner definition files are included in the scan; `scanner_source_is_generic()` proves they contain no legacy name-list encodings or hidden string-tuple fragments.

Private runtime data stays in the live brain directory and private brain symlink (gitignored). Brain v2 episode databases and neural SQLite files are never committed.

## Architecture layers

When Brain v2 policy is enabled, normal runtime uses only the Brain v2 modules below. Legacy neural graph edges and `HikariBrain` procedural/SKILL paths are **not** active in chat (see fail-closed quarantine at the top of this doc).

| Layer | Module | Purpose |
|-------|--------|---------|
| Working | `working_memory.py` | Active session, task, recent turns |
| Episodic | `StructuredEpisode` | Session summaries (support retrieval only) |
| Semantic | `SourceLinkedMemory` | Reviewed facts with evidence |
| Entity | `speaker_context.py` + reviewed metadata | Owner/guest speaker routing; household relation hints from accepted Brain v2 rows (not legacy neural graph) |
| Procedural | `retrieval.py` (reviewed memories) | Procedural context from accepted Brain v2 memories; legacy neural SKILL/RULE via `HikariBrain` is quarantined in normal chat |
| Consolidation | `consolidation_pipeline.py` | Segments → structured → candidates |
| Retrieval | `retrieval.py` | Ranked context packet |

## Typed memory inference (rule-based)

`core/brain_v2/memory_type.py` infers `candidate_type` and structured metadata from statement text during consolidation. There is **no LLM** in this path.

| Type | Example |
|------|---------|
| `identity` | “My name is Alex.” |
| `location` | “I live in City A.” |
| `preference` | “I prefer local-first private tools.” |
| `relation` / `education` | “My girlfriend Jamie is a medical student at River Medical University.” |
| `plan` / `event` | “On Sunday May 24 2026 I am meeting Jamie at Restaurant B for lunch.” |
| `decision` | “For HIKARI we decided reviewed Brain v2 memories should come before research.” |
| `travel` | Return-flight facts |

**Explicit remember** (`Remember this: …`) strips the prefix, runs the same inference, and sets `explicit_remember: true` in metadata. It does **not** default to `preference`.

Plan/event metadata may include `date_text`, `place`, `person`, and `relation` when detectable. Accepted memories copy these fields into source-linked metadata at review time.

Optional maintenance (manual only):

```bash
.venv/bin/python hikari.py --brain-v2-retag-accepted
```

Re-infers `candidate_type` and metadata for **already accepted** memories from their statements. Statements are unchanged; there is no neural promotion or deletion.

## Retrieval + Recall Quality

Brain v2 recall is **rule-based** (no LLM). `core/brain_v2/recall_intent.py` classifies queries, and `BrainV2Retrieval` boosts accepted memories by intent and `candidate_type` metadata. Queries that name a person prefer accepted memories that mention that person (so sister facts do not answer girlfriend/Jamie questions).

### Recall intents

| Intent | Example query |
|--------|----------------|
| `identity_self` | “who am I?” |
| `profile_summary` | “what do you know about me?” |
| `family_person` | “do you know my sister?” |
| `relationship` | “what does my gf do?” |
| `education` | “what does Jamie study?” |
| `preference` | “what do I prefer?” |
| `location` | “where do I live?” |
| `plan` | “what are my plans for Sunday May 24?”, “where am I meeting Jamie?” |
| `travel` | “when is my return flight?” |
| `hikari_project_decision` | “what did we decide about HIKARI?” |
| `general_memory` | “what do you remember?” |
| `non_memory` | general chat / non-personal questions |

### Accepted semantic vs episodic support

| Layer | Truth? | Use |
|-------|--------|-----|
| **Accepted source-linked** (`SourceLinkedMemory`) | Yes — only after manual review | Primary answers and prompt context |
| **Structured episodic** (session summaries) | Support only, capped score | Extra context when tokens overlap; never above accepted semantic |
| **Pending / rejected candidates** | Never | Suppress matching episodic text; never in semantic layer |

Accepted hits include `[source: N segment(s)]` evidence notes. Raw transcript text is not injected into normal prompts.

### Profile summary

For `profile_summary` (and orchestrator memory-first context), `build_profile_summary_context()` groups **accepted** memories into:

- identity  
- family / relationships  
- plans / events  
- preferences  
- locations / travel  
- HIKARI project decisions  

`try_answer_from_accepted_memories()` answers direct recall questions from accepted memories only. If none exist, HIKARI reports that no **reviewed** memory is available (no invention).

### Orchestrator order (memory-first, Brain v2 enabled)

1. Brain v2 accepted-memory or session-context answer for **all plausible personal recall** queries.
2. If no reviewed memory: `I do not have a reviewed memory for that yet.` — **no** legacy neural personal fallback.
3. `Remember this:` and similar statements are recorded for Brain v2 review — **not** written to legacy neural memory.
4. Brain v2 prompt context uses accepted semantic + profile summary only (no default legacy neural personal injection).
5. External research is **not** routed for personal recall questions.
6. Legacy `HikariBrain.answer()` is **not** called when Brain v2 policy is enabled (any query).
7. Legacy JSON / personality personal facts are not written when Brain v2 policy is enabled (`HIKARI_ENABLE_LEGACY_MEMORY=1` does not restore personal authority).
8. Durable neural writes require explicit `--brain-v2-accept --confirm-promote PROMOTE` only.

## Orchestrator retrieval order (context packet)

1. Working memory
2. Speaker / family context
3. Active task context
4. Accepted source-linked semantic memories (intent-boosted, with evidence note)
5. Structured episodic summaries (capped; support only)
6. External research only for non-personal recall queries

Legacy neural personal prompt context is **never** appended when Brain v2 policy is enabled. When the coordinator is unavailable, general AI prompts also omit legacy personal neural/personality/user-profile/memory context.

`--brain-v2-conflicts` uses redacted output by default. Private operator mode may read the configured/default neural DB read-only for reconciliation; never paste that output into public artifacts.

## Candidate scoring (no auto-accept)

Each pending candidate gets metadata:

- `rank_score` — confidence, salience, quality label, source evidence count, explicit remember boost, durable-entity boost, duplicate penalty  
- `normalized_statement` — duplicate detection key  
- `duplicate_of` / `duplicate_primary` / `duplicate_count` — safe duplicate marking among candidates  
- `duplicate_of_existing_memory` — normalized match to an already accepted source-linked memory  

## Mapping from old code

| Old | Brain v2 |
|-----|----------|
| `core/memory.py` JSON | Optional legacy; not authoritative |
| `neural_memory.models.Episode` | Neural DB episodes; separate from Brain v2 structured episodes |
| `HikariBrain` | **Legacy / quarantined** when Brain v2 policy is on: not used for normal personal Q&A, prompts, or writes. Available when `HIKARI_DISABLE_BRAIN_V2=1` (legacy policy) or in explicitly approved maintenance/promotion CLI paths only |
| `neural_memory_bridge` | Durable promotion target (CLI `PROMOTE` / operator maintenance only; not initialized in normal Brain-v2-on chat) |

## Tests

```bash
.venv/bin/python -m pytest tests/test_brain_v2_*.py -q
.venv/bin/python -m pytest tests/test_privacy_terms.py -q
```

Includes `tests/test_brain_v2_recall.py` for recall intent, ranking, profile summary, and orchestrator guards, `tests/test_brain_v2_memory_type.py` for typed explicit-remember extraction and plan/education recall, and `tests/test_privacy_terms.py` to block real private names/facts in tracked source.

## Not implemented yet

Brain v2 does **not** yet provide:

- Auto-accept or policy-based review  
- LLM-based extraction (rule/heuristic `v2` policy only)  
- Graph-vector hybrid retrieval (HippoRAG / LightRAG)  
- Karpathy wiki writeback  
- Live voice WebSocket segment streaming  
- Command-center brain UI  
- Memory decay jobs across Brain v2 + neural DB  

Planned review tooling:

- Accepted-memory edit / retire after mistaken review

Implemented CLI (see sections above): `--brain-v2-review`, `--brain-v2-eval`, `--brain-v2-conflicts`, promotion safety via `--confirm-promote PROMOTE`.

See `future-integrations/HIKARI_FEATURE_SOURCES_TRACKER.md` for ranked priorities.
