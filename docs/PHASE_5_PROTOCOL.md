# Phase 5 Protocol

## Purpose

Phase 5 WebSocket frames carry session lifecycle, capability proposal, approval,
and trusted-helper grant control. They do not execute tools, write Brain data,
capture camera frames, or contact emergency services.

Identity and authority are derived only from the server-owned pairing and
connection boundary. Client fields such as `actor_id`, `owner_id`, `role`,
`grant_id` claims, or free-form approval text cannot elevate authority.

## Protocol version

Every Phase 5 client and server frame includes `protocol_version: 1`.

## Client → server

| Type | Purpose |
|------|---------|
| `phase5_session_activate` | Activate owner, child, or trusted-helper session |
| `phase5_session_status` | Fetch privacy-safe session snapshot |
| `phase5_session_close` | Close a session |
| `phase5_session_lock` | Lock a session |
| `phase5_session_revoke` | Revoke a session |
| `phase5_capability_prepare` | Authorize then prepare a Teach Me / Guide / Care proposal |
| `phase5_capability_confirm` | Exact correlated owner approval for a pending request |
| `phase5_helper_grant_create` | Owner creates an expiring helper grant |
| `phase5_helper_grant_list` | Owner lists helper grants |
| `phase5_helper_grant_revoke` | Owner revokes a grant and invalidates helper sessions |

## Server → client

| Type | Purpose |
|------|---------|
| `phase5_session_update` | Privacy-safe session state |
| `phase5_capability_proposal` | Bounded proposal preview (no execution) |
| `phase5_approval_required` | Correlated pending approval |
| `phase5_helper_grants` | Bounded grant list |
| `phase5_error` | Fixed safe error code only |

## Validation rules

- Strict allowlisted keys; unknown fields rejected
- Canonical identifiers (`^[a-z0-9][a-z0-9_.-]{0,79}$`)
- Bounded arrays and text
- Finite timestamps
- Request/response correlation via `request_id`
- No raw evidence, private memory, secrets, or exception text
- No generic execute-tool message
- Parsing must not mutate inputs
- Duplicate or stale confirmations denied (`stale_request` / `duplicate_request`)
- Pending approvals expire after five minutes and are capped at 32 per connection
- Confirmed consent is scoped to the exact capability, action, resource, and data subject

## Safe error codes

`invalid_request`, `unauthorized`, `unavailable`, `denied`, `expired`,
`revoked`, `locked`, `closed`, `approval_required`, `not_found`,
`stale_request`, `duplicate_request`, `internal_error`

## Authority flow

```
transport frame
→ strict protocol validation
→ ActorContext from pairing/connection state
→ Phase5RuntimeService authorization
→ Phase5CapabilityService proposal (only after allow / correlated approval)
→ safe protocol response
→ accessible frontend state
```

Guest and unpaired clients receive `unauthorized`. Missing runtime or capability
services return `unavailable` (fail closed; no permissive stubs).

The current WebSocket surface is the owner control plane. Owner activation,
status, proposal approval, and helper-grant management are production-wired.
Child/helper sessions can be created by the owner, but a child/helper device is
not allowed to claim that identity from a client frame. End-to-end use from
those devices requires a future server-owned authenticated device-to-session
binding.
