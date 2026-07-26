"""Shared contracts for the HIKARI Phase 6 isolated optional adapter layer.

This module contains only pure value objects, exceptions, and fixed reason codes.
It performs no I/O, network, subprocess, model, or database access.
"""

from __future__ import annotations

from enum import StrEnum


class AdapterState(StrEnum):
    """Lifecycle state for optional adapters."""

    DISABLED = "disabled"
    ENABLED = "enabled"


class AdapterOutcome(StrEnum):
    """Common adapter decision outcomes."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"
    UNAVAILABLE = "unavailable"


class AdapterReason(StrEnum):
    """Common adapter reason codes.  Never embed raw identifiers or secrets."""

    OK = "ok"
    DISABLED = "disabled"
    INVALID_CONFIGURATION = "invalid_configuration"
    MISSING_DEPENDENCY = "missing_dependency"
    INVALID_INPUT = "invalid_input"
    UNAUTHORIZED = "unauthorized"
    POLICY_DENIED = "policy_denied"


class AdapterException(Exception):
    """Fixed, content-free exception for adapter failures."""

    def __init__(
        self,
        reason: AdapterReason = AdapterReason.INVALID_INPUT,
        detail: Optional[StrEnum] = None,
    ) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__("adapter_exception")

    def __repr__(self) -> str:
        return "AdapterException()"
