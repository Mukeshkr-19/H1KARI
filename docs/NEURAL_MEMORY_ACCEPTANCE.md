# Neural memory — acceptance standard (pass / fail)

This document is the **engineering bar** for `core/neural_memory/`. It is not a roadmap essay. Items are **PASS** (implemented + verified), **GATED** (required before a release or before merging certain classes of change), or **N/A** until the feature exists.

Every PR that touches neural memory **must** state which clauses it satisfies or explicitly defers (with a tracked issue). **Vibe-based merge is not acceptable** for memory-critical changes.

---

## 0. Scope and definitions

- **Neural memory** means: `core/neural_memory/`, `core/neural_memory_bridge.py`, orchestrator hooks, and any SQLite schema under the live brain directory (not committed).
- **Important memory**: nodes/edges that influence user-visible answers, preferences, or automation (FACT, PREFERENCE, PERSON anchors, high-weight edges, pinned nodes).
- **Upstream model**: any non-local LLM or API call that receives text built from retrieval.

---

## 1. Memory truth

| ID | Requirement | Pass criteria |
|----|----------------|---------------|
| T1 | **Provenance on important memories** | Every IMPORTANT node/edge persisted after merge has structured provenance: `source` ∈ {`user_stated`, `inferred`, `seeded`, `imported`}, `observed_at` (ISO 8601), optional `session_id` / `turn_ref`. Stored in DB (not only in RAM). |
| T2 | **No silent overwrite** | Changing a fact/preference that already exists does not replace history without trace. Either: version row, or **supersede** edge (`DERIVED_FROM` / `CONTRADICTS` + archived prior), or explicit tombstone. **FAIL** if `ON CONFLICT DO UPDATE` alone clears provenance without supersede record. |
| T3 | **Inferred vs stated** | Inferred extractions are tagged `source=inferred` and default **lower max salience** than `user_stated` unless promoted by user confirmation. |
| T4 | **Seeded vs live** | `seed_nodes.json` and programmatic seeds set `source=seeded` and never masquerade as user dialogue. |
| T5 | **Negation handling (GATED)** | Storing “I do not like X” does not blindly create the same positive edge as “I like X”. Either parse negation or abstain from edge creation. **GATED** until implemented; PRs adding preference edges **must** cite status. |

**Merge blocking:** PRs that add new write paths to nodes/edges without provenance fields → **BLOCK**.

---

## 2. Supersede and contradiction

| ID | Requirement | Pass criteria |
|----|----------------|---------------|
| S1 | **Supersede path** | Preference/project updates create a **new** canonical record or edge set; prior record is **archived or linked** via `DERIVED_FROM` / `CONTRADICTS` (schema-defined), not deleted without trace. |
| S2 | **Contradiction detection (GATED)** | Same subject + conflicting predicates cannot both remain active at max weight without a resolution policy (e.g. recency wins, user wins, or explicit `CONTRADICTS`). |
| S3 | **Single canonical preference (GATED)** | For a given `(user_id, preference_key or normalized name)` at most one **active** high-confidence preference unless explicitly multi-valued. |

**Merge blocking:** PRs that change preference/fact merge behavior without supersede story → **BLOCK** once S1 is marked in scope for that PR.

---

## 3. Retrieval explainability

| ID | Requirement | Pass criteria |
|----|----------------|---------------|
| R1 | **Why retrieved** | API (e.g. adapter or debug module) returns for each retrieved item: `node_id`, `strategy` (direct / recent / graph / fts / fallback), `score_components` (salience, recency, match quality), optional `path` (edge list ids). |
| R2 | **Strategy surfaced** | `ContextPacket` (or successor) exposes `retrieval_strategies_used` and it is **non-empty** on successful retrieval, or explicitly documents “empty by design” with reason. |
| R3 | **Inspectable in logs (opt-in)** | Default logs **must not** dump full node bodies. Verbose explain mode is behind env flag (e.g. `HIKARI_MEMORY_DEBUG_EXPLAIN=1`). |

**Merge blocking:** PRs that change retrieval ranking without updating explain payload or tests → **BLOCK** once R1 exists for that codepath.

---

## 4. Safety and privacy

| ID | Requirement | Pass criteria |
|----|----------------|---------------|
| P1 | **No raw DB to upstream** | Integration test or static audit: orchestrator / router **never** concatenates full DB dumps or unbounded `SELECT *` into prompts. Only bounded context packets. |
| P2 | **Brain path local** | DB and embeddings live under the live brain directory (or `config`-resolved path); repo contains **no** committed user brain files. |
| P3 | **Debug logging** | Default log level for memory does not print secrets, raw API keys, or full user messages in production paths; redact or truncate (document max length). |
| P4 | **Context size cap** | Hard cap on characters/tokens injected from memory into a single model call; overflow is summarized or dropped with logged notice. |

**Merge blocking:** Any PR widening prompt injection without P1/P4 review → **BLOCK**.

---

## 5. Durability and crash behavior

| ID | Requirement | Pass criteria |
|----|----------------|---------------|
| D1 | **WAL + transactions** | Schema uses WAL; multi-step writes for one logical turn use a transaction; `foreign_keys=ON` where applicable. |
| D2 | **Kill mid turn (GATED)** | Test: SIGKILL during `process_turn` / `compile_and_store` leaves DB **openable** and **no torn** session row without detectable state (or documented recovery). |
| D3 | **Kill mid consolidation (GATED)** | Same for `session_compaction` / `full_consolidation`. |
| D4 | **Startup recovery** | If DB exists but `schema_version` mismatches, migration or clear failure path is defined (no silent corruption). |

**Merge blocking:** PRs that add new multi-write flows without transaction boundaries → **BLOCK**.

---

## 6. Quality — scale and measurement

| ID | Requirement | Pass criteria |
|----|----------------|---------------|
| Q1 | **Soak test (GATED)** | Automated scenario: ≥ **200** synthetic turns with corrections and repeats; invariants: no unbounded node growth beyond documented factor; no crash. |
| Q2 | **Retrieval benchmark (GATED)** | Fixed query set with expected node ids / forbidden ids; minimum precision@k documented in CI or nightly job. |
| Q3 | **Junk rate** | After extraction, ratio of filtered-to-raw candidates logged in test harness; threshold documented (e.g. &lt; X% nodes named stopwords). |
| Q4 | **Duplicate explosion** | Metric: new nodes per turn under repeat phrasing stays below threshold (documented). |

**Merge blocking:** PRs that loosen extraction without Q3 test update → **BLOCK** once Q3 exists.

---

## 7. Correction controls (user-facing)

| ID | Requirement | Pass criteria |
|----|----------------|---------------|
| C1 | **Forget** | Command or API: mark archived / tombstone + remove from default retrieval. |
| C2 | **Correct** | User supplies replacement; triggers supersede (S1) + provenance update. |
| C3 | **Pin / demote** | Adjust `is_pinned` or salience bounds with audit trail. |
| C4 | **Mark outdated** | Soft flag; retrieval deprioritizes unless query asks for history. |

**Merge blocking:** None until C1 exists; then PRs breaking C1 semantics → **BLOCK**.

---

## 8. Consolidation

| ID | Requirement | Pass criteria |
|----|----------------|---------------|
| K1 | **Archive rules** | Documented: salience + age + access; implemented in code; tested. |
| K2 | **Merge rules** | Duplicate detection criteria documented (normalized name, type, user_id). |
| K3 | **Decay rules** | Salience / edge weight decay functions documented and bounded. |
| K4 | **Contradiction policy** | Linked to S2; not hand-waved in comments only. |

**Merge blocking:** PRs that change archive/merge without updating K1–K2 tests → **BLOCK** once those tests exist.

---

## 9. Regression and CI

| ID | Requirement | Pass criteria |
|----|----------------|---------------|
| I1 | **Core regressions** | `tests/test_neural_memory_hardening.py` (and successors) must pass on every PR touching `core/neural_memory/`. |
| I2 | **New failure = new test** | Any production bug in memory gets a test that fails without the fix. |

---

## 10. Current status snapshot (honest)

Use this row to update when the codebase changes.

| Area | Status (as of authoring) |
|------|---------------------------|
| Extraction filters / junk reduction | Partially implemented — extend tests as extraction grows |
| Row normalization / compaction safety | Partially implemented |
| Invalid placeholder edges | Addressed for current compiler path |
| User PERSON anchor | Implemented |
| Adapter boundary / `ingest_unstructured_text` | Implemented |
| Local-only seed / no repo PII | Implemented |
| Provenance DB fields | **GATED** — not complete for all writes |
| Supersede / contradiction graph | **GATED** |
| Explainability API | **GATED** |
| Kill-mid-write / soak / retrieval benchmark | **GATED** |
| Correction commands | **GATED** |

---

## 11. PR merge blocking criteria (summary)

A memory PR **must not merge** if it:

1. Adds or widens model prompt injection without bounded context (P1, P4).
2. Adds new persistence paths for important memory without provenance (T1–T4).
3. Uses overwrite for factual/preference memory where supersede is required by this doc (T2, S1) once those features are in scope for that subsystem.
4. Adds multi-step DB writes without transactions (D1).
5. Weakens extraction filters without test evidence (Q3).

---

## 12. How to use this doc in review comments

Template for reviewers:

> This PR touches neural memory. Please confirm: (a) which acceptance rows are satisfied, (b) which are explicitly out of scope with issue link, (c) new tests added per I2.

---

*Document owner: engineering. Update when acceptance criteria are promoted from GATED to PASS.*
