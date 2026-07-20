"""Bounded preparation contracts for Phase 3 email drafts.

This module validates and retains draft inputs only in memory until an
approved execution path consumes them. It performs no email, network,
provider, filesystem, logging, or persistence work.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from core.action_policy import ActorContext, validate_actor_context
from core.productivity.contracts import (
    ActionProposal,
    ActionTarget,
    PreviewField,
    ProductivityAction,
    TargetKind,
)

EMAIL_RECIPIENT_MAX = 320
EMAIL_SUBJECT_MAX = 998
EMAIL_BODY_MAX = 20_000
EMAIL_PREVIEW_MAX = 2_000
EMAIL_PROPOSAL_TTL = 300.0
EMAIL_PREPARATION_LIMIT = 64

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")


class EmailDraftPreparationError(ValueError):
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


@dataclass(frozen=True, repr=False)
class PreparedEmailDraft:
    """Server-private email draft input with a content-free representation."""

    recipient: str
    subject: str
    body: str

    def __post_init__(self) -> None:
        if not self.recipient or not _valid_text(
            self.recipient, EMAIL_RECIPIENT_MAX, allow_newline_tab=False
        ):
            raise EmailDraftPreparationError("invalid email draft")
        if not _valid_text(self.subject, EMAIL_SUBJECT_MAX, allow_newline_tab=False):
            raise EmailDraftPreparationError("invalid email draft")
        if not _valid_text(self.body, EMAIL_BODY_MAX, allow_newline_tab=True):
            raise EmailDraftPreparationError("invalid email draft")

    def __repr__(self) -> str:
        return "PreparedEmailDraft(...)"


@dataclass(frozen=True, repr=False)
class EmailDraftPreparation:
    """A public proposal paired with its server-private draft input."""

    proposal: ActionProposal
    draft: PreparedEmailDraft

    def __repr__(self) -> str:
        return "EmailDraftPreparation(...)"


class EmailDraftProposalFactory:
    """Create canonical email-draft proposals with injected time and IDs."""

    def __init__(
        self,
        clock: Callable[[], float],
        proposal_id_factory: Callable[[], str],
        *,
        ttl_seconds: float = EMAIL_PROPOSAL_TTL,
    ) -> None:
        if not callable(clock) or not callable(proposal_id_factory):
            raise TypeError("clock and proposal ID factory must be callable")
        if not isinstance(ttl_seconds, (int, float)) or isinstance(ttl_seconds, bool):
            raise ValueError("invalid proposal lifetime")
        if not math.isfinite(float(ttl_seconds)) or not 1 <= ttl_seconds <= 900:
            raise ValueError("invalid proposal lifetime")
        self._clock = clock
        self._proposal_id_factory = proposal_id_factory
        self._ttl_seconds = float(ttl_seconds)

    def prepare(
        self,
        actor: ActorContext,
        recipient: object,
        subject: object,
        body: object,
    ) -> EmailDraftPreparation:
        valid_actor, _ = validate_actor_context(actor)
        if not valid_actor:
            raise EmailDraftPreparationError("email draft preparation failed")
        try:
            now = self._clock()
            proposal_id = self._proposal_id_factory()
        except Exception:
            raise EmailDraftPreparationError("email draft preparation failed") from None
        if (
            isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(float(now))
            or not isinstance(proposal_id, str)
            or not _IDENTIFIER_RE.fullmatch(proposal_id)
        ):
            raise EmailDraftPreparationError("email draft preparation failed")

        draft = PreparedEmailDraft(recipient, subject, body)
        body_preview = draft.body[:EMAIL_PREVIEW_MAX]
        try:
            proposal = ActionProposal(
                proposal_id=proposal_id,
                action=ProductivityAction.EMAIL_DRAFT,
                actor=actor,
                targets=(
                    ActionTarget(TargetKind.EMAIL_RECIPIENT, draft.recipient),
                ),
                preview_fields=(
                    PreviewField("recipient", "Recipient", draft.recipient),
                    PreviewField("subject", "Subject", draft.subject),
                    PreviewField(
                        "body",
                        "Body",
                        body_preview,
                        truncated=len(draft.body) > len(body_preview),
                    ),
                ),
                created_at=float(now),
                expires_at=float(now) + self._ttl_seconds,
            )
        except Exception:
            raise EmailDraftPreparationError("email draft preparation failed") from None
        return EmailDraftPreparation(proposal, draft)


class EmailDraftPreparationRegistry:
    """Bounded actor/session-scoped in-memory registry for prepared drafts."""

    def __init__(self, *, limit: int = EMAIL_PREPARATION_LIMIT) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 64:
            raise ValueError("invalid registry limit")
        self._limit = limit
        self._items: dict[tuple[str, str, str], PreparedEmailDraft] = {}

    @staticmethod
    def _key(actor: ActorContext, proposal_id: str) -> tuple[str, str, str]:
        valid_actor, _ = validate_actor_context(actor)
        if not valid_actor or not _IDENTIFIER_RE.fullmatch(proposal_id):
            raise EmailDraftPreparationError("email draft registry operation failed")
        return actor.actor_id, actor.session_id, proposal_id

    def put(
        self, actor: ActorContext, proposal_id: str, draft: PreparedEmailDraft
    ) -> None:
        key = self._key(actor, proposal_id)
        if not isinstance(draft, PreparedEmailDraft):
            raise EmailDraftPreparationError("email draft registry operation failed")
        if key not in self._items and len(self._items) >= self._limit:
            raise EmailDraftPreparationError("email draft registry is full")
        self._items[key] = draft

    def get(self, actor: ActorContext, proposal_id: str) -> PreparedEmailDraft | None:
        return self._items.get(self._key(actor, proposal_id))

    def remove(self, actor: ActorContext, proposal_id: str) -> None:
        self._items.pop(self._key(actor, proposal_id), None)

    def clear_session(self, actor_id: str, session_id: str) -> None:
        for key in tuple(self._items):
            if key[0] == actor_id and key[1] == session_id:
                self._items.pop(key, None)
