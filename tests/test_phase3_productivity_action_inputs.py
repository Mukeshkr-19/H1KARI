"""Deterministic tests for the Phase 3 prepared-input to adapter-input bridge."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.productivity.calendar import PreparedCalendarEventDraft, PreparedCalendarRead
from core.productivity.contracts import ProductivityAction
from core.productivity.email_draft import PreparedEmailDraft
from core.productivity.execution import AdapterInput
from core.productivity.reminder import PreparedReminderInput
from core.productivity.research import PreparedResearchInput
from core.productivity.action_inputs import (
    ActionInputConversionError,
    BrowserResearchAdapterInput,
    CalendarDraftAdapterInput,
    CalendarReadAdapterInput,
    EmailDraftAdapterInput,
    ReminderCreateAdapterInput,
    _canonical_aware_iso,
    adapter_input_from_prepared,
    browser_research_adapter_input_from_prepared,
    calendar_draft_adapter_input_from_prepared,
    calendar_read_adapter_input_from_prepared,
    email_draft_adapter_input_from_prepared,
    reminder_create_adapter_input_from_prepared,
)


EST = timezone(timedelta(hours=-4))
UTC = timezone.utc


def _aware(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int,
    microsecond: int,
    tzinfo: timezone,
) -> datetime:
    return datetime(year, month, day, hour, minute, second, microsecond, tzinfo=tzinfo)


def _research() -> PreparedResearchInput:
    return PreparedResearchInput("Latest release notes", ("example.com",), 5)


def _email() -> PreparedEmailDraft:
    return PreparedEmailDraft("user@example.com", "Subject line", "Body text")


def _calendar_read() -> PreparedCalendarRead:
    start = _aware(2026, 7, 20, 9, 0, 0, 123456, EST)
    end = _aware(2026, 7, 20, 10, 0, 0, 654321, EST)
    return PreparedCalendarRead(start, end, "Work")


def _calendar_draft() -> PreparedCalendarEventDraft:
    start = _aware(2026, 7, 21, 13, 0, 0, 111111, UTC)
    end = _aware(2026, 7, 21, 14, 30, 0, 222222, UTC)
    return PreparedCalendarEventDraft(
        "Planning sync",
        start,
        end,
        "Work",
        "Room 3",
        "Bring notes",
    )


def _reminder() -> PreparedReminderInput:
    remind_at = _aware(2026, 8, 1, 9, 0, 0, 333333, EST)
    return PreparedReminderInput("Pick up package", remind_at, "Bring ID", "Errands")


def assert_content_free_repr(value: object) -> None:
    text = repr(value)
    assert "(...)" in text
    assert "@" not in text
    assert "example.com" not in text
    assert "Planning" not in text
    assert "package" not in text


def assert_no_identity_fields(input_value: AdapterInput) -> None:
    for item in input_value.__dataclass_fields__:
        lowered = item.lower()
        assert "actor" not in lowered
        assert "session" not in lowered
        assert "approval" not in lowered
        assert "proposal" not in lowered
        assert "provider" not in lowered
        assert "secret" not in lowered


# ---------------------------------------------------------------------------
# Successful conversions
# ---------------------------------------------------------------------------


def test_browser_research_conversion_preserves_fields() -> None:
    prepared = _research()
    converted = browser_research_adapter_input_from_prepared(prepared)

    assert isinstance(converted, BrowserResearchAdapterInput)
    assert converted.action is ProductivityAction.BROWSER_RESEARCH
    assert converted.query == prepared.query
    assert converted.domains == prepared.domains
    assert converted.max_results == prepared.max_results
    converted.validate()


def test_email_draft_conversion_preserves_fields() -> None:
    prepared = _email()
    converted = email_draft_adapter_input_from_prepared(prepared)

    assert isinstance(converted, EmailDraftAdapterInput)
    assert converted.action is ProductivityAction.EMAIL_DRAFT
    assert converted.recipient == prepared.recipient
    assert converted.subject == prepared.subject
    assert converted.body == prepared.body
    converted.validate()


def test_calendar_read_conversion_preserves_optional_name_and_iso_bounds() -> None:
    prepared = _calendar_read()
    converted = calendar_read_adapter_input_from_prepared(prepared)

    assert isinstance(converted, CalendarReadAdapterInput)
    assert converted.action is ProductivityAction.CALENDAR_READ
    assert converted.calendar_name == "Work"
    assert converted.start == "2026-07-20T09:00:00.123456-04:00"
    assert converted.end == "2026-07-20T10:00:00.654321-04:00"
    converted.validate()


def test_calendar_read_conversion_omits_none_calendar_name() -> None:
    start = _aware(2026, 7, 20, 9, 0, 0, 0, UTC)
    end = _aware(2026, 7, 20, 10, 0, 0, 0, UTC)
    prepared = PreparedCalendarRead(start, end, None)
    converted = calendar_read_adapter_input_from_prepared(prepared)

    assert converted.calendar_name is None
    assert converted.start == "2026-07-20T09:00:00Z"
    assert converted.end == "2026-07-20T10:00:00Z"


def test_calendar_draft_conversion_preserves_optional_fields() -> None:
    prepared = _calendar_draft()
    converted = calendar_draft_adapter_input_from_prepared(prepared)

    assert isinstance(converted, CalendarDraftAdapterInput)
    assert converted.action is ProductivityAction.CALENDAR_DRAFT
    assert converted.title == prepared.title
    assert converted.calendar_name == prepared.calendar_name
    assert converted.location == prepared.location
    assert converted.notes == prepared.notes
    assert converted.start == "2026-07-21T13:00:00.111111Z"
    assert converted.end == "2026-07-21T14:30:00.222222Z"
    converted.validate()


def test_calendar_draft_conversion_preserves_none_optional_fields() -> None:
    start = _aware(2026, 7, 21, 13, 0, 0, 0, UTC)
    end = _aware(2026, 7, 21, 14, 0, 0, 0, UTC)
    prepared = PreparedCalendarEventDraft("Title", start, end, "Work", None, None)
    converted = calendar_draft_adapter_input_from_prepared(prepared)

    assert converted.location is None
    assert converted.notes is None


def test_reminder_conversion_preserves_optional_fields() -> None:
    prepared = _reminder()
    converted = reminder_create_adapter_input_from_prepared(prepared)

    assert isinstance(converted, ReminderCreateAdapterInput)
    assert converted.action is ProductivityAction.REMINDER_CREATE
    assert converted.title == prepared.title
    assert converted.notes == prepared.notes
    assert converted.list_name == prepared.list_name
    assert converted.remind_at == "2026-08-01T09:00:00.333333-04:00"
    converted.validate()


def test_reminder_conversion_preserves_none_optional_fields() -> None:
    remind_at = _aware(2026, 8, 2, 12, 0, 0, 0, UTC)
    prepared = PreparedReminderInput("Title", remind_at, None, None)
    converted = reminder_create_adapter_input_from_prepared(prepared)

    assert converted.notes is None
    assert converted.list_name is None
    assert converted.remind_at == "2026-08-02T12:00:00Z"


@pytest.mark.parametrize(
    ("action", "prepared", "expected_type"),
    [
        (ProductivityAction.BROWSER_RESEARCH, _research(), BrowserResearchAdapterInput),
        (ProductivityAction.EMAIL_DRAFT, _email(), EmailDraftAdapterInput),
        (ProductivityAction.CALENDAR_READ, _calendar_read(), CalendarReadAdapterInput),
        (
            ProductivityAction.CALENDAR_DRAFT,
            _calendar_draft(),
            CalendarDraftAdapterInput,
        ),
        (ProductivityAction.REMINDER_CREATE, _reminder(), ReminderCreateAdapterInput),
    ],
)
def test_adapter_input_from_prepared_binds_exact_action(
    action: ProductivityAction,
    prepared: object,
    expected_type: type[AdapterInput],
) -> None:
    converted = adapter_input_from_prepared(action, prepared)
    assert isinstance(converted, expected_type)
    assert converted.action is action


# ---------------------------------------------------------------------------
# Canonical datetime handling
# ---------------------------------------------------------------------------


def test_canonical_iso_preserves_microseconds_and_offset() -> None:
    value = _aware(2026, 7, 20, 9, 0, 0, 123456, EST)
    assert _canonical_aware_iso(value) == "2026-07-20T09:00:00.123456-04:00"


def test_canonical_iso_normalizes_utc_to_z() -> None:
    value = _aware(2026, 7, 20, 9, 0, 0, 987654, UTC)
    assert _canonical_aware_iso(value) == "2026-07-20T09:00:00.987654Z"


def test_canonical_iso_rejects_naive_datetime() -> None:
    naive = datetime(2026, 7, 20, 9, 0, 0)
    with pytest.raises(ActionInputConversionError):
        _canonical_aware_iso(naive)


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("converter", "wrong"),
    [
        (browser_research_adapter_input_from_prepared, _email()),
        (email_draft_adapter_input_from_prepared, _research()),
        (calendar_read_adapter_input_from_prepared, _email()),
        (calendar_draft_adapter_input_from_prepared, _research()),
        (reminder_create_adapter_input_from_prepared, _email()),
    ],
)
def test_converters_reject_wrong_preparation_type(converter, wrong) -> None:
    with pytest.raises(ActionInputConversionError) as exc:
        converter(wrong)
    assert str(exc.value) == "action input conversion failed"
    assert "user@example.com" not in str(exc.value)


def test_adapter_input_from_prepared_rejects_action_mismatch() -> None:
    with pytest.raises(ActionInputConversionError):
        adapter_input_from_prepared(ProductivityAction.EMAIL_DRAFT, _research())


def test_adapter_input_from_prepared_rejects_unsupported_action() -> None:
    with pytest.raises(ActionInputConversionError):
        adapter_input_from_prepared(ProductivityAction.MCP_EXECUTE, _research())


def test_conversion_error_has_fixed_message_only() -> None:
    error = ActionInputConversionError()
    assert str(error) == "action input conversion failed"


# ---------------------------------------------------------------------------
# AdapterInput validation and immutability
# ---------------------------------------------------------------------------


def test_adapter_inputs_are_frozen_and_privacy_safe() -> None:
    converted = email_draft_adapter_input_from_prepared(_email())
    assert is_dataclass(converted)
    params = converted.__dataclass_params__
    assert params is not None and params.frozen
    assert_content_free_repr(converted)
    with pytest.raises(FrozenInstanceError):
        converted.recipient = "other@example.com"  # type: ignore[misc]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: BrowserResearchAdapterInput("bad\x00query", ("example.com",), 5),
        lambda: EmailDraftAdapterInput("user@example.com", "sub\x00ject", "body"),
        lambda: CalendarReadAdapterInput("2026\x0001-01T00:00:00Z", "2026-01-02T00:00:00Z", None),
        lambda: CalendarDraftAdapterInput("title", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "Work", "loc\x00", None),
        lambda: ReminderCreateAdapterInput("title", "2026-08-01T09:00:00Z", "note\x00s", None),
    ],
)
def test_malformed_adapter_inputs_fail_validate(factory) -> None:
    candidate = factory()
    with pytest.raises(ValueError):
        candidate.validate()


def test_valid_adapter_inputs_pass_validate() -> None:
    for converted in (
        browser_research_adapter_input_from_prepared(_research()),
        email_draft_adapter_input_from_prepared(_email()),
        calendar_read_adapter_input_from_prepared(_calendar_read()),
        calendar_draft_adapter_input_from_prepared(_calendar_draft()),
        reminder_create_adapter_input_from_prepared(_reminder()),
    ):
        converted.validate()
        assert_no_identity_fields(converted)


# ---------------------------------------------------------------------------
# Per-action field and aggregate bounds
# ---------------------------------------------------------------------------


def test_email_body_accepts_20000_and_rejects_20001() -> None:
    body_max = "B" * 20_000
    prepared = PreparedEmailDraft("user@example.com", "Subject", body_max)
    converted = email_draft_adapter_input_from_prepared(prepared)
    assert converted.body == body_max
    assert len(converted.body) == 20_000
    converted.validate()

    direct = EmailDraftAdapterInput("user@example.com", "Subject", body_max)
    direct.validate()

    with pytest.raises(ValueError):
        EmailDraftAdapterInput("user@example.com", "Subject", body_max + "X").validate()


def test_email_recipient_and_subject_bounds() -> None:
    recipient_max = "r" * 320
    subject_max = "s" * 998
    EmailDraftAdapterInput(recipient_max, subject_max, "body").validate()

    with pytest.raises(ValueError):
        EmailDraftAdapterInput(recipient_max + "x", subject_max, "body").validate()
    with pytest.raises(ValueError):
        EmailDraftAdapterInput(recipient_max, subject_max + "x", "body").validate()


def test_email_aggregate_bound_matches_field_sum() -> None:
    input_value = EmailDraftAdapterInput(
        "r" * 320,
        "s" * 998,
        "b" * 20_000,
    )
    input_value.validate()
    assert input_value.total_text_limit() == 320 + 998 + 20_000


def test_research_query_and_domain_bounds() -> None:
    query_max = "q" * 2000
    domain_max = "a" * 249 + ".com"
    assert len(domain_max) == 253
    BrowserResearchAdapterInput(query_max, (domain_max,), 10).validate()

    with pytest.raises(ValueError):
        BrowserResearchAdapterInput(query_max + "x", (domain_max,), 10).validate()
    with pytest.raises(ValueError):
        BrowserResearchAdapterInput(query_max, (domain_max + "x",), 10).validate()
    with pytest.raises(ValueError):
        BrowserResearchAdapterInput(
            "query",
            tuple(f"d{i}.example.com" for i in range(17)),
            10,
        ).validate()
    with pytest.raises(ValueError):
        BrowserResearchAdapterInput("query", ("example.com",), 1001).validate()
    BrowserResearchAdapterInput("query", ("example.com",), 20).validate()

    with pytest.raises(ValueError):
        BrowserResearchAdapterInput("query", ("example.com",), 21).validate()


def test_calendar_read_name_and_datetime_bounds() -> None:
    start = "2026-07-20T09:00:00.123456-04:00"
    end = "2026-07-20T10:00:00.654321-04:00"
    name_max = "n" * 200
    CalendarReadAdapterInput(start, end, name_max).validate()

    with pytest.raises(ValueError):
        CalendarReadAdapterInput(start, end, name_max + "x").validate()
    with pytest.raises(ValueError):
        CalendarReadAdapterInput("s" * 65, end, None).validate()
    with pytest.raises(ValueError):
        CalendarReadAdapterInput(start, "e" * 65, None).validate()


def test_calendar_draft_field_bounds() -> None:
    start = "2026-07-21T13:00:00.111111Z"
    end = "2026-07-21T14:30:00.222222Z"
    title_max = "t" * 500
    location_max = "l" * 500
    notes_max = "n" * 4000
    CalendarDraftAdapterInput(
        title_max, start, end, "Work", location_max, notes_max
    ).validate()

    with pytest.raises(ValueError):
        CalendarDraftAdapterInput(title_max + "x", start, end, "Work", None, None).validate()
    with pytest.raises(ValueError):
        CalendarDraftAdapterInput("Title", start, end, "Work", location_max + "x", None).validate()
    with pytest.raises(ValueError):
        CalendarDraftAdapterInput("Title", start, end, "Work", None, notes_max + "x").validate()
    with pytest.raises(ValueError):
        CalendarDraftAdapterInput("Title", start, end, "", None, None).validate()


def test_reminder_field_bounds() -> None:
    remind_at = "2026-08-01T09:00:00.333333-04:00"
    title_max = "t" * 500
    notes_max = "n" * 4000
    list_max = "l" * 200
    ReminderCreateAdapterInput(title_max, remind_at, notes_max, list_max).validate()

    with pytest.raises(ValueError):
        ReminderCreateAdapterInput(title_max + "x", remind_at, None, None).validate()
    with pytest.raises(ValueError):
        ReminderCreateAdapterInput("Title", remind_at, notes_max + "x", None).validate()
    with pytest.raises(ValueError):
        ReminderCreateAdapterInput("Title", remind_at, None, list_max + "x").validate()
    with pytest.raises(ValueError):
        ReminderCreateAdapterInput("Title", "r" * 65, None, None).validate()


def test_max_prepared_email_body_converts_without_truncation() -> None:
    body = "x" * 19_998 + "\n\t"
    assert len(body) == 20_000
    prepared = PreparedEmailDraft("user@example.com", "S" * 998, body)
    converted = email_draft_adapter_input_from_prepared(prepared)
    assert converted.body == body
    assert converted.subject == "S" * 998
    assert len(converted.body) == 20_000


def test_cf_and_control_policy_matches_preparation() -> None:
    with pytest.raises(ValueError):
        EmailDraftAdapterInput("user@example.com", "bad\u200bsubject", "body").validate()
    with pytest.raises(ValueError):
        BrowserResearchAdapterInput("bad\u0001query", ("example.com",), 5).validate()
    ReminderCreateAdapterInput(
        "Title",
        "2026-08-01T09:00:00Z",
        "notes\nwith\ttabs",
        None,
    ).validate()


# ---------------------------------------------------------------------------
# Source contracts
# ---------------------------------------------------------------------------


def test_action_inputs_source_has_no_side_effects_or_forbidden_fields() -> None:
    source = (
        Path(__file__).resolve().parent.parent
        / "core"
        / "productivity"
        / "action_inputs.py"
    ).read_text(encoding="utf-8")
    forbidden_imports = (
        "subprocess",
        "requests",
        "browser_automation",
        "mac_integration",
        "smtplib",
        "logging",
        "sqlite3",
        "random",
        "mcp",
        "skills",
    )
    for name in forbidden_imports:
        assert f"import {name}" not in source
        assert f"from {name}" not in source
    for banned in (
        "PreparationRegistry",
        "open(",
        "fetch(",
        "ActorContext",
        "approval_id",
        "proposal_id",
        "session_id",
    ):
        assert banned not in source

    assert "EMAIL_BODY_MAX" in source
    assert "field_text_limit" in source
    assert "total_text_limit" in source
