"""Bounded Phase 3 productivity execution coordinator.

The coordinator sits between ``ProductivityRuntime.authorize_execution`` and
injected action adapters. It enforces exactly-once authorization, fail-closed
adapter invocation, and canonical outbound messages. It performs no external
execution itself.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Any, Protocol

from core.action_policy import ActorContext, validate_actor_context
from core.protocol import validate_server_message
from core.productivity.action_results import BrowserSearchResult, CalendarReadResult
from core.productivity.contracts import ProductivityAction
from core.productivity.runtime import ProductivityRuntime
from core.productivity.service import ProductivityCode
from core.productivity.transport import (
    TransportError,
    calendar_result_message,
    error_message,
    research_result_message,
    update_message,
)


class AdapterResultStatus(StrEnum):
    """Bounded adapter result status."""

    SUCCESS = "success"
    FAILED = "failed"


@dataclass(frozen=True)
class AdapterResult:
    """Bounded adapter result.

    The ``code`` is an optional stable, bounded token that the adapter can use
    to communicate a failure category. It is never returned raw to the client.
    """

    status: AdapterResultStatus
    code: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, AdapterResultStatus):
            raise ValueError("invalid adapter result status")
        if self.code is not None and not isinstance(self.code, str):
            raise ValueError("adapter result code must be a string")


_MAX_ADAPTER_FIELDS = 32
_MAX_ADAPTER_TEXT = 4000
_MAX_ADAPTER_TOTAL_TEXT = 8192
_FORBIDDEN_INPUT_FIELD_PARTS = frozenset(
    ("actor", "session", "approval", "secret", "prompt", "provider", "exception")
)


@dataclass(frozen=True, repr=False)
class AdapterInput:
    """Base class for immutable, bounded, action-specific adapter inputs.

    Concrete subclasses must be frozen dataclasses, bind themselves to one
    ``ProductivityAction``, use only bounded scalar or tuple fields, and retain
    this content-free representation.

    Subclasses may tighten or raise individual field and aggregate text limits by
    overriding ``field_text_limit`` and ``total_text_limit``. Defaults stay
    conservative so undeclared adapter shapes cannot silently accept large text.
    """

    action: ProductivityAction

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(...)"

    def field_text_limit(self, field_name: str) -> int:
        """Return the maximum Unicode code-point length for one text field."""
        return _MAX_ADAPTER_TEXT

    def total_text_limit(self) -> int:
        """Return the maximum aggregate Unicode code-point length for this input."""
        return _MAX_ADAPTER_TOTAL_TEXT

    def validate(self) -> None:
        params = getattr(type(self), "__dataclass_params__", None)
        if params is None or not params.frozen:
            raise ValueError("adapter input must be a frozen dataclass")
        if type(self).__repr__ is not AdapterInput.__repr__:
            raise ValueError("adapter input must use the privacy-safe repr")
        if not isinstance(self.action, ProductivityAction):
            raise ValueError("adapter input action is invalid")

        declared_fields = fields(self)
        if len(declared_fields) > _MAX_ADAPTER_FIELDS:
            raise ValueError("adapter input has too many fields")

        total_text = 0
        total_limit = self.total_text_limit()
        for item in declared_fields:
            lowered_name = item.name.lower()
            if item.name != "action" and any(
                part in lowered_name for part in _FORBIDDEN_INPUT_FIELD_PARTS
            ):
                raise ValueError("adapter input contains a forbidden field")
            value = getattr(self, item.name)
            values = value if isinstance(value, tuple) else (value,)
            if not isinstance(values, tuple) or len(values) > _MAX_ADAPTER_FIELDS:
                raise ValueError("adapter input field is not bounded")
            field_limit = self.field_text_limit(item.name)
            for member in values:
                if member is None or isinstance(member, (ProductivityAction, bool)):
                    continue
                if isinstance(member, int):
                    if abs(member) > 9_007_199_254_740_991:
                        raise ValueError("adapter input integer exceeds the bound")
                    continue
                if not isinstance(member, str):
                    raise ValueError("adapter input field has an unsupported type")
                if len(member) > field_limit or "\x00" in member:
                    raise ValueError("adapter input text is invalid")
                if any(ord(char) < 32 and char not in "\n\t" for char in member):
                    raise ValueError("adapter input text contains control characters")
                total_text += len(member)
                if total_text > total_limit:
                    raise ValueError("adapter input text exceeds the total bound")


class _CoordinatorMarker:
    """Private identity marker compared by identity only.

    A ticket is bound to the coordinator that produced it by carrying the same
    marker object. The marker never appears in any repr and carries no data.
    """

    __slots__ = ()


@dataclass(frozen=True, repr=False)
class ExecutionTicket:
    """Opaque, single-use authorization ticket for an action execution.

    A ticket is produced only after ``ProductivityRuntime.authorize_execution``
    returns the exact canonical ``executing`` update for a specific proposal.
    It contains no actor/session identifiers, approval IDs, proposal content,
    targets, payload, or provider data in its repr.

    Replay protection is per-ticket: the ``consume`` method atomically marks the
    ticket used exactly once before any adapter is invoked.
    """

    _marker: _CoordinatorMarker
    action: ProductivityAction
    proposal_id: str
    adapter_input: AdapterInput
    _lock: threading.Lock
    _consumed: bool

    def __init__(
        self,
        marker: _CoordinatorMarker,
        action: ProductivityAction,
        proposal_id: str,
        adapter_input: AdapterInput,
    ) -> None:
        object.__setattr__(self, "_marker", marker)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "proposal_id", proposal_id)
        object.__setattr__(self, "adapter_input", adapter_input)
        object.__setattr__(self, "_lock", threading.Lock())
        object.__setattr__(self, "_consumed", False)

    def consume(self) -> bool:
        """Atomically mark this ticket consumed.

        Returns ``True`` exactly once (the first call); every subsequent call
        returns ``False``. The check-and-mark is guarded by a per-ticket lock so
        concurrent callers cannot both observe an unconsumed ticket.
        """
        with self._lock:
            if self._consumed:
                return False
            object.__setattr__(self, "_consumed", True)
            return True

    def __repr__(self) -> str:
        return f"ExecutionTicket(action={self.action.value!r})"


class ActionAdapter(Protocol):
    """Zero-dependency injected adapter protocol.

    Implementations must be synchronous, side-effect-free of retries, and must
    not perform network, subprocess, filesystem-content, or provider access
    beyond the single bounded action they represent.
    """

    def __call__(self, input: AdapterInput) -> AdapterResult: ...


@dataclass(frozen=True)
class ExecutionRequest:
    """Immutable, bounded execution request.

    Contains only the fields needed to authorize and dispatch one action. The
    adapter input is action-specific and must not embed actor/session fields,
    approval IDs, secrets, or provider exceptions.
    """

    actor: ActorContext
    approval_id: str
    action: ProductivityAction
    proposal_id: str
    adapter_input: AdapterInput

    def __post_init__(self) -> None:
        if not isinstance(self.actor, ActorContext):
            raise ValueError("actor must be an ActorContext")
        if not isinstance(self.approval_id, str) or not _IDENTIFIER_RE.fullmatch(
            self.approval_id
        ):
            raise ValueError("invalid approval_id")
        if not isinstance(self.action, ProductivityAction):
            raise ValueError("invalid action")
        if not isinstance(self.proposal_id, str) or not _IDENTIFIER_RE.fullmatch(
            self.proposal_id
        ):
            raise ValueError("invalid proposal_id")
        if not isinstance(self.adapter_input, AdapterInput):
            raise ValueError("adapter_input must be an AdapterInput")
        self.adapter_input.validate()
        if self.adapter_input.action is not self.action:
            raise ValueError("adapter input action does not match request action")

    def __repr__(self) -> str:
        return (
            f"ExecutionRequest(action={self.action.value!r}, "
            f"proposal_id={self.proposal_id!r}, input=...)"
        )


_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")


class ProductivityExecutionCoordinator:
    """Coordinate execution between runtime authorization and action adapters.

    The coordinator is pure: it does not import or use browser automation, mac
    integration, MCP SDKs, skills, subprocess, network, or provider modules.
    """

    def __init__(
        self,
        runtime: ProductivityRuntime,
        adapters: Mapping[ProductivityAction, ActionAdapter],
    ) -> None:
        if not isinstance(runtime, ProductivityRuntime):
            raise TypeError("runtime must be a ProductivityRuntime")
        if not isinstance(adapters, Mapping):
            raise TypeError("adapters must be a mapping")

        self._runtime = runtime
        self._adapters = self._validate_adapters(adapters)
        self._marker = _CoordinatorMarker()

    @staticmethod
    def _validate_adapters(
        adapters: Mapping[ProductivityAction, ActionAdapter],
    ) -> dict[ProductivityAction, ActionAdapter]:
        """Validate the adapter mapping and reject malformed or duplicate wiring.

        Duplicate adapter instances mapped to multiple actions are rejected
        because they indicate a wiring mistake that could lead to wrong-action
        execution. Undeclared keys (non-ProductivityAction) are rejected.
        """
        result: dict[ProductivityAction, ActionAdapter] = {}
        seen_ids: dict[int, ProductivityAction] = {}

        for action, adapter in adapters.items():
            if not isinstance(action, ProductivityAction):
                raise ValueError("adapter mapping keys must be ProductivityAction values")
            if action in result:
                raise ValueError(f"duplicate adapter entry for action {action.value}")
            if not callable(adapter):
                raise ValueError(f"adapter for {action.value} is not callable")

            adapter_id = id(adapter)
            if adapter_id in seen_ids:
                raise ValueError(
                    f"same adapter instance mapped to both "
                    f"{seen_ids[adapter_id].value} and {action.value}"
                )

            result[action] = adapter
            seen_ids[adapter_id] = action

        return result

    def authorize(self, request: ExecutionRequest) -> ExecutionTicket | dict[str, Any]:
        """Authorize a request and return a single-use ticket, or a safe error.

        On success returns an ``ExecutionTicket``. On any authorization failure
        returns a canonical safe ``productivity_error`` dictionary (never a
        private exception carrying a dictionary). No adapter is invoked.
        """
        if not isinstance(request, ExecutionRequest):
            return self._generic_error("invalid-proposal")

        try:
            adapter = self._adapters[request.action]
        except KeyError:
            return error_message(request.proposal_id, ProductivityCode.STATE_MISMATCH)

        # Authorize exactly once. The runtime enforces replay, expiry,
        # revocation, cross-session, wrong-action, and wrong-proposal checks.
        auth_response = self._runtime.authorize_execution(
            request.actor,
            request.approval_id,
            request.action,
            request.proposal_id,
        )

        if not isinstance(auth_response, dict) or (
            validate_server_message(auth_response) is not None
        ):
            return self._generic_error(request.proposal_id)

        # Only proceed if the runtime consumed the approval and signaled execution.
        if (
            auth_response.get("type") != "productivity_update"
            or auth_response.get("status") != "executing"
            or auth_response.get("proposal_id") != request.proposal_id
        ):
            # The runtime already returned a canonical safe message.
            return auth_response

        return ExecutionTicket(
            self._marker,
            request.action,
            request.proposal_id,
            request.adapter_input,
        )

    def execute_authorized(self, ticket: object) -> dict[str, Any]:
        """Consume a ticket and invoke exactly one bound adapter.

        Returns only canonical ``productivity_update``, research/calendar result,
        or ``productivity_error`` messages that pass ``validate_server_message``.
        Adapter exceptions are converted to generic errors without exception text
        or input reflection. A ticket can only be used once; replay fails closed
        without invoking the adapter again.
        """
        if not isinstance(ticket, ExecutionTicket):
            return self._generic_error("invalid-proposal")
        if ticket._marker is not self._marker:
            return error_message(
                ticket.proposal_id, ProductivityCode.STATE_MISMATCH
            )

        # Atomic check-and-mark before any adapter invocation.
        if not ticket.consume():
            return error_message(
                ticket.proposal_id, ProductivityCode.STATE_MISMATCH
            )

        adapter = self._adapters.get(ticket.action)
        if adapter is None:
            return error_message(
                ticket.proposal_id, ProductivityCode.STATE_MISMATCH
            )

        try:
            result = adapter(ticket.adapter_input)
        except Exception:
            return self._generic_error(ticket.proposal_id)

        if isinstance(result, CalendarReadResult):
            try:
                return calendar_result_message(ticket.proposal_id, result)
            except TransportError:
                return self._generic_error(ticket.proposal_id)

        if isinstance(result, AdapterResult):
            result_fields = getattr(type(result), "__dataclass_fields__", None)
            if isinstance(result_fields, dict) and "result" in result_fields:
                if result.status is AdapterResultStatus.FAILED:
                    return update_message(ticket.proposal_id, "failed")
                payload = getattr(result, "result", None)
                if (
                    result.status is AdapterResultStatus.SUCCESS
                    and isinstance(payload, BrowserSearchResult)
                ):
                    try:
                        return research_result_message(ticket.proposal_id, payload)
                    except TransportError:
                        return self._generic_error(ticket.proposal_id)
                return self._generic_error(ticket.proposal_id)
            if result.status is AdapterResultStatus.SUCCESS:
                return update_message(ticket.proposal_id, "completed")
            if result.status is AdapterResultStatus.FAILED:
                return update_message(ticket.proposal_id, "failed")

        return self._generic_error(ticket.proposal_id)

    def execute(self, request: ExecutionRequest) -> dict[str, Any]:
        """Compatibility wrapper that authorizes and then executes in two stages.

        Returns only canonical ``productivity_update`` or ``productivity_error``
        messages that pass ``validate_server_message``.
        """
        authorized = self.authorize(request)
        if not isinstance(authorized, ExecutionTicket):
            return authorized
        return self.execute_authorized(authorized)

    @staticmethod
    def _generic_error(proposal_id: str) -> dict[str, Any]:
        try:
            return error_message(proposal_id, ProductivityCode.CONSUMPTION_FAILED)
        except TransportError:
            return {
                "type": "productivity_error",
                "proposal_id": "invalid-proposal",
                "code": "unavailable",
            }
