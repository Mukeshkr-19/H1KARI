"""The server and web client must share one bounded WebSocket v1 contract."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.protocol import (
    CLIENT_MESSAGES,
    PROTOCOL_SCHEMA,
    PROTOCOL_VERSION,
    SERVER_MESSAGES,
    validate_client_message,
    validate_server_message,
)
from core.server import WebSocketServer


REPO_ROOT = Path(__file__).resolve().parent.parent


class MockWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))


def test_protocol_v1_declares_exact_message_directions():
    assert PROTOCOL_SCHEMA["name"] == "hikari.websocket"
    assert PROTOCOL_VERSION == 1
    assert set(CLIENT_MESSAGES) == {
        "pair",
        "ping",
        "identify",
        "message",
        "voice",
        "companion_preferences",
        "document_prepare",
        "document_confirm",
        "document_follow_up",
        "document_cancel",
        "task_status",
        "status",
        "pairing_prepare",
        "pairing_confirm",
        "pairing_cancel",
        "pairing_revoke",
        "handoff_prepare",
        "handoff_accept",
        "handoff_reject",
        "handoff_cancel",
        "handoff_status",
        "visual_transfer_begin",
        "visual_transfer_cancel",
        "visual_transfer_status",
        "vision_analysis_prepare",
        "vision_analysis_cancel",
        "vision_analysis_status",
        "productivity_email_draft_prepare",
        "productivity_calendar_read_prepare",
        "productivity_calendar_draft_prepare",
        "productivity_research_prepare",
        "productivity_reminder_prepare",
        "productivity_confirm",
        "productivity_cancel",
        "productivity_status",
        "scheduled_jobs_list",
        "scheduled_job_create",
        "scheduled_job_pause",
        "scheduled_job_resume",
        "scheduled_job_cancel",
    }


def test_scheduled_job_create_is_exact_and_bounded():
    valid = {
        "type": "scheduled_job_create",
        "request_id": "request-1",
        "proposal_id": "proposal-1",
        "next_run_at": "2026-07-21T09:00:00-04:00",
        "max_attempts": 3,
        "quiet_hours": {
            "start_minute": 1320,
            "end_minute": 420,
            "timezone": "America/New_York",
        },
    }
    assert validate_client_message(valid) is None
    assert validate_client_message({**valid, "actor_id": "owner"}) is not None
    assert validate_client_message({**valid, "max_attempts": 6}) is not None
    assert validate_client_message(
        {**valid, "quiet_hours": {**valid["quiet_hours"], "payload": "x"}}
    ) is not None


def test_scheduled_result_messages_are_bounded():
    research = {
        "type": "scheduled_job_research_result",
        "job_id": "job-1",
        "items": [
            {
                "title": "Release",
                "url": "https://example.com/release",
                "domain": "example.com",
            }
        ],
    }
    calendar = {
        "type": "scheduled_job_calendar_result",
        "job_id": "job-1",
        "events": [
            {
                "title": "Planning",
                "start": "2026-07-21T09:00:00Z",
                "end": "2026-07-21T10:00:00Z",
                "calendar": "Work",
            }
        ],
    }
    assert validate_server_message(research) is None
    assert validate_server_message(calendar) is None
    assert validate_server_message({**research, "proposal_id": "proposal-1"}) is not None
    assert set(SERVER_MESSAGES) == {
        "welcome",
        "paired",
        "pair_error",
        "pair_locked",
        "pairing_required",
        "protocol_error",
        "pong",
        "identified",
        "response",
        "status",
        "error",
        "companion_update",
        "companion_preferences_ack",
        "companion_preferences_error",
        "document_confirmation_required",
        "task_update",
        "document_explanation",
        "document_error",
        "pairing_challenge",
        "pairing_confirmed",
        "pairing_update",
        "pairing_error",
        "handoff_offer",
        "handoff_update",
        "handoff_error",
        "visual_transfer_ready",
        "visual_transfer_update",
        "visual_transfer_complete",
        "visual_transfer_error",
        "vision_analysis_ready",
        "vision_analysis_update",
        "vision_observation",
        "vision_analysis_error",
        "productivity_confirmation_required",
        "productivity_update",
        "productivity_error",
        "productivity_research_result",
        "productivity_calendar_result",
            "scheduled_jobs",
            "scheduled_job_update",
            "scheduled_job_error",
            "scheduled_job_research_result",
            "scheduled_job_calendar_result",
        }


@pytest.mark.parametrize(
    "message,error",
    [
        ({"type": "message"}, "Missing required field: text"),
        ({"type": "message", "text": 7}, "Invalid field type: text"),
        ({"type": "message", "text": "x", "extra": True}, "Unknown field: extra"),
        ({"type": "message", "text": "x" * 20001}, "Field too long: text"),
        ({"type": "pair", "code": "ABC123", "device_type": "x" * 65}, "Field too long: device_type"),
        ({"type": "voice"}, "Missing required field: text or listening"),
        ({"type": "pair", "code": "ABC123", "protocol_version": True}, "Invalid field type: protocol_version"),
        ({"type": "document_prepare", "path": "x" * 4097, "provider": "ollama"}, "Field too long: path"),
        ({"type": "document_confirm", "task_id": "task-1"}, "Missing required field: provider"),
        ({"type": "task_status", "task_id": "x" * 65}, "Field too long: task_id"),
        ({"type": "not-real"}, "Unknown message type"),
        ({"type": "productivity_email_draft_prepare", "request_id": "email-req-1", "recipient": "user@example.com", "subject": "Hello", "body": "Line one\n\tLine two"}, None),
        ({"type": "productivity_email_draft_prepare", "recipient": "user@example.com", "subject": "Hello", "body": "Line one"}, "Missing required field: request_id"),
        ({"type": "productivity_email_draft_prepare", "request_id": "Bad ID", "recipient": "user@example.com", "subject": "", "body": ""}, "Invalid field value: request_id"),
        ({"type": "productivity_email_draft_prepare", "request_id": "email-req-1", "recipient": "", "subject": "", "body": ""}, "Field too short: recipient"),
        ({"type": "productivity_email_draft_prepare", "request_id": "email-req-1", "recipient": "user@example.com", "subject": "bad\nsubject", "body": ""}, "Invalid field value: subject"),
        ({"type": "productivity_email_draft_prepare", "request_id": "email-req-1", "recipient": "user@example.com", "subject": "", "body": "bad\u202etext"}, "Invalid field value: body"),
        ({"type": "productivity_email_draft_prepare", "request_id": "email-req-1", "recipient": "user@example.com", "subject": "", "body": "bad\u200btext"}, "Invalid field value: body"),
        ({"type": "productivity_calendar_read_prepare", "request_id": "cal-read-1", "start": "2026-07-18T09:00:00-04:00", "end": "2026-07-18T10:00:00-04:00"}, None),
        ({"type": "productivity_calendar_read_prepare", "request_id": "cal-read-1", "start": "2026-07-18T09:00:00-04:00", "end": "2026-07-18T10:00:00-04:00", "calendar_name": "Work"}, None),
        ({"type": "productivity_calendar_read_prepare", "start": "2026-07-18T09:00:00-04:00", "end": "2026-07-18T10:00:00-04:00"}, "Missing required field: request_id"),
        ({"type": "productivity_calendar_read_prepare", "request_id": "Bad ID", "start": "2026-07-18T09:00:00-04:00", "end": "2026-07-18T10:00:00-04:00"}, "Invalid field value: request_id"),
        ({"type": "productivity_calendar_read_prepare", "request_id": "cal-read-1", "start": "2026-07-18T09:00:00-04:00", "end": "2026-07-18T10:00:00-04:00", "proposal_id": "prop-1"}, "Unknown field: proposal_id"),
        ({"type": "productivity_calendar_draft_prepare", "request_id": "cal-draft-1", "title": "Planning", "start": "2026-07-19T14:00:00Z", "end": "2026-07-19T15:30:00Z", "calendar_name": "Work"}, None),
        ({"type": "productivity_calendar_draft_prepare", "request_id": "cal-draft-1", "title": "Planning", "start": "2026-07-19T14:00:00Z", "end": "2026-07-19T15:30:00Z", "calendar_name": "Work", "location": "Office", "notes": "Line one\n\tLine two"}, None),
        ({"type": "productivity_calendar_draft_prepare", "request_id": "cal-draft-1", "title": "Planning", "start": "2026-07-19T14:00:00Z", "end": "2026-07-19T15:30:00Z"}, "Missing required field: calendar_name"),
        ({"type": "productivity_calendar_draft_prepare", "request_id": "cal-draft-1", "title": "", "start": "2026-07-19T14:00:00Z", "end": "2026-07-19T15:30:00Z", "calendar_name": "Work"}, "Field too short: title"),
        ({"type": "productivity_calendar_draft_prepare", "request_id": "cal-draft-1", "title": "bad\u200btitle", "start": "2026-07-19T14:00:00Z", "end": "2026-07-19T15:30:00Z", "calendar_name": "Work"}, "Invalid field value: title"),
        ({"type": "productivity_research_prepare", "request_id": "research-req-1", "query": "What changed?"}, None),
        ({"type": "productivity_research_prepare", "request_id": "research-req-1", "query": "What changed?", "domains": ["example.com"], "max_results": 10}, None),
        ({"type": "productivity_research_prepare", "query": "What changed?"}, "Missing required field: request_id"),
        ({"type": "productivity_research_prepare", "request_id": "Bad ID", "query": "What changed?"}, "Invalid field value: request_id"),
        ({"type": "productivity_research_prepare", "request_id": "research-req-1", "query": "   "}, None),
        ({"type": "productivity_research_prepare", "request_id": "research-req-1", "query": "q", "proposal_id": "prop-1"}, "Unknown field: proposal_id"),
        ({"type": "productivity_research_prepare", "request_id": "research-req-1", "query": "q", "domains": ["example.com", "example.com"]}, "Invalid field value: domains"),
        ({"type": "productivity_research_prepare", "request_id": "research-req-1", "query": "q", "max_results": 21}, "Invalid field value: max_results"),
        ({"type": "productivity_reminder_prepare", "request_id": "rem-1", "title": "Buy milk", "remind_at": "2026-07-21T09:00:00Z"}, None),
        ({"type": "productivity_reminder_prepare", "request_id": "rem-2", "title": "Doctor appointment", "remind_at": "2026-07-21T14:30:00-04:00", "notes": "Line one\n\tLine two", "list_name": "Personal"}, None),
        ({"type": "productivity_reminder_prepare", "title": "Buy milk", "remind_at": "2026-07-21T09:00:00Z"}, "Missing required field: request_id"),
        ({"type": "productivity_reminder_prepare", "request_id": "Bad ID", "title": "Buy milk", "remind_at": "2026-07-21T09:00:00Z"}, "Invalid field value: request_id"),
        ({"type": "productivity_reminder_prepare", "request_id": "rem-1", "title": "", "remind_at": "2026-07-21T09:00:00Z"}, "Field too short: title"),
        ({"type": "productivity_reminder_prepare", "request_id": "rem-1", "title": "Buy milk", "remind_at": "2026-07-21T09:00:00"}, "Invalid field value: remind_at"),
        ({"type": "productivity_reminder_prepare", "request_id": "rem-1", "title": "Buy milk", "remind_at": "2026-07-21T09:00:00Z", "proposal_id": "prop-1"}, "Unknown field: proposal_id"),
        ({"type": "productivity_reminder_prepare", "request_id": "rem-1", "title": "Buy milk", "remind_at": "2026-07-21T09:00:00Z", "actor_id": "owner"}, "Unknown field: actor_id"),
        ({"type": "productivity_reminder_prepare", "request_id": "rem-1", "title": "bad\u200btitle", "remind_at": "2026-07-21T09:00:00Z"}, "Invalid field value: title"),
        ({"type": "productivity_reminder_prepare", "request_id": "rem-1", "title": "   \t\n  ", "remind_at": "2026-07-21T09:00:00Z"}, "Invalid field value: title"),
        ({"type": "productivity_reminder_prepare", "request_id": "rem-1", "title": "\u2003\u2003", "remind_at": "2026-07-21T09:00:00Z"}, "Invalid field value: title"),
        ({"type": "productivity_reminder_prepare", "request_id": "rem-1", "title": "  Buy milk  ", "remind_at": "2026-07-21T09:00:00Z"}, None),
        ({"type": "productivity_reminder_prepare", "request_id": "rem-1", "title": "Buy milk", "remind_at": "2026-07-21T09:00:00Z", "list_name": ""}, "Field too short: list_name"),
        ({"type": "productivity_confirm", "proposal_id": "prop-1", "scope": "once"}, None),
        ({"type": "productivity_confirm", "proposal_id": "prop-1", "scope": "session"}, None),
        ({"type": "productivity_confirm", "proposal_id": "prop-1", "scope": "duration", "duration_seconds": 900}, None),
        ({"type": "productivity_confirm", "proposal_id": "prop-1", "scope": "precise_persistent", "acknowledged": True}, None),
        ({"type": "productivity_confirm", "proposal_id": "prop-1", "scope": "duration"}, "Missing required field: duration_seconds"),
        ({"type": "productivity_confirm", "proposal_id": "prop-1", "scope": "session", "duration_seconds": 900}, "Invalid field value: duration_seconds"),
        ({"type": "productivity_confirm", "proposal_id": "prop-1", "scope": "precise_persistent", "acknowledged": False}, "Invalid field value: acknowledged"),
        ({"type": "productivity_confirm", "proposal_id": "prop-1"}, "Missing required field: scope"),
        ({"type": "productivity_confirm", "proposal_id": "prop-1", "scope": "repeat"}, "Invalid field value: scope"),
        ({"type": "productivity_confirm", "proposal_id": "bad id!", "scope": "once"}, "Invalid field value: proposal_id"),
        ({"type": "productivity_confirm", "proposal_id": "Prop-1", "scope": "once"}, "Invalid field value: proposal_id"),
        ({"type": "productivity_confirm", "proposal_id": "prop:1", "scope": "once"}, "Invalid field value: proposal_id"),
        ({"type": "productivity_confirm", "proposal_id": "x" * 81, "scope": "once"}, "Field too long: proposal_id"),
        ({"type": "productivity_confirm", "proposal_id": "prop-1", "scope": "once", "extra": True}, "Unknown field: extra"),
        ({"type": "productivity_confirm", "proposal_id": 7, "scope": "once"}, "Invalid field type: proposal_id"),
        ({"type": "productivity_cancel", "proposal_id": "prop-1"}, None),
        ({"type": "productivity_cancel", "proposal_id": "bad id!"}, "Invalid field value: proposal_id"),
        ({"type": "productivity_cancel", "proposal_id": "Prop-1"}, "Invalid field value: proposal_id"),
        ({"type": "productivity_status", "proposal_id": "prop-1"}, None),
        ({"type": "productivity_status", "proposal_id": "bad id!"}, "Invalid field value: proposal_id"),
        ({"type": "productivity_status", "proposal_id": "Prop-1"}, "Invalid field value: proposal_id"),
        ({"type": "scheduled_jobs_list"}, None),
        ({"type": "scheduled_jobs_list", "actor_id": "owner"}, "Unknown field: actor_id"),
        ({"type": "scheduled_job_pause", "job_id": "job-1"}, None),
        ({"type": "scheduled_job_resume", "job_id": "Job-1"}, "Invalid field value: job_id"),
        ({"type": "scheduled_job_cancel", "job_id": "bad id"}, "Invalid field value: job_id"),
        ({"type": "vision_analysis_prepare", "request_id": "vision-req-1", "handoff_id": "handoff-1", "capability": "ocr"}, None),
        ({"type": "vision_analysis_prepare", "request_id": "vision-req-1", "handoff_id": "handoff-1", "capability": "describe"}, None),
        ({"type": "vision_analysis_prepare", "request_id": "vision-req-1", "handoff_id": "handoff-1"}, "Missing required field: capability"),
        ({"type": "vision_analysis_prepare", "request_id": "vision-req-1", "handoff_id": "handoff-1", "capability": "translate"}, "Invalid field value: capability"),
        ({"type": "vision_analysis_prepare", "request_id": "Bad ID", "handoff_id": "handoff-1", "capability": "ocr"}, "Invalid field value: request_id"),
        ({"type": "vision_analysis_prepare", "request_id": "vision-req-1", "handoff_id": "Bad ID", "capability": "ocr"}, "Invalid field value: handoff_id"),
        ({"type": "vision_analysis_prepare", "request_id": "vision-req-1", "handoff_id": "handoff-1", "capability": "ocr", "actor_id": "owner"}, "Unknown field: actor_id"),
        ({"type": "vision_analysis_prepare", "request_id": "vision-req-1", "handoff_id": "handoff-1", "capability": "ocr", "bytes": "raw"}, "Unknown field: bytes"),
        ({"type": "vision_analysis_prepare", "request_id": "vision-req-1", "handoff_id": "handoff-1", "capability": "ocr", "provider": "cloud"}, "Unknown field: provider"),
        ({"type": "vision_analysis_cancel", "request_id": "vision-req-1", "analysis_id": "analysis-1"}, None),
        ({"type": "vision_analysis_cancel", "request_id": "vision-req-1"}, "Missing required field: analysis_id"),
        ({"type": "vision_analysis_cancel", "request_id": "Bad ID", "analysis_id": "analysis-1"}, "Invalid field value: request_id"),
        ({"type": "vision_analysis_cancel", "request_id": "vision-req-1", "analysis_id": "Bad ID"}, "Invalid field value: analysis_id"),
        ({"type": "vision_analysis_cancel", "request_id": "vision-req-1", "analysis_id": "analysis-1", "session_id": "session"}, "Unknown field: session_id"),
        ({"type": "vision_analysis_status", "request_id": "vision-req-1", "analysis_id": "analysis-1"}, None),
        ({"type": "vision_analysis_status", "request_id": "vision-req-1"}, "Missing required field: analysis_id"),
        ({"type": "vision_analysis_status", "request_id": "vision-req-1", "analysis_id": "analysis-1", "approval_id": "approval"}, "Unknown field: approval_id"),
    ],
)
def test_client_message_validation_is_stable(message, error):
    assert validate_client_message(message) == error


def test_valid_client_messages_pass_schema_validation():
    messages = [
        {"type": "pair", "code": "ABC123", "protocol_version": 1},
        {"type": "ping"},
        {"type": "identify", "device_type": "web"},
        {"type": "message", "text": "hello"},
        {"type": "voice", "text": "hello"},
        {"type": "voice", "listening": True},
        {
            "type": "companion_preferences",
            "companion_type": "cat",
            "presentation": "female",
        },
        {"type": "document_prepare", "path": "/tmp/selected.txt", "provider": "ollama"},
        {"type": "document_confirm", "task_id": "task-1", "provider": "ollama"},
        {
            "type": "document_follow_up",
            "task_id": "task-1",
            "text": "Explain the second point.",
            "provider": "ollama",
            "fallback_provider": "google",
        },
        {"type": "document_cancel", "task_id": "task-1"},
        {"type": "task_status", "task_id": "task-1"},
        {"type": "status"},
        {"type": "productivity_reminder_prepare", "request_id": "rem-1", "title": "Buy milk", "remind_at": "2026-07-21T09:00:00Z"},
        {"type": "productivity_confirm", "proposal_id": "prop-1", "scope": "once"},
        {"type": "productivity_cancel", "proposal_id": "prop-1"},
        {"type": "productivity_status", "proposal_id": "prop-1"},
        {"type": "scheduled_jobs_list"},
        {"type": "scheduled_job_pause", "job_id": "job-1"},
        {"type": "scheduled_job_resume", "job_id": "job-1"},
        {"type": "scheduled_job_cancel", "job_id": "job-1"},
        {"type": "vision_analysis_prepare", "request_id": "vision-req-1", "handoff_id": "handoff-1", "capability": "ocr"},
        {"type": "vision_analysis_prepare", "request_id": "vision-req-1", "handoff_id": "handoff-1", "capability": "describe"},
        {"type": "vision_analysis_cancel", "request_id": "vision-req-1", "analysis_id": "analysis-1"},
        {"type": "vision_analysis_status", "request_id": "vision-req-1", "analysis_id": "analysis-1"},
    ]

    assert all(validate_client_message(message) is None for message in messages)


def test_existing_server_message_validation_remains_available():
    assert validate_server_message({"type": "pong"}) is None
    assert validate_server_message({"type": "response", "text": "hello"}) is None


def test_server_rejects_explicitly_unsupported_protocol_without_pair_attempt():
    server = WebSocketServer(MagicMock())
    server.pairing_code = "ABC123"
    websocket = MockWebSocket()

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "pair",
                    "code": "ABC123",
                    "protocol_version": 2,
                }
            ),
        )
    )

    assert websocket.sent == [
        {
            "type": "protocol_error",
            "message": "Unsupported protocol version",
            "supported_version": PROTOCOL_VERSION,
        }
    ]
    assert server._pair_attempts == {}


def test_omitted_protocol_version_remains_v1_compatible():
    server = WebSocketServer(MagicMock())
    server.pairing_code = "ABC123"
    websocket = MockWebSocket()

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "pair", "code": "ABC123"}),
        )
    )

    assert websocket.sent == [
        {
            "type": "paired",
            "message": "Device paired successfully",
            "protocol_version": PROTOCOL_VERSION,
        }
    ]


def test_paired_invalid_payload_is_rejected_before_orchestrator_call():
    server = WebSocketServer(MagicMock())
    websocket = MockWebSocket()
    server._paired_client_ids.add(str(id(websocket)))

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "message", "text": "x" * 20001}),
        )
    )

    assert websocket.sent == [{"type": "error", "message": "Field too long: text"}]
    server.orchestrator.process_input.assert_not_called()


def test_embedded_and_next_clients_use_protocol_v1_contract():
    server = WebSocketServer(MagicMock())
    _status, _headers, body = server._serve_connect_page()
    embedded = body.decode("utf-8")
    frontend = (REPO_ROOT / "hikari-frontend" / "src" / "app" / "page.tsx").read_text(
        encoding="utf-8"
    )

    assert f"protocol_version: {PROTOCOL_VERSION}" in embedded
    assert "__HIKARI_PROTOCOL_VERSION__" not in embedded
    assert 'import protocolSchema from "../../../protocol/hikari-v1.json"' in frontend
    assert "protocol_version: PROTOCOL_VERSION" in frontend
    assert "parseServerMessage(event.data)" in frontend
    assert 'encodeClientMessage("message"' in frontend
