"""Bounded preparation contracts for Phase 3 calendar reads and event drafts.

This module validates and retains calendar inputs only in memory until an
approved execution path consumes them. It performs no EventKit, AppleScript,
network, provider, filesystem, email, browser, reminders, MCP, or execution
work.

All time handling uses injected clocks and proposal-ID factories; no hidden
wall clock or randomness is used.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from core.action_policy import ActorContext, validate_actor_context
from core.productivity.contracts import (
    ActionProposal,
    ActionTarget,
    PreviewField,
    ProductivityAction,
    TargetKind,
)

CALENDAR_NAME_MAX = 200
CALENDAR_TITLE_MAX = 500
CALENDAR_LOCATION_MAX = 500
CALENDAR_NOTES_MAX = 4_000
CALENDAR_PREVIEW_MAX = 2_000
CALENDAR_PROPOSAL_TTL = 900.0
CALENDAR_PREPARATION_LIMIT = 64
CALENDAR_MAX_RANGE_SECONDS = 31 * 24 * 3600  # one month

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")


class CalendarPreparationError(ValueError):
    """Fixed preparation failure without user content or exception details."""


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


def _finite_ts(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        f = float(value)
    except Exception:
        return None
    if not math.isfinite(f):
        return None
    return f


def _usable_tzinfo(dt: object) -> bool:
    """Return whether ``dt`` is a tz-aware datetime with a usable offset.

    A usable timezone exposes a non-None ``utcoffset()``. Custom ``tzinfo``
    implementations may raise; any exception is swallowed and treated as
    unusable so raw exception text is never surfaced.
    """
    if not isinstance(dt, datetime):
        return False
    try:
        offset = dt.utcoffset()
    except Exception:
        return False
    return offset is not None


@dataclass(frozen=True, repr=False)
class PreparedCalendarRead:
    """Server-private calendar read input with a content-free representation."""

    start: datetime
    end: datetime
    calendar_name: str | None

    def __post_init__(self) -> None:
        try:
            if not _usable_tzinfo(self.start):
                raise CalendarPreparationError("invalid calendar read")
            if not _usable_tzinfo(self.end):
                raise CalendarPreparationError("invalid calendar read")
            if self.start >= self.end:
                raise CalendarPreparationError("invalid calendar read")
            if (
                self.end - self.start
            ).total_seconds() > CALENDAR_MAX_RANGE_SECONDS:
                raise CalendarPreparationError("invalid calendar read")
        except CalendarPreparationError:
            raise
        except Exception:
            raise CalendarPreparationError("invalid calendar read") from None
        if self.calendar_name is not None:
            if self.calendar_name == "" or not _valid_text(
                self.calendar_name, CALENDAR_NAME_MAX, allow_newline_tab=False
            ):
                raise CalendarPreparationError("invalid calendar read")

    def __repr__(self) -> str:
        return "PreparedCalendarRead(...)"


@dataclass(frozen=True, repr=False)
class PreparedCalendarEventDraft:
    """Server-private calendar event draft with a content-free representation."""

    title: str
    start: datetime
    end: datetime
    calendar_name: str
    location: str | None
    notes: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or self.title.strip() == "":
            raise CalendarPreparationError("invalid calendar event draft")
        if not _valid_text(self.title, CALENDAR_TITLE_MAX, allow_newline_tab=False):
            raise CalendarPreparationError("invalid calendar event draft")
        try:
            if not _usable_tzinfo(self.start):
                raise CalendarPreparationError("invalid calendar event draft")
            if not _usable_tzinfo(self.end):
                raise CalendarPreparationError("invalid calendar event draft")
            if self.start >= self.end:
                raise CalendarPreparationError("invalid calendar event draft")
            if (
                self.end - self.start
            ).total_seconds() > CALENDAR_MAX_RANGE_SECONDS:
                raise CalendarPreparationError("invalid calendar event draft")
        except CalendarPreparationError:
            raise
        except Exception:
            raise CalendarPreparationError("invalid calendar event draft") from None
        if not isinstance(self.calendar_name, str) or self.calendar_name == "":
            raise CalendarPreparationError("invalid calendar event draft")
        if not _valid_text(
            self.calendar_name, CALENDAR_NAME_MAX, allow_newline_tab=False
        ):
            raise CalendarPreparationError("invalid calendar event draft")
        if self.location is not None and not _valid_text(
            self.location, CALENDAR_LOCATION_MAX, allow_newline_tab=True
        ):
            raise CalendarPreparationError("invalid calendar event draft")
        if self.notes is not None and not _valid_text(
            self.notes, CALENDAR_NOTES_MAX, allow_newline_tab=True
        ):
            raise CalendarPreparationError("invalid calendar event draft")

    def __repr__(self) -> str:
        return "PreparedCalendarEventDraft(...)"


@dataclass(frozen=True, repr=False)
class CalendarReadPreparation:
    """A public read proposal paired with its server-private input."""

    proposal: ActionProposal
    read: PreparedCalendarRead

    def __repr__(self) -> str:
        return "CalendarReadPreparation(...)"


@dataclass(frozen=True, repr=False)
class CalendarDraftPreparation:
    """A public event-draft proposal paired with its server-private input."""

    proposal: ActionProposal
    draft: PreparedCalendarEventDraft

    def __repr__(self) -> str:
        return "CalendarDraftPreparation(...)"


class CalendarReadProposalFactory:
    """Create canonical calendar-read proposals with injected time and IDs."""

    def __init__(
        self,
        clock: Callable[[], float],
        proposal_id_factory: Callable[[], str],
        *,
        ttl_seconds: float = CALENDAR_PROPOSAL_TTL,
    ) -> None:
        if not callable(clock) or not callable(proposal_id_factory):
            raise TypeError("clock and proposal ID factory must be callable")
        if (
            not isinstance(ttl_seconds, (int, float))
            or isinstance(ttl_seconds, bool)
        ):
            raise ValueError("invalid proposal lifetime")
        try:
            ttl_f = float(ttl_seconds)
        except Exception:
            raise ValueError("invalid proposal lifetime") from None
        if not math.isfinite(ttl_f) or not 1.0 <= ttl_f <= 900.0:
            raise ValueError("invalid proposal lifetime")
        self._clock = clock
        self._proposal_id_factory = proposal_id_factory
        self._ttl_seconds = ttl_f

    def prepare(
        self,
        actor: ActorContext,
        start: object,
        end: object,
        calendar_name: object = None,
    ) -> CalendarReadPreparation:
        valid_actor, _ = validate_actor_context(actor)
        if not valid_actor:
            raise CalendarPreparationError("calendar read preparation failed")
        try:
            now = self._clock()
            proposal_id = self._proposal_id_factory()
        except Exception:
            raise CalendarPreparationError("calendar read preparation failed") from None
        if (
            _finite_ts(now) is None
            or not isinstance(proposal_id, str)
            or not _IDENTIFIER_RE.fullmatch(proposal_id)
        ):
            raise CalendarPreparationError("calendar read preparation failed")

        try:
            read = PreparedCalendarRead(start, end, calendar_name)  # type: ignore[arg-type]
            start_iso = read.start.isoformat()
            end_iso = read.end.isoformat()
        except Exception:
            raise CalendarPreparationError("calendar read preparation failed") from None

        name_preview = (
            read.calendar_name[:CALENDAR_PREVIEW_MAX] if read.calendar_name else None
        )
        targets = (
            (ActionTarget(TargetKind.CALENDAR, name_preview),)
            if name_preview
            else ()
        )
        preview_fields = (
            PreviewField("start", "Start", start_iso),
            PreviewField("end", "End", end_iso),
        )
        if name_preview:
            preview_fields = (
                *preview_fields,
                PreviewField("calendar", "Calendar", name_preview),
            )
        try:
            proposal = ActionProposal(
                proposal_id=proposal_id,
                action=ProductivityAction.CALENDAR_READ,
                actor=actor,
                targets=targets,
                preview_fields=preview_fields,
                created_at=float(now),
                expires_at=float(now) + self._ttl_seconds,
            )
        except Exception:
            raise CalendarPreparationError("calendar read preparation failed") from None
        return CalendarReadPreparation(proposal, read)


class CalendarDraftProposalFactory:
    """Create canonical calendar event-draft proposals with injected time/IDs."""

    def __init__(
        self,
        clock: Callable[[], float],
        proposal_id_factory: Callable[[], str],
        *,
        ttl_seconds: float = CALENDAR_PROPOSAL_TTL,
    ) -> None:
        if not callable(clock) or not callable(proposal_id_factory):
            raise TypeError("clock and proposal ID factory must be callable")
        if (
            not isinstance(ttl_seconds, (int, float))
            or isinstance(ttl_seconds, bool)
        ):
            raise ValueError("invalid proposal lifetime")
        try:
            ttl_f = float(ttl_seconds)
        except Exception:
            raise ValueError("invalid proposal lifetime") from None
        if not math.isfinite(ttl_f) or not 1.0 <= ttl_f <= 900.0:
            raise ValueError("invalid proposal lifetime")
        self._clock = clock
        self._proposal_id_factory = proposal_id_factory
        self._ttl_seconds = ttl_f

    def prepare(
        self,
        actor: ActorContext,
        title: object,
        start: object,
        end: object,
        calendar_name: object,
        location: object = None,
        notes: object = None,
    ) -> CalendarDraftPreparation:
        valid_actor, _ = validate_actor_context(actor)
        if not valid_actor:
            raise CalendarPreparationError("calendar draft preparation failed")
        try:
            now = self._clock()
            proposal_id = self._proposal_id_factory()
        except Exception:
            raise CalendarPreparationError(
                "calendar draft preparation failed"
            ) from None
        if (
            _finite_ts(now) is None
            or not isinstance(proposal_id, str)
            or not _IDENTIFIER_RE.fullmatch(proposal_id)
        ):
            raise CalendarPreparationError("calendar draft preparation failed")

        try:
            draft = PreparedCalendarEventDraft(
                title, start, end, calendar_name, location, notes
            )  # type: ignore[arg-type]
            start_iso = draft.start.isoformat()
            end_iso = draft.end.isoformat()
        except Exception:
            raise CalendarPreparationError("calendar draft preparation failed") from None

        title_preview = draft.title[:CALENDAR_PREVIEW_MAX]
        calendar_preview = draft.calendar_name[:CALENDAR_PREVIEW_MAX]
        targets = (ActionTarget(TargetKind.CALENDAR, draft.calendar_name),)
        preview_fields = (
            PreviewField("title", "Title", title_preview),
            PreviewField("start", "Start", start_iso),
            PreviewField("end", "End", end_iso),
            PreviewField(
                "calendar",
                "Calendar",
                calendar_preview,
                truncated=len(draft.calendar_name) > len(calendar_preview),
            ),
        )
        if draft.location is not None:
            loc_preview = draft.location[:CALENDAR_PREVIEW_MAX]
            preview_fields = (
                *preview_fields,
                PreviewField(
                    "location",
                    "Location",
                    loc_preview,
                    truncated=len(draft.location) > len(loc_preview),
                ),
            )
        if draft.notes is not None:
            notes_preview = draft.notes[:CALENDAR_PREVIEW_MAX]
            preview_fields = (
                *preview_fields,
                PreviewField(
                    "notes",
                    "Notes",
                    notes_preview,
                    truncated=len(draft.notes) > len(notes_preview),
                ),
            )
        try:
            proposal = ActionProposal(
                proposal_id=proposal_id,
                action=ProductivityAction.CALENDAR_DRAFT,
                actor=actor,
                targets=targets,
                preview_fields=preview_fields,
                created_at=float(now),
                expires_at=float(now) + self._ttl_seconds,
            )
        except Exception:
            raise CalendarPreparationError(
                "calendar draft preparation failed"
            ) from None
        return CalendarDraftPreparation(proposal, draft)


class CalendarPreparationRegistry:
    """Bounded actor/session-scoped in-memory registry for prepared inputs.

    Holds at most 64 entries keyed by exact (actor_id, session_id, proposal_id).
    No persistence is performed.
    """

    def __init__(self, *, limit: int = CALENDAR_PREPARATION_LIMIT) -> None:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 64
        ):
            raise ValueError("invalid registry limit")
        self._limit = limit
        self._items: dict[tuple[str, str, str], object] = {}

    @staticmethod
    def _key(actor: ActorContext, proposal_id: str) -> tuple[str, str, str]:
        valid_actor, _ = validate_actor_context(actor)
        if (
            not valid_actor
            or not isinstance(proposal_id, str)
            or not _IDENTIFIER_RE.fullmatch(proposal_id)
        ):
            raise CalendarPreparationError("calendar registry operation failed")
        return actor.actor_id, actor.session_id, proposal_id

    def put(
        self, actor: ActorContext, proposal_id: str, item: object
    ) -> None:
        if not isinstance(
            item, (PreparedCalendarRead, PreparedCalendarEventDraft)
        ):
            raise CalendarPreparationError("calendar registry item rejected")
        key = self._key(actor, proposal_id)
        if key not in self._items and len(self._items) >= self._limit:
            raise CalendarPreparationError("calendar registry is full")
        self._items[key] = item

    def get(self, actor: ActorContext, proposal_id: str) -> object | None:
        return self._items.get(self._key(actor, proposal_id))

    def remove(self, actor: ActorContext, proposal_id: str) -> None:
        self._items.pop(self._key(actor, proposal_id), None)

    def clear_session(self, actor_id: str, session_id: str) -> None:
        for key in tuple(self._items):
            if key[0] == actor_id and key[1] == session_id:
                self._items.pop(key, None)
