"""Deterministic tests for the Phase 3 pure reminder preparation contracts.

These tests cover only ``core.productivity.reminder``. They perform no I/O,
network, subprocess, EventKit, AppleScript, email, browser, reminders, MCP,
provider, or execution activity, and assert the absence of those imports.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

import pytest

from core.action_policy import Actor, ActorContext
from core.productivity import reminder as rem
from core.productivity.reminder import (
    DEFAULT_REMINDER_LIST_LABEL,
    PreparedReminderInput,
    ReminderPreparation,
    ReminderPreparationError,
    ReminderPreparationRegistry,
    ReminderProposalFactory,
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


# --------------------------------------------------------------------------
# Valid reminder proposals
# --------------------------------------------------------------------------


def test_reminder_preparation_valid_default_list():
    # now = 2026-07-20 00:00:00 UTC (1784505600.0)
    now_ts = 1784505600.0
    factory = ReminderProposalFactory(
        clock=_fixed_clock(now_ts), proposal_id_factory=lambda: "p-rem-1"
    )
    remind_at = _aware(2026, 7, 21, 9, 0)
    prep = factory.prepare(_owner(), "Buy milk", remind_at)

    assert isinstance(prep, ReminderPreparation)
    assert prep.proposal.action.value == "reminder.create"
    assert prep.proposal.proposal_id == "p-rem-1"
    assert prep.proposal.created_at == now_ts
    assert prep.reminder.title == "Buy milk"
    assert prep.reminder.notes is None
    assert prep.reminder.list_name is None

    # Target is REMINDER_LIST with default destination label
    targets = prep.proposal.targets
    assert len(targets) == 1
    assert targets[0].kind.value == "reminder_list"
    assert targets[0].value == DEFAULT_REMINDER_LIST_LABEL

    # Preview fields contain title and remind_at
    fields = {f.key: f.value for f in prep.proposal.preview_fields}
    assert fields["title"] == "Buy milk"
    assert fields["remind_at"] == remind_at.isoformat()
    assert "notes" not in fields
    assert "list" not in fields


def test_reminder_preparation_valid_custom_list_and_notes():
    now_ts = 1784505600.0
    factory = ReminderProposalFactory(
        clock=_fixed_clock(now_ts), proposal_id_factory=lambda: "p-rem-2"
    )
    remind_at = _aware(2026, 7, 21, 14, 30, tz=NY)
    prep = factory.prepare(
        _owner(),
        "Doctor appointment",
        remind_at,
        notes="Bring medical records\nRoom 302",
        list_name="Personal",
    )

    assert prep.reminder.title == "Doctor appointment"
    assert prep.reminder.notes == "Bring medical records\nRoom 302"
    assert prep.reminder.list_name == "Personal"

    targets = prep.proposal.targets
    assert len(targets) == 1
    assert targets[0].kind.value == "reminder_list"
    assert targets[0].value == "Personal"

    fields = {f.key: f.value for f in prep.proposal.preview_fields}
    assert fields["title"] == "Doctor appointment"
    assert fields["remind_at"] == remind_at.isoformat()
    assert fields["notes"] == "Bring medical records\nRoom 302"
    assert fields["list"] == "Personal"


# --------------------------------------------------------------------------
# Expiration bounded by min(now + TTL, remind_at)
# --------------------------------------------------------------------------


def test_proposal_expiration_bounded_by_ttl():
    now_ts = 1000.0
    factory = ReminderProposalFactory(
        clock=_fixed_clock(now_ts), proposal_id_factory=lambda: "p-exp-1"
    )
    # remind_at is 5000s in the future; TTL is 900s
    now_dt = datetime.fromtimestamp(now_ts, tz=UTC)
    remind_at = now_dt + timedelta(seconds=5000)
    prep = factory.prepare(_owner(), "Future task", remind_at)

    assert prep.proposal.expires_at == now_ts + 900.0


def test_proposal_expiration_bounded_by_remind_at():
    now_ts = 1000.0
    factory = ReminderProposalFactory(
        clock=_fixed_clock(now_ts), proposal_id_factory=lambda: "p-exp-2"
    )
    # remind_at is 300s in the future; TTL is 900s -> expires_at = now + 300
    now_dt = datetime.fromtimestamp(now_ts, tz=UTC)
    remind_at = now_dt + timedelta(seconds=300)
    prep = factory.prepare(_owner(), "Soon task", remind_at)

    assert prep.proposal.expires_at == remind_at.timestamp()
    assert prep.proposal.expires_at == now_ts + 300.0


# --------------------------------------------------------------------------
# Title validation
# --------------------------------------------------------------------------


def test_rejects_empty_title():
    factory = ReminderProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-bad-title-1"
    )
    remind_at = datetime.fromtimestamp(2000.0, tz=UTC)
    with pytest.raises(ReminderPreparationError):
        factory.prepare(_owner(), "", remind_at)


def test_rejects_whitespace_title():
    factory = ReminderProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-bad-title-2"
    )
    remind_at = datetime.fromtimestamp(2000.0, tz=UTC)
    with pytest.raises(ReminderPreparationError):
        factory.prepare(_owner(), "   \t\n  ", remind_at)


def test_title_max_length_boundary():
    factory = ReminderProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-title-bnd"
    )
    remind_at = datetime.fromtimestamp(2000.0, tz=UTC)
    valid_title = "T" * rem.REMINDER_TITLE_MAX
    prep = factory.prepare(_owner(), valid_title, remind_at)
    assert prep.reminder.title == valid_title

    with pytest.raises(ReminderPreparationError):
        factory.prepare(_owner(), "T" * (rem.REMINDER_TITLE_MAX + 1), remind_at)


def test_rejects_control_chars_in_title():
    factory = ReminderProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-ctrl-1"
    )
    remind_at = datetime.fromtimestamp(2000.0, tz=UTC)
    for bad_char in ("\x00", "\x01", "\x1f", "\x7f", "\n", "\t"):
        with pytest.raises(ReminderPreparationError):
            factory.prepare(_owner(), f"Title{bad_char}Bad", remind_at)


@pytest.mark.parametrize(
    "text",
    [
        "\u202eReversed",  # RIGHT-TO-LEFT OVERRIDE
        "Zero\u200bWidth",  # ZERO WIDTH SPACE
        "Soft\u00adHyphen",  # SOFT HYPHEN
    ],
)
def test_rejects_unicode_cf_in_title(text):
    factory = ReminderProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-cf-title"
    )
    remind_at = datetime.fromtimestamp(2000.0, tz=UTC)
    with pytest.raises(ReminderPreparationError):
        factory.prepare(_owner(), text, remind_at)


def test_accepts_normal_unicode_title():
    factory = ReminderProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-uni-title"
    )
    remind_at = datetime.fromtimestamp(2000.0, tz=UTC)
    title = "Rappeler Maman — Café & Pain"
    prep = factory.prepare(_owner(), title, remind_at)
    assert prep.reminder.title == title


# --------------------------------------------------------------------------
# Notes validation
# --------------------------------------------------------------------------


def test_notes_validation():
    factory = ReminderProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-notes"
    )
    remind_at = datetime.fromtimestamp(2000.0, tz=UTC)

    # Empty notes accepted
    prep = factory.prepare(_owner(), "Title", remind_at, notes="")
    assert prep.reminder.notes == ""

    # Multiline notes accepted
    prep = factory.prepare(_owner(), "Title", remind_at, notes="Line 1\nLine 2\tTabbed")
    assert prep.reminder.notes == "Line 1\nLine 2\tTabbed"

    # Max boundary
    valid_notes = "N" * rem.REMINDER_NOTES_MAX
    prep = factory.prepare(_owner(), "Title", remind_at, notes=valid_notes)
    assert prep.reminder.notes == valid_notes

    with pytest.raises(ReminderPreparationError):
        factory.prepare(_owner(), "Title", remind_at, notes="N" * (rem.REMINDER_NOTES_MAX + 1))

    # Reject other control chars
    with pytest.raises(ReminderPreparationError):
        factory.prepare(_owner(), "Title", remind_at, notes="Notes\x01Bad")

    # Reject Unicode Cf
    with pytest.raises(ReminderPreparationError):
        factory.prepare(_owner(), "Title", remind_at, notes="Zero\u200bWidth")


# --------------------------------------------------------------------------
# List name validation
# --------------------------------------------------------------------------


def test_list_name_validation():
    factory = ReminderProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-list"
    )
    remind_at = datetime.fromtimestamp(2000.0, tz=UTC)

    # Empty string list_name rejected
    with pytest.raises(ReminderPreparationError):
        factory.prepare(_owner(), "Title", remind_at, list_name="")

    # Max boundary
    valid_list = "L" * rem.REMINDER_LIST_NAME_MAX
    prep = factory.prepare(_owner(), "Title", remind_at, list_name=valid_list)
    assert prep.reminder.list_name == valid_list

    with pytest.raises(ReminderPreparationError):
        factory.prepare(_owner(), "Title", remind_at, list_name="L" * (rem.REMINDER_LIST_NAME_MAX + 1))

    # Reject controls including newline
    with pytest.raises(ReminderPreparationError):
        factory.prepare(_owner(), "Title", remind_at, list_name="List\nBad")

    # Reject Unicode Cf
    with pytest.raises(ReminderPreparationError):
        factory.prepare(_owner(), "Title", remind_at, list_name="Soft\u00adHyphen")


# --------------------------------------------------------------------------
# Timezone and clock boundaries (366-day limit, past timestamps)
# --------------------------------------------------------------------------


class _NaiveOffsetTZ(tzinfo):
    def utcoffset(self, dt):
        return None


class _RaisingTZ(tzinfo):
    def utcoffset(self, dt):
        raise RuntimeError("BOOM_TZ_DETAIL")


def test_rejects_naive_or_unusable_remind_at():
    factory = ReminderProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-tz-bad"
    )
    # Naive datetime
    with pytest.raises(ReminderPreparationError):
        factory.prepare(_owner(), "Title", datetime(2026, 7, 21, 9, 0))

    # Naive offset tzinfo
    with pytest.raises(ReminderPreparationError):
        factory.prepare(_owner(), "Title", datetime(2026, 7, 21, 9, 0, tzinfo=_NaiveOffsetTZ()))

    # Raising tzinfo
    with pytest.raises(ReminderPreparationError) as exc:
        factory.prepare(_owner(), "Title", datetime(2026, 7, 21, 9, 0, tzinfo=_RaisingTZ()))
    assert "BOOM_TZ_DETAIL" not in str(exc.value)


def test_rejects_remind_at_in_past_or_equal_to_now():
    now_ts = 1000.0
    factory = ReminderProposalFactory(
        clock=_fixed_clock(now_ts), proposal_id_factory=lambda: "p-past"
    )
    now_dt = datetime.fromtimestamp(now_ts, tz=UTC)

    # In the past
    with pytest.raises(ReminderPreparationError):
        factory.prepare(_owner(), "Title", now_dt - timedelta(seconds=10))

    # Equal to now
    with pytest.raises(ReminderPreparationError):
        factory.prepare(_owner(), "Title", now_dt)


def test_366_day_boundary():
    now_ts = 1000.0
    factory = ReminderProposalFactory(
        clock=_fixed_clock(now_ts), proposal_id_factory=lambda: "p-366"
    )
    now_dt = datetime.fromtimestamp(now_ts, tz=UTC)

    # Exactly 366 days in future: accepted
    exact_366 = now_dt + timedelta(days=366)
    prep = factory.prepare(_owner(), "Title", exact_366)
    assert prep.reminder.remind_at == exact_366

    # 366 days + 1 second: rejected
    over_366 = exact_366 + timedelta(seconds=1)
    with pytest.raises(ReminderPreparationError):
        factory.prepare(_owner(), "Title", over_366)


def test_rejects_invalid_clocks():
    # Infinite clock
    factory_inf = ReminderProposalFactory(
        clock=lambda: float("inf"), proposal_id_factory=lambda: "p-inf"
    )
    remind_at = datetime.fromtimestamp(2000.0, tz=UTC)
    with pytest.raises(ReminderPreparationError):
        factory_inf.prepare(_owner(), "Title", remind_at)

    # NaN clock
    factory_nan = ReminderProposalFactory(
        clock=lambda: float("nan"), proposal_id_factory=lambda: "p-nan"
    )
    with pytest.raises(ReminderPreparationError):
        factory_nan.prepare(_owner(), "Title", remind_at)

    # Boolean clock
    factory_bool = ReminderProposalFactory(
        clock=lambda: True, proposal_id_factory=lambda: "p-bool"
    )
    with pytest.raises(ReminderPreparationError):
        factory_bool.prepare(_owner(), "Title", remind_at)

    # Clock throwing exception
    factory_exc = ReminderProposalFactory(
        clock=lambda: (_ for _ in ()).throw(RuntimeError("CLOCK_SECRET")),
        proposal_id_factory=lambda: "p-exc",
    )
    with pytest.raises(ReminderPreparationError) as exc:
        factory_exc.prepare(_owner(), "Title", remind_at)
    assert "CLOCK_SECRET" not in str(exc.value)


# --------------------------------------------------------------------------
# TTL seconds validation
# --------------------------------------------------------------------------


def test_ttl_seconds_validation():
    clock = _fixed_clock(1000.0)
    id_fac = lambda: "p-ttl"

    # Valid TTL values
    f1 = ReminderProposalFactory(clock, id_fac, ttl_seconds=1.0)
    assert f1._ttl_seconds == 1.0
    f900 = ReminderProposalFactory(clock, id_fac, ttl_seconds=900.0)
    assert f900._ttl_seconds == 900.0

    # Invalid range or non-finite
    for bad_ttl in (0, 0.9, 900.1, 1000, float("inf"), float("nan"), True, "900"):
        with pytest.raises(ValueError) as exc:
            ReminderProposalFactory(clock, id_fac, ttl_seconds=bad_ttl)  # type: ignore[arg-type]
        assert str(exc.value) == "invalid proposal lifetime"


# --------------------------------------------------------------------------
# Content-free __repr__
# --------------------------------------------------------------------------


def test_repr_content_free():
    now_dt = datetime.fromtimestamp(1000.0, tz=UTC)
    remind_at = now_dt + timedelta(days=1)
    reminder = PreparedReminderInput(
        "Secret Title 999", remind_at, "Secret Notes 888", "Secret List 777"
    )
    repr_str = repr(reminder)
    assert repr_str == "PreparedReminderInput(...)"
    for secret in ("Secret Title 999", "Secret Notes 888", "Secret List 777"):
        assert secret not in repr_str

    factory = ReminderProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-repr-1"
    )
    prep = factory.prepare(_owner(), "Secret Title 999", remind_at, notes="Secret Notes 888")
    assert repr(prep) == "ReminderPreparation(...)"
    assert "Secret Title 999" not in repr(prep.proposal)


# --------------------------------------------------------------------------
# Registry tests
# --------------------------------------------------------------------------


def test_registry_capacity_and_isolation():
    reg = ReminderPreparationRegistry(limit=2)
    owner_a = _owner(actor_id="actor-a", session_id="session-1")
    owner_b = _owner(actor_id="actor-b", session_id="session-1")
    remind_at = datetime.fromtimestamp(2000.0, tz=UTC)
    item1 = PreparedReminderInput("Task 1", remind_at, None, None)
    item2 = PreparedReminderInput("Task 2", remind_at, None, None)
    item3 = PreparedReminderInput("Task 3", remind_at, None, None)

    reg.put(owner_a, "p-1", item1)
    reg.put(owner_a, "p-2", item2)

    # Capacity full
    with pytest.raises(ReminderPreparationError) as exc:
        reg.put(owner_a, "p-3", item3)
    assert str(exc.value) == "reminder registry is full"

    # Cross-session isolation
    assert reg.get(owner_b, "p-1") is None
    assert reg.get(owner_a, "p-1") is item1

    # Remove and clear_session
    reg.remove(owner_a, "p-1")
    assert reg.get(owner_a, "p-1") is None

    reg.clear_session("actor-a", "session-1")
    assert reg.get(owner_a, "p-2") is None


def test_registry_rejects_invalid_proposal_ids():
    reg = ReminderPreparationRegistry()
    actor = _owner()
    remind_at = datetime.fromtimestamp(2000.0, tz=UTC)
    item = PreparedReminderInput("Task", remind_at, None, None)

    for bad_id in (None, 12345, b"p-bytes", [1, 2]):
        with pytest.raises(ReminderPreparationError) as exc:
            reg.put(actor, bad_id, item)  # type: ignore[arg-type]
        assert "TypeError" not in str(exc.value)
        assert str(exc.value) == "reminder registry operation failed"

        with pytest.raises(ReminderPreparationError):
            reg.get(actor, bad_id)  # type: ignore[arg-type]

        with pytest.raises(ReminderPreparationError):
            reg.remove(actor, bad_id)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Adversarial exception redaction
# --------------------------------------------------------------------------


class _SecretValueErrorInt(int):
    def __float__(self):
        raise ValueError("SECRET_TTL_CONVERSION_FAIL")


class _StatefulCPERaisingTZ(tzinfo):
    def __init__(self, secret_detail: str = "SECRET_TZ_CPE_LEAK"):
        self._called = 0
        self._secret = secret_detail

    def utcoffset(self, dt):
        self._called += 1
        if self._called <= 3:
            return timedelta(hours=0)
        raise ReminderPreparationError(self._secret)

    def tzname(self, dt):
        return "STZ"

    def dst(self, dt):
        return timedelta(0)


def test_factory_ttl_secret_value_error_normalized():
    secret_ttl = _SecretValueErrorInt(100)
    with pytest.raises(ValueError) as exc:
        ReminderProposalFactory(
            clock=_fixed_clock(1000.0),
            proposal_id_factory=lambda: "p-ttl-sec",
            ttl_seconds=secret_ttl,
        )
    msg = str(exc.value)
    assert "SECRET_TTL_CONVERSION_FAIL" not in msg
    assert msg == "invalid proposal lifetime"
    assert exc.value.__cause__ is None


def test_factory_stateful_tzinfo_cpe_during_isoformat_normalized():
    factory = ReminderProposalFactory(
        clock=_fixed_clock(1000.0), proposal_id_factory=lambda: "p-stz-cpe"
    )
    stz = _StatefulCPERaisingTZ("SECRET_TZ_CPE_LEAK")
    now_dt = datetime.fromtimestamp(1000.0, tz=UTC)
    remind_at = datetime(now_dt.year, now_dt.month, now_dt.day + 1, 9, 0, tzinfo=stz)

    with pytest.raises(ReminderPreparationError) as exc:
        factory.prepare(_owner(), "Title", remind_at)
    msg = str(exc.value)
    assert "SECRET_TZ_CPE_LEAK" not in msg
    assert msg == "reminder preparation failed"
    assert exc.value.__cause__ is None


# --------------------------------------------------------------------------
# Injected-clock normalization: single conversion, no secret leak
# --------------------------------------------------------------------------


class _CountingFloat(float):
    """Count ``__float__`` calls and always succeed with the wrapped value."""

    def __new__(cls, value: float = 0.0) -> "_CountingFloat":
        inst = super().__new__(cls, value)
        inst.calls = 0
        return inst

    def __float__(self) -> float:
        self.calls += 1
        return super().__float__()


class _OnceSuccessfulFloat(float):
    """Succeed on the first ``__float__`` call, raise a secret error after.

    Models the defect: a stateful numeric object that passes an initial
    guarded conversion but fails on a later unguarded ``float()`` call.
    With single-conversion normalization the second call never happens.
    """

    _SECRET = "SECRET_LATER_CONVERSION_FAIL"

    def __new__(cls, value: float = 0.0) -> "_OnceSuccessfulFloat":
        inst = super().__new__(cls, value)
        inst.calls = 0
        return inst

    def __float__(self) -> float:
        self.calls += 1
        if self.calls > 1:
            raise ValueError(self._SECRET)
        return super().__float__()


class _AlwaysFailingFloat(float):
    """Raise a secret-bearing error on every ``__float__`` call."""

    _SECRET = "SECRET_FIRST_CONVERSION_FAIL"

    def __new__(cls, value: float = 0.0) -> "_AlwaysFailingFloat":
        inst = super().__new__(cls, value)
        inst.calls = 0
        return inst

    def __float__(self) -> float:
        self.calls += 1
        raise ValueError(self._SECRET)


class _DriftingFloat(float):
    """Return a different value on each ``__float__`` call.

    After single-conversion normalization, ``created_at`` and ``expires_at``
    must both derive from the first (only) converted instant.
    """

    def __new__(cls, base: float = 0.0) -> "_DriftingFloat":
        inst = super().__new__(cls, base)
        inst._base = base
        inst.calls = 0
        return inst

    def __float__(self) -> float:
        self.calls += 1
        return self._base + (self.calls - 1)


def _assert_no_secret(exc: BaseException, secret: str) -> None:
    """Assert ``secret`` is absent from str, repr, cause, and context."""
    assert secret not in str(exc)
    assert secret not in repr(exc)
    assert exc.__cause__ is None
    # With single-conversion normalization the failure path raises outside any
    # except block, so __context__ is None; if set, it must not carry the secret.
    ctx = exc.__context__
    if ctx is not None:
        assert secret not in str(ctx)
        assert secret not in repr(ctx)


def test_stateful_clock_later_conversion_failure_does_not_leak():
    """A stateful clock that succeeds first but would fail later must not leak.

    With single-conversion normalization the second ``float()`` call never
    occurs, so preparation succeeds and the secret is never raised.
    """
    now_raw = _OnceSuccessfulFloat(1000.0)
    factory = ReminderProposalFactory(
        clock=lambda: now_raw, proposal_id_factory=lambda: "p-once-1"
    )
    remind_at = datetime.fromtimestamp(2000.0, tz=UTC)
    prep = factory.prepare(_owner(), "Title", remind_at)

    # The second conversion never happened; preparation succeeded.
    assert now_raw.calls == 1
    assert prep.proposal.created_at == 1000.0
    assert _OnceSuccessfulFloat._SECRET not in repr(prep)


def test_stateful_clock_first_conversion_failure_redacted():
    """A clock whose first ``__float__`` raises must surface only the canonical
    safe error with no secret in str, repr, cause, or context."""
    now_raw = _AlwaysFailingFloat(1000.0)
    factory = ReminderProposalFactory(
        clock=lambda: now_raw, proposal_id_factory=lambda: "p-fail-1"
    )
    remind_at = datetime.fromtimestamp(2000.0, tz=UTC)
    with pytest.raises(ReminderPreparationError) as exc:
        factory.prepare(_owner(), "Title", remind_at)
    assert str(exc.value) == "reminder preparation failed"
    _assert_no_secret(exc.value, _AlwaysFailingFloat._SECRET)
    assert now_raw.calls == 1


def test_clock_retrieval_failure_has_no_secret_context():
    secret = "SECRET_CLOCK_RETRIEVAL"

    def failing_clock():
        raise ValueError(secret)

    factory = ReminderProposalFactory(
        clock=failing_clock, proposal_id_factory=lambda: "p-clock-fail"
    )
    remind_at = datetime.fromtimestamp(2000.0, tz=UTC)

    with pytest.raises(ReminderPreparationError) as exc:
        factory.prepare(_owner(), "Title", remind_at)

    assert str(exc.value) == "reminder preparation failed"
    _assert_no_secret(exc.value, secret)


def test_proposal_id_factory_failure_has_no_secret_context():
    secret = "SECRET_PROPOSAL_ID_FACTORY"

    def failing_id_factory():
        raise ValueError(secret)

    factory = ReminderProposalFactory(
        clock=lambda: 1000.0, proposal_id_factory=failing_id_factory
    )
    remind_at = datetime.fromtimestamp(2000.0, tz=UTC)

    with pytest.raises(ReminderPreparationError) as exc:
        factory.prepare(_owner(), "Title", remind_at)

    assert str(exc.value) == "reminder preparation failed"
    _assert_no_secret(exc.value, secret)


def test_successful_preparation_converts_clock_exactly_once():
    """A valid preparation must call ``__float__`` on the injected clock once."""
    now_raw = _CountingFloat(1000.0)
    factory = ReminderProposalFactory(
        clock=lambda: now_raw, proposal_id_factory=lambda: "p-count-1"
    )
    remind_at = datetime.fromtimestamp(2000.0, tz=UTC)
    prep = factory.prepare(_owner(), "Title", remind_at)

    assert now_raw.calls == 1
    assert prep.proposal.created_at == 1000.0
    assert prep.proposal.expires_at == 1000.0 + 900.0


def test_created_at_and_expires_at_share_normalized_instant():
    """``created_at`` and ``expires_at`` must use the same single normalized now.

    A drifting clock returns a different value on each ``__float__`` call;
    after single-conversion normalization both fields derive from the first
    (only) conversion, so they are mutually consistent.
    """
    now_raw = _DriftingFloat(1000.0)
    factory = ReminderProposalFactory(
        clock=lambda: now_raw, proposal_id_factory=lambda: "p-drift-1"
    )
    remind_at = datetime.fromtimestamp(2000.0, tz=UTC)
    prep = factory.prepare(_owner(), "Title", remind_at)

    assert now_raw.calls == 1
    # Both derive from the single normalized instant 1000.0.
    assert prep.proposal.created_at == 1000.0
    assert prep.proposal.expires_at == min(1000.0 + 900.0, remind_at.timestamp())
    assert prep.proposal.expires_at > prep.proposal.created_at


def test_expiry_boundary_behavior_intact_with_normalized_clock():
    """Existing expiry boundary behavior (min(now+TTL, remind_at)) is preserved."""
    # TTL-bound: remind_at far in the future -> expires_at == now + TTL
    now_raw_a = _CountingFloat(1000.0)
    factory_a = ReminderProposalFactory(
        clock=lambda: now_raw_a, proposal_id_factory=lambda: "p-bnd-a"
    )
    remind_at_far = datetime.fromtimestamp(1000.0, tz=UTC) + timedelta(seconds=5000)
    prep_a = factory_a.prepare(_owner(), "Future", remind_at_far)
    assert prep_a.proposal.expires_at == 1000.0 + 900.0
    assert now_raw_a.calls == 1

    # remind_at-bound: remind_at sooner than TTL -> expires_at == remind_at_ts
    now_raw_b = _CountingFloat(1000.0)
    factory_b = ReminderProposalFactory(
        clock=lambda: now_raw_b, proposal_id_factory=lambda: "p-bnd-b"
    )
    remind_at_soon = datetime.fromtimestamp(1000.0, tz=UTC) + timedelta(seconds=300)
    prep_b = factory_b.prepare(_owner(), "Soon", remind_at_soon)
    assert prep_b.proposal.expires_at == remind_at_soon.timestamp()
    assert prep_b.proposal.expires_at == 1000.0 + 300.0
    assert now_raw_b.calls == 1


# --------------------------------------------------------------------------
# Forbidden side effects check
# --------------------------------------------------------------------------


def test_no_forbidden_imports():
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "core"
        / "productivity"
        / "reminder.py"
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
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden, (
                    f"reminder.py imports forbidden module {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert node.module.split(".")[0] not in forbidden, (
                    f"reminder.py imports forbidden module {node.module}"
                )
