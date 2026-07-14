# Develop Branch Intake Ledger

Status: complete
Baseline: `develop` at `2616e24`  
Baseline source: `main` at `6b481b7` plus the canonical plan merge  
Updated: 2026-07-13

## Purpose

This ledger controls the review of pre-existing branches before any change enters `develop`. Historical branches are not merged wholesale. Each change is reviewed, tested, and either re-applied through a fresh issue branch or explicitly deferred.

## Baseline verification

| Gate | Result |
|---|---|
| Python tests | 854 passed; 6 warnings |
| Doctor | OK with one warning: frontend dependencies are not installed in the develop worktree |
| Brain v2 evaluation | 8/8 passed |
| Brain live QA | all scenarios passed |
| Privacy scan | passed |
| Git state | `develop` clean and tracking `origin/develop` |
| Public `main` | unchanged at `6b481b7` |

The warnings are known dependency/deprecation notices plus an unwritable pytest cache in the restricted worktree. They did not change test results.

Current verified implementation baseline: `ac9aec5` with 888 passing tests. Doctor, Brain v2 evaluation, Brain live QA, privacy, and diff checks pass, and public `main` remains unchanged at `6b481b7`.

## Status legend

- `ready`: small, understood, test-backed change suitable for a fresh intake branch
- `revise`: useful fix, but the existing patch needs adjustment or additional tests
- `characterize`: behavior must be captured before the change is accepted
- `defer`: depends on a planned architecture boundary that does not exist yet
- `superseded`: later code already contains the intended behavior
- `reject`: change should not enter `develop`
- `integrated`: accepted through a fresh intake branch and verified on `develop`

## Intake decisions

| Historical branch | Scope | Status | Reason and required evidence |
|---|---|---|---|
| `fix/calculator-eval-security` | replace arithmetic `eval` with bounded AST evaluation | integrated | Reworked as `safety/calculator-expression-parser`; accepted commit `a8b0903`. |
| `fix/research-url-encoding` | encode search query before URL construction | integrated | Reworked to use request parameters as `safety/research-query-encoding`; accepted commit `08ae690`. |
| `fix/server-pairing-safety` | escape pairing values and add response headers | integrated | Expanded with no-store, no-referrer, anti-framing, and CSP checks as `safety/server-pairing-pages`; accepted commit `991e18d`. |
| `fix/auth-and-safety-hardening` | reject path-like memory names and align auth trust thresholds | defer | The memory validator has no production callers, so it would not protect writes. Auth threshold changes require separate characterization. Wire validation at the real storage boundary and keep the concerns split. |
| `fix/mac-control-safety-gates` | require confirmation for destructive Mac actions | integrated | The historical patch guarded unused `MacControl`, so it was rejected as written. The valid production behavior was reworked in `SystemAgent` as `safety/system-action-confirmations`; accepted commit `f7896d7` requires exact confirmation before sleep, restart, shutdown, or empty-trash side effects. |
| `fix/security-policy-path-whitelist` | enforce allowed paths in `SecurityPolicy` | defer | `SecurityPolicy` currently has no production callers. Merging would create a false safety claim. Implement as part of the real central action path. |
| `fix/orchestrator-singleton-lock` | protect singleton initialization | integrated | Reworked with a deterministic constructor-count regression as `core/orchestrator-singleton-lock`; accepted commit `88bd4f5`. |
| `fix/system-agent-music-flow` | remove unreachable clipboard restore block | integrated | Reworked with a behavior test as `fix/system-music-unreachable-code`; accepted commit `0487918`. |
| `fix/exception-observability` | replace broad exception handlers across runtime paths | reject | Rejected as a broad patch: several narrowed handlers would turn recoverable optional-feature failures into crashes. Two proven defects were salvaged separately: action-result parsing at `d932b29` and thermostat input handling at `fe2d484`. |
| `fix/runtime-guard-cleanups` | guard optional collaborators and runtime stubs | reject | The tray guard duplicates the accepted singleton lock, CodeAgent already falls back to the AI router, and `ProactiveIntelligence` has no production constructor while its intended collaborators implement the required methods. |
| `fix/reminders-and-speech-runtime` | scheduler and speech runtime corrections | defer | Speech serialization was reworked and accepted separately at `1d7212d`. The reminder parser remains deferred because the historical patch is locale-dependent, loses time-of-day data, and treats `today` as midnight rather than a useful due time. |
| `fix/runtime-small-bugs` | browser, build executor, and menubar fixes | integrated | Reworked as three focused branches: `safety/browser-applescript-quoting` at `c062515`, `fix/build-executor-plan-state` at `b00a477`, and `fix/menubar-runtime-fallback` at `2e2aed4`. |
| `fix/neural-graph-algorithms` | graph algorithm corrections | integrated | Reworked with alternate-path, bridge non-mutation, breadth-first search, PageRank direction, dangling-node, and zero-weight coverage as `core/neural-graph-corrections`; accepted commit `312603e`. |
| `fix/voice-verification-math` | speaker verification scoring math | integrated | The advisory voice-feature calculation was corrected with focused edge-case tests as `fix/voice-feature-math`; accepted commit `b761a74`. The active ECAPA voice-lock threshold was deliberately left unchanged. |
| `fix/brain-v2-fixture-leakage` | remove hardcoded clean-room fixtures | reject | The synthetic city, school, and person values are privacy-safe clean-room fixtures, and the proposed removal of a non-person label was incorrect. The real hardcoded education-organization parser defect was salvaged separately at `0488f5e`. |
| `fix/hikari-daemon-runtime` | large daemon loop rewrite | integrated | Reworked as the minimal loop-structure repair with static and behavioral coverage in `fix/daemon-loop-structure`; accepted commit `183bd48`. The daemon now has one entrypoint and one listening loop, and enrolled-speaker verification errors fail closed. |
| `fix/router-provider-config-sync` | add one provider and synchronize fallback configuration | defer | Provider-specific configuration should follow the provider-neutral capability/router contract and a secrets/data-boundary review. |
| `fix/frontend-pwa-icons` | provide valid manifest icon sizes | integrated | Reworked as `frontend/pwa-manifest-icons` at `b32dd17` with metadata-free 192px and 512px assets matching the manifest. |

## Integration log

| Accepted branch | Commit | Branch verification | Post-merge verification |
|---|---|---|---|
| `safety/calculator-expression-parser` | `a8b0903` | 37 focused tests; 854 full tests; privacy passed | 854 full tests; privacy passed |
| `safety/research-query-encoding` | `08ae690` | 4 focused tests; 855 full tests; privacy passed | 4 focused tests; privacy passed |
| `safety/server-pairing-pages` | `991e18d` | 2 focused tests; 857 full tests; privacy passed | 2 focused tests; privacy passed |
| `core/orchestrator-singleton-lock` | `88bd4f5` | 1 focused test; 858 full tests; privacy passed | 1 focused test; privacy passed |
| `fix/system-music-unreachable-code` | `0487918` | 1 focused test; 859 full tests; privacy passed | 1 focused test; privacy passed |
| `frontend/pwa-manifest-icons` | `b32dd17` | manifest parse, exact dimensions, empty image metadata, privacy passed | asset and privacy checks passed |
| `safety/browser-applescript-quoting` | `c062515` | 2 focused tests; 861 full tests; privacy passed | 2 focused tests; privacy passed |
| `fix/build-executor-plan-state` | `b00a477` | 2 focused tests; 863 full tests; privacy passed | 2 focused tests; privacy passed |
| `fix/menubar-runtime-fallback` | `2e2aed4` | import/path check; 864 full tests; privacy passed | 1 focused test; privacy passed |
| `fix/action-result-parsing` | `d932b29` | 2 focused tests; 866 full tests; privacy passed | 2 focused tests; privacy passed |
| `fix/thermostat-input-errors` | `fe2d484` | 2 focused tests; 868 full tests; privacy passed | 2 focused tests; privacy passed |
| `safety/system-action-confirmations` | `f7896d7` | 7 focused tests; 875 full tests; privacy passed | 7 focused tests; privacy passed |
| `fix/service-speech-serialization` | `1d7212d` | 1 focused test; 876 full tests; privacy passed | 1 focused test; privacy passed |
| `fix/voice-feature-math` | `b761a74` | 2 focused tests; 878 full tests; privacy passed | 2 focused tests; privacy passed |
| `core/neural-graph-corrections` | `312603e` | 4 focused tests; 882 full tests; privacy passed | 4 focused tests; privacy passed |
| `fix/education-organization-extraction` | `0488f5e` | 2 focused tests; 884 full tests; privacy passed | 2 focused tests; privacy passed |
| `fix/daemon-loop-structure` | `183bd48` | 4 focused tests; 888 full tests; privacy passed | 4 focused tests; privacy passed |

The increasing full-test count reflects the focused regression tests added by accepted branches.

## Intake order

### Wave 1 - Small security and input-boundary fixes

1. ~~Calculator evaluation~~
2. ~~Research URL encoding~~
3. ~~Pairing-page output hardening~~
4. Auth and memory-name validation, deferred until the real boundaries are characterized and wired

### Wave 2 - Concurrency and contained runtime fixes

1. ~~Orchestrator singleton lock~~
2. ~~System music-flow deletion~~
3. ~~Frontend PWA manifest icons~~
4. ~~Split and integrate the valid runtime-small-bug changes~~
5. ~~Reject the redundant or unreachable runtime-guard changes~~
6. Exception observability, reviewed file by file; action-result parsing integrated

### Wave 3 - Side effects, voice, memory, and daemon behavior

1. ~~Mac destructive-action containment~~
2. ~~Speech serialization~~; reminder parsing deferred pending a locale-neutral, time-preserving contract
3. ~~Voice verification math~~
4. ~~Neural graph algorithms~~
5. ~~Brain fixture review and valid education-parser salvage~~
6. ~~Daemon characterization and minimal root-cause fix~~

### Architecture-bound work

- Real path scopes land with the central action-policy path, not as an unused policy class.
- Provider additions land through provider-neutral contracts.
- Large daemon or orchestrator restructuring waits for characterization coverage.

## Per-branch intake procedure

1. Create a new branch from current `develop`.
2. Re-implement or cherry-pick only the reviewed coherent change.
3. Inspect all callers and sibling paths.
4. Run the smallest focused check that proves the root-cause fix.
5. Run `git diff --check` and the privacy/attribution scan.
6. Run the full test suite for security, identity, memory, orchestrator, or shared-runtime changes.
7. Commit and push the focused branch.
8. Merge into `develop`, rerun relevant integration gates, and push `develop`.
9. Mark the row `integrated` with the accepted commit.
10. Do not modify or delete the historical branch unless separately requested.

## Completion condition

Phase A branch intake completes when every listed branch is `integrated`, `superseded`, `defer`, or `reject` with evidence; `develop` passes the full integration gates; and `main` remains unchanged.

## Phase A completion record

Every historical candidate has a terminal disposition. Accepted behavior entered through focused branches, flawed broad patches were rejected or narrowed, deferred work is tied to an explicit missing boundary, and no historical branch was merged wholesale. The final gate record is 888 passing tests, doctor success, Brain v2 evaluation 8/8, Brain live QA success, privacy success, and clean diff checks. `main` and `origin/main` remain frozen at `6b481b7`.
