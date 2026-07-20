"""Fail-closed invocation boundary for declared third-party tools.

The wrapper performs no tool discovery, I/O, persistence, logging, network, or
subprocess work. Tool behavior is supplied through exact zero-argument
callables. Their return values and exceptions never cross this boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional, Tuple

from core.productivity.contracts import ProductivityAction, TargetKind
from core.productivity.tool_permissions import (
    PermissionDecision,
    PermissionManifest,
    PermissionRequest,
    ToolKind,
    evaluate,
)


class ToolWrapperCode(StrEnum):
    """Fixed outcomes that never carry tool output or exception details."""

    OK = "ok"
    DISABLED = "disabled"
    INVALID_REQUEST = "invalid_request"
    BINDING_MISMATCH = "binding_mismatch"
    PERMISSION_DENIED = "permission_denied"
    INVOCATION_FAILED = "invocation_failed"


@dataclass(frozen=True)
class ToolWrapperResult:
    """Content-free invocation result."""

    code: ToolWrapperCode

    @property
    def succeeded(self) -> bool:
        return self.code is ToolWrapperCode.OK


class ToolWrapperConfigurationError(ValueError):
    """Fixed configuration failure with no reflected identifiers."""

    def __init__(self) -> None:
        super().__init__("tool wrapper configuration invalid")


@dataclass(frozen=True, repr=False)
class ToolRegistration:
    """One exact structural binding to an injected callable."""

    tool_id: str
    kind: ToolKind
    action: ProductivityAction
    target_kind: TargetKind
    target_value: str
    invoke: Callable[[], object]

    def __post_init__(self) -> None:
        try:
            PermissionRequest(
                tool_id=self.tool_id,
                kind=self.kind,
                action=self.action,
                target_kind=self.target_kind,
                target_value=self.target_value,
            )
            if not callable(self.invoke):
                raise ValueError
        except Exception:
            raise ToolWrapperConfigurationError() from None

    def __repr__(self) -> str:
        return (
            "ToolRegistration("
            f"kind={self.kind.value!r}, action={self.action.value!r}, "
            f"target_kind={self.target_kind.value!r})"
        )


_KIND_BINDING = {
    ToolKind.MCP: (ProductivityAction.MCP_EXECUTE, TargetKind.MCP_SERVER),
    ToolKind.SKILL: (ProductivityAction.SKILL_EXECUTE, TargetKind.SKILL),
}


def _binding_key(
    tool_id: str,
    kind: ToolKind,
    action: ProductivityAction,
    target_kind: TargetKind,
    target_value: str,
) -> tuple[str, ToolKind, ProductivityAction, TargetKind, str]:
    return (tool_id, kind, action, target_kind, target_value)


class ToolPolicyWrapper:
    """Evaluate an exact permission request immediately before invocation."""

    def __init__(
        self,
        manifest: PermissionManifest | None = None,
        registrations: Tuple[ToolRegistration, ...] = (),
    ) -> None:
        if manifest is None and registrations == ():
            self._manifest = None
            self._bindings = {}
            self._disabled = True
            return
        try:
            if not isinstance(manifest, PermissionManifest):
                raise ValueError
            if not isinstance(registrations, tuple) or not registrations:
                raise ValueError

            expected: set[
                tuple[str, ToolKind, ProductivityAction, TargetKind, str]
            ] = set()
            for permission in manifest.tools:
                action, target_kind = _KIND_BINDING[permission.kind]
                for target in permission.targets:
                    expected.add(
                        _binding_key(
                            permission.tool_id,
                            permission.kind,
                            action,
                            target_kind,
                            target,
                        )
                    )

            bindings: dict[
                tuple[str, ToolKind, ProductivityAction, TargetKind, str],
                ToolRegistration,
            ] = {}
            for registration in registrations:
                if not isinstance(registration, ToolRegistration):
                    raise ValueError
                key = _binding_key(
                    registration.tool_id,
                    registration.kind,
                    registration.action,
                    registration.target_kind,
                    registration.target_value,
                )
                if key in bindings:
                    raise ValueError
                bindings[key] = registration

            if set(bindings) != expected:
                raise ValueError
        except Exception:
            raise ToolWrapperConfigurationError() from None

        self._manifest: Optional[PermissionManifest] = manifest
        self._bindings = bindings
        self._disabled = False

    @classmethod
    def disabled(cls) -> "ToolPolicyWrapper":
        """Return a wrapper that denies every request without a manifest."""
        return cls()

    def invoke(self, request: object) -> ToolWrapperResult:
        """Invoke only after an immediately preceding pure permission check."""
        if self._disabled:
            return ToolWrapperResult(ToolWrapperCode.DISABLED)
        if not isinstance(request, PermissionRequest):
            return ToolWrapperResult(ToolWrapperCode.INVALID_REQUEST)
        if request.action not in {
            ProductivityAction.MCP_EXECUTE,
            ProductivityAction.SKILL_EXECUTE,
        }:
            return ToolWrapperResult(ToolWrapperCode.BINDING_MISMATCH)

        key = _binding_key(
            request.tool_id,
            request.kind,
            request.action,
            request.target_kind,
            request.target_value,
        )
        registration = self._bindings.get(key)
        if registration is None or self._manifest is None:
            return ToolWrapperResult(ToolWrapperCode.BINDING_MISMATCH)

        decision, _ = evaluate(self._manifest, request)
        if decision is not PermissionDecision.ALLOW:
            return ToolWrapperResult(ToolWrapperCode.PERMISSION_DENIED)
        try:
            registration.invoke()
        except Exception:
            return ToolWrapperResult(ToolWrapperCode.INVOCATION_FAILED)
        return ToolWrapperResult(ToolWrapperCode.OK)
