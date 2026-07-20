"""Source contracts for the Phase 3 scheduled-jobs frontend protocol adapter."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "scheduledJobsProtocol.ts"
)
PROTOCOL_TEST = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "scheduledJobsProtocol.test.ts"
)
PACKAGE = REPO_ROOT / "hikari-frontend" / "package.json"

SERVER_TYPES = (
    "scheduled_jobs",
    "scheduled_job_update",
    "scheduled_job_error",
)

CLIENT_ENCODERS = (
    "encodeScheduledJobsList",
    "encodeScheduledJobPause",
    "encodeScheduledJobResume",
    "encodeScheduledJobCancel",
)

ERROR_CODES = (
    "control_failed",
    "job_not_found",
    "unavailable",
)

WIRE_JOB_FIELDS = (
    "job_id",
    "action",
    "state",
    "next_run_at",
    "quiet_hours_label",
    "attempt_count",
    "max_attempts",
)


def _protocol() -> str:
    assert PROTOCOL.is_file()
    return PROTOCOL.read_text(encoding="utf-8")


def _tests() -> str:
    assert PROTOCOL_TEST.is_file()
    return PROTOCOL_TEST.read_text(encoding="utf-8")


def test_protocol_module_and_tests_exist():
    assert PROTOCOL.is_file()
    assert PROTOCOL_TEST.is_file()


def test_protocol_parses_required_server_message_types():
    text = _protocol()
    assert "export function parseScheduledJobsServerMessage(" in text
    for message_type in SERVER_TYPES:
        assert f'"{message_type}"' in text


def test_protocol_reuses_scheduled_jobs_helpers():
    text = _protocol()
    assert 'from "./scheduledJobs"' in text
    assert "parseScheduledJobView" in text
    assert "parseScheduledJobList" in text
    assert "isValidJobId" in text
    assert "isScheduledJobErrorCode" in text
    assert "SCHEDULED_JOB_LIST_MAX" in text


def test_protocol_wire_job_fields_and_ownership():
    text = _protocol()
    for field in WIRE_JOB_FIELDS:
        assert f'"{field}"' in text
    assert "pendingControl: null" in text
    assert "actionLabel: input.action" in text
    assert "nextRunLabel" in text
    tests = _tests()
    assert "SCHEDULED_JOB_OWNERSHIP_LABEL" in tests
    assert "Current session" in tests


def test_protocol_safe_error_codes_only():
    text = _protocol()
    for code in ERROR_CODES:
        assert f'"{code}"' in text or code in _tests()
    tests = _tests()
    assert "parses scheduled_job_error with safe codes only" in tests
    assert "provider_timeout" in tests


def test_protocol_encoders_validate_job_ids():
    text = _protocol()
    for encoder in CLIENT_ENCODERS:
        assert f"export function {encoder}(" in text
    assert 'type: "scheduled_jobs_list"' in text
    assert 'type: "scheduled_job_pause"' in text
    assert 'type: "scheduled_job_resume"' in text
    assert 'type: "scheduled_job_cancel"' in text
    assert "isValidJobId(jobId)" in text


def test_protocol_has_no_side_effects():
    text = _protocol()
    for banned in (
        "fetch(",
        "WebSocket",
        "localStorage",
        "sessionStorage",
        "setTimeout",
        "setInterval",
        "console.",
        "XMLHttpRequest",
        "navigator.",
        "window.",
        "document.",
        "useEffect",
        "useState",
    ):
        assert banned not in text


def test_protocol_rejects_unknown_fields_and_sensitive_keys():
    text = _protocol()
    assert "hasOnlyKeys" in text
    assert 'new Set(["type", "jobs"])' in text
    assert 'new Set(["type", "job"])' in text
    assert 'new Set(["type", "job_id", "code"])' in text
    tests = _tests()
    assert "rejects unknown fields on messages and jobs" in tests
    assert "actor_id" in tests
    assert "session_id" in tests
    assert "proposal_id" in tests
    assert "secret stack" in tests
    assert "provider" in tests


def test_unit_tests_cover_required_behaviors():
    text = _tests()
    for needle in (
        "parses a valid scheduled_jobs list as immutable",
        "parses every job state through scheduled_job_update",
        "rejects malformed and duplicate job ids",
        "rejects oversized job lists",
        "rejects inconsistent attempt counts",
        "bounds and sanitizes action and quiet hours labels",
        "encodes list pause resume and cancel requests",
        "parses JSON strings and returns null without throwing on bad JSON",
    ):
        assert needle in text


def test_unit_script_includes_protocol_and_existing_suites():
    package = PACKAGE.read_text(encoding="utf-8")
    assert "scheduledJobsProtocol.test.ts" in package
    assert "scheduledJobsProtocol.test.js" in package
    assert "scheduledJobs.test.js" in package
    assert "productivityProtocol.test.js" in package
    assert "actionLifecycle.test.js" in package
    assert "actionPreview.test.js" in package
    assert "voiceDocumentIntent.test.js" in package
    assert "speechOutput.test.js" in package
