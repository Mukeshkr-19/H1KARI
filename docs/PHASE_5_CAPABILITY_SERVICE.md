# Phase 5 Capability Service

## Purpose

`core/phase5/capability_service.py` provides a pure, stateless, proposal-only
layer for the three supported Phase 5 capabilities:

- **Teach Me**
- **Guide My Hands**
- **Care**

The service sits *behind* an already-computed `Phase5RuntimeDecision`. It
creates typed, bounded, immutable proposals. It does **not** execute actions,
perform I/O, access databases, call external services, or invoke models.

Every proposal is a value object. Every final decision explicitly records
whether owner approval is still required. No proposal is treated as owner
authority, and no proposal mutates accepted Brain v2 memory.

## Authority and Approval

### Runtime authorization is required

`Phase5CapabilityService.prepare` requires:

- a matching `Phase5RuntimeDecision`
- the runtime decision's `request_id` matches the capability request
- the runtime decision's `actor_id` and `actor` match the capability request
- the runtime decision's `capability` matches the capability request

The service **does not trust** caller-supplied `approval_required` flags. It
trusts only the underlying runtime decision `outcome` and the policy decision.

### Rejected authorizations

The following runtime outcomes are treated as terminal denials and produce a
service-level deny:

- `DENY`
- `EXPIRED`
- `REVOKED`
- `OUT_OF_SCOPE`
- `AUTHENTICATION_REQUIRED`

A runtime `REQUIRE_APPROVAL` outcome with a policy decision may produce an
approval-needed proposal, but never an executable or final output. A child
ambiguity decided before policy evaluation propagates only the approval state
and produces no proposal.

### Approval preservation

The service preserves explicit approval requirements:

- `REQUIRE_APPROVAL` runtime decisions return a `CapabilityServiceDecision`
  with `approval_required=True`.
- `ALLOW` runtime decisions for **Care** represent already-matched, scoped
  owner consent and therefore return `approval_required=False`.
- `ALLOW` runtime decisions for **Teach Me** and **Guide My Hands** return
  `approval_required=False`, unless a specific step or child context requires
  approval.

## Contracts

### Public types

| Type | Purpose |
|------|---------|
| `CapabilityExecutionRequest` | Caller-supplied request to prepare a proposal |
| `CapabilityAuthorizationProof` | Bounded proof extracted from a `Phase5RuntimeDecision` |
| `CapabilityServiceDecision` | Final immutable service decision with optional proposal |
| `CapabilityServiceReason` | Stable machine-readable reasons |
| `CapabilityProposalKind` | Discriminator for proposal kinds |
| `TeachMeProposal` | Lesson outline, steps, review questions, optional skill candidate |
| `GuideHandsProposal` | Ordered guidance steps, observation requests, clarification prompts |
| `GuideStep` | Typed step with `INFORMATIONAL`, `OBSERVATIONAL`, or `CONSEQUENTIAL` kind |
| `CareProposal` | Supportive language, check-ins, contact prompts, emergency recommendation |
| `Phase5CapabilityService` | Stateless service entry point with `prepare(...)` |

### CapabilityExecutionRequest

Immutable fields:

- `request_id` — bounded identifier
- `actor` — `Phase5ActorContext`
- `capability` — one of `Capability.TEACH_ME`, `Capability.GUIDE_MY_HANDS`, `Capability.CARE`
- `action` — optional, sanitized action descriptor
- `resource` — optional, sanitized resource reference
- `data_subject` — optional, sanitized data subject
- `topic` / `goal` / `care_prompt` — optional, sanitized, bounded human-readable strings
- `metadata` — tuple of sanitized, bounded strings

No arbitrary metadata dictionaries are permitted. All text is sanitized and
control characters are rejected.

### CapabilityServiceDecision

Immutable fields:

- `request_id`
- `outcome` — `Outcome.ALLOW`, `Outcome.REQUIRE_APPROVAL`, or `Outcome.DENY`
- `reason` — `CapabilityServiceReason`
- `approval_required` — explicit boolean
- `proposal` — optional typed `CapabilityProposal`
- `audit_id` — carried from the runtime decision

`__repr__` is content-free.

## Teach Me

### Allowed output

- Lesson outline (tuple of bounded strings)
- Learning steps (tuple of bounded strings)
- Review questions (tuple of bounded strings)
- Optional skill-evolution candidate reference (identifier only)

### Hard denials

The service denies any Teach Me request whose action indicates:

- `install`
- `deploy`
- `activate`
- `publish`

Assistant-authored content is always labeled non-authoritative. The service
does not write accepted Brain v2 memory, modify dependencies, or execute code.

### Actor behavior

- **Owner:** allowed for low-risk, bounded proposals.
- **Child:** allowed when child-scoped and safe.
- **Trusted Helper:** allowed when backed by a valid scoped grant.

## Guide My Hands

### Allowed output

- Ordered `GuideStep` objects
- Observation requests
- Uncertainty / clarification prompts
- Explicit approval markers on consequential steps

### Step kinds

| Kind | Description |
|------|-------------|
| `INFORMATIONAL` | Provides context or instruction |
| `OBSERVATIONAL` | Requests caller-supplied observation |
| `CONSEQUENTIAL` | Requires explicit approval before proceeding |

### Safety rules

- No physical or OS action is performed.
- No camera/vision payload is embedded in the proposal.
- Consequential steps require approval.
- Uncertainty produces a clarification prompt, not a guessed fact.
- Completion claims without caller-supplied evidence are not produced.

### Actor behavior

- **Owner:** allowed; consequential actions require approval at the step level.
- **Child:** allowed only with owner approval.
- **Trusted Helper:** allowed only within grant scope and with required approvals.

## Care

### Allowed output

- Supportive, fixed safe messages
- Check-in questions
- Prompt to contact a trusted human
- Bounded emergency escalation recommendation
- Explicit owner-approval requirement

### Hard denials

The service denies any Care request whose action indicates:

- `diagnose`
- `prescribe`
- `treat`
- `medical`

It also denies claims that emergency services were contacted without
caller-supplied evidence (e.g., metadata containing `contacted`, `called`, or
`notified` without `evidence` or `confirmed`).

### Safety rules

- No diagnosis, prescription, or treatment authority.
- No claim that emergency services were contacted.
- No fabricated professional advice.
- No external contact is performed.
- Care is always approval-gated by the runtime/policy boundary. The service
  only clears the flag after receiving an audited `ALLOW` proof.

### Actor behavior

- **Owner:** allowed only after scoped consent; the resulting proposal records
  that no additional approval is pending.
- **Child:** allowed only with explicit owner approval (`REQUIRE_APPROVAL`).
- **Trusted Helper:** allowed only within grant scope; owner approval required.

## Child-Authority Contract Consistency

The contracts, evaluator, tests, and documentation agree on the following:

| Capability | Child Rule |
|------------|------------|
| `teach_me` | Allowed when child-scoped and safe |
| `guide_my_hands` | Requires owner approval for consequential guidance |
| `care` | Requires owner approval; supportive-only |
| `child_mode` | Allowed |
| `trusted_helper_access` | Denied |
| Policy weakening | Denied |

`ACTOR_PROHIBITED[Phase5Actor.CHILD]` no longer includes `Capability.CARE`.
`evaluate_phase5_request` returns `REQUIRE_APPROVAL` with
`DecisionReason.CHILD_APPROVAL_REQUIRED` for child Care requests.

## Privacy and Content Safety

- All `__repr__` methods are content-free.
- Raw user content is sanitized and bounded before storage.
- Fixed safe messages are used for Care output; dangerous or private input is
  not echoed.
- No secrets, private-memory text, or runtime identifiers are exposed in
  proposal reprs.

## Integration

Typical orchestrator usage:

```python
from core.phase5.capability_service import (
    CapabilityExecutionRequest,
    Phase5CapabilityService,
)

service = Phase5CapabilityService()
decision = service.prepare(request, authorization=runtime_decision)
```

The orchestrator is responsible for:

1. Obtaining a valid `Phase5RuntimeDecision` from `Phase5RuntimeGuard`.
2. Constructing a `CapabilityExecutionRequest` with caller-supplied, sanitized
   fields.
3. Respecting `approval_required` before rendering or acting on a proposal.
4. Never treating assistant-authored proposals as owner authority.

## Remaining Integration

- The WebSocket runtime and frontend now render proposals only after runtime
  authorization and exact correlated approval.
- Persist skill-evolution candidates only after a separate owner-review
  boundary (not in this module).
- Add proposal versioning and provenance before any Brain v2 write.

## Verification

Run focused tests:

```bash
.venv/bin/python -m pytest tests/test_phase5_contracts.py tests/test_phase5_capability_service.py tests/test_phase5_policy.py tests/test_phase5_child_mode.py tests/test_phase5_runtime_guard.py -q
```

Compile check:

```bash
.venv/bin/python -m compileall -q core/phase5/contracts.py core/phase5/capability_service.py core/phase5/__init__.py
```

Git diff check:

```bash
git diff --check -- core/phase5/contracts.py core/phase5/capability_service.py core/phase5/__init__.py tests/test_phase5_contracts.py tests/test_phase5_capability_service.py docs/PHASE_5_CONTRACTS.md docs/PHASE_5_CAPABILITY_SERVICE.md
```

## Files

| File | Purpose |
|------|---------|
| `core/phase5/capability_service.py` | Capability service contracts and dispatch |
| `core/phase5/contracts.py` | Authority constants and evaluator |
| `core/phase5/__init__.py` | Public package exports |
| `tests/test_phase5_capability_service.py` | Capability service tests |
| `tests/test_phase5_contracts.py` | Contract and authority-matrix tests |
| `docs/PHASE_5_CONTRACTS.md` | Phase 5 authority and contract docs |
| `docs/PHASE_5_CAPABILITY_SERVICE.md` | This document |
