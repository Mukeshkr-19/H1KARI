"""Versioned WebSocket protocol metadata and message validation.

Client and server messages are validated against the bounded contract in
``protocol/hikari-v1.json``. Validation is intentionally small and dependency
free: it supports bounded strings, numbers, integers, arrays, and nested
objects, rejects unknown fields, and never treats a boolean as an integer.
"""

from __future__ import annotations

import json
import re
import unicodedata
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
    if expected == "array":
        return isinstance(value, list)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) != float("inf")


def _validate_field_value(field: str, value: Any, field_spec: dict) -> str | None:
    expected = field_spec["type"]
    if not _matches_type(value, expected):
        return f"Invalid field type: {field}"
    if "equals" in field_spec and value != field_spec["equals"]:
        return f"Invalid field value: {field}"
    if expected == "string":
        min_length = field_spec.get("min_length")
        if min_length is not None and len(value) < int(min_length):
            return f"Field too short: {field}"
        max_length = field_spec.get("max_length")
        if max_length is not None and len(value) > int(max_length):
            return f"Field too long: {field}"
        enum = field_spec.get("enum")
        if enum is not None and value not in enum:
            return f"Invalid field value: {field}"
        pattern = field_spec.get("pattern")
        if pattern is not None and not re.fullmatch(pattern, value):
            return f"Invalid field value: {field}"
        if field_spec.get("forbid_controls"):
            allow_newline_tab = bool(field_spec.get("allow_newline_tab"))
            for char in value:
                if allow_newline_tab and char in "\n\t":
                    continue
                if ord(char) < 32 or ord(char) == 127:
                    return f"Invalid field value: {field}"
        if field_spec.get("forbid_unicode_format") and any(
            unicodedata.category(char) == "Cf" for char in value
        ):
            return f"Invalid field value: {field}"
        if field_spec.get("not_whitespace_only") and value.strip() == "":
            return f"Invalid field value: {field}"
    elif expected == "integer":
        enum = field_spec.get("enum")
        if enum is not None and value not in enum:
            return f"Invalid field value: {field}"
        minimum = field_spec.get("minimum")
        if minimum is not None and value < int(minimum):
            return f"Invalid field value: {field}"
        maximum = field_spec.get("maximum")
        if maximum is not None and value > int(maximum):
            return f"Invalid field value: {field}"
    elif expected == "number":
        if field_spec.get("finite") and not _is_finite_number(value):
            return f"Invalid field value: {field}"
    elif expected == "array":
        min_items = field_spec.get("min_items")
        if min_items is not None and len(value) < int(min_items):
            return f"Invalid field value: {field}"
        max_items = field_spec.get("max_items")
        if max_items is not None and len(value) > int(max_items):
            return f"Array too long: {field}"
        exact = field_spec.get("exact")
        if exact is not None and value != exact:
            return f"Invalid field value: {field}"
        if field_spec.get("unique"):
            try:
                if len(value) != len(set(value)):
                    return f"Invalid field value: {field}"
            except TypeError:
                return f"Invalid field value: {field}"
        item_spec = field_spec.get("items")
        if item_spec is not None:
            for index, item in enumerate(value):
                error = _validate_typed_value(f"{field}[{index}]", item, item_spec)
                if error is not None:
                    return error
    elif expected == "object":
        error = _validate_object_value(field, value, field_spec)
        if error is not None:
            return error
    return None


def _validate_object_value(field: str, value: dict, field_spec: dict) -> str | None:
    required = field_spec.get("required", {})
    optional = field_spec.get("optional", {})
    fields = set(value)
    missing = set(required) - fields
    if missing:
        return f"Missing required field: {field}.{sorted(missing)[0]}"
    if field_spec.get("exact_keys"):
        unknown = fields - set(required) - set(optional)
        if unknown:
            return f"Unknown field: {field}.{sorted(unknown)[0]}"
    for sub, sub_spec in {**required, **optional}.items():
        if sub not in value:
            continue
        error = _validate_field_value(f"{field}.{sub}", value[sub], sub_spec)
        if error is not None:
            return error
    for lower_field, upper_field in field_spec.get("field_lte", []):
        if (
            lower_field in value
            and upper_field in value
            and value[lower_field] > value[upper_field]
        ):
            return f"Invalid field value: {field}.{lower_field}"
    return None


def _validate_typed_value(field: str, value: Any, field_spec: dict) -> str | None:
    return _validate_field_value(field, value, field_spec)


def _validate_message(message: dict, spec: dict) -> str | None:
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
        error = _validate_field_value(field, message[field], field_spec)
        if error is not None:
            return error

    variants = spec.get("variants")
    if variants:
        discriminator = variants.get("field")
        cases = variants.get("cases", {})
        case = cases.get(message.get(discriminator))
        if not isinstance(case, dict):
            return f"Invalid field value: {discriminator}"
        for required_field in case.get("required", []):
            if required_field not in message:
                return f"Missing required field: {required_field}"
        for forbidden_field in case.get("forbidden", []):
            if forbidden_field in message:
                return f"Invalid field value: {forbidden_field}"
        for exact_field, exact_value in case.get("equals", {}).items():
            if message.get(exact_field) != exact_value:
                return f"Invalid field value: {exact_field}"
    return None


def validate_client_message(message: dict) -> str | None:
    """Return a stable validation error, or None when a v1 client message is valid."""
    message_type = message.get("type")
    if not isinstance(message_type, str) or message_type not in CLIENT_MESSAGES:
        return "Unknown message type"
    return _validate_message(message, CLIENT_MESSAGES[message_type])


def validate_server_message(message: dict) -> str | None:
    """Return a stable validation error, or None when a v1 server message is valid.

    Server payloads are checked against the contract before they are sent, so a
    malformed outbound message is caught locally rather than delivered to the
    client. Legacy server messages use a field-list spec (required field names
    only); productivity messages use a typed spec with the same shape as client
    messages. Unknown fields are rejected so a server cannot leak an
    undocumented field (for example an arbitrary ``message`` on
    ``productivity_error``).
    """
    message_type = message.get("type")
    if not isinstance(message_type, str) or message_type not in SERVER_MESSAGES:
        return "Unknown message type"
    spec = SERVER_MESSAGES[message_type]
    fields = set(message) - {"type"}

    if isinstance(spec, list):
        required = set(spec)
        missing = required - fields
        if missing:
            return f"Missing required field: {sorted(missing)[0]}"
        unknown = fields - required
        if unknown:
            return f"Unknown field: {sorted(unknown)[0]}"
        return None

    return _validate_message(message, spec)
