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
