"""Transport-independent service for bounded Phase 4 task handoffs.

This module provides the public handoff controller. It performs no execution,
creates no authority, and stores no approval IDs, grants, or tickets. It
relies on an injected bounded task-lookup callable to prove a task exists in
the offering transport scope without querying global storage directly.
"""

from __future__ import annotations

import math
from typing import Callable, Optional

from core.action_policy import Actor, ActorContext, validate_actor_context
from core.handoff.contracts import (
    FrozenHandoffPreview,
    HandoffErrorCode,
    HandoffRecord,
    HandoffResult,
    HandoffState,
)
from core.handoff.store import (
    DuplicateHandoffError,
    HandoffStore,
    HandoffStoreError,
)


TaskLookup = Callable[[ActorContext, str], Optional[FrozenHandoffPreview]]
AcceptancePolicy = Callable[[ActorContext, FrozenHandoffPreview], bool]


class HandoffService:
    """Transport-independent controller for bounded task handoffs."""

    def __init__(
        self,
        store: HandoffStore,
        *,
        task_lookup: TaskLookup,
        acceptance_policy: AcceptancePolicy,
    ) -> None:
        if not isinstance(store, HandoffStore):
            raise ValueError("store must be a HandoffStore")
        if not callable(task_lookup):
            raise ValueError("task_lookup must be callable")
        if not callable(acceptance_policy):
            raise ValueError("acceptance_policy must be callable")
        self._store = store
        self._task_lookup = task_lookup
        self._acceptance_policy = acceptance_policy

    @property
    def store(self) -> HandoffStore:
        return self._store

    def _now(self) -> float:
        try:
            value = self._store.clock()
        except Exception:
            raise ValueError("handoff clock unavailable") from None
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise ValueError("handoff clock unavailable")
        return float(value)

    def _visible_record(
        self,
        actor: ActorContext,
        handoff_id: str,
    ) -> Optional[HandoffRecord]:
        scoped = self._store.get_scoped(
            handoff_id,
            actor_id=actor.actor_id,
            session_id=actor.session_id,
        )
        if scoped is not None or actor.actor is not Actor.OWNER:
            return scoped
        offered = self._store.get_for_owner(handoff_id)
        if offered is not None and offered.actor_id == actor.actor_id:
            return None
        return offered

    def prepare(
        self,
        actor: ActorContext,
        task_reference: str,
        summary: str,
        request_id: str,
    ) -> HandoffResult:
        """Offer a handoff for an exact task in the actor's transport scope."""
        # The caller-supplied summary is verified against the authoritative
        # preview from the bounded task lookup; it cannot alter the stored task.
        valid, _ = self._validate_actor_context(actor)
        if not valid:
            return HandoffResult(
                success=False,
                request_id=request_id,
                error_code=HandoffErrorCode.UNAUTHORIZED,
            )

        try:
            preview = self._task_lookup(actor, task_reference)
        except Exception:
            # Never leak exception text or provider details to callers.
            return HandoffResult(
                success=False,
                request_id=request_id,
                error_code=HandoffErrorCode.UNAVAILABLE,
            )

        if preview is None:
            return HandoffResult(
                success=False,
                request_id=request_id,
                error_code=HandoffErrorCode.TASK_NOT_FOUND,
            )
        if preview.task_id != task_reference:
            return HandoffResult(
                success=False,
                request_id=request_id,
                error_code=HandoffErrorCode.TASK_NOT_FOUND,
            )
        # Caller-supplied summary must match the authoritative preview.
        if summary != preview.summary:
            return HandoffResult(
                success=False,
                request_id=request_id,
                error_code=HandoffErrorCode.INVALID_REQUEST,
            )

        try:
            record = self._store.create_offer(
                actor_id=actor.actor_id,
                session_id=actor.session_id,
                task_id=preview.task_id,
                summary=preview.summary,
                snapshot_digest=preview.snapshot_digest,
                request_id=request_id,
            )
        except DuplicateHandoffError:
            return HandoffResult(
                success=False,
                request_id=request_id,
                error_code=HandoffErrorCode.HANDOFF_CONFLICT,
            )
        except (HandoffStoreError, ValueError):
            return HandoffResult(
                success=False,
                request_id=request_id,
                error_code=HandoffErrorCode.UNAVAILABLE,
            )

        return HandoffResult(
            success=True,
            request_id=request_id,
            handoff_id=record.handoff_id,
            state=record.state,
        )

    def accept(
        self,
        desktop_actor: ActorContext,
        handoff_id: str,
        acknowledged: bool,
    ) -> HandoffResult:
        """Record explicit desktop acknowledgement of a handoff.

        Acceptance performs no execution and creates no authority. The stored
        frozen preview is authoritative and is evaluated by the injected
        desktop policy before the state transition.
        """
        valid, _ = self._validate_actor_context(desktop_actor)
        if not valid:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.UNAUTHORIZED,
            )

        if desktop_actor.actor is not Actor.OWNER:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.UNAUTHORIZED,
            )
        if acknowledged is not True:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.INVALID_REQUEST,
            )

        record = self._visible_record(desktop_actor, handoff_id)
        if record is None:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.HANDOFF_NOT_FOUND,
            )
        if record.state is not HandoffState.OFFERED:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.HANDOFF_CONFLICT,
            )
        if record.is_expired(self._now()):
            self._store.expire_for_owner(handoff_id, now=self._now())
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.HANDOFF_EXPIRED,
            )

        preview = FrozenHandoffPreview(
            task_id=record.task_id,
            summary=record.summary,
        )
        try:
            allowed = self._acceptance_policy(desktop_actor, preview)
        except Exception:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.UNAVAILABLE,
            )
        if allowed is not True:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.POLICY_DENIED,
            )

        accepted = self._store.accept_for_owner(handoff_id)
        if accepted is None:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.HANDOFF_CONFLICT,
            )

        return HandoffResult(
            success=True,
            handoff_id=handoff_id,
            state=accepted.state,
        )

    def reject(
        self,
        desktop_actor: ActorContext,
        handoff_id: str,
    ) -> HandoffResult:
        """Record explicit rejection of a handoff."""
        valid, _ = self._validate_actor_context(desktop_actor)
        if not valid:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.UNAUTHORIZED,
            )

        if desktop_actor.actor is not Actor.OWNER:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.UNAUTHORIZED,
            )

        record = self._visible_record(desktop_actor, handoff_id)
        if record is None:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.HANDOFF_NOT_FOUND,
            )
        if record.state is HandoffState.REJECTED:
            return HandoffResult(
                success=True,
                handoff_id=handoff_id,
                state=record.state,
            )
        if record.state is not HandoffState.OFFERED:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.HANDOFF_CONFLICT,
            )
        if record.is_expired(self._now()):
            self._store.expire_for_owner(handoff_id, now=self._now())
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.HANDOFF_EXPIRED,
            )

        rejected = self._store.reject_for_owner(handoff_id)
        if rejected is None:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.HANDOFF_CONFLICT,
            )

        return HandoffResult(
            success=True,
            handoff_id=handoff_id,
            state=rejected.state,
        )

    def cancel(
        self,
        actor: ActorContext,
        handoff_id: str,
    ) -> HandoffResult:
        """Cancel a handoff from the offering side."""
        valid, _ = self._validate_actor_context(actor)
        if not valid:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.UNAUTHORIZED,
            )

        record = self._visible_record(actor, handoff_id)
        if record is None:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.HANDOFF_NOT_FOUND,
            )
        if record.state is HandoffState.CANCELLED:
            return HandoffResult(
                success=True,
                handoff_id=handoff_id,
                state=record.state,
            )
        if record.state is not HandoffState.OFFERED:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.HANDOFF_CONFLICT,
            )
        if record.is_expired(self._now()):
            if actor.actor is Actor.OWNER:
                self._store.expire_for_owner(handoff_id, now=self._now())
            else:
                self._store.expire(
                    handoff_id,
                    actor_id=actor.actor_id,
                    session_id=actor.session_id,
                    now=self._now(),
                )
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.HANDOFF_EXPIRED,
            )

        cancelled = (
            self._store.cancel_for_owner(handoff_id)
            if actor.actor is Actor.OWNER
            else self._store.cancel(
                handoff_id,
                actor_id=actor.actor_id,
                session_id=actor.session_id,
            )
        )
        if cancelled is None:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.HANDOFF_CONFLICT,
            )

        return HandoffResult(
            success=True,
            handoff_id=handoff_id,
            state=cancelled.state,
        )

    def status(
        self,
        actor: ActorContext,
        handoff_id: str,
    ) -> HandoffResult:
        """Return the current handoff state without mutating it."""
        valid, _ = self._validate_actor_context(actor)
        if not valid:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.UNAUTHORIZED,
            )

        record = self._visible_record(actor, handoff_id)
        if record is None:
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.HANDOFF_NOT_FOUND,
            )
        if record.state is HandoffState.OFFERED and record.is_expired(self._now()):
            if actor.actor is Actor.OWNER:
                self._store.expire_for_owner(handoff_id, now=self._now())
            else:
                self._store.expire(
                    handoff_id,
                    actor_id=actor.actor_id,
                    session_id=actor.session_id,
                    now=self._now(),
                )
            return HandoffResult(
                success=False,
                handoff_id=handoff_id,
                error_code=HandoffErrorCode.HANDOFF_EXPIRED,
            )

        return HandoffResult(
            success=True,
            handoff_id=handoff_id,
            state=record.state,
        )

    def expire_due(self) -> int:
        """Transition all past-TTL offered handoffs to expired."""
        return self._store.expire_due(now=self._now())

    def _validate_actor_context(self, actor: object) -> tuple[bool, str]:
        """Validate server-derived actor metadata."""
        return validate_actor_context(actor)
