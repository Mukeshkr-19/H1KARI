"""Lazy, fail-closed Phase 5 subsystem composition.

Importing this module must not open databases, load models, or touch private data.
Call ``create_phase5_subsystem`` explicitly from the server boundary.
"""

from __future__ import annotations

import importlib
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from core.action_audit import ActionAuditStore
from core.grants import GrantStore
from core.phase5.policy import Phase5ConsentStore, Phase5GrantStore, Phase5PolicyService
from core.phase5.runtime_guard import Phase5RuntimeGuard
from core.runtime_paths import hikari_home


class Phase5BootstrapError(RuntimeError):
    """Fixed public bootstrap failure without paths or exception details."""

    def __init__(self) -> None:
        super().__init__("phase 5 bootstrap failed")

    def __repr__(self) -> str:
        return "Phase5BootstrapError()"


@dataclass(frozen=True)
class Phase5Subsystem:
    """Composed Phase 5 services. Missing optional services stay None (deny)."""

    policy_service: Phase5PolicyService
    runtime_guard: Phase5RuntimeGuard
    grant_store: Phase5GrantStore
    consent_store: Phase5ConsentStore
    audit_store: ActionAuditStore
    session_store: Any | None
    runtime_service: Any | None
    capability_service: Any | None


def _resolve_path(explicit: Path | str | None, *parts: str) -> Path:
    if explicit is None:
        return hikari_home().joinpath(*parts)
    return Path(explicit).expanduser().resolve()


def _load_optional(module_name: str, attr: str) -> Any | None:
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    return getattr(module, attr, None)


def create_phase5_subsystem(
    *,
    clock: Callable[[], float] | None = None,
    id_factory: Callable[[], str] | None = None,
    core_grants_db_path: Path | str | None = None,
    audit_db_path: Path | str | None = None,
    phase5_grants_db_path: Path | str | None = None,
    phase5_consents_db_path: Path | str | None = None,
    session_db_path: Path | str | None = None,
    policy_service: Phase5PolicyService | None = None,
    runtime_guard: Phase5RuntimeGuard | None = None,
    session_store: Any | None = None,
    runtime_service: Any | None = None,
    capability_service: Any | None = None,
) -> Phase5Subsystem:
    """Construct Phase 5 state only when explicitly called.

    Never silently substitutes permissive stubs. If SessionStore / RuntimeService /
    CapabilityService modules are absent, those fields remain None and callers
    must return a fixed unavailable/deny response.
    """
    try:
        clock_fn = clock or time.time
        ids = id_factory or (lambda: uuid.uuid4().hex)

        if policy_service is None:
            grants = GrantStore(_resolve_path(core_grants_db_path, "phase5", "core_grants.db"))
            audit = ActionAuditStore(_resolve_path(audit_db_path, "phase5", "audit.db"))
            p5_grants = Phase5GrantStore(
                _resolve_path(phase5_grants_db_path, "phase5", "grants.db")
            )
            p5_consents = Phase5ConsentStore(
                _resolve_path(phase5_consents_db_path, "phase5", "consents.db")
            )
            policy_service = Phase5PolicyService(
                grants=grants,
                audit=audit,
                phase5_grants=p5_grants,
                phase5_consents=p5_consents,
                clock=clock_fn,
                id_factory=ids,
            )
        else:
            p5_grants = policy_service.phase5_grants
            p5_consents = policy_service.phase5_consents
            audit = policy_service.audit

        if runtime_guard is None:
            runtime_guard = Phase5RuntimeGuard(policy_service=policy_service)

        if session_store is None:
            store_cls = _load_optional("core.phase5.session_store", "Phase5SessionStore")
            if store_cls is not None:
                session_store = store_cls(_resolve_path(session_db_path, "phase5", "sessions.db"))

        if runtime_service is None:
            runtime_cls = _load_optional("core.phase5.runtime_service", "Phase5RuntimeService")
            if runtime_cls is not None and session_store is not None:
                runtime_service = runtime_cls(
                    policy_service=policy_service,
                    runtime_guard=runtime_guard,
                    session_store=session_store,
                    clock=clock_fn,
                    id_factory=ids,
                )

        if capability_service is None:
            cap_cls = _load_optional("core.phase5.capability_service", "Phase5CapabilityService")
            if cap_cls is not None:
                capability_service = cap_cls()

        return Phase5Subsystem(
            policy_service=policy_service,
            runtime_guard=runtime_guard,
            grant_store=p5_grants,
            consent_store=p5_consents,
            audit_store=audit,
            session_store=session_store,
            runtime_service=runtime_service,
            capability_service=capability_service,
        )
    except Phase5BootstrapError:
        raise
    except Exception as exc:  # noqa: BLE001 - map to fixed public error
        raise Phase5BootstrapError() from None


__all__ = [
    "Phase5BootstrapError",
    "Phase5Subsystem",
    "create_phase5_subsystem",
]
