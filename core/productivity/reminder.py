"""Bounded preparation contracts for Phase 3 reminder creation.

This module validates and retains reminder inputs only in memory until an
approved execution path consumes them. It performs no creation, reading,
listing, modification, network, provider, filesystem, email, browser,
reminders app, MCP, or execution work.

All time handling uses injected clocks and proposal-ID factories; no hidden
wall clock or randomness is used.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from core.action_policy import ActorContext, validate_actor_context
from core.productivity.contracts import (
    ActionProposal,
    ActionTarget,
    PreviewField,
    ProductivityAction,
    TargetKind,
)

REMINDER_TITLE_MAX = 500
REMINDER_NOTES_MAX = 4_000
REMINDER_LIST_NAME_MAX = 200
REMINDER_PROPOSAL_TTL = 900.0
REMINDER_PREPARATION_LIMIT = 64
DEFAULT_REMINDER_LIST_LABEL = "Default reminder list"

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")


class ReminderPreparationError(ValueError):
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
    return offset is not None and isinstance(offset, timedelta)


@dataclass(frozen=True, repr=False)
class PreparedReminderInput:
    """Server-private reminder input with a content-free representation."""

    title: str
    remind_at: datetime
    notes: str | None
    list_name: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or self.title.strip() == "":
            raise ReminderPreparationError("invalid reminder input")
        if not _valid_text(self.title, REMINDER_TITLE_MAX, allow_newline_tab=False):
            raise ReminderPreparationError("invalid reminder input")
        try:
            if not _usable_tzinfo(self.remind_at):
                raise ReminderPreparationError("invalid reminder input")
        except Exception:
            raise ReminderPreparationError("invalid reminder input") from None

        if self.notes is not None and not _valid_text(
            self.notes, REMINDER_NOTES_MAX, allow_newline_tab=True
        ):
            raise ReminderPreparationError("invalid reminder input")

        if self.list_name is not None:
            if self.list_name == "" or not _valid_text(
                self.list_name, REMINDER_LIST_NAME_MAX, allow_newline_tab=False
            ):
                raise ReminderPreparationError("invalid reminder input")

    def __repr__(self) -> str:
        return "PreparedReminderInput(...)"


@dataclass(frozen=True, repr=False)
class ReminderPreparation:
    """A public reminder creation proposal paired with its server-private input."""

    proposal: ActionProposal
    reminder: PreparedReminderInput

    def __repr__(self) -> str:
        return "ReminderPreparation(...)"


class ReminderProposalFactory:
    """Create canonical reminder creation proposals with injected time and IDs."""

    def __init__(
        self,
        clock: Callable[[], float],
        proposal_id_factory: Callable[[], str],
        *,
        ttl_seconds: float = REMINDER_PROPOSAL_TTL,
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
        remind_at: object,
        notes: object = None,
        list_name: object = None,
    ) -> ReminderPreparation:
        valid_actor, _ = validate_actor_context(actor)
        if not valid_actor:
            raise ReminderPreparationError("reminder preparation failed")

        # Normalize the injected clock exactly once into a plain float. The
        # raw result may be a stateful numeric object whose __float__ succeeds
        # on the first call but raises on a later one, so it is converted a
        # single time inside this guarded block and the resulting ``now_f`` is
        # the only value used for all downstream time computations
        # (fromtimestamp, created_at, expiry). ``_finite_ts`` rejects booleans,
        # non-numeric types, NaN, and infinities and never re-exposes a
        # conversion exception.
        clock_failed = False
        try:
            now_raw = self._clock()
            now_f = _finite_ts(now_raw)
        except Exception:
            clock_failed = True
            now_f = None

        if clock_failed or now_f is None:
            raise ReminderPreparationError("reminder preparation failed") from None

        proposal_id_failed = False
        try:
            proposal_id = self._proposal_id_factory()
        except Exception:
            proposal_id_failed = True
            proposal_id = None

        if (
            proposal_id_failed
            or not isinstance(proposal_id, str)
            or not _IDENTIFIER_RE.fullmatch(proposal_id)
        ):
            raise ReminderPreparationError("reminder preparation failed")

        try:
            reminder = PreparedReminderInput(title, remind_at, notes, list_name)  # type: ignore[arg-type]
        except ReminderPreparationError as e:
            if str(e) == "invalid reminder input":
                raise
            raise ReminderPreparationError("reminder preparation failed") from None
        except Exception:
            raise ReminderPreparationError("reminder preparation failed") from None

        try:
            now_dt = datetime.fromtimestamp(now_f, tz=timezone.utc)
            if reminder.remind_at <= now_dt:
                raise ReminderPreparationError("invalid reminder input")
            if (reminder.remind_at - now_dt).total_seconds() > 366 * 24 * 3600:
                raise ReminderPreparationError("invalid reminder input")
            remind_at_iso = reminder.remind_at.isoformat()
            remind_at_ts = reminder.remind_at.timestamp()
        except ReminderPreparationError as e:
            if str(e) == "invalid reminder input":
                raise
            raise ReminderPreparationError("reminder preparation failed") from None
        except Exception:
            raise ReminderPreparationError("reminder preparation failed") from None
        destination_label = (
            reminder.list_name if reminder.list_name is not None else DEFAULT_REMINDER_LIST_LABEL
        )
        try:
            targets = (ActionTarget(TargetKind.REMINDER_LIST, destination_label),)
        except Exception:
            raise ReminderPreparationError("reminder preparation failed") from None

        preview_fields = [
            PreviewField("title", "Title", reminder.title),
            PreviewField("remind_at", "Remind At", remind_at_iso),
        ]
        if reminder.notes is not None:
            preview_fields.append(PreviewField("notes", "Notes", reminder.notes))
        if reminder.list_name is not None:
            preview_fields.append(PreviewField("list", "List", reminder.list_name))

        expires_at = min(now_f + self._ttl_seconds, remind_at_ts)

        try:
            proposal = ActionProposal(
                proposal_id=proposal_id,
                action=ProductivityAction.REMINDER_CREATE,
                actor=actor,
                targets=targets,
                preview_fields=tuple(preview_fields),
                created_at=now_f,
                expires_at=expires_at,
            )
        except Exception:
            raise ReminderPreparationError("reminder preparation failed") from None

        return ReminderPreparation(proposal, reminder)


class ReminderPreparationRegistry:
    """Bounded actor/session-scoped in-memory registry for prepared reminder inputs.

    Holds at most 64 entries keyed by exact (actor_id, session_id, proposal_id).
    No persistence is performed.
    """

    def __init__(self, *, limit: int = REMINDER_PREPARATION_LIMIT) -> None:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 64
        ):
            raise ValueError("invalid registry limit")
        self._limit = limit
        self._items: dict[tuple[str, str, str], PreparedReminderInput] = {}

    @staticmethod
    def _key(actor: ActorContext, proposal_id: str) -> tuple[str, str, str]:
        valid_actor, _ = validate_actor_context(actor)
        if (
            not valid_actor
            or not isinstance(proposal_id, str)
            or not _IDENTIFIER_RE.fullmatch(proposal_id)
        ):
            raise ReminderPreparationError("reminder registry operation failed")
        return actor.actor_id, actor.session_id, proposal_id

    def put(
        self, actor: ActorContext, proposal_id: str, item: object
    ) -> None:
        if not isinstance(item, PreparedReminderInput):
            raise ReminderPreparationError("reminder registry item rejected")
        key = self._key(actor, proposal_id)
        if key not in self._items and len(self._items) >= self._limit:
            raise ReminderPreparationError("reminder registry is full")
        self._items[key] = item

    def get(self, actor: ActorContext, proposal_id: str) -> PreparedReminderInput | None:
        return self._items.get(self._key(actor, proposal_id))

    def remove(self, actor: ActorContext, proposal_id: str) -> None:
        self._items.pop(self._key(actor, proposal_id), None)

    def clear_session(self, actor_id: str, session_id: str) -> None:
        for key in tuple(self._items):
            if key[0] == actor_id and key[1] == session_id:
                self._items.pop(key, None)
