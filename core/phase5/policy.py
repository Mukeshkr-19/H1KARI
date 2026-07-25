"""Phase 5 policy service: stateful authorization with grants, consents, and audit.

This module provides the stateful policy evaluation layer that integrates
with core grants, audit, and action policy. It performs no external actions.
"""

from __future__ import annotations

import math
import re
import sqlite3
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional, Tuple

from core.action_audit import ActionAuditStore, opaque_resource_reference
from core.action_policy import Actor, ActorContext, PolicyOutcome, validate_actor_context
from core.grants import GrantStore
from core.phase5.contracts import (
    ACTOR_PROHIBITED,
    CAPABILITY_REQUIRES_APPROVAL,
    CAPABILITY_RISK,
    Capability,
    CapabilityGrant,
    ConsentRecord,
    DecisionReason,
    Outcome,
    Phase5Actor,
    Phase5ActorContext,
    Phase5Decision,
    Phase5Request,
    RiskLevel,
    ScopeConstraint,
    check_approval_bypass,
    check_audit_bypass,
    check_brain_write_denied,
    evaluate_phase5_request,
)

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_ACTOR_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_SAFE_TIMESTAMP = 2**53


# Map Phase 5 Outcome to core PolicyOutcome for audit recording
_OUTCOME_TO_POLICY_OUTCOME = {
    Outcome.ALLOW: PolicyOutcome.ALLOW,
    Outcome.REQUIRE_APPROVAL: PolicyOutcome.REQUIRE_CONFIRMATION,
    Outcome.DENY: PolicyOutcome.DENY,
    Outcome.EXPIRED: PolicyOutcome.DENY,
    Outcome.REVOKED: PolicyOutcome.DENY,
    Outcome.OUT_OF_SCOPE: PolicyOutcome.DENY,
    Outcome.AUTHENTICATION_REQUIRED: PolicyOutcome.DENY,
}


class _ClosingConnection(sqlite3.Connection):
    """Preserve sqlite transaction contexts while closing deterministically."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _validate_identifier(value: Optional[str], field: str) -> None:
    if value is None:
        raise ValueError(f"{field} is required")
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"invalid {field}")


def _validate_actor_identifier(value: Optional[str], field: str) -> None:
    if value is None:
        raise ValueError(f"{field} is required")
    if not isinstance(value, str) or not _ACTOR_IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"invalid {field}")


def _validate_finite_timestamp(value: float, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    if isinstance(value, int) and abs(value) > _MAX_SAFE_TIMESTAMP:
        raise ValueError(f"{field} is too large")
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise ValueError(f"{field} must be finite")


def _validate_hex_digest(value: str, field: str) -> None:
    if not isinstance(value, str) or not _HEX_DIGEST_RE.fullmatch(value):
        raise ValueError(f"{field} must be 64 lowercase hex characters")


@dataclass(frozen=True)
class Phase5AuthorizationRequest:
    """Internal authorization request for policy service."""

    request: Phase5Request
    actor: ActorContext
    user_initiated: bool = False
    grant_id: Optional[str] = None


@dataclass(frozen=True)
class Phase5AuthorizationDecision:
    """Authorization decision with audit trail."""

    decision: Phase5Decision
    audit_id: str
    approval_required: bool = False


class Phase5GrantStore:
    """SQLite-backed store for capability grants."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.chmod(0o700)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS capability_grants (
                    grant_id TEXT PRIMARY KEY,
                    helper_actor_id TEXT NOT NULL,
                    owner_actor_id TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    scope_data_subject TEXT,
                    scope_resource_pattern TEXT,
                    scope_max_duration_seconds INTEGER,
                    scope_allowed_actions TEXT,
                    issued_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    revoked_at REAL
                )
                """
            )
        self.db_path.chmod(0o600)

    def issue(self, grant: CapabilityGrant) -> CapabilityGrant:
        _validate_identifier(grant.grant_id, "grant_id")
        _validate_actor_identifier(grant.helper_actor_id, "helper_actor_id")
        _validate_actor_identifier(grant.owner_actor_id, "owner_actor_id")
        _validate_finite_timestamp(grant.issued_at, "issued_at")
        _validate_finite_timestamp(grant.expires_at, "expires_at")
        if grant.expires_at <= grant.issued_at:
            raise ValueError("expires_at must be greater than issued_at")
        if grant.revoked and grant.revoked_at is not None:
            _validate_finite_timestamp(grant.revoked_at, "revoked_at")

        scope = grant.scope
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO capability_grants (
                    grant_id, helper_actor_id, owner_actor_id, capability,
                    scope_data_subject, scope_resource_pattern,
                    scope_max_duration_seconds, scope_allowed_actions,
                    issued_at, expires_at, revoked, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grant.grant_id,
                    grant.helper_actor_id,
                    grant.owner_actor_id,
                    grant.capability.value,
                    scope.data_subject,
                    scope.resource_pattern,
                    scope.max_duration_seconds,
                    ",".join(scope.allowed_actions) if scope.allowed_actions else "",
                    grant.issued_at,
                    grant.expires_at,
                    1 if grant.revoked else 0,
                    grant.revoked_at,
                ),
            )
        return grant

    def revoke(self, grant_id: str, now: float) -> bool:
        _validate_identifier(grant_id, "grant_id")
        _validate_finite_timestamp(now, "now")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE capability_grants SET revoked = 1, revoked_at = ? WHERE grant_id = ?",
                (now, grant_id),
            )
        return cursor.rowcount == 1

    def get(self, grant_id: str) -> Optional[CapabilityGrant]:
        _validate_identifier(grant_id, "grant_id")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM capability_grants WHERE grant_id = ?", (grant_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_grant(row)

    def list_for_helper(self, helper_actor_id: str, now: float, include_expired_revoked: bool = False) -> Tuple[CapabilityGrant, ...]:
        _validate_actor_identifier(helper_actor_id, "helper_actor_id")
        _validate_finite_timestamp(now, "now")
        with self._connect() as conn:
            if include_expired_revoked:
                rows = conn.execute(
                    "SELECT * FROM capability_grants WHERE helper_actor_id = ?",
                    (helper_actor_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM capability_grants WHERE helper_actor_id = ? AND revoked = 0 AND expires_at > ?",
                    (helper_actor_id, now),
                ).fetchall()
        return tuple(self._row_to_grant(row) for row in rows)

    def list_for_owner(self, owner_actor_id: str) -> Tuple[CapabilityGrant, ...]:
        _validate_actor_identifier(owner_actor_id, "owner_actor_id")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM capability_grants WHERE owner_actor_id = ?",
                (owner_actor_id,),
            ).fetchall()
        return tuple(self._row_to_grant(row) for row in rows)

    def _row_to_grant(self, row: sqlite3.Row) -> CapabilityGrant:
        allowed_actions = tuple(row["scope_allowed_actions"].split(",")) if row["scope_allowed_actions"] else ()
        scope = ScopeConstraint(
            capability=Capability(row["capability"]),
            data_subject=row["scope_data_subject"],
            resource_pattern=row["scope_resource_pattern"],
            max_duration_seconds=row["scope_max_duration_seconds"],
            allowed_actions=allowed_actions,
        )
        return CapabilityGrant(
            grant_id=row["grant_id"],
            helper_actor_id=row["helper_actor_id"],
            owner_actor_id=row["owner_actor_id"],
            capability=Capability(row["capability"]),
            scope=scope,
            issued_at=row["issued_at"],
            expires_at=row["expires_at"],
            revoked=bool(row["revoked"]),
            revoked_at=row["revoked_at"],
        )


class Phase5ConsentStore:
    """SQLite-backed store for owner consent records."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.chmod(0o700)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS owner_consents (
                    consent_id TEXT PRIMARY KEY,
                    owner_actor_id TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    scope_data_subject TEXT,
                    scope_resource_pattern TEXT,
                    scope_max_duration_seconds INTEGER,
                    scope_allowed_actions TEXT,
                    granted_at REAL NOT NULL,
                    expires_at REAL,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    revoked_at REAL
                )
                """
            )
        self.db_path.chmod(0o600)

    def issue(self, consent: ConsentRecord) -> ConsentRecord:
        _validate_identifier(consent.consent_id, "consent_id")
        _validate_actor_identifier(consent.owner_actor_id, "owner_actor_id")
        _validate_finite_timestamp(consent.granted_at, "granted_at")
        if consent.expires_at is not None:
            _validate_finite_timestamp(consent.expires_at, "expires_at")
            if consent.expires_at <= consent.granted_at:
                raise ValueError("expires_at must be greater than granted_at")
        if consent.revoked and consent.revoked_at is not None:
            _validate_finite_timestamp(consent.revoked_at, "revoked_at")

        scope = consent.scope
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO owner_consents (
                    consent_id, owner_actor_id, capability,
                    scope_data_subject, scope_resource_pattern,
                    scope_max_duration_seconds, scope_allowed_actions,
                    granted_at, expires_at, revoked, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    consent.consent_id,
                    consent.owner_actor_id,
                    consent.capability.value,
                    scope.data_subject,
                    scope.resource_pattern,
                    scope.max_duration_seconds,
                    ",".join(scope.allowed_actions) if scope.allowed_actions else "",
                    consent.granted_at,
                    consent.expires_at,
                    1 if consent.revoked else 0,
                    consent.revoked_at,
                ),
            )
        return consent

    def revoke(self, consent_id: str, now: float) -> bool:
        _validate_identifier(consent_id, "consent_id")
        _validate_finite_timestamp(now, "now")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE owner_consents SET revoked = 1, revoked_at = ? WHERE consent_id = ?",
                (now, consent_id),
            )
        return cursor.rowcount == 1

    def get(self, consent_id: str) -> Optional[ConsentRecord]:
        _validate_identifier(consent_id, "consent_id")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM owner_consents WHERE consent_id = ?", (consent_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_consent(row)

    def list_for_owner(self, owner_actor_id: str, now: float) -> Tuple[ConsentRecord, ...]:
        _validate_actor_identifier(owner_actor_id, "owner_actor_id")
        _validate_finite_timestamp(now, "now")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM owner_consents WHERE owner_actor_id = ? AND revoked = 0 AND (expires_at IS NULL OR expires_at > ?)",
                (owner_actor_id, now),
            ).fetchall()
        return tuple(self._row_to_consent(row) for row in rows)

    def _row_to_consent(self, row: sqlite3.Row) -> ConsentRecord:
        allowed_actions = tuple(row["scope_allowed_actions"].split(",")) if row["scope_allowed_actions"] else ()
        scope = ScopeConstraint(
            capability=Capability(row["capability"]),
            data_subject=row["scope_data_subject"],
            resource_pattern=row["scope_resource_pattern"],
            max_duration_seconds=row["scope_max_duration_seconds"],
            allowed_actions=allowed_actions,
        )
        return ConsentRecord(
            consent_id=row["consent_id"],
            owner_actor_id=row["owner_actor_id"],
            capability=Capability(row["capability"]),
            scope=scope,
            granted_at=row["granted_at"],
            expires_at=row["expires_at"],
            revoked=bool(row["revoked"]),
            revoked_at=row["revoked_at"],
        )


class Phase5PolicyService:
    """Stateful Phase 5 authorization service.

    Integrates with core grants, audit, and action policy.
    """

    def __init__(
        self,
        grants: GrantStore,
        audit: ActionAuditStore,
        phase5_grants: Phase5GrantStore,
        phase5_consents: Phase5ConsentStore,
        clock: Callable[[], float],
    ):
        self.grants = grants
        self.audit = audit
        self.phase5_grants = phase5_grants
        self.phase5_consents = phase5_consents
        if not callable(clock):
            raise ValueError("clock must be callable")
        self.clock = clock

    def _now(self) -> float:
        now = self.clock()
        _validate_finite_timestamp(now, "clock result")
        return float(now)

    def authorize(self, request: Phase5AuthorizationRequest) -> Phase5AuthorizationDecision:
        """Authorize a Phase 5 capability request.

        Returns a decision with audit trail. Performs no external actions.
        """
        if not isinstance(request, Phase5AuthorizationRequest):
            raise ValueError("request must be Phase5AuthorizationRequest")
        valid_actor, _reason = validate_actor_context(request.actor)
        if not valid_actor:
            raise ValueError("invalid actor context")

        core_actor = request.request.actor.to_action_policy_context()
        actor_matches = (
            core_actor.actor_id == request.actor.actor_id
            and core_actor.actor is request.actor.actor
            and core_actor.session_id == request.actor.session_id
        )
        if not actor_matches:
            now = self._now()
            decision = Phase5Decision(
                request_id=request.request.request_id,
                capability=request.request.capability,
                actor_id=request.actor.actor_id,
                actor=Phase5Actor.GUEST,
                outcome=Outcome.DENY,
                reason=DecisionReason.INVALID_INPUT,
                granted_at=now,
            )
            audit_id = self._record_audit(request, decision, "actor_context_mismatch")
            decision = replace(decision, audit_id=audit_id)
            return Phase5AuthorizationDecision(decision, audit_id)

        now = self._now()
        # Fetch all grants (including expired/revoked) so evaluator can return correct outcome
        grants = self.phase5_grants.list_for_helper(core_actor.actor_id, now, include_expired_revoked=True) if core_actor.actor == Actor.GUEST else ()
        consents = self.phase5_consents.list_for_owner(core_actor.actor_id, now) if core_actor.actor == Actor.OWNER else ()

        decision = evaluate_phase5_request(
            request.request,
            grants=grants,
            consents=consents,
            now=now,
        )

        if not check_approval_bypass(request.request, decision):
            decision = Phase5Decision(
                request_id=decision.request_id,
                capability=decision.capability,
                actor_id=decision.actor_id,
                actor=decision.actor,
                outcome=Outcome.DENY,
                reason=DecisionReason.APPROVAL_BYPASS_BLOCKED,
                granted_at=decision.granted_at,
            )

        if not check_brain_write_denied(request.request, decision):
            decision = Phase5Decision(
                request_id=decision.request_id,
                capability=decision.capability,
                actor_id=decision.actor_id,
                actor=decision.actor,
                outcome=Outcome.DENY,
                reason=DecisionReason.BRAIN_WRITE_DENIED,
                granted_at=decision.granted_at,
            )

        audit_id = self._record_audit(request, decision, decision.reason.value)
        decision = replace(decision, audit_id=audit_id)
        if not check_audit_bypass(decision):
            raise RuntimeError("phase5 decision was not audited")

        approval_required = (
            request.request.capability in CAPABILITY_REQUIRES_APPROVAL
            and decision.outcome == Outcome.REQUIRE_APPROVAL
        )

        return Phase5AuthorizationDecision(decision, audit_id, approval_required)

    def _record_audit(
        self,
        request: Phase5AuthorizationRequest,
        decision: Phase5Decision,
        reason: str,
    ) -> str:
        return self.audit.record_decision(
            actor=request.actor,
            task_id=request.request.request_id,
            action=f"phase5.{request.request.capability.value}",
            resource_ref=opaque_resource_reference(request.request.resource),
            destination=None,
            outcome=_OUTCOME_TO_POLICY_OUTCOME.get(decision.outcome, PolicyOutcome.DENY),
            reason=reason,
        )

    def issue_helper_grant(
        self,
        *,
        owner_actor: ActorContext,
        helper_actor_id: str,
        capability: Capability,
        scope: ScopeConstraint,
        expires_at: float,
        grant_id: Optional[str] = None,
    ) -> CapabilityGrant:
        """Issue a capability grant for a trusted helper.

        Only owner actors may issue grants. Expiration is required.
        """
        valid_actor, _reason = validate_actor_context(owner_actor)
        if not valid_actor or owner_actor.actor != Actor.OWNER:
            raise ValueError("only owner actors may issue helper grants")

        _validate_actor_identifier(helper_actor_id, "helper_actor_id")
        _validate_finite_timestamp(expires_at, "expires_at")
        now = self._now()
        if expires_at <= now:
            raise ValueError("expires_at must be in the future")

        grant = CapabilityGrant(
            grant_id=grant_id or str(uuid.uuid4()),
            helper_actor_id=helper_actor_id,
            owner_actor_id=owner_actor.actor_id,
            capability=capability,
            scope=scope,
            issued_at=now,
            expires_at=expires_at,
        )
        return self.phase5_grants.issue(grant)

    def revoke_helper_grant(self, grant_id: str) -> bool:
        """Revoke a helper grant."""
        return self.phase5_grants.revoke(grant_id, self._now())

    def issue_owner_consent(
        self,
        *,
        owner_actor: ActorContext,
        capability: Capability,
        scope: ScopeConstraint,
        expires_at: Optional[float] = None,
        consent_id: Optional[str] = None,
    ) -> ConsentRecord:
        """Record explicit owner consent for a capability."""
        valid_actor, _reason = validate_actor_context(owner_actor)
        if not valid_actor or owner_actor.actor != Actor.OWNER:
            raise ValueError("only owner actors may grant consent")

        if expires_at is not None:
            _validate_finite_timestamp(expires_at, "expires_at")
            now = self._now()
            if expires_at <= now:
                raise ValueError("expires_at must be in the future")
        else:
            now = self._now()

        consent = ConsentRecord(
            consent_id=consent_id or str(uuid.uuid4()),
            owner_actor_id=owner_actor.actor_id,
            capability=capability,
            scope=scope,
            granted_at=now,
            expires_at=expires_at,
        )
        return self.phase5_consents.issue(consent)

    def revoke_owner_consent(self, consent_id: str) -> bool:
        """Revoke an owner consent."""
        return self.phase5_consents.revoke(consent_id, self._now())


__all__ = [
    "Phase5AuthorizationRequest",
    "Phase5AuthorizationDecision",
    "Phase5GrantStore",
    "Phase5ConsentStore",
    "Phase5PolicyService",
]
