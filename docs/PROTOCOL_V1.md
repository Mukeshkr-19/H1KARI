# HIKARI WebSocket Protocol v1

The source of truth is `protocol/hikari-v1.json`. The Python server validates
client messages from that file, and the Next.js client imports the same file for
message encoding, type allowlists, required server fields, and the protocol version.

## Compatibility

- Version `1` is the only supported version.
- The server advertises the version in `welcome` and `paired` messages.
- New clients send `protocol_version: 1` when pairing.
- A missing pairing version is treated as v1 for compatibility with existing clients.
- An explicitly unsupported version returns `protocol_error` without consuming a
  pairing attempt.
- Removing or renaming a message or field, changing its type, or tightening a limit
  incompatibly requires a new protocol file and version.

## Authorization and validation

Only `pair`, `ping`, and the bounded `pairing_prepare`, `pairing_confirm`,
`pairing_cancel`, and owner-authorized `pairing_revoke` control messages are accepted
before pairing. All other client messages require per-connection pairing. Required
fields, field types, unknown fields, and string limits are checked before protected
work reaches the orchestrator. Text and voice payloads are limited to 20,000
characters; legacy display-only device labels are limited to 64 characters.

Server and client message names and their required fields are declared in the JSON
contract. New message types must be added there first, then implemented and tested on
both sides. Public HTTP status and QR/connect pages never expose the pairing secret.

## Phase 4 pairing and handoff control contract

Phase 4 adds control-message contracts without changing the meaning of an existing
v1 message. These declarations are additive and therefore remain protocol v1. The
bounded runtime is wired alongside the legacy `pair` exchange so existing clients
remain compatible while new clients use one-use challenges.

Pairing challenges prove temporary-secret possession only. They never establish
owner identity. The server continues to derive actor and session identity from the
transport, and a paired non-loopback device remains a guest. Device IDs are opaque
display and revocation references; they are not reconnect credentials, grants, or
bearer authority.

Pairing client messages are:

- `pairing_prepare` with one canonical `request_id`.
- `pairing_confirm` with the correlated `request_id`, an opaque `challenge_id`, and
  exactly six uppercase hexadecimal characters in `code`.
- `pairing_cancel` with the correlated `request_id` and `challenge_id`.
- `pairing_revoke` with the correlated `request_id` and opaque `device_id`; the
  transport adapter must authorize revocation as a local-owner operation.

The server answers with `pairing_challenge`, `pairing_confirmed`, `pairing_update`, or
`pairing_error`. Challenge secrets are never echoed. Expiry values must be finite.
Errors contain a fixed safe code only, and every reply carries the exact `request_id`.

A handoff transfers a bounded task reference, never mobile authority. The guest sends
`handoff_prepare` with a canonical `request_id`, opaque `task_id`, and bounded frozen
summary. The server generates the `handoff_id` and returns `handoff_offer`. A local
owner may send `handoff_accept` only with `acknowledged: true`, or may reject or cancel
the offer. `handoff_status` is read-only. Acceptance must still perform fresh desktop
policy evaluation; approval IDs, grants, and execution tickets are not portable.
`handoff_update` and `handoff_error` carry correlation and status or safe codes, not
task contents or identity fields.

Visual-transfer JSON messages carry metadata and lifecycle state only:

- `visual_transfer_begin` binds one image to an accepted `handoff_id`. It allows only
  `image/png` or `image/jpeg`, at most 1,048,576 encoded bytes, dimensions from 1
  through 4096, and exactly one frame.
- `visual_transfer_ready` returns the server-generated opaque `transfer_id`.
- `visual_transfer_status`, `visual_transfer_update`, and `visual_transfer_cancel`
  correlate that exact transfer without carrying image content.
- `visual_transfer_complete` returns only a `sha256.`-prefixed lowercase digest as a
  receipt. The digest is not authorization and must not become a durable tracking ID.
- `visual_transfer_error` contains one fixed safe code.

Bytes, base64, data URLs, filenames, filesystem paths, actor/session IDs, approval
IDs, grants, execution tickets, provider details, task payloads, and raw errors are
unknown fields and fail validation. After `visual_transfer_ready`, one authenticated
WebSocket binary frame may carry the declared image on the same exact connection and
transfer scope. Image bytes never enter JSON and are removed on completion, failure,
cancel, expiry, or disconnect. This boundary performs no capture, upload, OCR,
provider call, or external execution.

## Phase 4 vision-analysis control contract

Vision analysis is an additive control-plane contract layered on the accepted
handoff and the authenticated bounded binary-transfer path. It remains protocol v1:
no existing message is renamed, retyped, or removed, and `visual_transfer_begin`
gains only one optional `analysis_id` field (backward-compatible; omitting it is
unchanged v1 behavior).

`vision_analysis_prepare` does not capture or upload. It declares intent to run one
bounded capability (`ocr` or `describe`) against an image that still travels the
authenticated bounded binary-transfer path. The current JSON protocol carries no
image bytes: `bytes`, `data`, `base64`, `data_url`, filenames, filesystem paths, and
URLs are unknown fields and fail validation. No OCR execution, provider selection,
upload, or external action occurs at prepare time.

The analysis must be bound to the same accepted handoff/session that produced the
transfer. Remote devices remain guests; desktop permissions are freshly evaluated
for every analysis and are not portable across sessions, handoffs, or transfers.
Approval IDs, grants, execution tickets, provider/model/destination fields, and
content hashes as authorization are unknown fields and fail validation.

`request_id` correlates the prepare attempt. `analysis_id` is always server-generated
and opaque; stale or mismatched IDs disclose nothing beyond a fixed safe error code.
Cancellation is terminal only after `vision_analysis_update` reports `cancelled`.
`vision_observation` is terminal. Errors contain fixed codes only — no raw
`message`, `detail`, or `stack`.

OCR and description output is user content. It must not enter logs, audits, or any
durable tracking surface. `confidence_milli` is evidence about the observation, not
authorization; it never grants, escalates, or bypasses policy. Uncertainty must be
communicated through the bounded observation and confidence fields, never silently
suppressed.

Client messages:

- `vision_analysis_prepare` carries exactly `request_id` (canonical ID, max 80),
  `handoff_id` (canonical ID, max 80), and `capability` (enum: `ocr`, `describe`).
  No optional fields.
- `vision_analysis_cancel` carries exactly `request_id` and `analysis_id` (canonical
  ID, max 80). No optional fields.
- `vision_analysis_status` carries exactly `request_id` and `analysis_id` (canonical
  ID, max 80). No optional fields.
- `visual_transfer_begin` gains one optional `analysis_id` (canonical ID, max 80)
  that binds the transfer to a prepared analysis. Omitting it is unchanged v1
  behavior and remains fully compatible.

Server messages:

- `vision_analysis_ready` carries exactly `request_id`, the server-generated
  `analysis_id` (canonical ID, max 80), and `expires_at` (finite number; booleans
  and non-finite values rejected). No optional fields.
- `vision_analysis_update` carries exactly `request_id`, `analysis_id`, and `state`
  (enum: `awaiting_image`, `analyzing`, `cancelled`, `expired`). No optional fields.
- `vision_observation` is terminal and carries exactly `request_id`, `analysis_id`,
  and `observations` (array, 1–16 items). Each observation requires exact fields
  `kind` (enum: `text`, `description`) and `text` (string, 1–2000 Unicode code
  points). OCR text may contain newline and tab; descriptions reject all controls
  and whitespace-only values. Unicode format characters are always rejected.
  `confidence_milli` is optional and, when measured by the analyzer, is an integer
  from 0 through 1000 with booleans rejected. Its absence means confidence was not
  available; callers must not fabricate a score.
- `vision_analysis_error` carries exactly `request_id` and `code` (enum:
  `invalid_request`, `analysis_not_found`, `handoff_not_accepted`,
  `transfer_mismatch`, `analysis_expired`, `analysis_cancelled`,
  `capability_unavailable`, `analysis_failed`, `unavailable`). Optional
  `analysis_id` (canonical ID, max 80) may be present; no other fields.

Actor/session/device IDs, approval/grant/execution-ticket fields,
provider/model/destination, bytes/data/base64/data_url, filename/filesystem path/URL,
task content/payload, raw error/message/detail/stack, OCR/image contents in request
messages, content hashes as authorization, unknown fields, non-finite numbers,
invalid IDs, oversized arrays/text, invalid confidence, and wrong states/codes all
fail validation.

## Document task flow

The Phase 1 document flow is additive to the existing v1 chat protocol:

1. `document_prepare` selects one UTF-8 text file and an ordered provider choice. The
   server does not read the file yet.
2. `document_confirmation_required` returns the immutable path/provider snapshot that
   the client must show before confirmation.
3. `document_confirm` authorizes that exact snapshot. The server reads the file through
   a one-use grant and grants each provider attempt separately immediately before egress.
4. `document_follow_up` creates a child task from the bounded prior explanation; it does
   not reread the file.
5. `task_status` reconnects to durable state and `document_cancel` cancels nonterminal
   work. Confirmation and follow-up work run in background jobs so status and cancel
   remain usable on the same connection.

Every document result carries `task_id` and `root_task_id`. Clients must correlate both,
ignore stale or malformed messages, and never replace the displayed confirmation
snapshot with editable form state. One paired transport is not proof of owner identity;
the loopback-only server derives the local-owner actor context rather than accepting an
actor supplied by the client.

## Phase 3 productivity vertical slice

The productivity flow is a bounded, scoped approval contract. Its confirmation is
preview-only and exact-snapshot bound: the client never sends executable proposal
content, approval IDs, actor IDs, or session IDs. Implemented adapters execute only
after server-side authorization consumption.

Proposal IDs use the canonical pattern `^[a-z0-9][a-z0-9_.-]{0,79}$`: lowercase
alphanumeric first character, then lowercase alphanumeric, underscore, dot, or hyphen,
up to 80 characters. Uppercase letters and colons are rejected.

Client messages:

- `productivity_email_draft_prepare` carries exactly `request_id`, `recipient`
  (1–320 characters), `subject` (0–998), and `body` (0–20,000). Control and
  Unicode format characters are rejected; body alone may contain newline and tab.
  The server creates the proposal ID and retains the full draft only in a
  bounded, actor/session-scoped in-memory registry.
- `productivity_calendar_read_prepare` carries exactly `request_id`, `start`,
  and `end` (explicit ISO 8601 date-times with required offset or `Z`). Optional
  `calendar_name` (1–200 characters) may be supplied. Control and Unicode format
  characters are rejected. The server creates the proposal ID and retains the
  read input only in a bounded, actor/session-scoped in-memory registry.
- `productivity_calendar_draft_prepare` carries exactly `request_id`, `title`
  (1–500), `start`, `end` (same date-time contract as read prepare), and
  required `calendar_name` (1–200 characters). Optional `location` (1–500) and
  `notes` (1–4000) may be supplied; notes and location alone may contain newline
  and tab. Control and Unicode format characters are rejected. The confirmed
  destination is the exact `calendar_name` target shown in the proposal preview.
  Aware ISO date-times keep microsecond precision through validation; macOS
  Calendar/Reminders AppleScript execution uses second-level date precision when
  the platform cannot represent fractional seconds.
  The server creates the proposal ID and retains the draft only in a bounded,
  actor/session-scoped in-memory registry.
- `productivity_research_prepare` carries exactly `request_id` and `query`
  (1–2000 Unicode code points; whitespace-only queries are rejected). Optional
  `domains` is an array of at most 16 domain strings (each 1–253 code points,
  duplicate-free on the wire). Optional `max_results` is an integer from 1
  through 20; when omitted the server applies its default. Control and Unicode
  format characters are rejected. The server canonicalizes domains with IDNA and
  rejects IP literals, single-label hosts, and malformed hosts. The server
  creates the proposal ID and retains the research input only in a bounded,
  actor/session-scoped in-memory registry.
- `productivity_reminder_prepare` carries exactly `request_id`, `title`
  (1–500 Unicode code points; whitespace-only titles, including Unicode
  whitespace, are rejected), and `remind_at` (an explicit ISO 8601 date-time
  with a required `Z` or numeric timezone offset; naive date-times are rejected
  without inventing a timezone). Optional `notes` (0–4000 Unicode code points;
  newline and tab allowed) and `list_name` (1–200 Unicode code points; explicitly
  empty list names are rejected) may be supplied. Control and Unicode format
  characters are rejected. The server
  creates the proposal ID and retains the reminder input only in a bounded,
  actor/session-scoped in-memory registry. The server-generated proposal ID is
  not exposed until preparation succeeds.
- `productivity_confirm` requires `proposal_id` (canonical ID, max 80) and `scope`.
  `once` and `session` allow no additional fields. `duration` requires
  `duration_seconds` equal to `900`, `3600`, or `28800`. `precise_persistent` requires
  `acknowledged: true`. Fields belonging to another scope are rejected.
- `productivity_cancel` requires `proposal_id` (canonical ID, max 80).
- `productivity_status` requires `proposal_id` (canonical ID, max 80).

Server messages:

- `productivity_confirmation_required` carries the preview-only snapshot the client must
  show before confirmation: `proposal_id` (canonical ID), `action` (enum:
  `browser.research`, `email.draft`, `calendar.read`, `calendar.draft`,
  `reminder.create`, `scheduled_job.manage`, `skill.execute`, `mcp.execute`),
  `heading` (string, max 120), `risk_label` (string, max 120), `targets` and
  `payload` (arrays, each with max 32 entries; each entry has exact keys `label`
  (max 120) and `value` (max 2000), plus optional boolean `truncated`), `expires_at` (finite
  number; booleans and non-finite values rejected), and `allowed_scopes` (a non-empty,
  duplicate-free subset of `once`, `session`, `duration`, and `precise_persistent`).
  The `payload` is a preview only; it is not the authorized work.
- `productivity_update` carries `proposal_id` (canonical ID) and `status` (enum:
  `preview`, `confirming`, `approved`, `executing`, `completed`, `failed`, `cancelling`,
  `cancelled`).
- `productivity_error` carries `proposal_id` (canonical ID) and `code` (enum:
  `confirm_failed`, `cancel_failed`, `proposal_expired`, `proposal_invalid`,
  `unavailable`) only. It has no `message`, `detail`, `provider`, `stack`, or unknown
  fields; clients map `code` to local wording.
- `productivity_research_result` is a terminal research delivery: `proposal_id`
  (canonical ID) and `items` (array, max 20). Each item has exact keys `title`
  (1–500), HTTPS `url` (1–2048), canonical lowercase `domain` (1–253), and optional
  `snippet` (1–2000; newline/tab allowed). Unsafe URLs, credentials, fragments,
  actor/session/approval fields, provider payloads, and unknown keys are rejected.
- `productivity_calendar_result` is a terminal calendar-read delivery: `proposal_id`
  (canonical ID) and `events` (array, max 100). Each event has exact keys `title`
  (1–500), aware ISO `start`/`end`, `calendar` label (1–200), and optional
  `location` (1–500; newline/tab allowed). Naive datetimes, non-finite values,
  actor/session/approval fields, and unknown keys are rejected.

Confirmation is exact-ID and frozen-snapshot bound: the server matches the confirmation
to the same proposal it presented and generates the approval ID internally. Session,
duration, and precise-persistent approvals remain bound to the exact action,
destinations, and snapshot; they do not authorize broader content. Proposal content
shown to the user is a preview, not an executable instruction, and the server validates
every client and server payload before it is sent or acted on.

## Phase 3 scheduled jobs

Scheduled-job control is an actor-scoped status and control surface. The client
may create a one-shot read job with `scheduled_job_create`, request
`scheduled_jobs_list` with no additional fields, or send
`scheduled_job_pause`, `scheduled_job_resume`, or `scheduled_job_cancel` with
one canonical `job_id`. Job IDs use the same lowercase 80-character contract
as proposal IDs. Actor, session, proposal, payload, and provider fields are not
accepted from the client.

`scheduled_job_create` requires a canonical `request_id`, the exact active
`proposal_id`, an aware ISO 8601 `next_run_at`, and `max_attempts` from 1 through
5. Optional quiet hours require exact integer `start_minute` and `end_minute`
values from 0 through 1439 plus a bounded IANA timezone. Only frozen
`browser.research` and `calendar.read` previews can be scheduled. The server
derives the stable local-installation owner scope, retains the matching prepared
read input in private local state, and generates the job ID. Write actions cannot
be scheduled.

The server returns `scheduled_jobs` with at most 64 jobs,
`scheduled_job_update` with one job, or `scheduled_job_error` with a canonical
`job_id` and one safe code: `control_failed`, `job_not_found`, or `unavailable`.
An error never carries raw exception text or provider details.
Creation updates and errors may echo the canonical `request_id` so the client
can clear only the matching pending form. Completed read jobs deliver either
`scheduled_job_research_result` or `scheduled_job_calendar_result`; these use
the same bounded item/event contracts as their immediate productivity-result
counterparts and correlate by server-generated `job_id`.

Each job has exactly `job_id`, `action`, `state`, `next_run_at`,
`attempt_count`, and `max_attempts`, plus optional `quiet_hours_label`. Actions
are bounded opaque identifiers. State is one of `scheduled`, `paused`,
`running`, `interrupted`, `completed`, `failed`, or `cancelled`.
`next_run_at` is a bounded timezone-aware ISO 8601 timestamp. Attempt values
are integers in the range 0 through 100, `max_attempts` is at least 1, and
`attempt_count` cannot exceed `max_attempts`. Unknown or privacy-sensitive
fields are rejected before a message is accepted or sent.

Scheduled ownership is stable across reconnects for a paired loopback owner on
one HIKARI installation. It is not a portable multi-device identity. Guests are
never rebound to that scope. Startup recovers interrupted reads through audited
compare-and-swap transitions. Delivery uses job-ID correlation and durable
meaningful-change fingerprints; transport acceptance is not represented as an
end-user reading acknowledgement.
