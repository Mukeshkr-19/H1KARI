"""Deterministic tests for the bounded Phase 3 execution dispatch resolver."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.action_policy import Actor, ActorContext
from core.productivity import (
    CalendarPreparationRegistry,
    EmailDraftPreparationRegistry,
    ReminderPreparationRegistry,
    ResearchPreparationRegistry,
)
from core.productivity.action_inputs import (
    BrowserResearchAdapterInput,
    CalendarDraftAdapterInput,
    CalendarReadAdapterInput,
    EmailDraftAdapterInput,
    ReminderCreateAdapterInput,
)
from core.productivity.calendar import (
    PreparedCalendarEventDraft,
    PreparedCalendarRead,
)
from core.productivity.contracts import ProductivityAction
from core.productivity.dispatch import DispatchError, build_execution_request
from core.productivity.email_draft import PreparedEmailDraft
from core.productivity.reminder import PreparedReminderInput
from core.productivity.research import PreparedResearchInput
from core.productivity.runtime import ConfirmationResult


@pytest.fixture
def owner() -> ActorContext:
    return ActorContext(actor_id="owner_1", actor=Actor.OWNER, session_id="session_1")


@pytest.fixture
def other_session(owner: ActorContext) -> ActorContext:
    return ActorContext(
        actor_id=owner.actor_id,
        actor=Actor.OWNER,
        session_id="session_2",
    )


@pytest.fixture
def registries() -> tuple[
    EmailDraftPreparationRegistry,
    CalendarPreparationRegistry,
    ResearchPreparationRegistry,
    ReminderPreparationRegistry,
]:
    return (
        EmailDraftPreparationRegistry(),
        CalendarPreparationRegistry(),
        ResearchPreparationRegistry(),
        ReminderPreparationRegistry(),
    )


def _confirmation(approval_id: str = "approval_1") -> ConfirmationResult:
    return ConfirmationResult(
        public_message={"type": "productivity_update", "proposal_id": "proposal_1", "status": "approved"},
        approval_id=approval_id,
        proposal_id="proposal_1",
    )


def test_dispatch_resolves_email_draft(
    owner: ActorContext,
    registries: tuple,
) -> None:
    email_registry, calendar_registry, research_registry, reminder_registry = registries
    prepared = PreparedEmailDraft("user@example.com", "Subject", "Body")
    email_registry.put(owner, "proposal_1", prepared)

    request = build_execution_request(
        owner,
        "proposal_1",
        _confirmation(),
        email_registry=email_registry,
        calendar_registry=calendar_registry,
        research_registry=research_registry,
        reminder_registry=reminder_registry,
    )

    assert request.action is ProductivityAction.EMAIL_DRAFT
    assert request.proposal_id == "proposal_1"
    assert request.approval_id == "approval_1"
    assert isinstance(request.adapter_input, EmailDraftAdapterInput)


def test_dispatch_resolves_calendar_read(
    owner: ActorContext,
    registries: tuple,
) -> None:
    email_registry, calendar_registry, research_registry, reminder_registry = registries
    now = datetime.now(timezone.utc)
    prepared = PreparedCalendarRead(now, now + timedelta(hours=1), None)
    calendar_registry.put(owner, "proposal_1", prepared)

    request = build_execution_request(
        owner,
        "proposal_1",
        _confirmation(),
        email_registry=email_registry,
        calendar_registry=calendar_registry,
        research_registry=research_registry,
        reminder_registry=reminder_registry,
    )

    assert request.action is ProductivityAction.CALENDAR_READ
    assert isinstance(request.adapter_input, CalendarReadAdapterInput)


def test_dispatch_resolves_calendar_draft(
    owner: ActorContext,
    registries: tuple,
) -> None:
    email_registry, calendar_registry, research_registry, reminder_registry = registries
    now = datetime.now(timezone.utc)
    prepared = PreparedCalendarEventDraft("Title", now, now + timedelta(hours=1), "Work", None, None)
    calendar_registry.put(owner, "proposal_1", prepared)

    request = build_execution_request(
        owner,
        "proposal_1",
        _confirmation(),
        email_registry=email_registry,
        calendar_registry=calendar_registry,
        research_registry=research_registry,
        reminder_registry=reminder_registry,
    )

    assert request.action is ProductivityAction.CALENDAR_DRAFT
    assert isinstance(request.adapter_input, CalendarDraftAdapterInput)
    assert request.adapter_input.calendar_name == "Work"


def test_dispatch_resolves_browser_research(
    owner: ActorContext,
    registries: tuple,
) -> None:
    email_registry, calendar_registry, research_registry, reminder_registry = registries
    prepared = PreparedResearchInput("hikari", ("example.com",), 10)
    research_registry.put(owner, "proposal_1", prepared)

    request = build_execution_request(
        owner,
        "proposal_1",
        _confirmation(),
        email_registry=email_registry,
        calendar_registry=calendar_registry,
        research_registry=research_registry,
        reminder_registry=reminder_registry,
    )

    assert request.action is ProductivityAction.BROWSER_RESEARCH
    assert isinstance(request.adapter_input, BrowserResearchAdapterInput)


def test_dispatch_resolves_reminder_create(
    owner: ActorContext,
    registries: tuple,
) -> None:
    email_registry, calendar_registry, research_registry, reminder_registry = registries
    now = datetime.now(timezone.utc)
    prepared = PreparedReminderInput("Title", now, None, None)
    reminder_registry.put(owner, "proposal_1", prepared)

    request = build_execution_request(
        owner,
        "proposal_1",
        _confirmation(),
        email_registry=email_registry,
        calendar_registry=calendar_registry,
        research_registry=research_registry,
        reminder_registry=reminder_registry,
    )

    assert request.action is ProductivityAction.REMINDER_CREATE
    assert isinstance(request.adapter_input, ReminderCreateAdapterInput)


def test_dispatch_fails_closed_on_zero_matches(
    owner: ActorContext,
    registries: tuple,
) -> None:
    email_registry, calendar_registry, research_registry, reminder_registry = registries

    with pytest.raises(DispatchError):
        build_execution_request(
            owner,
            "proposal_1",
            _confirmation(),
            email_registry=email_registry,
            calendar_registry=calendar_registry,
            research_registry=research_registry,
            reminder_registry=reminder_registry,
        )


def test_dispatch_fails_closed_on_multiple_matches(
    owner: ActorContext,
    registries: tuple,
) -> None:
    email_registry, calendar_registry, research_registry, reminder_registry = registries
    email_registry.put(owner, "proposal_1", PreparedEmailDraft("a@b.com", "S", "B"))
    reminder_registry.put(
        owner,
        "proposal_1",
        PreparedReminderInput("Title", datetime.now(timezone.utc), None, None),
    )

    with pytest.raises(DispatchError):
        build_execution_request(
            owner,
            "proposal_1",
            _confirmation(),
            email_registry=email_registry,
            calendar_registry=calendar_registry,
            research_registry=research_registry,
            reminder_registry=reminder_registry,
        )


def test_dispatch_fails_closed_for_cross_session_entry(
    owner: ActorContext,
    other_session: ActorContext,
    registries: tuple,
) -> None:
    email_registry, calendar_registry, research_registry, reminder_registry = registries
    email_registry.put(other_session, "proposal_1", PreparedEmailDraft("a@b.com", "S", "B"))

    with pytest.raises(DispatchError):
        build_execution_request(
            owner,
            "proposal_1",
            _confirmation(),
            email_registry=email_registry,
            calendar_registry=calendar_registry,
            research_registry=research_registry,
            reminder_registry=reminder_registry,
        )


def test_dispatch_fails_closed_without_valid_approval_id(
    owner: ActorContext,
    registries: tuple,
) -> None:
    email_registry, calendar_registry, research_registry, reminder_registry = registries
    email_registry.put(owner, "proposal_1", PreparedEmailDraft("a@b.com", "S", "B"))

    empty_confirmation = ConfirmationResult(
        public_message={"type": "productivity_update", "proposal_id": "proposal_1", "status": "approved"},
        approval_id="",
        proposal_id="proposal_1",
    )

    with pytest.raises(DispatchError):
        build_execution_request(
            owner,
            "proposal_1",
            empty_confirmation,
            email_registry=email_registry,
            calendar_registry=calendar_registry,
            research_registry=research_registry,
            reminder_registry=reminder_registry,
        )

    none_confirmation = ConfirmationResult(
        public_message={"type": "productivity_update", "proposal_id": "proposal_1", "status": "approved"},
        approval_id=None,  # type: ignore[arg-type]
        proposal_id="proposal_1",
    )

    with pytest.raises(DispatchError):
        build_execution_request(
            owner,
            "proposal_1",
            none_confirmation,
            email_registry=email_registry,
            calendar_registry=calendar_registry,
            research_registry=research_registry,
            reminder_registry=reminder_registry,
        )


def test_dispatch_does_not_remove_prepared_input(
    owner: ActorContext,
    registries: tuple,
) -> None:
    email_registry, calendar_registry, research_registry, reminder_registry = registries
    prepared = PreparedEmailDraft("user@example.com", "Subject", "Body")
    email_registry.put(owner, "proposal_1", prepared)

    build_execution_request(
        owner,
        "proposal_1",
        _confirmation(),
        email_registry=email_registry,
        calendar_registry=calendar_registry,
        research_registry=research_registry,
        reminder_registry=reminder_registry,
    )

    assert email_registry.get(owner, "proposal_1") is prepared


# ---------------------------------------------------------------------------
# ConfirmationResult validation (defect 5/6/7)
# ---------------------------------------------------------------------------


def _approved_message(proposal_id: str = "proposal_1") -> dict:
    return {
        "type": "productivity_update",
        "proposal_id": proposal_id,
        "status": "approved",
    }


def test_dispatch_accepts_valid_confirmation(
    owner: ActorContext,
    registries: tuple,
) -> None:
    email_registry, calendar_registry, research_registry, reminder_registry = registries
    email_registry.put(owner, "proposal_1", PreparedEmailDraft("a@b.com", "S", "B"))

    request = build_execution_request(
        owner,
        "proposal_1",
        _confirmation(),
        email_registry=email_registry,
        calendar_registry=calendar_registry,
        research_registry=research_registry,
        reminder_registry=reminder_registry,
    )
    assert request.approval_id == "approval_1"
    assert request.proposal_id == "proposal_1"


@pytest.mark.parametrize(
    "confirmation",
    [
        # Not a ConfirmationResult at all.
        {"public_message": _approved_message(), "approval_id": "approval_1"},
        # proposal_id mismatch (ConfirmationResult.proposal_id != requested).
        ConfirmationResult(
            public_message=_approved_message(),
            approval_id="approval_1",
            proposal_id="proposal_other",
        ),
        # Wrong public message type.
        ConfirmationResult(
            public_message={"type": "productivity_error", "proposal_id": "proposal_1", "code": "unavailable"},
            approval_id="approval_1",
            proposal_id="proposal_1",
        ),
        # Wrong status.
        ConfirmationResult(
            public_message={"type": "productivity_update", "proposal_id": "proposal_1", "status": "completed"},
            approval_id="approval_1",
            proposal_id="proposal_1",
        ),
        # Public message proposal_id mismatch.
        ConfirmationResult(
            public_message={"type": "productivity_update", "proposal_id": "proposal_other", "status": "approved"},
            approval_id="approval_1",
            proposal_id="proposal_1",
        ),
        # Invalid approval_id syntax.
        ConfirmationResult(
            public_message=_approved_message(),
            approval_id="BAD ID",
            proposal_id="proposal_1",
        ),
        # Extra field in public message.
        ConfirmationResult(
            public_message={"type": "productivity_update", "proposal_id": "proposal_1", "status": "approved", "extra": "x"},
            approval_id="approval_1",
            proposal_id="proposal_1",
        ),
    ],
)
def test_dispatch_rejects_malformed_confirmation(
    owner: ActorContext,
    registries: tuple,
    confirmation: object,
) -> None:
    email_registry, calendar_registry, research_registry, reminder_registry = registries
    email_registry.put(owner, "proposal_1", PreparedEmailDraft("a@b.com", "S", "B"))

    with pytest.raises(DispatchError):
        build_execution_request(
            owner,
            "proposal_1",
            confirmation,  # type: ignore[arg-type]
            email_registry=email_registry,
            calendar_registry=calendar_registry,
            research_registry=research_registry,
            reminder_registry=reminder_registry,
        )


def test_dispatch_rejects_invalid_actor(
    owner: ActorContext,
    registries: tuple,
) -> None:
    email_registry, calendar_registry, research_registry, reminder_registry = registries
    email_registry.put(owner, "proposal_1", PreparedEmailDraft("a@b.com", "S", "B"))

    with pytest.raises(DispatchError):
        build_execution_request(
            "not-an-actor",  # type: ignore[arg-type]
            "proposal_1",
            _confirmation(),
            email_registry=email_registry,
            calendar_registry=calendar_registry,
            research_registry=research_registry,
            reminder_registry=reminder_registry,
        )


def test_dispatch_rejects_invalid_proposal_id(
    owner: ActorContext,
    registries: tuple,
) -> None:
    email_registry, calendar_registry, research_registry, reminder_registry = registries
    email_registry.put(owner, "proposal_1", PreparedEmailDraft("a@b.com", "S", "B"))

    with pytest.raises(DispatchError):
        build_execution_request(
            owner,
            "BAD ID",
            _confirmation(),
            email_registry=email_registry,
            calendar_registry=calendar_registry,
            research_registry=research_registry,
            reminder_registry=reminder_registry,
        )


def test_dispatch_rejects_registry_exception(
    owner: ActorContext,
    registries: tuple,
) -> None:
    email_registry, calendar_registry, research_registry, reminder_registry = registries

    class BoomRegistry:
        def get(self, actor: object, proposal_id: object) -> object:
            raise RuntimeError("registry down")

    with pytest.raises(DispatchError):
        build_execution_request(
            owner,
            "proposal_1",
            _confirmation(),
            email_registry=BoomRegistry(),
            calendar_registry=calendar_registry,
            research_registry=research_registry,
            reminder_registry=reminder_registry,
        )


def test_dispatch_rejects_malformed_calendar_registry_object(
    owner: ActorContext,
    registries: tuple,
) -> None:
    email_registry, calendar_registry, research_registry, reminder_registry = registries

    class QuietRegistry:
        def get(self, actor: object, proposal_id: object) -> object:
            return object()  # not a known prepared type

    with pytest.raises(DispatchError):
        build_execution_request(
            owner,
            "proposal_1",
            _confirmation(),
            email_registry=email_registry,
            calendar_registry=QuietRegistry(),
            research_registry=research_registry,
            reminder_registry=reminder_registry,
        )


def test_dispatch_rejects_conversion_exception(
    owner: ActorContext,
    registries: tuple,
) -> None:
    email_registry, calendar_registry, research_registry, reminder_registry = registries
    from datetime import datetime, timezone

    from core.productivity.action_inputs import ActionInputConversionError
    from core.productivity.calendar import PreparedCalendarRead

    calendar_registry.put(
        owner,
        "proposal_1",
        PreparedCalendarRead(datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, 1, tzinfo=timezone.utc), None),
    )

    # Force the conversion bridge to raise, exercising the conversion-exception
    # path that must collapse into the single generic DispatchError.
    original = build_execution_request.__globals__["adapter_input_from_prepared"]

    def _boom(action, prepared):
        raise ActionInputConversionError()

    try:
        build_execution_request.__globals__["adapter_input_from_prepared"] = _boom
        with pytest.raises(DispatchError):
            build_execution_request(
                owner,
                "proposal_1",
                _confirmation(),
                email_registry=email_registry,
                calendar_registry=calendar_registry,
                research_registry=research_registry,
                reminder_registry=reminder_registry,
            )
    finally:
        build_execution_request.__globals__["adapter_input_from_prepared"] = original


def test_dispatch_single_generic_error_message(
    owner: ActorContext,
    registries: tuple,
) -> None:
    email_registry, calendar_registry, research_registry, reminder_registry = registries
    email_registry.put(owner, "proposal_1", PreparedEmailDraft("a@b.com", "S", "B"))

    seen = set()
    for bad in (
        ConfirmationResult(
            public_message=_approved_message(),
            approval_id="approval_1",
            proposal_id="proposal_other",
        ),
        ConfirmationResult(
            public_message={"type": "productivity_update", "proposal_id": "proposal_1", "status": "approved", "extra": "x"},
            approval_id="approval_1",
            proposal_id="proposal_1",
        ),
    ):
        try:
            build_execution_request(
                owner,
                "proposal_1",
                bad,  # type: ignore[arg-type]
                email_registry=email_registry,
                calendar_registry=calendar_registry,
                research_registry=research_registry,
                reminder_registry=reminder_registry,
            )
        except DispatchError as exc:
            seen.add(str(exc))
    # Every dispatch failure collapses to one fixed generic message.
    assert seen == {"dispatch failed"}
