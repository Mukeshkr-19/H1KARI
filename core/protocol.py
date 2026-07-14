"""Versioned WebSocket protocol metadata and client-message validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROTOCOL_PATH = Path(__file__).resolve().parent.parent / "protocol" / "hikari-v1.json"
PROTOCOL_SCHEMA = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
PROTOCOL_VERSION = int(PROTOCOL_SCHEMA["version"])
CLIENT_MESSAGES = PROTOCOL_SCHEMA["client_to_server"]
SERVER_MESSAGES = PROTOCOL_SCHEMA["server_to_client"]


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    return False


def validate_client_message(message: dict) -> str | None:
    """Return a stable validation error, or None when a v1 message is valid."""
    message_type = message.get("type")
    if not isinstance(message_type, str) or message_type not in CLIENT_MESSAGES:
        return "Unknown message type"

    spec = CLIENT_MESSAGES[message_type]
    required = spec["required"]
    optional = spec["optional"]
    fields = set(message) - {"type"}
    missing = set(required) - fields
    if missing:
        return f"Missing required field: {sorted(missing)[0]}"
    at_least_one = spec.get("at_least_one", [])
    if at_least_one and not fields.intersection(at_least_one):
        return "Missing required field: " + " or ".join(at_least_one)

    unknown = fields - set(required) - set(optional)
    if unknown:
        return f"Unknown field: {sorted(unknown)[0]}"

    for field, field_spec in {**required, **optional}.items():
        if field not in message:
            continue
        value = message[field]
        if not _matches_type(value, field_spec["type"]):
            return f"Invalid field type: {field}"
        max_length = field_spec.get("max_length")
        if max_length is not None and len(value) > int(max_length):
            return f"Field too long: {field}"
    return None
