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

Only `pair` and `ping` are accepted before pairing. All other client messages require
the per-connection authorization established by `pair`. Required fields, field types,
unknown fields, and string limits are checked before protected work reaches the
orchestrator. Text and voice payloads are limited to 20,000 characters; device labels
are limited to 64 characters.

Server and client message names and their required fields are declared in the JSON
contract. New message types must be added there first, then implemented and tested on
both sides. Public HTTP status and QR/connect pages never expose the pairing secret.

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
