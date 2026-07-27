# Phase 6 Platform/Provider Evaluation Matrix

## Purpose

This matrix records the repository-verified evaluation of candidate provider
families that could be wired into HIKARI's optional Phase 6 live backends.  No
winner is claimed without measured evidence.  Mutually exclusive stacks are
compared, not all shipped.

## Evaluation Dimensions

- **Requirement**: What HIKARI capability needs from this provider family.
- **Privacy Class**: `LOCAL_ONLY`, `GATEWAY_OK`, or `REMOTE_OK`.
- **Authority Required**: What actor/grant is needed to enable it.
- **Evidence Currently Present**: What proof of fitness exists in the repo today.
- **Benchmark Needed**: What measurement is required before adoption.
- **Licensing/Provenance Status**: Whether licenses/attribution are clear.
- **Disposition**: `hikari-native` | `optional-adapter` | `evaluated` | `rejected` | `missing-evidence`.
- **Next Evidence Needed**: Exact artifact needed to move to a final disposition.

## Candidate Family Matrix

| Family | Requirement | Privacy Class | Authority Required | Evidence Present | Benchmark Needed | Licensing/Provenance | Disposition | Next Evidence Needed |
|--------|-------------|---------------|--------------------|------------------|------------------|----------------------|-------------|----------------------|
| Local model providers | On-device inference for text/voice/vision | LOCAL_ONLY | Owner-configured device policy | `core/router.py`, local model routing in `core/phase6_ecosystem/model_evaluation.py` | Latency, memory, quality, safety per model | Depends on model license; repository contains `docs/PROVENANCE_INVENTORY.md` | optional-adapter | Measured benchmarks for each candidate model and provenance audit |
| Remote model gateways | Fallback inference when local model unavailable | GATEWAY_OK / REMOTE_OK | Owner-approved egress policy | `core/router.py`, `core/phase6_ecosystem/model_evaluation.py` | Egress, latency, cost, privacy class per gateway | Depends on gateway ToS; unknown upstream model licenses | missing-evidence | Signed gateway terms, model provenance, egress cost benchmark |
| MCP tools | Tool use through Model Context Protocol | REMOTE_OK | Owner-approved tool grant | `core/phase6_agent/` contracts, `core/productivity/tool_permissions.py` | Capability coverage, safety, latency per server | MCP protocol open; per-server licenses unknown | optional-adapter | Sandbox/security review of each MCP server |
| Controlled browser automation | Web reading/interaction | REMOTE_OK | Owner approval | None currently in repo | Isolation, cookie/data handling, egress | N/A | missing-evidence | Design and isolation benchmark |
| Desktop shell | OS-level actions | LOCAL_ONLY | Owner explicit grant | `core/action_policy.py`, `core/productivity/tool_permissions.py` | Scope, sandbox, approval flow | N/A (platform-owned) | rejected | Reconsider only with verified sandbox |
| Mobile shell | Mobile device actions | LOCAL_ONLY | Owner explicit grant | None | Scope, sandbox, approval flow | N/A (platform-owned) | rejected | Reconsider only with verified sandbox |
| Sandbox/isolation provider | Contain tool/model execution | LOCAL_ONLY | Owner-configured policy | None in repo | Escape resistance, performance, I/O isolation | Depends on provider | missing-evidence | Evaluate seatbelt/Docker/macOS app sandbox alternatives |
| Observability provider | Metrics/telemetry | REMOTE_OK | Owner consent | None | Privacy class, data minimization, retention | Depends on provider | missing-evidence | Privacy review and DPA |
| Packaging/update mechanism | Skill/package delivery | REMOTE_OK | Owner-approved publisher trust | `core/phase6_ecosystem/skill_package.py` | Integrity, signature, rollback | Publisher-dependent | optional-adapter | Signed publisher key rotation and review process |
| Documentation site stack | Public docs | REMOTE_OK | Owner publication | `docs/` Markdown, no generated site | Build reproducibility, no secrets | Repo-owned | hikari-native | Static build verification |

## Explicitly Rejected Parallel Runtimes

The following projects/patterns are rejected as second runtimes or parallel
orchestrators.  Their patterns may be implemented only through HIKARI-owned
policy, Brain, and task boundaries.

| Runtime | Reason for Rejection |
|---------|----------------------|
| Jarvis | Implicit global state; second orchestrator |
| DeerFlow | Second task runtime; conflicts with `core/task_planner.py` |
| Ruflo | Parallel workflow engine; scope-widening risk |
| OpenSwarm | Multi-agent runtime; bypasses HIKARI actor/session model |
| Hermes | Second messaging/orchestration layer |
| OpenClaw | Privileged automation runtime; uncontrolled blast radius |
| Any second Brain/orchestrator/task runtime | Violates single-owner-policy and single-task-ledger invariant |

## Licensing / Provenance Blockers

1. **Non-commercial upstream code**: Any upstream dependency or model with a
   non-commercial license must remain optional and clearly documented.
2. **Unknown model/dataset licenses**: Models or datasets with unclear terms
   cannot be shipped as default; they remain `missing-evidence` until provenance
   is recorded in `docs/PROVENANCE_INVENTORY.md`.
3. **Externally sourced instructions/assets**: Any third-party instruction or
   asset must retain original attribution; unverified material is rejected.
4. **Unselected project license**: The repository license selection is pending;
   final disposition of third-party code cannot be finalized until the project
   license is chosen.

## Disposition Definitions

- **hikari-native**: Core, always-present HIKARI-owned code with no external
  dependency.
- **optional-adapter**: Real implementation exists behind an injected interface;
  enabled only by owner configuration.
- **evaluated**: Measured evidence exists and the candidate is approved for use
  under stated constraints.
- **rejected**: Ruled out due to safety, licensing, or architecture conflicts.
- **missing-evidence**: Candidate is plausible but lacks required evidence.

## Mira-Owned Next Steps

1. Populate measured benchmarks for each `optional-adapter` and
   `missing-evidence` candidate.
2. Resolve licensing blockers by selecting a project license and auditing
   `docs/PROVENANCE_INVENTORY.md`.
3. Design and implement sandbox/isolation provider integration before enabling
   any shell or MCP tool execution.
4. Wire live backends in `core/phase6_live` into the orchestration layer with
   owner configuration and audit.


## Live backend exact-cap note

Measured routing live backend retains exact configured capacity; prune only on overflow.
