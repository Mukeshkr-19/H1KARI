"""Production-facing Phase 5 runtime coordinator.

Composes Phase5PolicyService, Phase5RuntimeGuard, Phase5SessionStore, and pure session
lifecycle evaluation into a single application-facing coordinator.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import replace
from typing import Callable, List, Optional, Tuple

from core.action_policy import Actor, ActorContext, validate_actor_context
from core.phase5.contracts import Outcome
from core.phase5.policy import Phase5PolicyService
from core.phase5.runtime_guard import (
    Phase5RuntimeDecision,
    Phase5RuntimeGuard,
    Phase5RuntimeRequest,
    RuntimeDecisionReason,
)
from core.phase5.session_lifecycle import (
    AccessSession,
    AuthoritySource,
    SessionActivationRequest,
    SessionAuthoritySnapshot,
    SessionDecision,
    SessionDecisionReason,
    SessionPolicy,
    SessionState,
    default_session_policy,
    evaluate_activation_request,
    transition_session,
)
from core.phase5.session_store import Phase5SessionStore


class Phase5RuntimeService:
    """Production runtime coordinator for Phase 5 session management and authorization."""

    def __init__(
        self,
        policy_service: Phase5PolicyService,
        runtime_guard: Phase5RuntimeGuard,
        session_store: Phase5SessionStore,
        session_policy: Optional[SessionPolicy] = None,
        clock: Optional[Callable[[], float]] = None,
        id_factory: Optional[Callable[[], str]] = None,
    ):
        if policy_service is None:
            raise ValueError("policy_service is required")
        if runtime_guard is None:
            raise ValueError("runtime_guard is required")
        if session_store is None:
            raise ValueError("session_store is required")

        self.policy_service = policy_service
        self.runtime_guard = runtime_guard
        self.session_store = session_store
        self.session_policy = session_policy if session_policy is not None else default_session_policy()
        self._clock = clock if clock is not None else (lambda: 0.0)
        self._id_factory = id_factory if id_factory is not None else (lambda: uuid.uuid4().hex)

    def now(self) -> float:
        val = self._clock()
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
            return 0.0
        return float(val)

    def activate_session(
        self,
        actor_context: ActorContext,
        activation_request: SessionActivationRequest,
    ) -> SessionDecision:
        """Activate a new access session following caller-supplied authentication.

        Does not perform authentication or issue implicit grants. Persists successful
        sessions atomically in the session store.
        """
        validate_actor_context(actor_context)
        if not isinstance(activation_request, SessionActivationRequest):
            raise ValueError("activation_request must be SessionActivationRequest")

        current_time = self.now()
        # Session creation is an owner-authority operation for every session
        # type. Child/helper actor identifiers are request scope, never caller
        # identity or proof of authority.
        if (
            actor_context.actor is not Actor.OWNER
            or actor_context.actor_id != activation_request.owner_actor_id
            or actor_context.session_id is None
        ):
            return SessionDecision(
                request_id=activation_request.request_id,
                session_id=activation_request.request_id,
                outcome=Outcome.DENY,
                reason=SessionDecisionReason.AUTHORITY_MISMATCH,
                session=None,
                transition=None,
                timestamp=current_time,
            )
        decision = evaluate_activation_request(
            activation_request,
            self.session_policy,
            current_time,
        )

        if decision.outcome is Outcome.ALLOW and decision.session is not None:
            self.session_store.save_session(decision.session)

        return decision

    def authorize(self, request: Phase5RuntimeRequest) -> Phase5RuntimeDecision:
        """Authorize a runtime request through the Phase 5 runtime guard.

        Calls Phase5RuntimeGuard.authorize exactly once using injected timestamp
        and loaded active session if none was explicitly attached to the request.
        """
        current_time = request.now if (request.now > 0 and math.isfinite(request.now)) else self.now()
        effective_request = request

        # Ensure single timestamp consistency
        if effective_request.now != current_time:
            effective_request = replace(effective_request, now=current_time)

        # Load active session from store if missing on request
        if effective_request.access_session is None:
            actor_id = effective_request.context.actor_context.actor_id
            active_session = self.session_store.get_active_session_for_actor(actor_id, current_time)
            if active_session is not None:
                effective_request = replace(effective_request, access_session=active_session)

        # Execute single authorize call
        return self.runtime_guard.authorize(effective_request)

    def transition_session(
        self,
        session_id: str,
        to_state: SessionState,
        actor_id: str,
        authority: SessionAuthoritySnapshot,
    ) -> SessionDecision:
        """Transition an existing session's lifecycle state atomically."""
        current_time = self.now()
        session = self.session_store.get_session(session_id)
        if session is None:
            return SessionDecision(
                request_id=f"{session_id}.transition.missing",
                session_id=session_id,
                outcome=Outcome.DENY,
                reason=SessionDecisionReason.INVALID_INPUT,
                session=None,
                transition=None,
                timestamp=current_time,
            )

        expected_rev = self.session_store.get_revision(session_id)
        decision = transition_session(
            session,
            to_state,
            actor_id,
            authority,
            self.session_policy,
            current_time,
        )

        if decision.outcome is Outcome.ALLOW and decision.session is not None:
            self.session_store.save_session(decision.session, expected_revision=expected_rev)

        return decision

    def get_session_status(self, session_id: str, actor_id: str) -> Optional[AccessSession]:
        """Return a privacy-safe view of a session if caller matches owner or actor."""
        session = self.session_store.get_session(session_id)
        if session is None:
            return None
        if actor_id not in {session.owner_actor_id, session.session_actor_id}:
            return None
        return session

    def list_owner_sessions(
        self,
        owner_actor_id: str,
        caller_actor_id: str,
        limit: int = 50,
    ) -> Tuple[AccessSession, ...]:
        """Owner-only query to list recent sessions owned by owner_actor_id."""
        if caller_actor_id != owner_actor_id:
            return ()
        return self.session_store.list_owner_sessions(owner_actor_id, limit=limit)

    def revoke_helper_access(self, grant_id: str, owner_actor_id: str) -> bool:
        """Immediately revoke a helper grant and all dependent sessions.

        Idempotent and owner-authenticated.
        """
        current_time = self.now()
        grant = self.policy_service.get_helper_grant(grant_id)
        if grant is None or grant.owner_actor_id != owner_actor_id:
            return False
        revoked_ok = self.policy_service.phase5_grants.revoke(grant_id, current_time)

        # Revoke any sessions bound to this grant
        sessions = self.session_store.list_owner_sessions(owner_actor_id, limit=100)
        authority = SessionAuthoritySnapshot(
            source=AuthoritySource.OWNER_DIRECT,
            owner_actor_id=owner_actor_id,
        )

        for s in sessions:
            if (
                s.authority_snapshot.grant is not None
                and s.authority_snapshot.grant.grant_id == grant_id
                and not s.is_terminal()
            ):
                self.transition_session(
                    s.session_id,
                    SessionState.REVOKED,
                    owner_actor_id,
                    authority,
                )

        return revoked_ok

    def expire_due_sessions(self) -> int:
        """Mark active sessions past their expiration timestamp as EXPIRED."""
        return self.session_store.expire_due_sessions(self.now())

    def close(self) -> None:
        """Close resources."""
        self.session_store.close()

    def __repr__(self) -> str:
        return "Phase5RuntimeService()"
