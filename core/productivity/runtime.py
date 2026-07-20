"""Bounded Phase 3 productivity runtime controller.

The controller coordinates proposal registration, scoped approval, cancellation,
status reporting, and execution authorization. It emits only canonical transport
messages and performs no external action itself.
"""

from __future__ import annotations

import math
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from core.action_policy import ActorContext
from core.productivity.authorization import ApprovalScope
from core.productivity.contracts import ActionProposal, ProductivityAction
from core.productivity.service import ProductivityCode, ProductivityService, ServiceResult
from core.productivity.transport import (
    TransportError,
    confirmation_required,
    error_message,
    update_message,
)


_APPROVAL_TTL_SECONDS = 300.0
_SESSION_TTL_SECONDS = 28_800.0
_DURATION_CHOICES = frozenset({900, 3600, 28800})
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_FALLBACK_PROPOSAL_ID = "invalid-proposal"
_STATUS_TO_UPDATE = {
    "pending": "preview",
    "confirmed": "approved",
    "consumed": "completed",
    "revoked": "cancelled",
}


@dataclass(frozen=True)
class ConfirmationResult:
    """Server-private confirmation result.

    Contains the canonical public message and the server-private approval ID
    only when confirmation succeeds. The repr is intentionally minimal and never
    exposes the approval ID, actor/session IDs, proposal data, targets,
    preview content, or provider information.
    """

    public_message: dict[str, Any]
    approval_id: Optional[str] = None
    proposal_id: str = ""
    scope: Optional[ApprovalScope] = None

    def __repr__(self) -> str:
        ok = (
            isinstance(self.public_message, dict)
            and self.public_message.get("type") == "productivity_update"
        )
        return f"ConfirmationResult(ok={ok})"


class ProductivityRuntime:
    """Coordinate productivity contracts without executing external work."""

    def __init__(
        self,
        service: ProductivityService,
        clock: Callable[[], float],
        approval_id_factory: Callable[[], str],
    ) -> None:
        if not isinstance(service, ProductivityService):
            raise TypeError("service must be a ProductivityService")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not callable(approval_id_factory):
            raise TypeError("approval_id_factory must be callable")
        self._service = service
        self._clock = clock
        self._approval_id_factory = approval_id_factory
        self._cancelled: set[tuple[str, str | None, str]] = set()
        self._lock = threading.RLock()

    @staticmethod
    def _proposal_id(value: object) -> str:
        if isinstance(value, str) and _IDENTIFIER_RE.fullmatch(value):
            return value
        return _FALLBACK_PROPOSAL_ID

    @staticmethod
    def _scope_key(
        actor: object,
        proposal_id: str,
    ) -> tuple[str, str | None, str] | None:
        if not isinstance(actor, ActorContext):
            return None
        return (actor.actor_id, actor.session_id, proposal_id)

    def _now(self) -> float | None:
        try:
            value = self._clock()
        except Exception:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            numeric = float(value)
        except (OverflowError, TypeError, ValueError):
            return None
        if not math.isfinite(numeric):
            return None
        return numeric

    @staticmethod
    def _error(
        proposal_id: object,
        code: ProductivityCode,
        *,
        context: str | None = None,
    ) -> dict[str, Any]:
        safe_id = ProductivityRuntime._proposal_id(proposal_id)
        try:
            return error_message(safe_id, code, context=context)
        except (TransportError, TypeError, ValueError):
            return error_message(
                _FALLBACK_PROPOSAL_ID,
                ProductivityCode.CONSUMPTION_FAILED,
                context=context,
            )

    @staticmethod
    def _state(result: ServiceResult) -> str | None:
        payload = result.payload
        if not isinstance(payload, dict):
            return None
        state = payload.get("state")
        return state if isinstance(state, str) else None

    def prepare(self, actor: ActorContext, proposal: ActionProposal) -> dict[str, Any]:
        """Register a proposal and return its canonical confirmation preview."""
        proposal_id = getattr(proposal, "proposal_id", _FALLBACK_PROPOSAL_ID)
        now = self._now()
        if now is None:
            return self._error(proposal_id, ProductivityCode.CONSUMPTION_FAILED)
        try:
            result = self._service.register_proposal(actor, proposal, now)
        except Exception:
            return self._error(proposal_id, ProductivityCode.CONSUMPTION_FAILED)
        if result.code is not ProductivityCode.OK:
            return self._error(proposal_id, result.code)
        try:
            return confirmation_required(proposal)
        except Exception:
            try:
                self._service.cancel_proposal(actor, self._proposal_id(proposal_id), now)
            except Exception:
                pass
            return self._error(proposal_id, ProductivityCode.CONSUMPTION_FAILED)

    def confirm(self, actor: ActorContext, proposal_id: str) -> dict[str, Any]:
        """Issue one server-generated ONCE approval without exposing the approval ID."""
        return self.confirm_and_ticket(actor, proposal_id, ApprovalScope.ONCE).public_message

    def confirm_and_ticket(
        self,
        actor: ActorContext,
        proposal_id: str,
        scope: ApprovalScope,
        *,
        duration_seconds: Optional[int] = None,
        acknowledge: bool = False,
    ) -> ConfirmationResult:
        """Issue a scoped approval and return a server-private confirmation ticket.

        The public ``public_message`` is safe to send to the client. The
        ``approval_id`` is server-private and must never be serialized to the
        client.
        """
        safe_id = self._proposal_id(proposal_id)
        now = self._now()
        if now is None:
            return ConfirmationResult(
                public_message=self._error(
                    safe_id, ProductivityCode.CONSUMPTION_FAILED, context="confirm"
                ),
                proposal_id=safe_id,
            )

        if not isinstance(scope, ApprovalScope):
            return ConfirmationResult(
                public_message=self._error(
                    safe_id, ProductivityCode.INVALID_SCOPE, context="confirm"
                ),
                proposal_id=safe_id,
            )

        with self._lock:
            try:
                deadline = self._service.get_proposal_expiry(actor, proposal_id, now)
            except Exception:
                return ConfirmationResult(
                    public_message=self._error(
                        safe_id, ProductivityCode.CONSUMPTION_FAILED, context="confirm"
                    ),
                    proposal_id=safe_id,
                )
            if deadline.code is not ProductivityCode.OK:
                return ConfirmationResult(
                    public_message=self._error(safe_id, deadline.code, context="confirm"),
                    proposal_id=safe_id,
                )
            expires_at = deadline.payload
            if (
                isinstance(expires_at, bool)
                or not isinstance(expires_at, (int, float))
                or not math.isfinite(float(expires_at))
            ):
                return ConfirmationResult(
                    public_message=self._error(
                        safe_id, ProductivityCode.STATE_MISMATCH, context="confirm"
                    ),
                    proposal_id=safe_id,
                )

            try:
                current = self._service.status(actor, proposal_id, now)
            except Exception:
                return ConfirmationResult(
                    public_message=self._error(
                        safe_id, ProductivityCode.CONSUMPTION_FAILED, context="confirm"
                    ),
                    proposal_id=safe_id,
                )
            if current.code is not ProductivityCode.OK:
                return ConfirmationResult(
                    public_message=self._error(safe_id, current.code, context="confirm"),
                    proposal_id=safe_id,
                )

            state = self._state(current)
            if state == "consumed":
                return ConfirmationResult(
                    public_message=update_message(safe_id, "completed"),
                    proposal_id=safe_id,
                    scope=scope,
                )
            if state == "revoked":
                return ConfirmationResult(
                    public_message=update_message(safe_id, "cancelled"),
                    proposal_id=safe_id,
                    scope=scope,
                )
            if state == "expired":
                return ConfirmationResult(
                    public_message=self._error(
                        safe_id, ProductivityCode.PROPOSAL_EXPIRED, context="confirm"
                    ),
                    proposal_id=safe_id,
                )
            if state not in ("pending", "confirmed"):
                return ConfirmationResult(
                    public_message=self._error(
                        safe_id, ProductivityCode.STATE_MISMATCH, context="confirm"
                    ),
                    proposal_id=safe_id,
                )

            if scope is ApprovalScope.PRECISE_PERSISTENT:
                if duration_seconds is not None:
                    return ConfirmationResult(
                        public_message=self._error(
                            safe_id, ProductivityCode.INVALID_EXPIRY, context="confirm"
                        ),
                        proposal_id=safe_id,
                    )
                if acknowledge is not True:
                    return ConfirmationResult(
                        public_message=self._error(
                            safe_id,
                            ProductivityCode.INVALID_ACKNOWLEDGEMENT,
                            context="confirm",
                        ),
                        proposal_id=safe_id,
                    )
                approval_expiry: Optional[float] = None
            else:
                if acknowledge:
                    return ConfirmationResult(
                        public_message=self._error(
                            safe_id,
                            ProductivityCode.INVALID_ACKNOWLEDGEMENT,
                            context="confirm",
                        ),
                        proposal_id=safe_id,
                    )
                if scope is ApprovalScope.SESSION:
                    if duration_seconds is not None:
                        return ConfirmationResult(
                            public_message=self._error(
                                safe_id, ProductivityCode.INVALID_EXPIRY, context="confirm"
                            ),
                            proposal_id=safe_id,
                        )
                    approval_expiry = min(
                        float(expires_at), now + _SESSION_TTL_SECONDS
                    )
                elif scope is ApprovalScope.DURATION:
                    if duration_seconds not in _DURATION_CHOICES:
                        return ConfirmationResult(
                            public_message=self._error(
                                safe_id, ProductivityCode.INVALID_EXPIRY, context="confirm"
                            ),
                            proposal_id=safe_id,
                        )
                    approval_expiry = min(float(expires_at), now + duration_seconds)
                else:
                    # ONCE
                    if duration_seconds is not None:
                        return ConfirmationResult(
                            public_message=self._error(
                                safe_id, ProductivityCode.INVALID_EXPIRY, context="confirm"
                            ),
                            proposal_id=safe_id,
                        )
                    approval_expiry = min(float(expires_at), now + _APPROVAL_TTL_SECONDS)

                if approval_expiry <= now:
                    return ConfirmationResult(
                        public_message=self._error(
                            safe_id, ProductivityCode.PROPOSAL_EXPIRED, context="confirm"
                        ),
                        proposal_id=safe_id,
                    )

            def _make_approval_id() -> str:
                value = self._approval_id_factory()
                if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
                    raise ValueError("invalid approval_id")
                return value

            try:
                result = self._service.confirm(
                    actor,
                    proposal_id,
                    _make_approval_id,
                    now,
                    scope,
                    expiry=approval_expiry,
                    acknowledge=acknowledge,
                )
            except Exception:
                return ConfirmationResult(
                    public_message=self._error(
                        safe_id, ProductivityCode.CONSUMPTION_FAILED, context="confirm"
                    ),
                    proposal_id=safe_id,
                )
            if result.code is not ProductivityCode.OK:
                return ConfirmationResult(
                    public_message=self._error(safe_id, result.code, context="confirm"),
                    proposal_id=safe_id,
                )

            # Idempotency: the service may have returned an existing approval_id.
            if not isinstance(result.payload, str) or not _IDENTIFIER_RE.fullmatch(
                result.payload
            ):
                return ConfirmationResult(
                    public_message=self._error(
                        safe_id, ProductivityCode.CONSUMPTION_FAILED, context="confirm"
                    ),
                    proposal_id=safe_id,
                )
            effective_approval_id = result.payload
            return ConfirmationResult(
                public_message=update_message(safe_id, "approved"),
                approval_id=effective_approval_id,
                proposal_id=safe_id,
                scope=scope,
            )

    def cancel(self, actor: ActorContext, proposal_id: str) -> dict[str, Any]:
        """Cancel a proposal, revoke its approval, and remain idempotent."""
        safe_id = self._proposal_id(proposal_id)
        now = self._now()
        if now is None:
            return self._error(safe_id, ProductivityCode.CONSUMPTION_FAILED, context="cancel")
        scope_key = self._scope_key(actor, proposal_id)

        with self._lock:
            if scope_key is not None and scope_key in self._cancelled:
                return update_message(safe_id, "cancelled")
            try:
                result = self._service.cancel_proposal(actor, proposal_id, now)
            except Exception:
                return self._error(safe_id, ProductivityCode.CONSUMPTION_FAILED, context="cancel")
            if result.code is not ProductivityCode.OK:
                return self._error(safe_id, result.code, context="cancel")
            if scope_key is not None:
                self._cancelled.add(scope_key)
            return update_message(safe_id, "cancelled")

    def status(self, actor: ActorContext, proposal_id: str) -> dict[str, Any]:
        """Return a safe canonical status without disclosing another session."""
        safe_id = self._proposal_id(proposal_id)
        now = self._now()
        if now is None:
            return self._error(safe_id, ProductivityCode.CONSUMPTION_FAILED)
        scope_key = self._scope_key(actor, proposal_id)
        if scope_key is not None and scope_key in self._cancelled:
            return update_message(safe_id, "cancelled")
        try:
            result = self._service.status(actor, proposal_id, now)
        except Exception:
            return self._error(safe_id, ProductivityCode.CONSUMPTION_FAILED)
        if result.code is ProductivityCode.PROPOSAL_NOT_FOUND:
            try:
                deadline = self._service.get_proposal_expiry(actor, proposal_id, now)
            except Exception:
                deadline = ServiceResult(ProductivityCode.CONSUMPTION_FAILED)
            if deadline.code is ProductivityCode.PROPOSAL_EXPIRED:
                return self._error(safe_id, ProductivityCode.PROPOSAL_EXPIRED)
        if result.code is not ProductivityCode.OK:
            return self._error(safe_id, result.code)
        state = self._state(result)
        if state == "expired":
            return self._error(safe_id, ProductivityCode.PROPOSAL_EXPIRED)
        mapped = _STATUS_TO_UPDATE.get(state or "")
        if mapped is None:
            return self._error(safe_id, ProductivityCode.STATE_MISMATCH)
        return update_message(safe_id, mapped)

    def authorize_execution(
        self,
        actor: ActorContext,
        approval_id: str,
        action: ProductivityAction,
        proposal_id: str,
    ) -> dict[str, Any]:
        """Consume an exact scoped approval and authorize, but do not execute."""
        safe_id = self._proposal_id(proposal_id)
        now = self._now()
        if now is None:
            return self._error(safe_id, ProductivityCode.CONSUMPTION_FAILED)
        if not isinstance(approval_id, str) or not _IDENTIFIER_RE.fullmatch(approval_id):
            return self._error(safe_id, ProductivityCode.STATE_MISMATCH)
        if not isinstance(action, ProductivityAction):
            return self._error(safe_id, ProductivityCode.STATE_MISMATCH)
        try:
            result = self._service.consume(
                actor,
                approval_id,
                action,
                proposal_id,
                now,
            )
        except Exception:
            return self._error(safe_id, ProductivityCode.CONSUMPTION_FAILED)
        if result.code is not ProductivityCode.OK:
            return self._error(safe_id, result.code)
        return update_message(safe_id, "executing")
