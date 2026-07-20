"""Deterministic tests for the Phase 3 pure calendar preparation contracts.

These tests cover only ``core.productivity.calendar``. They perform no I/O,
network, subprocess, EventKit, AppleScript, email, browser, reminders, MCP,
provider, or execution activity, and assert the absence of those imports.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

import pytest

from core.action_policy import Actor, ActorContext
from core.productivity import calendar as cal
from core.productivity.calendar import (
    CalendarDraftPreparation,
    CalendarDraftProposalFactory,
    CalendarPreparationError,
    CalendarPreparationRegistry,
    CalendarReadPreparation,
    CalendarReadProposalFactory,
    PreparedCalendarEventDraft,
    PreparedCalendarRead,
)

UTC = timezone.utc
NY = ZoneInfo("America/New_York")


def _aware(year, month, day, hour, minute, tz=UTC):
    return datetime(year, month, day, hour, minute, tzinfo=tz)


def _owner(actor_id="actor-1", session_id="session-1"):
    return ActorContext(
        actor_id=actor_id,
        actor=Actor.OWNER,
        session_id=session_id,
        source="text",
    )


def _fixed_clock(value):
    return lambda: value


def _id_factory(values):
    it = iter(values)
    return lambda: next(it)


# --------------------------------------------------------------------------
# Valid read proposals
# --------------------------------------------------------------------------


def test_read_preparation_valid():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-read-1"
    )
    prep = factory.prepare(_owner(), _aware(2026, 7, 18, 9, 0), _aware(2026, 7, 18, 10, 0))
    assert isinstance(prep, CalendarReadPreparation)
    assert prep.proposal.action.value == "calendar.read"
    assert prep.proposal.proposal_id == "p-read-1"
    assert prep.proposal.created_at == 1000.0
    assert prep.proposal.expires_at == 1000.0 + cal.CALENDAR_PROPOSAL_TTL
    assert prep.read.start == _aware(2026, 7, 18, 9, 0)
    assert prep.read.end == _aware(2026, 7, 18, 10, 0)


def test_read_preparation_with_calendar_name():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-read-2"
    )
    prep = factory.prepare(
        _owner(),
        _aware(2026, 7, 18, 9, 0),
        _aware(2026, 7, 18, 10, 0),
        calendar_name="Work",
    )
    assert prep.read.calendar_name == "Work"
    kinds = {t.kind.value for t in prep.proposal.targets}
    assert "calendar" in kinds
    keys = {f.key for f in prep.proposal.preview_fields}
    assert "calendar" in keys


def test_read_preparation_excludes_calendar_name_when_none():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-read-3"
    )
    prep = factory.prepare(
        _owner(), _aware(2026, 7, 18, 9, 0), _aware(2026, 7, 18, 10, 0)
    )
    assert prep.read.calendar_name is None
    assert prep.proposal.targets == ()


# --------------------------------------------------------------------------
# Valid draft proposals
# --------------------------------------------------------------------------


def test_draft_preparation_valid():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-draft-1"
    )
    prep = factory.prepare(
        _owner(),
        "Team Sync",
        _aware(2026, 7, 18, 9, 0),
        _aware(2026, 7, 18, 10, 0),
        "Work",
        location="Office",
        notes="Bring agenda",
    )
    assert isinstance(prep, CalendarDraftPreparation)
    assert prep.proposal.action.value == "calendar.draft"
    assert prep.draft.title == "Team Sync"
    assert prep.draft.location == "Office"
    assert prep.draft.notes == "Bring agenda"
    keys = {f.key for f in prep.proposal.preview_fields}
    assert keys == {"title", "start", "end", "calendar", "location", "notes"}
    assert prep.draft.calendar_name == "Work"
    assert prep.proposal.targets[0].value == "Work"


def test_draft_preparation_optional_fields_none():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-draft-2"
    )
    prep = factory.prepare(
        _owner(),
        "Standup",
        _aware(2026, 7, 18, 9, 0),
        _aware(2026, 7, 18, 9, 30),
        "Work",
    )
    assert prep.draft.location is None
    assert prep.draft.notes is None
    keys = {f.key for f in prep.proposal.preview_fields}
    assert keys == {"title", "start", "end", "calendar"}
    assert prep.draft.calendar_name == "Work"


# --------------------------------------------------------------------------
# Date boundaries
# --------------------------------------------------------------------------


def test_read_rejects_start_after_end():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-bad-1"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            _aware(2026, 7, 18, 10, 0),
            _aware(2026, 7, 18, 9, 0),
        )


def test_read_rejects_equal_start_end():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-bad-2"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            _aware(2026, 7, 18, 9, 0),
            _aware(2026, 7, 18, 9, 0),
        )


def test_draft_rejects_start_after_end():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-bad-3"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            "X",
            _aware(2026, 7, 18, 10, 0),
            _aware(2026, 7, 18, 9, 0),
            "Work",
        )


def test_read_accepts_naive_rejected_by_prepared_type():
    # PreparedCalendarRead requires timezone-aware datetimes.
    with pytest.raises(CalendarPreparationError):
        PreparedCalendarRead(
            datetime(2026, 7, 18, 9, 0), datetime(2026, 7, 18, 10, 0), None
        )


def test_draft_accepts_timezone_aware_across_zones():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-tz-1"
    )
    prep = factory.prepare(
        _owner(),
        "Cross",
        _aware(2026, 7, 18, 9, 0, tz=NY),
        _aware(2026, 7, 18, 10, 0, tz=NY),
        "Work",
    )
    assert prep.draft.start.tzinfo is not None


# --------------------------------------------------------------------------
# Excessive ranges
# --------------------------------------------------------------------------


def test_read_rejects_excessive_range():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-range-1"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            _aware(2026, 1, 1, 0, 0),
            _aware(2027, 1, 1, 0, 0),  # > one month
        )


def test_draft_rejects_excessive_range():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-range-2"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            "Long",
            _aware(2026, 1, 1, 0, 0),
            _aware(2027, 1, 1, 0, 0),
            "Work",
        )


def test_read_accepts_one_month_range_boundary():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-range-3"
    )
    prep = factory.prepare(
        _owner(),
        _aware(2026, 7, 1, 0, 0),
        _aware(2026, 8, 1, 0, 0),
    )
    assert prep.read.end == _aware(2026, 8, 1, 0, 0)


# --------------------------------------------------------------------------
# Malformed text
# --------------------------------------------------------------------------


def test_read_rejects_control_chars_in_calendar_name():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-txt-1"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            _aware(2026, 7, 18, 9, 0),
            _aware(2026, 7, 18, 10, 0),
            calendar_name="Bad\x01Name",
        )


def test_draft_rejects_control_chars_in_title():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-txt-2"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            "Bad\x7fTitle",
            _aware(2026, 7, 18, 9, 0),
            _aware(2026, 7, 18, 10, 0),
            "Work",
        )


def test_draft_rejects_control_chars_in_location():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-txt-3"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            "Title",
            _aware(2026, 7, 18, 9, 0),
            _aware(2026, 7, 18, 10, 0),
            "Work",
            location="Loc\x00",
        )


def test_draft_rejects_control_chars_in_notes():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-txt-4"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            "Title",
            _aware(2026, 7, 18, 9, 0),
            _aware(2026, 7, 18, 10, 0),
            "Work",
            notes="Note\x01x",
        )


def test_draft_rejects_overlong_title():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-txt-5"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            "T" * (cal.CALENDAR_TITLE_MAX + 1),
            _aware(2026, 7, 18, 9, 0),
            _aware(2026, 7, 18, 10, 0),
            "Work",
        )


# --------------------------------------------------------------------------
# Unicode Cf (format characters)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "\u202eReversed",  # RIGHT-TO-LEFT OVERRIDE
        "Zero\u200bWidth",  # ZERO WIDTH SPACE
        "Soft\u00adHyphen",  # SOFT HYPHEN
    ],
)
def test_draft_rejects_unicode_cf_in_title(text):
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-cf-1"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            text,
            _aware(2026, 7, 18, 9, 0),
            _aware(2026, 7, 18, 10, 0),
            "Work",
        )


def test_draft_accepts_normal_unicode_title():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-cf-2"
    )
    prep = factory.prepare(
        _owner(),
        "Café Réunion — Équipe",
        _aware(2026, 7, 18, 9, 0),
        _aware(2026, 7, 18, 10, 0),
        "Work",
    )
    assert prep.draft.title == "Café Réunion — Équipe"


# --------------------------------------------------------------------------
# Invalid factory output
# --------------------------------------------------------------------------


def test_read_rejects_invalid_proposal_id():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "BAD ID!"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(), _aware(2026, 7, 18, 9, 0), _aware(2026, 7, 18, 10, 0)
        )


def test_read_rejects_non_string_proposal_id():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: 12345
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(), _aware(2026, 7, 18, 9, 0), _aware(2026, 7, 18, 10, 0)
        )


def test_read_rejects_nan_clock():
    factory = CalendarReadProposalFactory(
        clock=lambda: float("nan"), proposal_id_factory=lambda: "p-nan-1"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(), _aware(2026, 7, 18, 9, 0), _aware(2026, 7, 18, 10, 0)
        )


def test_read_rejects_boolean_clock():
    factory = CalendarReadProposalFactory(
        clock=lambda: True, proposal_id_factory=lambda: "p-bool-1"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(), _aware(2026, 7, 18, 9, 0), _aware(2026, 7, 18, 10, 0)
        )


def test_read_rejects_clock_exception():
    factory = CalendarReadProposalFactory(
        clock=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        proposal_id_factory=lambda: "p-exc-1",
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(), _aware(2026, 7, 18, 9, 0), _aware(2026, 7, 18, 10, 0)
        )


def test_factory_rejects_bad_ttl():
    with pytest.raises(ValueError):
        CalendarReadProposalFactory(
            clock=_fixed_clock(1000.0),
            proposal_id_factory=lambda: "x",
            ttl_seconds=0,
        )
    with pytest.raises(ValueError):
        CalendarReadProposalFactory(
            clock=_fixed_clock(1000.0),
            proposal_id_factory=lambda: "x",
            ttl_seconds=901,
        )


def test_factory_rejects_non_callable():
    with pytest.raises(TypeError):
        CalendarReadProposalFactory(clock="nope", proposal_id_factory=lambda: "x")


# --------------------------------------------------------------------------
# Expiration
# --------------------------------------------------------------------------


def test_proposal_expiration_within_fifteen_minutes():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(5000.0), proposal_id_factory=lambda: "p-exp-1"
    )
    prep = factory.prepare(
        _owner(), _aware(2026, 7, 18, 9, 0), _aware(2026, 7, 18, 10, 0)
    )
    # TTL is 900s == 15 minutes, the maximum allowed.
    assert prep.proposal.expires_at - prep.proposal.created_at == 900.0
    assert prep.proposal.expires_at <= prep.proposal.created_at + 900.0


def test_proposal_is_expired_after_ttl():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(5000.0), proposal_id_factory=lambda: "p-exp-2"
    )
    prep = factory.prepare(
        _owner(), _aware(2026, 7, 18, 9, 0), _aware(2026, 7, 18, 10, 0)
    )
    assert prep.proposal.is_expired(5000.0 + 900.0) is True
    assert prep.proposal.is_expired(5000.0 + 899.0) is False


# --------------------------------------------------------------------------
# Registry capacity
# --------------------------------------------------------------------------


def _sample_read() -> PreparedCalendarRead:
    return PreparedCalendarRead(
        _aware(2026, 7, 18, 9, 0), _aware(2026, 7, 18, 10, 0), None
    )


def _sample_draft() -> PreparedCalendarEventDraft:
    return PreparedCalendarEventDraft(
        "Sample Title",
        _aware(2026, 7, 18, 9, 0),
        _aware(2026, 7, 18, 10, 0),
        "Work",
        None,
        None,
    )


def test_registry_put_and_get():
    reg = CalendarPreparationRegistry()
    actor = _owner()
    item = _sample_read()
    reg.put(actor, "p-reg-1", item)
    assert reg.get(actor, "p-reg-1") is item


def test_registry_capacity_limit():
    reg = CalendarPreparationRegistry(limit=2)
    actor = _owner()
    reg.put(actor, "p-1", _sample_read())
    reg.put(actor, "p-2", _sample_draft())
    with pytest.raises(CalendarPreparationError):
        reg.put(actor, "p-3", _sample_read())


def test_registry_rejects_invalid_limit():
    with pytest.raises(ValueError):
        CalendarPreparationRegistry(limit=0)
    with pytest.raises(ValueError):
        CalendarPreparationRegistry(limit=65)
    with pytest.raises(ValueError):
        CalendarPreparationRegistry(limit=True)  # type: ignore[arg-type]


def test_registry_rejects_bad_actor_or_id():
    reg = CalendarPreparationRegistry()
    with pytest.raises(CalendarPreparationError):
        reg.put(_owner(actor_id="bad id"), "p-x", _sample_read())
    with pytest.raises(CalendarPreparationError):
        reg.get(_owner(), "BAD ID!")


# --------------------------------------------------------------------------
# Cross-session isolation
# --------------------------------------------------------------------------


def test_registry_cross_session_isolation():
    reg = CalendarPreparationRegistry()
    owner_a = _owner(session_id="session-a")
    owner_b = _owner(session_id="session-b")
    item = _sample_read()
    reg.put(owner_a, "p-shared", item)
    assert reg.get(owner_b, "p-shared") is None
    assert reg.get(owner_a, "p-shared") is item


def test_registry_cross_actor_isolation():
    reg = CalendarPreparationRegistry()
    a = _owner(actor_id="actor-a")
    b = _owner(actor_id="actor-b")
    item = _sample_draft()
    reg.put(a, "p-x", item)
    assert reg.get(b, "p-x") is None


# --------------------------------------------------------------------------
# Cleanup
# --------------------------------------------------------------------------


def test_registry_remove():
    reg = CalendarPreparationRegistry()
    actor = _owner()
    reg.put(actor, "p-rm-1", _sample_read())
    reg.remove(actor, "p-rm-1")
    assert reg.get(actor, "p-rm-1") is None


def test_registry_clear_session():
    reg = CalendarPreparationRegistry()
    a1 = _owner(session_id="s1")
    a2 = _owner(session_id="s2")
    reg.put(a1, "p-1", _sample_read())
    reg.put(a1, "p-2", _sample_draft())
    reg.put(a2, "p-3", _sample_read())
    reg.clear_session("actor-1", "s1")
    assert reg.get(a1, "p-1") is None
    assert reg.get(a1, "p-2") is None
    assert reg.get(a2, "p-3") is not None


# --------------------------------------------------------------------------
# Repr redaction
# --------------------------------------------------------------------------


def test_prepared_read_repr_excludes_content():
    read = PreparedCalendarRead(
        _aware(2026, 7, 18, 9, 0), _aware(2026, 7, 18, 10, 0), "Secret Cal"
    )
    text = repr(read)
    assert "Secret Cal" not in text
    assert "09:00" not in text
    assert text == "PreparedCalendarRead(...)"


def test_prepared_draft_repr_excludes_content():
    draft = PreparedCalendarEventDraft(
        "Secret Title",
        _aware(2026, 7, 18, 9, 0),
        _aware(2026, 7, 18, 10, 0),
        "Work",
        "Secret Location",
        "Secret Notes",
    )
    text = repr(draft)
    for forbidden in ("Secret Title", "Secret Location", "Secret Notes", "09:00"):
        assert forbidden not in text
    assert text == "PreparedCalendarEventDraft(...)"


def test_preparation_repr_excludes_content():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-rep-1"
    )
    prep = factory.prepare(
        _owner(),
        "Hidden Title",
        _aware(2026, 7, 18, 9, 0),
        _aware(2026, 7, 18, 10, 0),
        "Work",
        location="Hidden Loc",
        notes="Hidden Notes",
    )
    text = repr(prep)
    for forbidden in (
        "Hidden Title",
        "Hidden Loc",
        "Hidden Notes",
        "p-rep-1",
        "actor-1",
        "session-1",
    ):
        assert forbidden not in text


def test_proposal_inside_preparation_has_content_free_repr():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-rep-2"
    )
    prep = factory.prepare(
        _owner(),
        "Distinctive Calendar Title 98765",
        _aware(2026, 7, 18, 9, 0),
        _aware(2026, 7, 18, 10, 0),
        "Work",
        location="Distinctive Location 12345",
        notes="Distinctive Notes abcdef",
    )
    text = repr(prep.proposal)
    forbidden = (
        "p-rep-2",
        "Distinctive Calendar Title 98765",
        "Distinctive Location 12345",
        "Distinctive Notes abcdef",
        "actor-1",
        "session-1",
        "2000.0",
    )
    for value in forbidden:
        assert value not in text
    # Destination target is present; target value stays out of ActionProposal repr.
    assert text == "ActionProposal(targets=1, preview_fields=6)"
    assert "Work" not in text


def test_draft_destination_change_after_preview_does_not_mutate_frozen_input():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-frozen-1"
    )
    prep = factory.prepare(
        _owner(),
        "Team Sync",
        _aware(2026, 7, 18, 9, 0),
        _aware(2026, 7, 18, 10, 0),
        "Work",
    )
    frozen_name = prep.draft.calendar_name
    later = factory.prepare(
        _owner(),
        "Team Sync",
        _aware(2026, 7, 18, 9, 0),
        _aware(2026, 7, 18, 10, 0),
        "Home",
    )
    assert frozen_name == "Work"
    assert prep.draft.calendar_name == "Work"
    assert prep.proposal.targets[0].value == "Work"
    assert later.draft.calendar_name == "Home"
    assert later.proposal.targets[0].value == "Home"


def test_draft_rejects_empty_calendar_name():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-cal-empty"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            "Title",
            _aware(2026, 7, 18, 9, 0),
            _aware(2026, 7, 18, 10, 0),
            "",
        )


def test_proposal_user_preview_excludes_actor_ids():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-prev-1"
    )
    prep = factory.prepare(
        _owner(), "Title", _aware(2026, 7, 18, 9, 0), _aware(2026, 7, 18, 10, 0), "Work"
    )
    preview = prep.proposal.user_preview()
    serialized = str(preview)
    # user_preview is the explicit client-facing structure; it intentionally
    # carries proposal_id but must never leak actor/session identifiers.
    assert "actor-1" not in serialized
    assert "session-1" not in serialized


# --------------------------------------------------------------------------
# Timezone validation: usable utcoffset, custom tzinfo, exception redaction
# --------------------------------------------------------------------------


class _NaiveOffsetTZ(tzinfo):
    """tzinfo whose utcoffset returns None (no usable offset)."""

    def utcoffset(self, dt):
        return None

    def tzname(self, dt):
        return None

    def dst(self, dt):
        return None


class _RaisingTZ(tzinfo):
    """tzinfo whose utcoffset raises to simulate a broken custom timezone."""

    def utcoffset(self, dt):
        raise ValueError("boom internal detail")

    def tzname(self, dt):
        return None

    def dst(self, dt):
        return None


def _tz_aware(year, month, day, hour, minute, tzinfo):
    return datetime(year, month, day, hour, minute, tzinfo=tzinfo)


def test_read_rejects_tzinfo_without_usable_offset():
    with pytest.raises(CalendarPreparationError):
        PreparedCalendarRead(
            _tz_aware(2026, 7, 18, 9, 0, _NaiveOffsetTZ()),
            _tz_aware(2026, 7, 18, 10, 0, _NaiveOffsetTZ()),
            None,
        )


def test_draft_rejects_tzinfo_without_usable_offset():
    with pytest.raises(CalendarPreparationError):
        PreparedCalendarEventDraft(
            "Title",
            _tz_aware(2026, 7, 18, 9, 0, _NaiveOffsetTZ()),
            _tz_aware(2026, 7, 18, 10, 0, _NaiveOffsetTZ()),
            "Work",
            None,
            None,
        )


def test_read_rejects_raising_tzinfo_without_leak():
    with pytest.raises(CalendarPreparationError) as exc:
        PreparedCalendarRead(
            _tz_aware(2026, 7, 18, 9, 0, _RaisingTZ()),
            _tz_aware(2026, 7, 18, 10, 0, _RaisingTZ()),
            None,
        )
    assert "boom" not in str(exc.value)
    assert "internal" not in str(exc.value)


def test_draft_rejects_raising_tzinfo_without_leak():
    with pytest.raises(CalendarPreparationError) as exc:
        PreparedCalendarEventDraft(
            "Title",
            _tz_aware(2026, 7, 18, 9, 0, _RaisingTZ()),
            _tz_aware(2026, 7, 18, 10, 0, _RaisingTZ()),
            "Work",
            None,
            None,
        )
    assert "boom" not in str(exc.value)
    assert "internal" not in str(exc.value)


def test_read_factory_rejects_raising_tzinfo_without_leak():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-tzr-1"
    )
    with pytest.raises(CalendarPreparationError) as exc:
        factory.prepare(
            _owner(),
            _tz_aware(2026, 7, 18, 9, 0, _RaisingTZ()),
            _tz_aware(2026, 7, 18, 10, 0, _RaisingTZ()),
        )
    assert "boom" not in str(exc.value)


def test_draft_factory_rejects_raising_tzinfo_without_leak():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-tzr-2"
    )
    with pytest.raises(CalendarPreparationError) as exc:
        factory.prepare(
            _owner(),
            "Title",
            _tz_aware(2026, 7, 18, 9, 0, _RaisingTZ()),
            _tz_aware(2026, 7, 18, 10, 0, _RaisingTZ()),
            "Work",
        )
    assert "boom" not in str(exc.value)


# --------------------------------------------------------------------------
# Invalid registry item
# --------------------------------------------------------------------------


def test_registry_rejects_arbitrary_object():
    reg = CalendarPreparationRegistry()
    with pytest.raises(CalendarPreparationError):
        reg.put(_owner(), "p-bad-item", object())


def test_registry_rejects_non_prepared_types():
    reg = CalendarPreparationRegistry()
    for bad in (None, "string", 123, [], {}, _owner()):
        with pytest.raises(CalendarPreparationError):
            reg.put(_owner(), "p-bad-item", bad)


# --------------------------------------------------------------------------
# Empty title / empty calendar name
# --------------------------------------------------------------------------


def test_draft_rejects_empty_title():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-empty-1"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(), "", _aware(2026, 7, 18, 9, 0), _aware(2026, 7, 18, 10, 0), "Work"
        )


def test_draft_rejects_whitespace_only_title():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-empty-2"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            "   ",
            _aware(2026, 7, 18, 9, 0),
            _aware(2026, 7, 18, 10, 0),
            "Work",
        )


def test_read_rejects_empty_calendar_name():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-empty-3"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            _aware(2026, 7, 18, 9, 0),
            _aware(2026, 7, 18, 10, 0),
            calendar_name="",
        )


def test_read_accepts_none_calendar_name_as_unspecified():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-empty-4"
    )
    prep = factory.prepare(
        _owner(), _aware(2026, 7, 18, 9, 0), _aware(2026, 7, 18, 10, 0)
    )
    assert prep.read.calendar_name is None
    assert prep.proposal.targets == ()


# --------------------------------------------------------------------------
# Infinite clocks and TTL inputs
# --------------------------------------------------------------------------


def test_read_rejects_infinite_clock():
    factory = CalendarReadProposalFactory(
        clock=lambda: float("inf"), proposal_id_factory=lambda: "p-inf-1"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(), _aware(2026, 7, 18, 9, 0), _aware(2026, 7, 18, 10, 0)
        )


def test_draft_rejects_negative_infinite_clock():
    factory = CalendarDraftProposalFactory(
        clock=lambda: float("-inf"), proposal_id_factory=lambda: "p-inf-2"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            "Title",
            _aware(2026, 7, 18, 9, 0),
            _aware(2026, 7, 18, 10, 0),
            "Work",
        )


def test_factory_rejects_infinite_ttl():
    with pytest.raises(ValueError):
        CalendarReadProposalFactory(
            clock=_fixed_clock(1000.0),
            proposal_id_factory=lambda: "x",
            ttl_seconds=float("inf"),
        )
    with pytest.raises(ValueError):
        CalendarDraftProposalFactory(
            clock=_fixed_clock(1000.0),
            proposal_id_factory=lambda: "x",
            ttl_seconds=float("nan"),
        )


# --------------------------------------------------------------------------
# Unicode Cf in calendar name, location, and notes
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "\u202eReversed",  # RIGHT-TO-LEFT OVERRIDE
        "Zero\u200bWidth",  # ZERO WIDTH SPACE
        "Soft\u00adHyphen",  # SOFT HYPHEN
    ],
)
def test_read_rejects_unicode_cf_in_calendar_name(text):
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-cf-3"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            _aware(2026, 7, 18, 9, 0),
            _aware(2026, 7, 18, 10, 0),
            calendar_name=text,
        )


@pytest.mark.parametrize(
    "text",
    [
        "\u202eReversed",  # RIGHT-TO-LEFT OVERRIDE
        "Zero\u200bWidth",  # ZERO WIDTH SPACE
        "Soft\u00adHyphen",  # SOFT HYPHEN
    ],
)
def test_draft_rejects_unicode_cf_in_location(text):
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-cf-4"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            "Title",
            _aware(2026, 7, 18, 9, 0),
            _aware(2026, 7, 18, 10, 0),
            "Work",
            location=text,
        )


@pytest.mark.parametrize(
    "text",
    [
        "\u202eReversed",  # RIGHT-TO-LEFT OVERRIDE
        "Zero\u200bWidth",  # ZERO WIDTH SPACE
        "Soft\u00adHyphen",  # SOFT HYPHEN
    ],
)
def test_draft_rejects_unicode_cf_in_notes(text):
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-cf-5"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            "Title",
            _aware(2026, 7, 18, 9, 0),
            _aware(2026, 7, 18, 10, 0),
            "Work",
            notes=text,
        )


# --------------------------------------------------------------------------
# Exact bounds for calendar name, location, and notes
# --------------------------------------------------------------------------


def test_read_accepts_max_length_calendar_name():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-bnd-1"
    )
    name = "N" * cal.CALENDAR_NAME_MAX
    prep = factory.prepare(
        _owner(), _aware(2026, 7, 18, 9, 0), _aware(2026, 7, 18, 10, 0),
        calendar_name=name,
    )
    assert prep.read.calendar_name == name


def test_read_rejects_overlong_calendar_name():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-bnd-2"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            _aware(2026, 7, 18, 9, 0),
            _aware(2026, 7, 18, 10, 0),
            calendar_name="N" * (cal.CALENDAR_NAME_MAX + 1),
        )


def test_draft_accepts_max_length_location_and_notes():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-bnd-3"
    )
    loc = "L" * cal.CALENDAR_LOCATION_MAX
    notes = "X" * cal.CALENDAR_NOTES_MAX
    prep = factory.prepare(
        _owner(),
        "Title",
        _aware(2026, 7, 18, 9, 0),
        _aware(2026, 7, 18, 10, 0),
        "Work",
        location=loc,
        notes=notes,
    )
    assert prep.draft.location == loc
    assert prep.draft.notes == notes


def test_draft_rejects_overlong_location():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-bnd-4"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            "Title",
            _aware(2026, 7, 18, 9, 0),
            _aware(2026, 7, 18, 10, 0),
            "Work",
            location="L" * (cal.CALENDAR_LOCATION_MAX + 1),
        )


def test_draft_rejects_overlong_notes():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-bnd-5"
    )
    with pytest.raises(CalendarPreparationError):
        factory.prepare(
            _owner(),
            "Title",
            _aware(2026, 7, 18, 9, 0),
            _aware(2026, 7, 18, 10, 0),
            "Work",
            notes="X" * (cal.CALENDAR_NOTES_MAX + 1),
        )


# --------------------------------------------------------------------------
# Event drafts require an explicit calendar destination target
# --------------------------------------------------------------------------


def test_draft_preview_includes_exact_destination_target():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-tgt-1"
    )
    prep = factory.prepare(
        _owner(),
        "Team Sync",
        _aware(2026, 7, 18, 9, 0),
        _aware(2026, 7, 18, 10, 0),
        "Work",
        location="Office",
        notes="Bring agenda",
    )
    assert len(prep.proposal.targets) == 1
    assert prep.proposal.targets[0].kind.value == "calendar"
    assert prep.proposal.targets[0].value == "Work"
    assert prep.draft.calendar_name == "Work"
    keys = {f.key for f in prep.proposal.preview_fields}
    assert keys == {"title", "start", "end", "calendar", "location", "notes"}
    calendar_field = next(f for f in prep.proposal.preview_fields if f.key == "calendar")
    assert calendar_field.value == "Work"


# --------------------------------------------------------------------------
# Regressions for bounded error handling and conversion safety
# --------------------------------------------------------------------------


class _StatefulIsoformatRaisingTZ(tzinfo):
    """tzinfo whose utcoffset succeeds initially but raises during isoformat."""

    def __init__(self, secret_detail: str = "SECRET_TZ_INTERNAL_DETAIL"):
        self._called = 0
        self._secret = secret_detail

    def utcoffset(self, dt):
        self._called += 1
        if self._called <= 3:
            return timedelta(hours=0)
        raise RuntimeError(self._secret)

    def tzname(self, dt):
        return "STZ"

    def dst(self, dt):
        return timedelta(0)


def test_stateful_tzinfo_read_factory_isoformat_raises_without_leak():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-stz-read"
    )
    stz = _StatefulIsoformatRaisingTZ("SECRET_READ_TZ_BOOM")
    with pytest.raises(CalendarPreparationError) as exc:
        factory.prepare(
            _owner(),
            datetime(2026, 7, 18, 9, 0, tzinfo=stz),
            datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
        )
    msg = str(exc.value)
    assert "SECRET_READ_TZ_BOOM" not in msg
    assert "RuntimeError" not in msg
    assert msg == "calendar read preparation failed"


def test_stateful_tzinfo_draft_factory_isoformat_raises_without_leak():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-stz-draft"
    )
    stz = _StatefulIsoformatRaisingTZ("SECRET_DRAFT_TZ_BOOM")
    with pytest.raises(CalendarPreparationError) as exc:
        factory.prepare(
            _owner(),
            "Title",
            datetime(2026, 7, 18, 9, 0, tzinfo=stz),
            datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
            "Work",
        )
    msg = str(exc.value)
    assert "SECRET_DRAFT_TZ_BOOM" not in msg
    assert "RuntimeError" not in msg
    assert msg == "calendar draft preparation failed"


def test_read_factory_rejects_enormous_clock_integer_without_overflow_leak():
    factory = CalendarReadProposalFactory(
        clock=lambda: 10**10000, proposal_id_factory=lambda: "p-clock-overflow"
    )
    with pytest.raises(CalendarPreparationError) as exc:
        factory.prepare(
            _owner(), _aware(2026, 7, 18, 9, 0), _aware(2026, 7, 18, 10, 0)
        )
    msg = str(exc.value)
    assert "OverflowError" not in msg
    assert "too large" not in msg
    assert msg == "calendar read preparation failed"


def test_draft_factory_rejects_enormous_clock_integer_without_overflow_leak():
    factory = CalendarDraftProposalFactory(
        clock=lambda: 10**10000, proposal_id_factory=lambda: "p-clock-overflow"
    )
    with pytest.raises(CalendarPreparationError) as exc:
        factory.prepare(
            _owner(), "Title", _aware(2026, 7, 18, 9, 0), _aware(2026, 7, 18, 10, 0), "Work"
        )
    msg = str(exc.value)
    assert "OverflowError" not in msg
    assert "too large" not in msg
    assert msg == "calendar draft preparation failed"


def test_read_factory_rejects_enormous_ttl_integer_without_overflow_leak():
    with pytest.raises(ValueError) as exc:
        CalendarReadProposalFactory(
            clock=_fixed_clock(1000.0),
            proposal_id_factory=lambda: "p-ttl-overflow",
            ttl_seconds=10**10000,
        )
    msg = str(exc.value)
    assert "OverflowError" not in msg
    assert "too large" not in msg
    assert msg == "invalid proposal lifetime"


def test_draft_factory_rejects_enormous_ttl_integer_without_overflow_leak():
    with pytest.raises(ValueError) as exc:
        CalendarDraftProposalFactory(
            clock=_fixed_clock(1000.0),
            proposal_id_factory=lambda: "p-ttl-overflow",
            ttl_seconds=10**10000,
        )
    msg = str(exc.value)
    assert "OverflowError" not in msg
    assert "too large" not in msg
    assert msg == "invalid proposal lifetime"


def test_registry_rejects_non_string_proposal_ids():
    reg = CalendarPreparationRegistry()
    actor = _owner()
    item = _sample_read()
    for bad_id in (None, 12345, b"p-bytes-id", [1, 2]):
        with pytest.raises(CalendarPreparationError) as exc:
            reg.put(actor, bad_id, item)  # type: ignore[arg-type]
        msg = str(exc.value)
        assert "TypeError" not in msg
        assert "regex" not in msg.lower()
        assert msg == "calendar registry operation failed"

        with pytest.raises(CalendarPreparationError) as exc:
            reg.get(actor, bad_id)  # type: ignore[arg-type]
        msg = str(exc.value)
        assert "TypeError" not in msg
        assert msg == "calendar registry operation failed"

        with pytest.raises(CalendarPreparationError) as exc:
            reg.remove(actor, bad_id)  # type: ignore[arg-type]
        msg = str(exc.value)
        assert "TypeError" not in msg
        assert msg == "calendar registry operation failed"


class _SecretValueErrorInt(int):
    def __float__(self):
        raise ValueError("SECRET_TTL_CONVERSION_FAIL")


def test_read_factory_ttl_secret_value_error_normalized():
    secret_ttl = _SecretValueErrorInt(100)
    with pytest.raises(ValueError) as exc:
        CalendarReadProposalFactory(
            clock=_fixed_clock(1000.0),
            proposal_id_factory=lambda: "p-ttl-sec-1",
            ttl_seconds=secret_ttl,
        )
    msg = str(exc.value)
    assert "SECRET_TTL_CONVERSION_FAIL" not in msg
    assert msg == "invalid proposal lifetime"
    assert exc.value.__cause__ is None


def test_draft_factory_ttl_secret_value_error_normalized():
    secret_ttl = _SecretValueErrorInt(100)
    with pytest.raises(ValueError) as exc:
        CalendarDraftProposalFactory(
            clock=_fixed_clock(1000.0),
            proposal_id_factory=lambda: "p-ttl-sec-2",
            ttl_seconds=secret_ttl,
        )
    msg = str(exc.value)
    assert "SECRET_TTL_CONVERSION_FAIL" not in msg
    assert msg == "invalid proposal lifetime"
    assert exc.value.__cause__ is None


class _StatefulCPERaisingTZ(tzinfo):
    """tzinfo whose utcoffset succeeds during post-init validation but raises CalendarPreparationError during isoformat."""

    def __init__(self, secret_detail: str = "SECRET_TZ_CPE_LEAK"):
        self._called = 0
        self._secret = secret_detail

    def utcoffset(self, dt):
        self._called += 1
        if self._called <= 3:
            return timedelta(hours=0)
        raise CalendarPreparationError(self._secret)

    def tzname(self, dt):
        return "STZ"

    def dst(self, dt):
        return timedelta(0)


def test_read_factory_stateful_tzinfo_cpe_during_isoformat_normalized():
    factory = CalendarReadProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-stz-cpe-read"
    )
    stz = _StatefulCPERaisingTZ("SECRET_TZ_CPE_READ_LEAK")
    with pytest.raises(CalendarPreparationError) as exc:
        factory.prepare(
            _owner(),
            datetime(2026, 7, 18, 9, 0, tzinfo=stz),
            datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
        )
    msg = str(exc.value)
    assert "SECRET_TZ_CPE_READ_LEAK" not in msg
    assert msg == "calendar read preparation failed"
    assert exc.value.__cause__ is None


def test_draft_factory_stateful_tzinfo_cpe_during_isoformat_normalized():
    factory = CalendarDraftProposalFactory(
        clock=_fixed_clock(2000.0), proposal_id_factory=lambda: "p-stz-cpe-draft"
    )
    stz = _StatefulCPERaisingTZ("SECRET_TZ_CPE_DRAFT_LEAK")
    with pytest.raises(CalendarPreparationError) as exc:
        factory.prepare(
            _owner(),
            "Title",
            datetime(2026, 7, 18, 9, 0, tzinfo=stz),
            datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
            "Work",
        )
    msg = str(exc.value)
    assert "SECRET_TZ_CPE_DRAFT_LEAK" not in msg
    assert msg == "calendar draft preparation failed"
    assert exc.value.__cause__ is None


# --------------------------------------------------------------------------
# Forbidden side effects
# --------------------------------------------------------------------------



def test_no_forbidden_imports():
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "core"
        / "productivity"
        / "calendar.py"
    )
    forbidden = {
        "subprocess",
        "socket",
        "threading",
        "os",
        "sqlite3",
        "requests",
        "asyncio",
        "logging",
        "smtplib",
        "http",
        "webbrowser",
        "eventkit",
        "applescript",
        "reminders",
        "mcp",
        "network",
        "provider",
    }
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden, (
                    f"calendar.py imports forbidden module {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert node.module.split(".")[0] not in forbidden, (
                    f"calendar.py imports forbidden module {node.module}"
                )
