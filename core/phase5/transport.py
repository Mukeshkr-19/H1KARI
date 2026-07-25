"""Pure Phase 5 WebSocket protocol specs and safe response builders.

No I/O, no network, no database access, and no mutation of caller inputs.
Identity and authority are never taken from client-supplied role fields.
"""

from __future__ import annotations

import copy
from typing import Any, Optional, Protocol, Sequence, Tuple

from core.phase5.capability_service import (
    CareProposal,
    CapabilityServiceDecision,
    GuideHandsProposal,
    TeachMeProposal,
)
from core.phase5.contracts import Capability, CapabilityGrant, Outcome
from core.phase5.runtime_guard import Phase5RuntimeDecision
from core.phase5.session_lifecycle import AccessSession, SessionState

_CANONICAL_ID = {
    "type": "string",
    "max_length": 80,
    "pattern": r"^[a-z0-9][a-z0-9_.-]{0,79}$",
}
_ACTOR_ID = {
    "type": "string",
    "max_length": 128,
    "pattern": r"^[a-z0-9][a-z0-9_.-]{0,127}$",
}
_BOUNDED_TEXT = {
    "type": "string",
    "max_length": 1024,
    "forbid_controls": True,
    "forbid_unicode_format": True,
}
_BOUNDED_TOPIC = {
    "type": "string",
    "min_length": 1,
    "max_length": 1024,
    "forbid_controls": True,
    "forbid_unicode_format": True,
    "not_whitespace_only": True,
}
_FINITE_TS = {"type": "number", "finite": True}
_CAPABILITY_ENUM = {
    "type": "string",
    "enum": [
        "teach_me",
        "guide_my_hands",
        "care",
        "child_mode",
        "trusted_helper_access",
    ],
}
_SESSION_TYPE_ENUM = {
    "type": "string",
    "enum": ["owner", "child", "trusted_helper"],
}
_PROTOCOL_VERSION = {"type": "integer", "enum": [1]}

PHASE5_ERROR_CODES = frozenset(
    {
        "invalid_request",
        "unauthorized",
        "unavailable",
        "denied",
        "expired",
        "revoked",
        "locked",
        "closed",
        "approval_required",
        "not_found",
        "stale_request",
        "duplicate_request",
        "internal_error",
    }
)

PHASE5_CLIENT_MESSAGE_TYPES = frozenset(
    {
        "phase5_session_activate",
        "phase5_session_status",
        "phase5_session_close",
        "phase5_session_lock",
        "phase5_session_revoke",
        "phase5_capability_prepare",
        "phase5_capability_confirm",
        "phase5_helper_grant_create",
        "phase5_helper_grant_list",
        "phase5_helper_grant_revoke",
    }
)

PHASE5_SERVER_MESSAGE_TYPES = frozenset(
    {
        "phase5_session_update",
        "phase5_capability_proposal",
        "phase5_approval_required",
        "phase5_helper_grants",
        "phase5_error",
    }
)

PHASE5_CLIENT_MESSAGE_SPECS: dict[str, dict[str, Any]] = {
    "phase5_session_activate": {
        "required": {
            "request_id": _CANONICAL_ID,
            "protocol_version": _PROTOCOL_VERSION,
            "session_type": _SESSION_TYPE_ENUM,
            "capabilities": {
                "type": "array",
                "min_items": 1,
                "max_items": 8,
                "unique": True,
                "items": _CAPABILITY_ENUM,
            },
            "expires_at": _FINITE_TS,
        },
        "optional": {
            "session_actor_id": _ACTOR_ID,
            "grant_id": _CANONICAL_ID,
            "activation_evidence": {
                "type": "string",
                "min_length": 1,
                "max_length": 256,
                "forbid_controls": True,
                "forbid_unicode_format": True,
                "not_whitespace_only": True,
            },
        },
    },
    "phase5_session_status": {
        "required": {
            "request_id": _CANONICAL_ID,
            "protocol_version": _PROTOCOL_VERSION,
            "session_id": _CANONICAL_ID,
        },
        "optional": {},
    },
    "phase5_session_close": {
        "required": {
            "request_id": _CANONICAL_ID,
            "protocol_version": _PROTOCOL_VERSION,
            "session_id": _CANONICAL_ID,
        },
        "optional": {},
    },
    "phase5_session_lock": {
        "required": {
            "request_id": _CANONICAL_ID,
            "protocol_version": _PROTOCOL_VERSION,
            "session_id": _CANONICAL_ID,
        },
        "optional": {},
    },
    "phase5_session_revoke": {
        "required": {
            "request_id": _CANONICAL_ID,
            "protocol_version": _PROTOCOL_VERSION,
            "session_id": _CANONICAL_ID,
        },
        "optional": {},
    },
    "phase5_capability_prepare": {
        "required": {
            "request_id": _CANONICAL_ID,
            "protocol_version": _PROTOCOL_VERSION,
            "capability": {
                "type": "string",
                "enum": ["teach_me", "guide_my_hands", "care"],
            },
        },
        "optional": {
            "topic": _BOUNDED_TOPIC,
            "goal": _BOUNDED_TEXT,
            "care_prompt": _BOUNDED_TEXT,
            "action": {
                "type": "string",
                "max_length": 128,
                "forbid_controls": True,
                "forbid_unicode_format": True,
            },
            "resource": {
                "type": "string",
                "max_length": 256,
                "forbid_controls": True,
                "forbid_unicode_format": True,
            },
            "data_subject": {
                "type": "string",
                "max_length": 128,
                "forbid_controls": True,
                "forbid_unicode_format": True,
            },
            "session_id": _CANONICAL_ID,
        },
    },
    "phase5_capability_confirm": {
        "required": {
            "request_id": _CANONICAL_ID,
            "protocol_version": _PROTOCOL_VERSION,
            "pending_request_id": _CANONICAL_ID,
            "acknowledged": {"type": "boolean", "equals": True},
        },
        "optional": {},
    },
    "phase5_helper_grant_create": {
        "required": {
            "request_id": _CANONICAL_ID,
            "protocol_version": _PROTOCOL_VERSION,
            "helper_actor_id": _ACTOR_ID,
            "capability": _CAPABILITY_ENUM,
            "expires_at": _FINITE_TS,
        },
        "optional": {
            "data_subject": {
                "type": "string",
                "max_length": 128,
                "forbid_controls": True,
                "forbid_unicode_format": True,
            },
            "resource_pattern": {
                "type": "string",
                "max_length": 256,
                "forbid_controls": True,
                "forbid_unicode_format": True,
            },
            "allowed_actions": {
                "type": "array",
                "max_items": 16,
                "unique": True,
                "items": {
                    "type": "string",
                    "min_length": 1,
                    "max_length": 64,
                    "forbid_controls": True,
                    "forbid_unicode_format": True,
                    "not_whitespace_only": True,
                },
            },
        },
    },
    "phase5_helper_grant_list": {
        "required": {
            "request_id": _CANONICAL_ID,
            "protocol_version": _PROTOCOL_VERSION,
        },
        "optional": {},
    },
    "phase5_helper_grant_revoke": {
        "required": {
            "request_id": _CANONICAL_ID,
            "protocol_version": _PROTOCOL_VERSION,
            "grant_id": _CANONICAL_ID,
        },
        "optional": {},
    },
}

PHASE5_SERVER_MESSAGE_SPECS: dict[str, dict[str, Any]] = {
    "phase5_session_update": {
        "required": {
            "request_id": _CANONICAL_ID,
            "protocol_version": _PROTOCOL_VERSION,
            "session_id": _CANONICAL_ID,
            "session_type": _SESSION_TYPE_ENUM,
            "state": {
                "type": "string",
                "enum": [
                    "inactive",
                    "pending_owner_approval",
                    "active",
                    "expired",
                    "revoked",
                    "locked",
                    "closed",
                ],
            },
            "expires_at": _FINITE_TS,
            "capabilities": {
                "type": "array",
                "max_items": 8,
                "items": _CAPABILITY_ENUM,
            },
        },
        "optional": {},
    },
    "phase5_capability_proposal": {
        "required": {
            "request_id": _CANONICAL_ID,
            "protocol_version": _PROTOCOL_VERSION,
            "capability": {
                "type": "string",
                "enum": ["teach_me", "guide_my_hands", "care"],
            },
            "outcome": {
                "type": "string",
                "enum": ["allow", "require_approval"],
            },
            "approval_required": {"type": "boolean"},
            "summary": {
                "type": "string",
                "min_length": 1,
                "max_length": 512,
                "forbid_controls": True,
                "forbid_unicode_format": True,
                "not_whitespace_only": True,
            },
            "items": {
                "type": "array",
                "max_items": 32,
                "items": {
                    "type": "string",
                    "min_length": 1,
                    "max_length": 512,
                    "forbid_controls": True,
                    "forbid_unicode_format": True,
                    "not_whitespace_only": True,
                },
            },
        },
        "optional": {
            "installs_skills": {"type": "boolean", "equals": False},
            "camera_accessed": {"type": "boolean", "equals": False},
            "contact_made": {"type": "boolean", "equals": False},
            "uncertainty_disclosed": {"type": "boolean"},
            "emergency_limitation": {
                "type": "string",
                "max_length": 512,
                "forbid_controls": True,
                "forbid_unicode_format": True,
            },
        },
    },
    "phase5_approval_required": {
        "required": {
            "request_id": _CANONICAL_ID,
            "protocol_version": _PROTOCOL_VERSION,
            "pending_request_id": _CANONICAL_ID,
            "capability": {
                "type": "string",
                "enum": ["teach_me", "guide_my_hands", "care", "trusted_helper_access"],
            },
            "reason_code": {
                "type": "string",
                "enum": sorted(PHASE5_ERROR_CODES),
            },
        },
        "optional": {
            "summary": {
                "type": "string",
                "max_length": 512,
                "forbid_controls": True,
                "forbid_unicode_format": True,
            },
        },
    },
    "phase5_helper_grants": {
        "required": {
            "request_id": _CANONICAL_ID,
            "protocol_version": _PROTOCOL_VERSION,
            "grants": {
                "type": "array",
                "max_items": 50,
                "items": {
                    "type": "object",
                    "exact_keys": True,
                    "required": {
                        "grant_id": _CANONICAL_ID,
                        "helper_actor_id": _ACTOR_ID,
                        "capability": _CAPABILITY_ENUM,
                        "expires_at": _FINITE_TS,
                        "revoked": {"type": "boolean"},
                    },
                    "optional": {
                        "data_subject": {
                            "type": "string",
                            "max_length": 128,
                            "forbid_controls": True,
                            "forbid_unicode_format": True,
                        },
                    },
                },
            },
        },
        "optional": {},
    },
    "phase5_error": {
        "required": {
            "request_id": _CANONICAL_ID,
            "protocol_version": _PROTOCOL_VERSION,
            "code": {
                "type": "string",
                "enum": sorted(PHASE5_ERROR_CODES),
            },
        },
        "optional": {},
    },
}


class Phase5CapabilityPreparer(Protocol):
    """Narrow injectable interface for optional capability proposal creation."""

    def prepare(self, request: Any, authorization: Phase5RuntimeDecision) -> CapabilityServiceDecision:
        ...


class Phase5RuntimeCoordinator(Protocol):
    """Narrow injectable interface for optional runtime session coordination."""

    def activate_session(self, actor_context: Any, activation_request: Any) -> Any:
        ...

    def authorize(self, request: Any) -> Phase5RuntimeDecision:
        ...

    def transition_session(
        self,
        session_id: str,
        to_state: SessionState,
        actor_id: str,
        authority: Any,
    ) -> Any:
        ...

    def get_session_status(self, session_id: str, actor_id: str) -> Optional[AccessSession]:
        ...

    def revoke_helper_access(self, grant_id: str, owner_actor_id: str) -> bool:
        ...


def register_phase5_protocol(
    client_messages: dict[str, Any],
    server_messages: dict[str, Any],
) -> None:
    """Merge Phase 5 specs into existing protocol registries without replacing them."""
    for name, spec in PHASE5_CLIENT_MESSAGE_SPECS.items():
        client_messages[name] = copy.deepcopy(spec)
    for name, spec in PHASE5_SERVER_MESSAGE_SPECS.items():
        server_messages[name] = copy.deepcopy(spec)


def safe_error_code(code: str) -> str:
    if code in PHASE5_ERROR_CODES:
        return code
    return "internal_error"


def build_phase5_error(*, request_id: str, code: str) -> dict[str, Any]:
    rid = request_id if isinstance(request_id, str) and request_id else "invalid-request"
    return {
        "type": "phase5_error",
        "request_id": rid,
        "protocol_version": 1,
        "code": safe_error_code(code),
    }


def session_to_update_message(*, request_id: str, session: AccessSession) -> dict[str, Any]:
    """Privacy-safe session snapshot for the wire (no evidence or private content)."""
    return {
        "type": "phase5_session_update",
        "request_id": request_id,
        "protocol_version": 1,
        "session_id": session.session_id,
        "session_type": session.session_type.value,
        "state": session.state.value,
        "expires_at": float(session.expires_at),
        "capabilities": [capability.value for capability in session.capabilities],
    }


def grant_to_wire(grant: CapabilityGrant) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "grant_id": grant.grant_id,
        "helper_actor_id": grant.helper_actor_id,
        "capability": grant.capability.value,
        "expires_at": float(grant.expires_at),
        "revoked": bool(grant.revoked),
    }
    if grant.scope.data_subject:
        payload["data_subject"] = grant.scope.data_subject
    return payload


def build_helper_grants_message(
    *,
    request_id: str,
    grants: Sequence[CapabilityGrant],
) -> dict[str, Any]:
    return {
        "type": "phase5_helper_grants",
        "request_id": request_id,
        "protocol_version": 1,
        "grants": [grant_to_wire(grant) for grant in grants[:50]],
    }


def _proposal_items(proposal: Any) -> Tuple[str, ...]:
    if isinstance(proposal, TeachMeProposal):
        items = list(proposal.outline[:8]) + list(proposal.learning_steps[:8])
        return tuple(items[:32])
    if isinstance(proposal, GuideHandsProposal):
        return tuple(step.description[:512] for step in proposal.steps[:32])
    if isinstance(proposal, CareProposal):
        return tuple(
            list(proposal.supportive_language[:8]) + list(proposal.check_in_questions[:8])
        )[:32]
    return ()


def build_capability_proposal_message(
    *,
    request_id: str,
    capability: Capability,
    decision: CapabilityServiceDecision,
) -> dict[str, Any]:
    summary = "Proposal ready for review."
    items: Tuple[str, ...] = ()
    uncertainty = False
    emergency: Optional[str] = None
    if decision.proposal is not None:
        items = _proposal_items(decision.proposal)
        if isinstance(decision.proposal, TeachMeProposal):
            summary = "Teach Me proposal ready. Skills are not installed."
        elif isinstance(decision.proposal, GuideHandsProposal):
            summary = "Guide My Hands proposal ready. No automatic camera access."
            uncertainty = bool(decision.proposal.uncertainty_disclosed)
        elif isinstance(decision.proposal, CareProposal):
            summary = "Care proposal ready. Supportive assistance only."
            emergency = (
                "HIKARI does not contact emergency services or claim that anyone was contacted."
            )
    outcome = (
        Outcome.REQUIRE_APPROVAL.value
        if decision.outcome is Outcome.REQUIRE_APPROVAL
        else Outcome.ALLOW.value
    )
    message: dict[str, Any] = {
        "type": "phase5_capability_proposal",
        "request_id": request_id,
        "protocol_version": 1,
        "capability": capability.value,
        "outcome": outcome,
        "approval_required": bool(decision.approval_required),
        "summary": summary,
        "items": list(items),
        "installs_skills": False,
        "camera_accessed": False,
        "contact_made": False,
    }
    if uncertainty:
        message["uncertainty_disclosed"] = True
    if emergency is not None:
        message["emergency_limitation"] = emergency
    return message


def build_approval_required_message(
    *,
    request_id: str,
    pending_request_id: str,
    capability: Capability,
    reason_code: str = "approval_required",
    summary: str = "Owner approval is required before continuing.",
) -> dict[str, Any]:
    return {
        "type": "phase5_approval_required",
        "request_id": request_id,
        "protocol_version": 1,
        "pending_request_id": pending_request_id,
        "capability": capability.value,
        "reason_code": safe_error_code(reason_code),
        "summary": summary[:512],
    }


def outcome_to_error_code(outcome: Outcome) -> str:
    mapping = {
        Outcome.DENY: "denied",
        Outcome.EXPIRED: "expired",
        Outcome.REVOKED: "revoked",
        Outcome.OUT_OF_SCOPE: "denied",
        Outcome.AUTHENTICATION_REQUIRED: "unauthorized",
        Outcome.REQUIRE_APPROVAL: "approval_required",
        Outcome.ALLOW: "denied",
    }
    return mapping.get(outcome, "denied")


__all__ = [
    "PHASE5_CLIENT_MESSAGE_SPECS",
    "PHASE5_SERVER_MESSAGE_SPECS",
    "PHASE5_CLIENT_MESSAGE_TYPES",
    "PHASE5_SERVER_MESSAGE_TYPES",
    "PHASE5_ERROR_CODES",
    "Phase5CapabilityPreparer",
    "Phase5RuntimeCoordinator",
    "register_phase5_protocol",
    "build_phase5_error",
    "session_to_update_message",
    "build_helper_grants_message",
    "build_capability_proposal_message",
    "build_approval_required_message",
    "outcome_to_error_code",
    "safe_error_code",
]
