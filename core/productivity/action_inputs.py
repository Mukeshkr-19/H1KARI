"""Pure Phase 3 bridge from prepared inputs to bounded adapter inputs.

Converts validated preparation objects into immutable ``AdapterInput`` values
for execution adapters. Performs no retained-input store access, storage, logging,
network, subprocess, provider, or external execution work.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

from core.productivity.calendar import (
    CALENDAR_LOCATION_MAX,
    CALENDAR_NAME_MAX,
    CALENDAR_NOTES_MAX,
    CALENDAR_TITLE_MAX,
    PreparedCalendarEventDraft,
    PreparedCalendarRead,
)
from core.productivity.contracts import ProductivityAction
from core.productivity.email_draft import (
    EMAIL_BODY_MAX,
    EMAIL_RECIPIENT_MAX,
    EMAIL_SUBJECT_MAX,
    PreparedEmailDraft,
)
from core.productivity.execution import AdapterInput
from core.productivity.reminder import (
    REMINDER_LIST_NAME_MAX,
    REMINDER_NOTES_MAX,
    REMINDER_TITLE_MAX,
    PreparedReminderInput,
)
from core.productivity.research import (
    DOMAIN_MAX,
    DOMAINS_MAX,
    MAX_RESULTS_MAX,
    QUERY_MAX,
    PreparedResearchInput,
)

# Canonical ISO-8601 with offset/Z and up to 6 fractional digits stays well under this.
_DATETIME_ISO_MAX = 64

_EMAIL_FIELD_LIMITS = {
    "recipient": EMAIL_RECIPIENT_MAX,
    "subject": EMAIL_SUBJECT_MAX,
    "body": EMAIL_BODY_MAX,
}
_EMAIL_TOTAL_MAX = EMAIL_RECIPIENT_MAX + EMAIL_SUBJECT_MAX + EMAIL_BODY_MAX

_RESEARCH_FIELD_LIMITS = {
    "query": QUERY_MAX,
    "domains": DOMAIN_MAX,
}
_RESEARCH_TOTAL_MAX = QUERY_MAX + (DOMAINS_MAX * DOMAIN_MAX)

_CALENDAR_READ_FIELD_LIMITS = {
    "start": _DATETIME_ISO_MAX,
    "end": _DATETIME_ISO_MAX,
    "calendar_name": CALENDAR_NAME_MAX,
}
_CALENDAR_READ_TOTAL_MAX = (
    _DATETIME_ISO_MAX + _DATETIME_ISO_MAX + CALENDAR_NAME_MAX
)

_CALENDAR_DRAFT_FIELD_LIMITS = {
    "title": CALENDAR_TITLE_MAX,
    "start": _DATETIME_ISO_MAX,
    "end": _DATETIME_ISO_MAX,
    "calendar_name": CALENDAR_NAME_MAX,
    "location": CALENDAR_LOCATION_MAX,
    "notes": CALENDAR_NOTES_MAX,
}
_CALENDAR_DRAFT_TOTAL_MAX = (
    CALENDAR_TITLE_MAX
    + _DATETIME_ISO_MAX
    + _DATETIME_ISO_MAX
    + CALENDAR_NAME_MAX
    + CALENDAR_LOCATION_MAX
    + CALENDAR_NOTES_MAX
)

_REMINDER_FIELD_LIMITS = {
    "title": REMINDER_TITLE_MAX,
    "remind_at": _DATETIME_ISO_MAX,
    "notes": REMINDER_NOTES_MAX,
    "list_name": REMINDER_LIST_NAME_MAX,
}
_REMINDER_TOTAL_MAX = (
    REMINDER_TITLE_MAX
    + _DATETIME_ISO_MAX
    + REMINDER_NOTES_MAX
    + REMINDER_LIST_NAME_MAX
)


class ActionInputConversionError(ValueError):
    """Fixed conversion failure without reflected input or exception detail."""

    def __init__(self) -> None:
        super().__init__("action input conversion failed")


def _canonical_aware_iso(value: datetime) -> str:
    """Return one canonical ISO-8601 string for an aware datetime."""
    if not isinstance(value, datetime):
        raise ActionInputConversionError()
    try:
        offset = value.utcoffset()
    except Exception:
        raise ActionInputConversionError() from None
    if value.tzinfo is None or offset is None:
        raise ActionInputConversionError()
    timespec = "microseconds" if value.microsecond else "seconds"
    text = value.isoformat(timespec=timespec)
    if text.endswith("+00:00"):
        return f"{text[:-6]}Z"
    return text


def _require_prepared(value: object, expected_type: type[object]) -> None:
    if not isinstance(value, expected_type):
        raise ActionInputConversionError()


def _valid_text(value: object, maximum: int, *, allow_newline_tab: bool) -> bool:
    if not isinstance(value, str) or len(value) > maximum:
        return False
    for char in value:
        if allow_newline_tab and char in "\n\t":
            continue
        if ord(char) < 32 or ord(char) == 127:
            return False
        if unicodedata.category(char) == "Cf":
            return False
    return True


def _finalize(input_value: AdapterInput) -> AdapterInput:
    try:
        input_value.validate()
    except ValueError:
        raise ActionInputConversionError() from None
    return input_value


@dataclass(frozen=True, repr=False)
class BrowserResearchAdapterInput(AdapterInput):
    query: str
    domains: tuple[str, ...]
    max_results: int
    action: ProductivityAction = field(
        default=ProductivityAction.BROWSER_RESEARCH,
        init=False,
        repr=False,
    )

    def field_text_limit(self, field_name: str) -> int:
        return _RESEARCH_FIELD_LIMITS.get(field_name, super().field_text_limit(field_name))

    def total_text_limit(self) -> int:
        return _RESEARCH_TOTAL_MAX

    def validate(self) -> None:
        super().validate()
        if not isinstance(self.query, str) or not self.query or not self.query.strip():
            raise ValueError("adapter input text is invalid")
        if not _valid_text(self.query, QUERY_MAX, allow_newline_tab=False):
            raise ValueError("adapter input text is invalid")
        if not isinstance(self.domains, tuple) or len(self.domains) > DOMAINS_MAX:
            raise ValueError("adapter input field is not bounded")
        for domain in self.domains:
            if not _valid_text(domain, DOMAIN_MAX, allow_newline_tab=False):
                raise ValueError("adapter input text is invalid")
        if isinstance(self.max_results, bool) or not isinstance(self.max_results, int):
            raise ValueError("adapter input integer exceeds the bound")
        if not 1 <= self.max_results <= MAX_RESULTS_MAX:
            raise ValueError("adapter input integer exceeds the bound")


@dataclass(frozen=True, repr=False)
class EmailDraftAdapterInput(AdapterInput):
    recipient: str
    subject: str
    body: str
    action: ProductivityAction = field(
        default=ProductivityAction.EMAIL_DRAFT,
        init=False,
        repr=False,
    )

    def field_text_limit(self, field_name: str) -> int:
        return _EMAIL_FIELD_LIMITS.get(field_name, super().field_text_limit(field_name))

    def total_text_limit(self) -> int:
        return _EMAIL_TOTAL_MAX

    def validate(self) -> None:
        super().validate()
        if not self.recipient or not _valid_text(
            self.recipient, EMAIL_RECIPIENT_MAX, allow_newline_tab=False
        ):
            raise ValueError("adapter input text is invalid")
        if not _valid_text(self.subject, EMAIL_SUBJECT_MAX, allow_newline_tab=False):
            raise ValueError("adapter input text is invalid")
        if not _valid_text(self.body, EMAIL_BODY_MAX, allow_newline_tab=True):
            raise ValueError("adapter input text is invalid")


@dataclass(frozen=True, repr=False)
class CalendarReadAdapterInput(AdapterInput):
    start: str
    end: str
    calendar_name: str | None
    action: ProductivityAction = field(
        default=ProductivityAction.CALENDAR_READ,
        init=False,
        repr=False,
    )

    def field_text_limit(self, field_name: str) -> int:
        return _CALENDAR_READ_FIELD_LIMITS.get(
            field_name, super().field_text_limit(field_name)
        )

    def total_text_limit(self) -> int:
        return _CALENDAR_READ_TOTAL_MAX

    def validate(self) -> None:
        super().validate()
        if not _valid_text(self.start, _DATETIME_ISO_MAX, allow_newline_tab=False):
            raise ValueError("adapter input text is invalid")
        if not _valid_text(self.end, _DATETIME_ISO_MAX, allow_newline_tab=False):
            raise ValueError("adapter input text is invalid")
        if self.calendar_name is not None:
            if self.calendar_name == "" or not _valid_text(
                self.calendar_name, CALENDAR_NAME_MAX, allow_newline_tab=False
            ):
                raise ValueError("adapter input text is invalid")


@dataclass(frozen=True, repr=False)
class CalendarDraftAdapterInput(AdapterInput):
    title: str
    start: str
    end: str
    calendar_name: str
    location: str | None
    notes: str | None
    action: ProductivityAction = field(
        default=ProductivityAction.CALENDAR_DRAFT,
        init=False,
        repr=False,
    )

    def field_text_limit(self, field_name: str) -> int:
        return _CALENDAR_DRAFT_FIELD_LIMITS.get(
            field_name, super().field_text_limit(field_name)
        )

    def total_text_limit(self) -> int:
        return _CALENDAR_DRAFT_TOTAL_MAX

    def validate(self) -> None:
        super().validate()
        if not isinstance(self.title, str) or self.title.strip() == "":
            raise ValueError("adapter input text is invalid")
        if not _valid_text(self.title, CALENDAR_TITLE_MAX, allow_newline_tab=False):
            raise ValueError("adapter input text is invalid")
        if not _valid_text(self.start, _DATETIME_ISO_MAX, allow_newline_tab=False):
            raise ValueError("adapter input text is invalid")
        if not _valid_text(self.end, _DATETIME_ISO_MAX, allow_newline_tab=False):
            raise ValueError("adapter input text is invalid")
        if not isinstance(self.calendar_name, str) or self.calendar_name == "":
            raise ValueError("adapter input text is invalid")
        if not _valid_text(
            self.calendar_name, CALENDAR_NAME_MAX, allow_newline_tab=False
        ):
            raise ValueError("adapter input text is invalid")
        if self.location is not None and not _valid_text(
            self.location, CALENDAR_LOCATION_MAX, allow_newline_tab=True
        ):
            raise ValueError("adapter input text is invalid")
        if self.notes is not None and not _valid_text(
            self.notes, CALENDAR_NOTES_MAX, allow_newline_tab=True
        ):
            raise ValueError("adapter input text is invalid")


@dataclass(frozen=True, repr=False)
class ReminderCreateAdapterInput(AdapterInput):
    title: str
    remind_at: str
    notes: str | None
    list_name: str | None
    action: ProductivityAction = field(
        default=ProductivityAction.REMINDER_CREATE,
        init=False,
        repr=False,
    )

    def field_text_limit(self, field_name: str) -> int:
        return _REMINDER_FIELD_LIMITS.get(
            field_name, super().field_text_limit(field_name)
        )

    def total_text_limit(self) -> int:
        return _REMINDER_TOTAL_MAX

    def validate(self) -> None:
        super().validate()
        if not isinstance(self.title, str) or self.title.strip() == "":
            raise ValueError("adapter input text is invalid")
        if not _valid_text(self.title, REMINDER_TITLE_MAX, allow_newline_tab=False):
            raise ValueError("adapter input text is invalid")
        if not _valid_text(self.remind_at, _DATETIME_ISO_MAX, allow_newline_tab=False):
            raise ValueError("adapter input text is invalid")
        if self.notes is not None and not _valid_text(
            self.notes, REMINDER_NOTES_MAX, allow_newline_tab=True
        ):
            raise ValueError("adapter input text is invalid")
        if self.list_name is not None:
            if self.list_name == "" or not _valid_text(
                self.list_name, REMINDER_LIST_NAME_MAX, allow_newline_tab=False
            ):
                raise ValueError("adapter input text is invalid")


def browser_research_adapter_input_from_prepared(
    prepared: object,
) -> BrowserResearchAdapterInput:
    _require_prepared(prepared, PreparedResearchInput)
    assert isinstance(prepared, PreparedResearchInput)
    return _finalize(
        BrowserResearchAdapterInput(
            prepared.query,
            prepared.domains,
            prepared.max_results,
        )
    )


def email_draft_adapter_input_from_prepared(
    prepared: object,
) -> EmailDraftAdapterInput:
    _require_prepared(prepared, PreparedEmailDraft)
    assert isinstance(prepared, PreparedEmailDraft)
    return _finalize(
        EmailDraftAdapterInput(
            prepared.recipient,
            prepared.subject,
            prepared.body,
        )
    )


def calendar_read_adapter_input_from_prepared(
    prepared: object,
) -> CalendarReadAdapterInput:
    _require_prepared(prepared, PreparedCalendarRead)
    assert isinstance(prepared, PreparedCalendarRead)
    return _finalize(
        CalendarReadAdapterInput(
            _canonical_aware_iso(prepared.start),
            _canonical_aware_iso(prepared.end),
            prepared.calendar_name,
        )
    )


def calendar_draft_adapter_input_from_prepared(
    prepared: object,
) -> CalendarDraftAdapterInput:
    _require_prepared(prepared, PreparedCalendarEventDraft)
    assert isinstance(prepared, PreparedCalendarEventDraft)
    return _finalize(
        CalendarDraftAdapterInput(
            prepared.title,
            _canonical_aware_iso(prepared.start),
            _canonical_aware_iso(prepared.end),
            prepared.calendar_name,
            prepared.location,
            prepared.notes,
        )
    )


def reminder_create_adapter_input_from_prepared(
    prepared: object,
) -> ReminderCreateAdapterInput:
    _require_prepared(prepared, PreparedReminderInput)
    assert isinstance(prepared, PreparedReminderInput)
    return _finalize(
        ReminderCreateAdapterInput(
            prepared.title,
            _canonical_aware_iso(prepared.remind_at),
            prepared.notes,
            prepared.list_name,
        )
    )


def adapter_input_from_prepared(
    action: ProductivityAction,
    prepared: object,
) -> AdapterInput:
    if not isinstance(action, ProductivityAction):
        raise ActionInputConversionError()
    if action is ProductivityAction.BROWSER_RESEARCH:
        return browser_research_adapter_input_from_prepared(prepared)
    if action is ProductivityAction.EMAIL_DRAFT:
        return email_draft_adapter_input_from_prepared(prepared)
    if action is ProductivityAction.CALENDAR_READ:
        return calendar_read_adapter_input_from_prepared(prepared)
    if action is ProductivityAction.CALENDAR_DRAFT:
        return calendar_draft_adapter_input_from_prepared(prepared)
    if action is ProductivityAction.REMINDER_CREATE:
        return reminder_create_adapter_input_from_prepared(prepared)
    raise ActionInputConversionError()
