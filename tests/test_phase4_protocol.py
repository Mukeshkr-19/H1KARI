"""Bounded control-plane contracts for Phase 4 pairing and handoff."""

from __future__ import annotations

import math

import pytest

from core.protocol import (
    PROTOCOL_VERSION,
    validate_client_message,
    validate_server_message,
)


REQUEST_ID = "request-1"
CHALLENGE_ID = "challenge-1"
DEVICE_ID = "device:1"
HANDOFF_ID = "handoff-1"
TASK_ID = "task:1"
TRANSFER_ID = "transfer:1"


VALID_CLIENT_MESSAGES = (
    {"type": "pairing_prepare", "request_id": REQUEST_ID},
    {
        "type": "pairing_confirm",
        "request_id": REQUEST_ID,
        "challenge_id": CHALLENGE_ID,
        "code": "01A2FF",
    },
    {
        "type": "pairing_cancel",
        "request_id": REQUEST_ID,
        "challenge_id": CHALLENGE_ID,
    },
    {
        "type": "pairing_revoke",
        "request_id": REQUEST_ID,
        "device_id": DEVICE_ID,
    },
    {
        "type": "handoff_prepare",
        "request_id": REQUEST_ID,
        "task_id": TASK_ID,
        "summary": "Review the bounded task on desktop.",
    },
    {
        "type": "handoff_accept",
        "request_id": REQUEST_ID,
        "handoff_id": HANDOFF_ID,
        "acknowledged": True,
    },
    {
        "type": "handoff_reject",
        "request_id": REQUEST_ID,
        "handoff_id": HANDOFF_ID,
    },
    {
        "type": "handoff_cancel",
        "request_id": REQUEST_ID,
        "handoff_id": HANDOFF_ID,
    },
    {
        "type": "handoff_status",
        "request_id": REQUEST_ID,
        "handoff_id": HANDOFF_ID,
    },
    {
        "type": "visual_transfer_begin",
        "request_id": REQUEST_ID,
        "handoff_id": HANDOFF_ID,
        "mime_type": "image/png",
        "size_bytes": 1_048_576,
        "width": 4096,
        "height": 4096,
        "frame_count": 1,
    },
    {
        "type": "visual_transfer_cancel",
        "request_id": REQUEST_ID,
        "transfer_id": TRANSFER_ID,
    },
    {
        "type": "visual_transfer_status",
        "request_id": REQUEST_ID,
        "transfer_id": TRANSFER_ID,
    },
)


VALID_SERVER_MESSAGES = (
    {
        "type": "pairing_challenge",
        "request_id": REQUEST_ID,
        "challenge_id": CHALLENGE_ID,
        "expires_at": 1000.25,
    },
    {
        "type": "pairing_confirmed",
        "request_id": REQUEST_ID,
        "device_id": DEVICE_ID,
        "expires_at": 1000.25,
        "protocol_version": 1,
    },
    {
        "type": "pairing_update",
        "request_id": REQUEST_ID,
        "challenge_id": CHALLENGE_ID,
        "status": "cancelled",
    },
    {
        "type": "pairing_error",
        "request_id": REQUEST_ID,
        "code": "challenge_expired",
    },
    {
        "type": "handoff_offer",
        "request_id": REQUEST_ID,
        "handoff_id": HANDOFF_ID,
        "task_id": TASK_ID,
        "summary": "Review the bounded task on desktop.",
        "expires_at": 1000.25,
    },
    {
        "type": "handoff_update",
        "request_id": REQUEST_ID,
        "handoff_id": HANDOFF_ID,
        "status": "accepted",
    },
    {
        "type": "handoff_error",
        "request_id": REQUEST_ID,
        "handoff_id": HANDOFF_ID,
        "code": "policy_denied",
    },
    {
        "type": "visual_transfer_ready",
        "request_id": REQUEST_ID,
        "transfer_id": TRANSFER_ID,
        "expires_at": 1000.25,
    },
    {
        "type": "visual_transfer_update",
        "request_id": REQUEST_ID,
        "transfer_id": TRANSFER_ID,
        "status": "receiving",
        "bytes_received": 1_048_576,
    },
    {
        "type": "visual_transfer_complete",
        "request_id": REQUEST_ID,
        "transfer_id": TRANSFER_ID,
        "content_hash": "sha256." + ("a" * 64),
    },
    {
        "type": "visual_transfer_error",
        "request_id": REQUEST_ID,
        "transfer_id": TRANSFER_ID,
        "code": "mime_mismatch",
    },
)


@pytest.mark.parametrize("message", VALID_CLIENT_MESSAGES)
def test_phase4_client_messages_validate(message: dict[str, object]):
    assert validate_client_message(message) is None


@pytest.mark.parametrize("message", VALID_SERVER_MESSAGES)
def test_phase4_server_messages_validate(message: dict[str, object]):
    assert validate_server_message(message) is None


@pytest.mark.parametrize(
    ("message", "field", "value"),
    [
        (VALID_CLIENT_MESSAGES[0], "actor_id", "owner"),
        (VALID_CLIENT_MESSAGES[1], "session_id", "session"),
        (VALID_CLIENT_MESSAGES[4], "approval_id", "approval"),
        (VALID_CLIENT_MESSAGES[5], "grant_id", "grant"),
        (VALID_CLIENT_MESSAGES[9], "execution_ticket", "ticket"),
        (VALID_CLIENT_MESSAGES[9], "provider", "cloud"),
        (VALID_CLIENT_MESSAGES[9], "path", "forbidden-path"),
        (VALID_CLIENT_MESSAGES[9], "bytes", "raw"),
        (VALID_CLIENT_MESSAGES[9], "base64", "AA=="),
        (VALID_CLIENT_MESSAGES[9], "data_url", "data:image/png;base64,AA=="),
        (VALID_CLIENT_MESSAGES[4], "task_payload", {"content": "private"}),
    ],
)
def test_phase4_client_messages_reject_authority_content_and_unknown_fields(
    message: dict[str, object], field: str, value: object
):
    assert validate_client_message({**message, field: value}) is not None


@pytest.mark.parametrize(
    ("message", "field", "value"),
    [
        (VALID_SERVER_MESSAGES[0], "challenge_code", "01A2FF"),
        (VALID_SERVER_MESSAGES[1], "actor_id", "owner"),
        (VALID_SERVER_MESSAGES[4], "session_id", "session"),
        (VALID_SERVER_MESSAGES[5], "approval_id", "approval"),
        (VALID_SERVER_MESSAGES[7], "upload_url", "https://example.invalid"),
        (VALID_SERVER_MESSAGES[8], "bytes", "raw"),
        (VALID_SERVER_MESSAGES[9], "path", "forbidden-path"),
        (VALID_SERVER_MESSAGES[10], "message", "raw failure"),
        (VALID_SERVER_MESSAGES[10], "provider", "cloud"),
    ],
)
def test_phase4_server_messages_reject_secrets_content_and_raw_errors(
    message: dict[str, object], field: str, value: object
):
    assert validate_server_message({**message, field: value}) is not None


@pytest.mark.parametrize("code", ["01A2F", "01A2FFF", "01a2ff", "01A2FG"])
def test_pairing_code_is_exactly_six_uppercase_hex_characters(code: str):
    message = {**VALID_CLIENT_MESSAGES[1], "code": code}
    assert validate_client_message(message) is not None


def test_pairing_update_requires_the_correlated_identifier_for_each_status():
    assert validate_server_message(
        {
            "type": "pairing_update",
            "request_id": REQUEST_ID,
            "status": "cancelled",
        }
    ) is not None
    assert validate_server_message(
        {
            "type": "pairing_update",
            "request_id": REQUEST_ID,
            "device_id": DEVICE_ID,
            "status": "revoked",
        }
    ) is None


def test_handoff_accept_requires_explicit_true_acknowledgement():
    message = {**VALID_CLIENT_MESSAGES[5], "acknowledged": False}
    assert validate_client_message(message) is not None


@pytest.mark.parametrize("summary", ["", " ", "\u0085", "safe\u200btext", "bad\ntext"])
def test_handoff_summary_is_bounded_and_rejects_controls_and_format_chars(summary: str):
    message = {**VALID_CLIENT_MESSAGES[4], "summary": summary}
    assert validate_client_message(message) is not None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("size_bytes", 0),
        ("size_bytes", 1_048_577),
        ("width", 0),
        ("width", 4097),
        ("height", 0),
        ("height", 4097),
        ("frame_count", 0),
        ("frame_count", 2),
        ("mime_type", "image/gif"),
    ],
)
def test_visual_transfer_metadata_is_bounded(field: str, value: object):
    message = {**VALID_CLIENT_MESSAGES[9], field: value}
    assert validate_client_message(message) is not None


@pytest.mark.parametrize("expires_at", [math.nan, math.inf, -math.inf])
def test_phase4_expiry_values_must_be_finite(expires_at: float):
    message = {**VALID_SERVER_MESSAGES[0], "expires_at": expires_at}
    assert validate_server_message(message) is not None


@pytest.mark.parametrize("code", ["raw exception", "provider_error", "stack_trace"])
def test_phase4_error_codes_are_closed_enums(code: str):
    assert validate_server_message(
        {"type": "pairing_error", "request_id": REQUEST_ID, "code": code}
    ) is not None
    assert validate_server_message(
        {"type": "handoff_error", "request_id": REQUEST_ID, "code": code}
    ) is not None
    assert validate_server_message(
        {"type": "visual_transfer_error", "request_id": REQUEST_ID, "code": code}
    ) is not None


def test_phase4_contract_is_additive_protocol_v1_control_plane_only():
    assert PROTOCOL_VERSION == 1
    assert validate_client_message({"type": "ping"}) is None
    assert validate_server_message({"type": "pong"}) is None
    assert all("bytes" not in message for message in VALID_CLIENT_MESSAGES)
    assert all("bytes" not in message for message in VALID_SERVER_MESSAGES)
