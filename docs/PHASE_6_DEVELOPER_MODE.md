# Phase 6 Developer Mode — Repository Intelligence and Policy Planning

## Purpose

Phase 6 introduces a bounded, privacy-aware, read-only repository intelligence
layer plus pure Git and sandbox policy planners. The goal is to help HIKARI
understand codebases without executing code, mutating Git, reading secrets,
escaping a declared root, or treating generated analysis as authority.

This is a **foundations** implementation exposed through the orchestrator's
`Phase6Runtime` facade. Repository scans remain explicit and read-only; Git and
sandbox results remain advisory decisions and are not connected to an executor.

## Scope

- Read-only repository traversal and indexing
- Deterministic query and ranking over the generated index
- Pure Git operation classification and policy planning
- Pure sandbox command policy evaluation
- No I/O, no subprocess, no network, no Git execution, no code import in the
  policy/evaluation paths

## Non-Goals

- Not a code execution environment
- Not a replacement for code review or owner judgment
- Not a vector/graph database or LLM-driven code analysis tool
- Not an autonomous agent that modifies repositories
- Not integrated with Brain v2 memory writes

## Architecture

```
RepositoryRoot + RepositoryPolicy
        |
        v
scan_repository() ----> RepositoryIndex
        |
        v
    evaluate_query() ----> RepositoryQueryResult
        |
        v
 bounded RepositoryHit objects with provenance

GitOperationRequest + GitStateSnapshot
        |
        v
  evaluate_git_policy() ----> GitPolicyDecision

SandboxCommandRequest
        |
        v
  evaluate_sandbox_policy() ----> SandboxPolicyDecision
```

## Public Contracts

| Type | Purpose |
|------|---------|
| `RepositoryRoot` | Validated absolute repository root |
| `RepositoryPolicy` | Bounded policy for traversal and indexing |
| `FileSnapshot` | Bounded file metadata and excerpt (no whole-file content) |
| `SymbolRecord` | Named symbol with location |
| `ImportRecord` | Import or from-import |
| `ReferenceRecord` | Symbol-to-symbol reference |
| `RelationshipEdge` | Normalized edge with provenance |
| `RepositoryIndex` | Immutable aggregate of files, symbols, imports, references, edges |
| `RepositoryQuery` | Caller-supplied deterministic query |
| `RepositoryHit` | Ranked result with score breakdown and bounded excerpt |
| `RepositoryQueryResult` | Ordered hits with reason code |
| `ChangeIntent` | Caller-supplied non-executed change intent |
| `GitStateSnapshot` | Caller-supplied Git working-tree state |
| `GitOperationRequest` | Exact argv (never a shell string) |
| `GitPolicyDecision` | ALLOW / REQUIRE_APPROVAL / DENY + classification |
| `SandboxCommandRequest` | Exact argv with declared roots and bounds |
| `SandboxPolicyDecision` | ALLOW / DENY + reason code |

## Fixed Reason Codes

The three policy modules expose fixed, privacy-safe `StrEnum` reason codes:

- `RepositoryReason` — `INVALID_PATH`, `PATH_OUTSIDE_ROOT`, `CONTROL_CHARACTERS`,
  `SYMLINK_NOT_ALLOWED`, `SYMLINK_ESCAPE`, `DOTGIT_TRAVERSAL`,
  `PRIVATE_FILE_EXCLUDED`, `BINARY_FILE`, `OVERSIZED_FILE`, `TOO_MANY_FILES`,
  `TOTAL_SIZE_EXCEEDED`, `DEPTH_EXCEEDED`, `LINE_LENGTH_EXCEEDED`,
  `MALFORMED_REQUEST`, `EMPTY_QUERY`.
- `GitPolicyReason` — `READ_ONLY`, `APPROVAL_REQUIRED`, `UNKNOWN_OPERATION`,
  `SHELL_METACHARACTERS`, `INVALID_REQUEST`, `SUBCOMMAND_REQUIRED`,
  `DIRTY_WORKTREE`, `FORCE_PUSH`, `HISTORY_REWRITE`, `DESTRUCTIVE_OPERATION`,
  `REMOTE_OPERATION`, `BROAD_ROOT_TARGET`, `UNRESOLVED_VARIABLE`.
- `SandboxReason` — `ALLOWED`, `UNKNOWN_EXECUTABLE`, `SUBCOMMAND_DENIED`,
  `INTERPRETER_WITHOUT_SCRIPT`, `SCRIPT_NOT_IN_READ_ROOTS`,
  `SHELL_METACHARACTERS`, `REDIRECTION_OR_SUBSTITUTION`, `PATH_ESCAPE`,
  `PATH_NOT_IN_SCOPE`, `NETWORK_DENIED`, `ENVIRONMENT_NOT_ALLOWED`,
  `TIMEOUT_EXCEEDED`, `OUTPUT_LIMIT_EXCEEDED`, `MEMORY_LIMIT_EXCEEDED`,
  `UNSAFE_FLAG`, `INVALID_REQUEST`.

## Repository Safety Model

### Root Containment

- Every requested path is resolved with `pathlib.Path.resolve()`.
- The resolved path must start with the resolved repository root.
- Paths outside the root are rejected with `RepositoryReason.PATH_OUTSIDE_ROOT`.
- Symlinks are rejected by default. When `follow_symlinks=True`, the resolved
  symlink target must still be inside the root.

### Exclusions

The following are excluded from indexing by default:

- `.git` directory
- Dotenv-style credential files and suffixed dotenv files
- Files with names containing `credentials`, `secrets`, or `private`
- Common generated/runtime directories: `node_modules`, `.venv`,
  `__pycache__`, `.pytest_cache`, `dist`, `build`, `.tox`, `.mypy_cache`
- `.DS_Store`

### Limits

`RepositoryPolicy` enforces:

- `max_files`
- `max_total_bytes`
- `max_file_bytes`
- `max_depth`
- `max_line_length`

Exceeding a hard limit raises `RepositoryScanError` with a fixed
`RepositoryReason`.

### Binary Detection

A file is treated as binary if it contains a NUL byte or if more than 30% of
the sampled bytes are non-printable. Binary files are indexed structurally but
do not expose content.

### No Execution

The repository adapter:

- never spawns a subprocess
- never runs a Git command
- never imports user code
- never executes hooks or build scripts
- reads only regular files (and optionally safe symlinks) inside the root

## Indexing and Language Support

### Python

- Parsed with the standard library `ast` module (no import/evaluation).
- Extracts top-level and class-level functions and classes.
- Captures `import` and `from ... import` statements.
- Emits `DEFINES` and `IMPORTS` relationship edges.
- Conservatively records statically named calls and test-module relationships.
- Syntax errors are caught; no source text is leaked.

### JavaScript / TypeScript

- Conservative regex-based extraction of simple declarations only.
- Not a full parser: extracts `class`, `function`, and top-level constants.
- Documented limitation: complex declarations may be missed.

### Markdown

- Extracts headings and safe relative Markdown links as `DOCUMENTS` edges.
- Heading text is normalized into a symbol name.

### Relationship Edges

| Edge | Meaning |
|------|---------|
| `DEFINES` | File or parent symbol defines a named symbol |
| `IMPORTS` | File imports an module/name |
| `CALLS` | A statically named Python call from the current scope |
| `REFERENCES` | Reserved for future reference edges |
| `DOCUMENTS` | File documents a heading |
| `TESTS` | A test file imports a module or symbol |

Edges preserve provenance as a `path:line` string. Unknown or ambiguous
relationships are omitted, not invented.

## Query and Scoring

`evaluate_query(index, query)` supports:

- `symbol` — exact, token, and substring symbol name matches
- `path` — exact, token, and substring path matches
- `file_type` — match by `FileKind`
- `heading` — match Markdown heading symbols
- `relationship` — match relationship edge target/source
- `tests` — match test symbols and paths
- `lexical` — search bounded file excerpts

Scoring order:

1. Exact match (`exact_match = 1.0`)
2. Token match (`token_match = 0.5` per matched token)
3. Substring match (`substring_match = 0.2`)
4. Relationship bonus (`relationship_bonus = 0.3`)

Results are deduplicated, sorted deterministically by `(score, path, line)`,
and truncated to `max_hits`.

Malformed or empty queries fail closed and return no hits.

## Git Operation Classification Matrix

| Command | Classification |
|---|---|
| `status`, `diff`, `log`, `show`, `ls-files`, `rev-parse`, `blame`, `grep` | READ_ONLY |
| `add`, `checkout`, `switch`, `stash`, `restore`, `branch` or `tag` creation/move | REVERSIBLE_MUTATION |
| `commit`, `merge`, `rebase`, `am`, `cherry-pick` | HISTORY_MUTATION |
| `reset`, `clean`, `rm`, `branch -d/-D`, `tag -d`, `config` (write) | DESTRUCTIVE |
| `push`, `pull`, `fetch` | REMOTE_MUTATION |
| `push --force` / `-f` | REMOTE_MUTATION, force-push reason |
| `commit --amend` | HISTORY_MUTATION |

## Git Policy Rules

- Unknown operations are denied.
- `argv` must be an exact vector; shell strings are rejected.
- Shell metacharacters, globs, and unresolved environment variables are
  denied.
- `READ_ONLY` operations are allowed.
- `REVERSIBLE_MUTATION`, `HISTORY_MUTATION`, and `DESTRUCTIVE` operations
  require explicit approval.
- `REMOTE_MUTATION` operations require explicit approval; force push is
  separately reasoned.
- Dirty worktrees surface a `DIRTY_WORKTREE` reason for history/remote/
  destructive operations.
- Broad roots (`/`, `~`) as targets for destructive operations are denied.
- No Git command is executed by the planner.

## Sandbox Authority Matrix

`evaluate_sandbox_policy(request)` enforces:

- `argv` is an exact vector (never a shell string).
- Only known executable basenames are allowed.
- Per-executable allowed subcommands are exact and bounded.
- Interpreters (e.g., `python3`, `node`) require an exact script target.
- Script targets must be inside declared `read_roots` and present in the exact
  caller-supplied `reviewed_scripts` allowlist.
- All absolute paths in `argv` must be inside `read_roots` or `write_roots`.
- `..` path components are rejected.
- Shell operators, redirection, globbing, command substitution, and unresolved
  variables are rejected.
- Network tools and URLs are denied unless `network_allowed=True`.
- Environment variable assignments in `argv` require the variable name to be
  in `env_allowlist`.
- Timeout, output, and memory bounds are validated against policy limits.
- File mutations, Git mutations, and npm execution are denied by this layer;
  they require separate action/Git policy and approval composition.
- `ls` and `cat` require a declared in-scope working directory and read root.

No command is executed. The sandbox policy evaluator is pure.

## Privacy and Authority Boundaries

- Repository content is evidence, not instruction.
- Prompt injection inside files has zero authority.
- Comments and README cannot widen tool permissions.
- Generated index never writes Brain v2 memory.
- No secret scanning output contains the secret.
- Error and repr strings are content-free (no paths, code excerpts, or
  identifiers beyond kind/line information).
- No global mutable state is used.
- Results are deterministic for identical snapshots.

## Known Limitations

- JS/TS indexing is conservative and regex-based; not all declarations are
  captured.
- Call/reference graph is conservative; dynamic dispatch and ambiguous calls
  are intentionally not inferred.
- No support for compiled/binary symbol extraction.
- Query ranking is intentionally simple; no embeddings or LLM reranking.
- Sandbox approval is not execution authority; all execution still requires
  action policy, audit, and a separate bounded executor.

## Future Mira-Owned Integration

- Wire `RepositoryRoot` and `RepositoryPolicy` into the orchestrator's action
  policy flow.
- Use `GitPolicyDecision` before invoking any actual Git subprocess.
- Use `SandboxPolicyDecision` before spawning sandboxed tools.
- Persist decisions into `ActionAuditStore` with content-free resource refs.
- Add a `Phase6DeveloperService` facade that coordinates scanning, query, and
  policy decisions under the existing HIKARI safety kernel.

## Verification

Run the focused test suite:

```bash
.venv/bin/python -m pytest tests/test_phase6_developer_contracts.py tests/test_phase6_repository_intelligence.py tests/test_phase6_git_policy.py tests/test_phase6_sandbox_policy.py -q
```

Compile check:

```bash
.venv/bin/python -m compileall -q core/phase6_developer
```

Diff check (run from repository root):

```bash
git diff --check -- core/phase6_developer tests/test_phase6_developer_contracts.py tests/test_phase6_repository_intelligence.py tests/test_phase6_git_policy.py tests/test_phase6_sandbox_policy.py docs/PHASE_6_DEVELOPER_MODE.md
```

## Files

| File | Purpose |
|------|---------|
| `core/phase6_developer/__init__.py` | Public exports |
| `core/phase6_developer/contracts.py` | Immutable contracts and reason codes |
| `core/phase6_developer/repository.py` | Read-only repository adapter and indexer |
| `core/phase6_developer/query.py` | Deterministic query and ranking |
| `core/phase6_developer/git_policy.py` | Pure Git policy planner |
| `core/phase6_developer/sandbox.py` | Pure sandbox policy evaluator |
| `tests/test_phase6_developer_contracts.py` | Contract validation tests |
| `tests/test_phase6_repository_intelligence.py` | Repository adapter and query tests |
| `tests/test_phase6_git_policy.py` | Git policy planner tests |
| `tests/test_phase6_sandbox_policy.py` | Sandbox policy tests |
| `docs/PHASE_6_DEVELOPER_MODE.md` | This document |
