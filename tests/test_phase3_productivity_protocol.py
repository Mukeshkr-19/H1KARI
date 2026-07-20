"""Phase 3 productivity WebSocket protocol contract tests.

These tests pin the bounded v1 productivity messages and the generic
validation extensions (string enum, string regex pattern, array limits,
nested entry shape, finite-number rejection, boolean never passes as number,
unknown-field rejection, and server-message validation). They use the shared
contract in ``protocol/hikari-v1.json`` only.
"""

from __future__ import annotations

import math

import pytest

from core.protocol import (
    PROTOCOL_VERSION,
    SERVER_MESSAGES,
    validate_client_message,
    validate_server_message,
)


PROPOSAL_ID = "prop-1"
VALID_ACTION = "email.draft"


def _confirmation_required(**over) -> dict:
    base = {
        "type": "productivity_confirmation_required",
        "proposal_id": PROPOSAL_ID,
        "action": VALID_ACTION,
        "heading": "Daily digest",
        "risk_label": "low",
        "targets": [{"label": "Destination", "value": "inbox"}],
        "payload": [{"label": "summary", "value": "preview text"}],
        "expires_at": 1752831000,
        "allowed_scopes": ["once"],
    }
    base.update(over)
    return base


def test_productivity_confirm_requires_canonical_identifier_and_scoped_fields():
    # Valid.
    assert (
        validate_client_message(
            {"type": "productivity_confirm", "proposal_id": PROPOSAL_ID, "scope": "once"}
        )
        is None
    )
    # Missing scope.
    assert (
        validate_client_message({"type": "productivity_confirm", "proposal_id": PROPOSAL_ID})
        == "Missing required field: scope"
    )
    # Invalid scope enum value.
    assert (
        validate_client_message(
            {"type": "productivity_confirm", "proposal_id": PROPOSAL_ID, "scope": "repeat"}
        )
        == "Invalid field value: scope"
    )
    # Uppercase ID rejected by canonical pattern.
    assert (
        validate_client_message(
            {"type": "productivity_confirm", "proposal_id": "Prop-1", "scope": "once"}
        )
        == "Invalid field value: proposal_id"
    )
    # Colon rejected by canonical pattern.
    assert (
        validate_client_message(
            {"type": "productivity_confirm", "proposal_id": "prop:1", "scope": "once"}
        )
        == "Invalid field value: proposal_id"
    )
    # Invalid identifier characters.
    assert (
        validate_client_message(
            {"type": "productivity_confirm", "proposal_id": "bad id!", "scope": "once"}
        )
        == "Invalid field value: proposal_id"
    )
    # Over-length proposal_id.
    assert (
        validate_client_message(
            {"type": "productivity_confirm", "proposal_id": "x" * 81, "scope": "once"}
        )
        == "Field too long: proposal_id"
    )
    # Non-string proposal_id.
    assert (
        validate_client_message(
            {"type": "productivity_confirm", "proposal_id": 7, "scope": "once"}
        )
        == "Invalid field type: proposal_id"
    )
    # Unknown field.
    assert (
        validate_client_message(
            {
                "type": "productivity_confirm",
                "proposal_id": PROPOSAL_ID,
                "scope": "once",
                "extra": True,
            }
        )
        == "Unknown field: extra"
    )
    assert validate_client_message(
        {"type": "productivity_confirm", "proposal_id": PROPOSAL_ID, "scope": "session"}
    ) is None
    assert validate_client_message(
        {
            "type": "productivity_confirm",
            "proposal_id": PROPOSAL_ID,
            "scope": "duration",
            "duration_seconds": 900,
        }
    ) is None
    assert validate_client_message(
        {
            "type": "productivity_confirm",
            "proposal_id": PROPOSAL_ID,
            "scope": "precise_persistent",
            "acknowledged": True,
        }
    ) is None


@pytest.mark.parametrize(
    "message,expected",
    [
        (
            {"type": "productivity_confirm", "proposal_id": PROPOSAL_ID, "scope": "duration"},
            "Missing required field: duration_seconds",
        ),
        (
            {"type": "productivity_confirm", "proposal_id": PROPOSAL_ID, "scope": "duration", "duration_seconds": 100},
            "Invalid field value: duration_seconds",
        ),
        (
            {"type": "productivity_confirm", "proposal_id": PROPOSAL_ID, "scope": "session", "duration_seconds": 900},
            "Invalid field value: duration_seconds",
        ),
        (
            {"type": "productivity_confirm", "proposal_id": PROPOSAL_ID, "scope": "precise_persistent"},
            "Missing required field: acknowledged",
        ),
        (
            {"type": "productivity_confirm", "proposal_id": PROPOSAL_ID, "scope": "precise_persistent", "acknowledged": False},
            "Invalid field value: acknowledged",
        ),
        (
            {"type": "productivity_confirm", "proposal_id": PROPOSAL_ID, "scope": "once", "acknowledged": True},
            "Invalid field value: acknowledged",
        ),
    ],
)
def test_productivity_confirm_rejects_mismatched_scope_fields(message, expected):
    assert validate_client_message(message) == expected


def test_productivity_cancel_requires_canonical_identifier():
    assert (
        validate_client_message({"type": "productivity_cancel", "proposal_id": PROPOSAL_ID})
        is None
    )
    assert (
        validate_client_message({"type": "productivity_cancel", "proposal_id": "Prop-1"})
        == "Invalid field value: proposal_id"
    )
    assert (
        validate_client_message({"type": "productivity_cancel", "proposal_id": "bad id!"})
        == "Invalid field value: proposal_id"
    )
    assert (
        validate_client_message({"type": "productivity_cancel"})
        == "Missing required field: proposal_id"
    )


def test_productivity_status_requires_canonical_identifier():
    assert (
        validate_client_message({"type": "productivity_status", "proposal_id": PROPOSAL_ID})
        is None
    )
    assert (
        validate_client_message({"type": "productivity_status", "proposal_id": "Prop-1"})
        == "Invalid field value: proposal_id"
    )
    assert (
        validate_client_message({"type": "productivity_status", "proposal_id": "bad id!"})
        == "Invalid field value: proposal_id"
    )
    assert (
        validate_client_message({"type": "productivity_status"})
        == "Missing required field: proposal_id"
    )


def test_productivity_confirmation_required_server_fields():
    # Valid canonical payload.
    assert validate_server_message(_confirmation_required()) is None
    # Missing required field.
    partial = _confirmation_required()
    del partial["allowed_scopes"]
    assert (
        validate_server_message(partial) == "Missing required field: allowed_scopes"
    )


def test_productivity_confirmation_required_rejects_invalid_action():
    assert (
        validate_server_message(_confirmation_required(action="summarize"))
        == "Invalid field value: action"
    )
    assert (
        validate_server_message(_confirmation_required(action="email.draft"))
        is None
    )


def test_productivity_confirmation_required_rejects_object_payload():
    # Payload must be an array, not an object.
    assert (
        validate_server_message(
            _confirmation_required(payload={"preview": "..."})
        )
        == "Invalid field type: payload"
    )


def test_productivity_confirmation_required_rejects_string_timestamp():
    # expires_at must be a finite number, not a string.
    assert (
        validate_server_message(
            _confirmation_required(expires_at="2026-07-18T09:30:00Z")
        )
        == "Invalid field type: expires_at"
    )


def test_productivity_confirmation_required_rejects_nan_and_infinity():
    assert (
        validate_server_message(_confirmation_required(expires_at=math.nan))
        == "Invalid field value: expires_at"
    )
    assert (
        validate_server_message(_confirmation_required(expires_at=math.inf))
        == "Invalid field value: expires_at"
    )


def test_productivity_confirmation_required_rejects_boolean_as_number():
    assert (
        validate_server_message(_confirmation_required(expires_at=True))
        == "Invalid field type: expires_at"
    )


def test_productivity_confirmation_required_rejects_oversized_arrays():
    assert (
        validate_server_message(
            _confirmation_required(payload=[{"label": "l", "value": "v"}] * 33)
        )
        == "Array too long: payload"
    )
    assert (
        validate_server_message(
            _confirmation_required(
                targets=[{"label": "Destination", "value": "inbox"}] * 33
            )
        )
        == "Array too long: targets"
    )


def test_productivity_confirmation_required_rejects_malformed_entries():
    # Targets and payload use the same bounded preview-entry shape.
    assert (
        validate_server_message(_confirmation_required(targets=["inbox"]))
        == "Invalid field type: targets[0]"
    )
    assert (
        validate_server_message(
            _confirmation_required(targets=[{"label": "Destination"}])
        )
        == "Missing required field: targets[0].value"
    )
    # Missing required entry key.
    assert (
        validate_server_message(
            _confirmation_required(payload=[{"value": "v"}])
        )
        == "Missing required field: payload[0].label"
    )
    # Unknown entry key.
    assert (
        validate_server_message(
            _confirmation_required(
                payload=[{"label": "l", "value": "v", "extra": True}]
            )
        )
        == "Unknown field: payload[0].extra"
    )
    # Non-boolean truncated.
    assert (
        validate_server_message(
            _confirmation_required(
                payload=[{"label": "l", "value": "v", "truncated": "yes"}]
            )
        )
        == "Invalid field type: payload[0].truncated"
    )


def test_productivity_confirmation_required_rejects_empty_scopes():
    assert (
        validate_server_message(_confirmation_required(allowed_scopes=[]))
        == "Invalid field value: allowed_scopes"
    )
    assert (
        validate_server_message(_confirmation_required(allowed_scopes=["repeat"]))
        == "Invalid field value: allowed_scopes[0]"
    )
    assert validate_server_message(
        _confirmation_required(
            allowed_scopes=["once", "session", "duration", "precise_persistent"]
        )
    ) is None
    assert (
        validate_server_message(_confirmation_required(allowed_scopes=["once", "once"]))
        == "Invalid field value: allowed_scopes"
    )


def test_productivity_update_server_fields():
    assert (
        validate_server_message(
            {"type": "productivity_update", "proposal_id": PROPOSAL_ID, "status": "approved"}
        )
        is None
    )
    assert (
        validate_server_message({"type": "productivity_update", "proposal_id": PROPOSAL_ID})
        == "Missing required field: status"
    )
    assert (
        validate_server_message(
            {"type": "productivity_update", "proposal_id": PROPOSAL_ID, "status": "bogus"}
        )
        == "Invalid field value: status"
    )


def test_productivity_error_has_no_extra_fields():
    # Valid: only proposal_id and code from the allowed enum.
    assert (
        validate_server_message(
            {"type": "productivity_error", "proposal_id": PROPOSAL_ID, "code": "unavailable"}
        )
        is None
    )
    # The contract forbids an arbitrary message field.
    assert (
        validate_server_message(
            {
                "type": "productivity_error",
                "proposal_id": PROPOSAL_ID,
                "code": "unavailable",
                "message": "boom",
            }
        )
        == "Unknown field: message"
    )
    # Code must be from the allowed enum.
    assert (
        validate_server_message(
            {"type": "productivity_error", "proposal_id": PROPOSAL_ID, "code": "not_found"}
        )
        == "Invalid field value: code"
    )


def test_validate_server_message_rejects_unknown_type():
    assert validate_server_message({"type": "nope"}) == "Unknown message type"


def test_boolean_never_passes_as_integer():
    # protocol_version is an integer field; a boolean must be rejected.
    assert (
        validate_client_message(
            {"type": "pair", "code": "ABC123", "protocol_version": True}
        )
        == "Invalid field type: protocol_version"
    )
    assert (
        validate_client_message({"type": "pair", "code": "ABC123", "protocol_version": 1})
        is None
    )


def test_enum_rejection_is_stable():
    assert (
        validate_client_message(
            {"type": "productivity_confirm", "proposal_id": PROPOSAL_ID, "scope": 1}
        )
        == "Invalid field type: scope"
    )


def test_pattern_rejection_is_stable():
    assert (
        validate_client_message(
            {"type": "productivity_confirm", "proposal_id": "", "scope": "once"}
        )
        == "Invalid field value: proposal_id"
    )


def test_productivity_messages_declared_in_contract():
    assert "productivity_confirm" in validate_client_message.__globals__["CLIENT_MESSAGES"]
    assert "productivity_cancel" in validate_client_message.__globals__["CLIENT_MESSAGES"]
    assert "productivity_status" in validate_client_message.__globals__["CLIENT_MESSAGES"]
    assert "productivity_confirmation_required" in SERVER_MESSAGES
    assert "productivity_update" in SERVER_MESSAGES
    assert "productivity_error" in SERVER_MESSAGES
    assert "productivity_research_result" in SERVER_MESSAGES
    assert "productivity_calendar_result" in SERVER_MESSAGES
    assert PROTOCOL_VERSION == 1


def test_productivity_research_result_bounds_and_exact_keys():
    valid = {
        "type": "productivity_research_result",
        "proposal_id": PROPOSAL_ID,
        "items": [
            {
                "title": "Hello",
                "url": "https://example.com/path",
                "domain": "example.com",
                "snippet": "A line\nwith tab\there",
            }
        ],
    }
    assert validate_server_message(valid) is None
    assert (
        validate_server_message(
            {
                **valid,
                "items": [
                    {
                        "title": "Hello",
                        "url": "http://example.com/path",
                        "domain": "example.com",
                    }
                ],
            }
        )
        == "Invalid field value: items[0].url"
    )
    assert (
        validate_server_message({**valid, "query": "secret"})
        == "Unknown field: query"
    )
    assert (
        validate_server_message({**valid, "actor_id": "owner"})
        == "Unknown field: actor_id"
    )
    assert (
        validate_server_message(
            {
                "type": "productivity_research_result",
                "proposal_id": PROPOSAL_ID,
                "items": [
                    {
                        "title": "Hello",
                        "url": "https://example.com/path",
                        "domain": "example.com",
                        "provider": "bing",
                    }
                ],
            }
        )
        == "Unknown field: items[0].provider"
    )
    oversized = [
        {
            "title": f"Item {index}",
            "url": f"https://example.com/{index}",
            "domain": "example.com",
        }
        for index in range(21)
    ]
    assert (
        validate_server_message(
            {
                "type": "productivity_research_result",
                "proposal_id": PROPOSAL_ID,
                "items": oversized,
            }
        )
        == "Array too long: items"
    )


def test_productivity_calendar_result_bounds_and_exact_keys():
    valid = {
        "type": "productivity_calendar_result",
        "proposal_id": PROPOSAL_ID,
        "events": [
            {
                "title": "Meet",
                "start": "2026-07-20T13:00:00Z",
                "end": "2026-07-20T14:00:00+00:00",
                "calendar": "Work",
                "location": "Room 1",
            }
        ],
    }
    assert validate_server_message(valid) is None
    assert (
        validate_server_message(
            {
                **valid,
                "events": [
                    {
                        "title": "Meet",
                        "start": "2026-07-20T13:00:00",
                        "end": "2026-07-20T14:00:00Z",
                        "calendar": "Work",
                    }
                ],
            }
        )
        == "Invalid field value: events[0].start"
    )
    assert (
        validate_server_message({**valid, "session_id": "session"})
        == "Unknown field: session_id"
    )
    assert (
        validate_server_message(
            {
                "type": "productivity_calendar_result",
                "proposal_id": PROPOSAL_ID,
                "events": [
                    {
                        "title": "Meet",
                        "start": "2026-07-20T13:00:00Z",
                        "end": "2026-07-20T14:00:00Z",
                        "calendar": "Work",
                        "error": "boom",
                    }
                ],
            }
        )
        == "Unknown field: events[0].error"
    )
    oversized = [
        {
            "title": f"Event {index}",
            "start": "2026-07-20T13:00:00Z",
            "end": "2026-07-20T14:00:00Z",
            "calendar": "Work",
        }
        for index in range(101)
    ]
    assert (
        validate_server_message(
            {
                "type": "productivity_calendar_result",
                "proposal_id": PROPOSAL_ID,
                "events": oversized,
            }
        )
        == "Array too long: events"
    )
