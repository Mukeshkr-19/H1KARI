"""Strict Phase 6 Command-Center transport frame definitions and parsers.

Provides fail-closed parsing and validation for Phase 6 WebSocket frames covering
integration capability status, bounded agent runs, time sense, repository intelligence,
skill evolution, Home Assistant confirmation, encrypted sync, remote workers, and model
evaluation without executing actions or mutating state.
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Optional, Sequence, Tuple

# Canonical identifier pattern
_CANONICAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")

# Forbidden Unicode format/directional characters
_UNICODE_FORMAT_RE = re.compile(r"[\u200e\u200f\u202a-\u202e\u2060-\u2069\ufeff]")

# Protocol constants
PROTOCOL_VERSION = 1
MAX_TEXT_LENGTH = 1024
MAX_ITEMS_COUNT = 64
MAX_SUMMARY_LENGTH = 512


class Phase6ErrorCode(StrEnum):
    """Fixed safe error codes for Phase 6 transport errors."""

    INVALID_REQUEST = "invalid_request"
    UNAUTHORIZED = "unauthorized"
    UNAVAILABLE = "unavailable"
    DENIED = "denied"
    EXPIRED = "expired"
    REVOKED = "revoked"
    LOCKED = "locked"
    CLOSED = "closed"
    APPROVAL_REQUIRED = "approval_required"
    NOT_FOUND = "not_found"
    STALE_REQUEST = "stale_request"
    DUPLICATE_REQUEST = "duplicate_request"
    INTERNAL_ERROR = "internal_error"


SAFE_ERROR_MESSAGES: Mapping[Phase6ErrorCode, str] = {
    Phase6ErrorCode.INVALID_REQUEST: "The Phase 6 command-center request was invalid.",
    Phase6ErrorCode.UNAUTHORIZED: "Phase 6 requires a paired owner connection.",
    Phase6ErrorCode.UNAVAILABLE: "Phase 6 command-center features are unavailable.",
    Phase6ErrorCode.DENIED: "The Phase 6 request was denied.",
    Phase6ErrorCode.EXPIRED: "The Phase 6 request or proposal has expired.",
    Phase6ErrorCode.REVOKED: "The requested Phase 6 resource was revoked.",
    Phase6ErrorCode.LOCKED: "The Phase 6 session is locked.",
    Phase6ErrorCode.CLOSED: "The Phase 6 transport connection is closed.",
    Phase6ErrorCode.APPROVAL_REQUIRED: "Owner approval is required for this action.",
    Phase6ErrorCode.NOT_FOUND: "The requested Phase 6 resource was not found.",
    Phase6ErrorCode.STALE_REQUEST: "That proposal or request is no longer active.",
    Phase6ErrorCode.DUPLICATE_REQUEST: "That request was already submitted.",
    Phase6ErrorCode.INTERNAL_ERROR: "A Phase 6 transport error occurred.",
}


# Valid status/state enums
INTEGRATION_STATUSES = frozenset({
    "unavailable", "disabled", "configuring", "ready", "degraded",
    "approval_required", "active", "cancelling", "cancelled", "completed", "failed", "revoked",
})

AGENT_RUN_STATES = frozenset({
    "preview", "waiting_for_approval", "running", "observing", "correcting",
    "succeeded", "denied", "failed", "cancelled", "exhausted",
})

SKILL_EVOLUTION_STATES = frozenset({
    "proposal", "validation", "review", "rejection", "revocation", "install_plan_ready",
})

HOME_ASSISTANT_RISKS = frozenset({"low", "medium", "high", "critical"})

PRIVACY_CLASSES = frozenset({"local_only", "gateway_ok", "remote_ok"})

PHASE6_CLIENT_MESSAGE_TYPES = frozenset({
    "phase6_integration_list_request",
    "phase6_home_assistant_prepare_request",
    "phase6_home_assistant_confirm_request",
    "phase6_proposal_cancel_request",
    "phase6_agent_run_request",
    "phase6_snapshot_refresh_request",
})

PHASE6_SERVER_MESSAGE_TYPES = frozenset({
    "phase6_integration_status",
    "phase6_agent_run_update",
    "phase6_time_sense_update",
    "phase6_repo_intel_update",
    "phase6_skill_evolution_update",
    "phase6_home_assistant_proposal",
    "phase6_encrypted_sync_update",
    "phase6_remote_worker_update",
    "phase6_model_eval_update",
    "phase6_error",
})

# Protocol schema definitions for core/protocol.py validation
_CANONICAL_ID_SPEC = {
    "type": "string",
    "max_length": 80,
    "pattern": r"^[a-z0-9][a-z0-9_.-]{0,79}$",
}
_SAFE_TEXT_64 = {
    "type": "string",
    "min_length": 1,
    "max_length": 64,
    "forbid_controls": True,
    "forbid_unicode_format": True,
}
_SAFE_TEXT_128 = {
    "type": "string",
    "min_length": 1,
    "max_length": 128,
    "forbid_controls": True,
    "forbid_unicode_format": True,
}
_SAFE_TEXT_512 = {
    "type": "string",
    "min_length": 1,
    "max_length": 512,
    "forbid_controls": True,
    "forbid_unicode_format": True,
}

PHASE6_CLIENT_MESSAGE_SPECS: dict[str, Any] = {
    "phase6_integration_list_request": {
        "required": {
            "request_id": _CANONICAL_ID_SPEC,
            "protocol_version": {"type": "integer", "enum": [1]},
        },
        "optional": {},
    },
    "phase6_home_assistant_prepare_request": {
        "required": {
            "request_id": _CANONICAL_ID_SPEC,
            "protocol_version": {"type": "integer", "enum": [1]},
            "entity_id": _SAFE_TEXT_128,
            "domain": _SAFE_TEXT_128,
            "service": _SAFE_TEXT_128,
            "risk": {
                "type": "string",
                "enum": list(HOME_ASSISTANT_RISKS),
            },
            "effect_summary": _SAFE_TEXT_512,
        },
        "optional": {},
    },
    "phase6_home_assistant_confirm_request": {
        "required": {
            "request_id": _CANONICAL_ID_SPEC,
            "protocol_version": {"type": "integer", "enum": [1]},
            "proposal_id": _CANONICAL_ID_SPEC,
            "nonce": {
                "type": "string",
                "min_length": 1,
                "max_length": 80,
                "forbid_controls": True,
                "forbid_unicode_format": True,
            },
        },
        "optional": {},
    },
    "phase6_proposal_cancel_request": {
        "required": {
            "request_id": _CANONICAL_ID_SPEC,
            "protocol_version": {"type": "integer", "enum": [1]},
            "proposal_id": _CANONICAL_ID_SPEC,
        },
        "optional": {},
    },
    "phase6_agent_run_request": {
        "required": {
            "request_id": _CANONICAL_ID_SPEC,
            "protocol_version": {"type": "integer", "enum": [1]},
            "action": {
                "type": "string",
                "enum": ["preview", "start", "confirm", "cancel", "status"],
            },
            "run_id": _CANONICAL_ID_SPEC,
        },
        "optional": {
            "nonce": {
                "type": "string",
                "min_length": 1,
                "max_length": 80,
                "forbid_controls": True,
                "forbid_unicode_format": True,
            },
            "budget_limit": {"type": "integer", "minimum": 0},
            "task_summary": _SAFE_TEXT_512,
        },
    },
    "phase6_snapshot_refresh_request": {
        "required": {
            "request_id": _CANONICAL_ID_SPEC,
            "protocol_version": {"type": "integer", "enum": [1]},
            "target": {
                "type": "string",
                "enum": ["all", "time_sense", "repo_intel", "integrations"],
            },
        },
        "optional": {},
    },
}

PHASE6_SERVER_MESSAGE_SPECS: dict[str, Any] = {
    "phase6_integration_status": {
        "required": {
            "request_id": _CANONICAL_ID_SPEC,
            "protocol_version": {"type": "integer", "enum": [1]},
            "integration_id": _CANONICAL_ID_SPEC,
            "name": _SAFE_TEXT_128,
            "status": {
                "type": "string",
                "enum": list(INTEGRATION_STATUSES),
            },
        },
        "optional": {
            "details_summary": _SAFE_TEXT_512,
        },
    },
    "phase6_agent_run_update": {
        "required": {
            "request_id": _CANONICAL_ID_SPEC,
            "protocol_version": {"type": "integer", "enum": [1]},
            "run_id": _CANONICAL_ID_SPEC,
            "state": {
                "type": "string",
                "enum": list(AGENT_RUN_STATES),
            },
            "step_count": {"type": "integer", "minimum": 0},
            "action_count": {"type": "integer", "minimum": 0},
            "budget_limit": {"type": "integer", "minimum": 0},
            "safe_summary": _SAFE_TEXT_512,
        },
        "optional": {},
    },
    "phase6_time_sense_update": {
        "required": {
            "request_id": _CANONICAL_ID_SPEC,
            "protocol_version": {"type": "integer", "enum": [1]},
            "task_age_seconds": {"type": "number", "finite": True, "minimum": 0},
            "heartbeat_status": _SAFE_TEXT_64,
            "next_allowed_checkin": {"type": "number", "finite": True},
            "suppression_state": _SAFE_TEXT_64,
            "background_status": _SAFE_TEXT_64,
        },
        "optional": {
            "stuck_reason": _SAFE_TEXT_512,
        },
    },
    "phase6_repo_intel_update": {
        "required": {
            "request_id": _CANONICAL_ID_SPEC,
            "protocol_version": {"type": "integer", "enum": [1]},
            "scan_state": _SAFE_TEXT_64,
            "query_summary": _SAFE_TEXT_128,
            "hit_count": {"type": "integer", "minimum": 0},
            "results": {
                "type": "array",
                "max_items": MAX_ITEMS_COUNT,
                "items": {
                    "type": "object",
                    "exact_keys": True,
                    "required": {
                        "path": {
                            "type": "string",
                            "min_length": 1,
                            "max_length": 256,
                            "forbid_controls": True,
                            "forbid_unicode_format": True,
                        },
                        "line": {"type": "integer", "minimum": 0},
                        "score": {"type": "number", "finite": True},
                        "provenance": _SAFE_TEXT_128,
                    },
                    "optional": {
                        "symbol": _SAFE_TEXT_128,
                    },
                },
            },
        },
        "optional": {},
    },
    "phase6_skill_evolution_update": {
        "required": {
            "request_id": _CANONICAL_ID_SPEC,
            "protocol_version": {"type": "integer", "enum": [1]},
            "package_id": _CANONICAL_ID_SPEC,
            "version": _SAFE_TEXT_64,
            "state": {
                "type": "string",
                "enum": list(SKILL_EVOLUTION_STATES),
            },
            "permissions_summary": {
                "type": "array",
                "max_items": MAX_ITEMS_COUNT,
                "items": _SAFE_TEXT_128,
            },
            "rollback_ready": {"type": "boolean"},
            "allows_auto_install": {"type": "boolean", "equals": False},
        },
        "optional": {},
    },
    "phase6_home_assistant_proposal": {
        "required": {
            "request_id": _CANONICAL_ID_SPEC,
            "protocol_version": {"type": "integer", "enum": [1]},
            "proposal_id": _CANONICAL_ID_SPEC,
            "entity_id": _SAFE_TEXT_128,
            "domain": _SAFE_TEXT_128,
            "service": _SAFE_TEXT_128,
            "risk": {
                "type": "string",
                "enum": list(HOME_ASSISTANT_RISKS),
            },
            "effect_summary": _SAFE_TEXT_512,
            "expires_at": {"type": "number", "finite": True},
            "nonce": {
                "type": "string",
                "min_length": 1,
                "max_length": 80,
                "forbid_controls": True,
                "forbid_unicode_format": True,
            },
        },
        "optional": {},
    },
    "phase6_encrypted_sync_update": {
        "required": {
            "request_id": _CANONICAL_ID_SPEC,
            "protocol_version": {"type": "integer", "enum": [1]},
            "enabled": {"type": "boolean"},
            "configured": {"type": "boolean"},
            "status": _SAFE_TEXT_64,
            "conflict_count": {"type": "integer", "minimum": 0},
            "exposes_plaintext": {"type": "boolean", "equals": False},
        },
        "optional": {},
    },
    "phase6_remote_worker_update": {
        "required": {
            "request_id": _CANONICAL_ID_SPEC,
            "protocol_version": {"type": "integer", "enum": [1]},
            "job_id": _CANONICAL_ID_SPEC,
            "worker_id": _CANONICAL_ID_SPEC,
            "state": _SAFE_TEXT_64,
            "has_evidence": {"type": "boolean"},
            "quarantined": {"type": "boolean"},
            "verified_local_authority": {"type": "boolean", "equals": False},
        },
        "optional": {},
    },
    "phase6_model_eval_update": {
        "required": {
            "request_id": _CANONICAL_ID_SPEC,
            "protocol_version": {"type": "integer", "enum": [1]},
            "candidate_id": _CANONICAL_ID_SPEC,
            "privacy_class": {
                "type": "string",
                "enum": list(PRIVACY_CLASSES),
            },
            "capabilities": {
                "type": "array",
                "max_items": MAX_ITEMS_COUNT,
                "items": _SAFE_TEXT_64,
            },
            "quality_score": {"type": "number", "finite": True},
            "safety_score": {"type": "number", "finite": True},
            "latency_ms": {"type": "number", "finite": True},
            "recommendation": _SAFE_TEXT_128,
        },
        "optional": {
            "rejection_reason": _SAFE_TEXT_128,
        },
    },
    "phase6_error": {
        "required": {
            "request_id": _CANONICAL_ID_SPEC,
            "protocol_version": {"type": "integer", "enum": [1]},
            "code": {
                "type": "string",
                "enum": [c.value for c in Phase6ErrorCode],
            },
            "message": _SAFE_TEXT_512,
        },
        "optional": {},
    },
}


def register_phase6_protocol(
    client_messages: dict[str, Any],
    server_messages: dict[str, Any],
) -> None:
    """Merge Phase 6 specs into existing protocol registries without replacing them."""
    for name, spec in PHASE6_CLIENT_MESSAGE_SPECS.items():
        client_messages[name] = copy.deepcopy(spec)
    for name, spec in PHASE6_SERVER_MESSAGE_SPECS.items():
        server_messages[name] = copy.deepcopy(spec)


def _is_valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_CANONICAL_ID_RE.fullmatch(value))


def _is_safe_text(value: Any, max_len: int = MAX_TEXT_LENGTH) -> bool:
    if not isinstance(value, str) or not value or len(value) > max_len:
        return False
    if _UNICODE_FORMAT_RE.search(value):
        return False
    for char in value:
        code = ord(char)
        if (code < 32 and code not in (9, 10, 13)) or code == 127:
            return False
    return True


def _is_finite_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass(frozen=True)
class Phase6FrameHeader:
    type: str
    request_id: str
    protocol_version: int = PROTOCOL_VERSION


def parse_phase6_client_frame(data: Mapping[str, Any]) -> Tuple[bool, Optional[Phase6FrameHeader], Phase6ErrorCode, str]:
    """Parse and validate client -> server frame envelope."""
    if not isinstance(data, dict):
        return False, None, Phase6ErrorCode.INVALID_REQUEST, "frame must be a dictionary"

    msg_type = data.get("type")
    request_id = data.get("request_id")
    proto_ver = data.get("protocol_version")

    if not isinstance(msg_type, str) or msg_type not in PHASE6_CLIENT_MESSAGE_TYPES:
        return False, None, Phase6ErrorCode.INVALID_REQUEST, "invalid frame type"
    if not _is_valid_id(request_id):
        return False, None, Phase6ErrorCode.INVALID_REQUEST, "invalid request_id"
    if proto_ver != PROTOCOL_VERSION:
        return False, None, Phase6ErrorCode.INVALID_REQUEST, "unsupported protocol version"
    spec = PHASE6_CLIENT_MESSAGE_SPECS[msg_type]
    expected = {"type", *spec["required"], *spec["optional"]}
    if set(data) != expected:
        return False, None, Phase6ErrorCode.INVALID_REQUEST, "unexpected or missing fields"
    if msg_type == "phase6_home_assistant_confirm_request":
        if not _is_valid_id(data.get("proposal_id")) or not _is_safe_text(
            data.get("nonce"), max_len=80
        ):
            return False, None, Phase6ErrorCode.INVALID_REQUEST, "invalid confirmation fields"

    header = Phase6FrameHeader(type=msg_type, request_id=request_id, protocol_version=PROTOCOL_VERSION)
    return True, header, Phase6ErrorCode.INVALID_REQUEST, "ok"



class Phase6CorrelationTracker:
    """Track request IDs and nonces to prevent duplicate or stale submissions."""

    def __init__(self, max_history: int = 256):
        if type(max_history) is not int or not 1 <= max_history <= 4096:
            raise ValueError("invalid max_history")
        self.max_history = max_history
        self.seen_request_ids: set[str] = set()
        self.seen_nonces: set[str] = set()
        self.request_order: list[str] = []
        self.nonce_order: list[str] = []

    def track_request(self, request_id: str) -> bool:
        if not _is_valid_id(request_id):
            return False
        if request_id in self.seen_request_ids:
            return False
        self.seen_request_ids.add(request_id)
        self.request_order.append(request_id)
        if len(self.request_order) > self.max_history:
            oldest = self.request_order.pop(0)
            self.seen_request_ids.remove(oldest)
        return True

    def track_nonce(self, nonce: str) -> bool:
        if not _is_safe_text(nonce, max_len=80):
            return False
        if nonce in self.seen_nonces:
            return False
        self.seen_nonces.add(nonce)
        self.nonce_order.append(nonce)
        if len(self.nonce_order) > self.max_history:
            oldest = self.nonce_order.pop(0)
            self.seen_nonces.remove(oldest)
        return True

    def clear(self) -> None:
        self.seen_request_ids.clear()
        self.seen_nonces.clear()
        self.request_order.clear()
        self.nonce_order.clear()


# Builders for safe server -> client transport messages

def build_integration_status_frame(
    request_id: str,
    integration_id: str,
    name: str,
    status: str,
    details_summary: Optional[str] = None,
) -> Mapping[str, Any]:
    if not _is_valid_id(request_id) or not _is_valid_id(integration_id):
        raise ValueError("invalid identifier")
    if status not in INTEGRATION_STATUSES:
        raise ValueError(f"invalid status: {status}")
    if not _is_safe_text(name, max_len=128):
        raise ValueError("invalid name")

    res = {
        "type": "phase6_integration_status",
        "request_id": request_id,
        "protocol_version": PROTOCOL_VERSION,
        "integration_id": integration_id,
        "name": name,
        "status": status,
    }
    if details_summary is not None:
        if not _is_safe_text(details_summary, max_len=MAX_SUMMARY_LENGTH):
            raise ValueError("invalid summary")
        res["details_summary"] = details_summary
    return res


def build_agent_run_frame(
    request_id: str,
    run_id: str,
    state: str,
    step_count: int,
    action_count: int,
    budget_limit: int,
    safe_summary: str,
) -> Mapping[str, Any]:
    if not _is_valid_id(request_id) or not _is_valid_id(run_id):
        raise ValueError("invalid identifier")
    if state not in AGENT_RUN_STATES:
        raise ValueError(f"invalid state: {state}")
    if not _is_non_negative_int(step_count) or not _is_non_negative_int(action_count) or not _is_non_negative_int(budget_limit):
        raise ValueError("counts must be non-negative integers")
    if not _is_safe_text(safe_summary, max_len=MAX_SUMMARY_LENGTH):
        raise ValueError("invalid summary")

    return {
        "type": "phase6_agent_run_update",
        "request_id": request_id,
        "protocol_version": PROTOCOL_VERSION,
        "run_id": run_id,
        "state": state,
        "step_count": step_count,
        "action_count": action_count,
        "budget_limit": budget_limit,
        "safe_summary": safe_summary,
    }


def build_time_sense_frame(
    request_id: str,
    task_age_seconds: float,
    heartbeat_status: str,
    stuck_reason: Optional[str],
    next_allowed_checkin: float,
    suppression_state: str,
    background_status: str,
) -> Mapping[str, Any]:
    if not _is_valid_id(request_id):
        raise ValueError("invalid request_id")
    if not _is_finite_num(task_age_seconds) or task_age_seconds < 0:
        raise ValueError("invalid task_age_seconds")
    if not _is_finite_num(next_allowed_checkin) or next_allowed_checkin < 0:
        raise ValueError("invalid next_allowed_checkin")
    if not _is_safe_text(heartbeat_status, max_len=64) or not _is_safe_text(suppression_state, max_len=64) or not _is_safe_text(background_status, max_len=64):
        raise ValueError("invalid text status")

    res = {
        "type": "phase6_time_sense_update",
        "request_id": request_id,
        "protocol_version": PROTOCOL_VERSION,
        "task_age_seconds": task_age_seconds,
        "heartbeat_status": heartbeat_status,
        "next_allowed_checkin": next_allowed_checkin,
        "suppression_state": suppression_state,
        "background_status": background_status,
    }
    if stuck_reason is not None:
        if not _is_safe_text(stuck_reason, max_len=MAX_SUMMARY_LENGTH):
            raise ValueError("invalid stuck_reason")
        res["stuck_reason"] = stuck_reason
    return res


def build_repo_intel_frame(
    request_id: str,
    scan_state: str,
    query_summary: str,
    hit_count: int,
    results: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    if not _is_valid_id(request_id):
        raise ValueError("invalid request_id")
    if not _is_safe_text(scan_state, max_len=64) or not _is_safe_text(query_summary, max_len=128):
        raise ValueError("invalid query text")
    if not _is_non_negative_int(hit_count):
        raise ValueError("invalid hit_count")
    if len(results) > MAX_ITEMS_COUNT or hit_count != len(results):
        raise ValueError("results count mismatch or exceeds maximum")

    clean_results = []
    for r in results:
        path = r.get("path")
        line = r.get("line")
        symbol = r.get("symbol")
        score = r.get("score")
        provenance = r.get("provenance")

        if not _is_safe_text(path, max_len=256) or not _is_non_negative_int(line):
            raise ValueError("invalid result path or line")
        if symbol is not None and not _is_safe_text(symbol, max_len=128):
            raise ValueError("invalid result symbol")
        if not _is_finite_num(score) or not 0.0 <= score <= 1.0 or not _is_safe_text(provenance, max_len=128):
            raise ValueError("invalid result score or provenance")

        clean_result = {
            "path": path,
            "line": line,
            "score": float(score),
            "provenance": provenance,
        }
        if symbol is not None:
            clean_result["symbol"] = symbol
        clean_results.append(clean_result)

    return {
        "type": "phase6_repo_intel_update",
        "request_id": request_id,
        "protocol_version": PROTOCOL_VERSION,
        "scan_state": scan_state,
        "query_summary": query_summary,
        "hit_count": hit_count,
        "results": clean_results,
    }


def build_skill_evolution_frame(
    request_id: str,
    package_id: str,
    version: str,
    state: str,
    permissions_summary: Sequence[str],
    rollback_ready: bool,
) -> Mapping[str, Any]:
    if not _is_valid_id(request_id) or not _is_valid_id(package_id):
        raise ValueError("invalid identifier")
    if state not in SKILL_EVOLUTION_STATES:
        raise ValueError(f"invalid evolution state: {state}")
    if not _is_safe_text(version, max_len=64) or not isinstance(rollback_ready, bool):
        raise ValueError("invalid version or rollback flag")
    if len(permissions_summary) > MAX_ITEMS_COUNT:
        raise ValueError("permissions count exceeds maximum")

    clean_perms = []
    for p in permissions_summary:
        if not _is_safe_text(p, max_len=128):
            raise ValueError("invalid permission summary string")
        clean_perms.append(p)

    return {
        "type": "phase6_skill_evolution_update",
        "request_id": request_id,
        "protocol_version": PROTOCOL_VERSION,
        "package_id": package_id,
        "version": version,
        "state": state,
        "permissions_summary": clean_perms,
        "rollback_ready": rollback_ready,
        "allows_auto_install": False,  # Explicitly false
    }


def build_home_assistant_proposal_frame(
    request_id: str,
    proposal_id: str,
    entity_id: str,
    domain: str,
    service: str,
    risk: str,
    effect_summary: str,
    expires_at: float,
    nonce: str,
) -> Mapping[str, Any]:
    if not _is_valid_id(request_id) or not _is_valid_id(proposal_id):
        raise ValueError("invalid identifier")
    if not all(
        _is_safe_text(value, max_len=128) for value in (entity_id, domain, service)
    ):
        raise ValueError("invalid Home Assistant identifier")
    if "*" in entity_id or "*" in domain or "*" in service:
        raise ValueError("wildcards prohibited")
    if risk not in HOME_ASSISTANT_RISKS:
        raise ValueError(f"invalid risk: {risk}")
    if not _is_safe_text(effect_summary, max_len=MAX_SUMMARY_LENGTH):
        raise ValueError("invalid effect_summary")
    if not _is_finite_num(expires_at) or not _is_safe_text(nonce, max_len=80):
        raise ValueError("invalid expires_at or nonce")

    return {
        "type": "phase6_home_assistant_proposal",
        "request_id": request_id,
        "protocol_version": PROTOCOL_VERSION,
        "proposal_id": proposal_id,
        "entity_id": entity_id,
        "domain": domain,
        "service": service,
        "risk": risk,
        "effect_summary": effect_summary,
        "expires_at": expires_at,
        "nonce": nonce,
    }


def build_encrypted_sync_frame(
    request_id: str,
    enabled: bool,
    configured: bool,
    status: str,
    conflict_count: int,
) -> Mapping[str, Any]:
    if not _is_valid_id(request_id):
        raise ValueError("invalid request_id")
    if not isinstance(enabled, bool) or not isinstance(configured, bool):
        raise ValueError("enabled/configured must be booleans")
    if not _is_safe_text(status, max_len=64) or not _is_non_negative_int(conflict_count):
        raise ValueError("invalid status or conflict_count")

    return {
        "type": "phase6_encrypted_sync_update",
        "request_id": request_id,
        "protocol_version": PROTOCOL_VERSION,
        "enabled": enabled,
        "configured": configured,
        "status": status,
        "conflict_count": conflict_count,
        "exposes_plaintext": False,  # Explicitly false
    }


def build_remote_worker_frame(
    request_id: str,
    job_id: str,
    worker_id: str,
    state: str,
    has_evidence: bool,
    quarantined: bool,
) -> Mapping[str, Any]:
    if not _is_valid_id(request_id) or not _is_valid_id(job_id) or not _is_valid_id(worker_id):
        raise ValueError("invalid identifier")
    if not isinstance(has_evidence, bool) or not isinstance(quarantined, bool):
        raise ValueError("evidence/quarantine flags must be booleans")
    if not _is_safe_text(state, max_len=64):
        raise ValueError("invalid state")

    return {
        "type": "phase6_remote_worker_update",
        "request_id": request_id,
        "protocol_version": PROTOCOL_VERSION,
        "job_id": job_id,
        "worker_id": worker_id,
        "state": state,
        "has_evidence": has_evidence,
        "quarantined": quarantined,
        "verified_local_authority": False,  # Explicitly false: remote output never local authority
    }


def build_model_eval_frame(
    request_id: str,
    candidate_id: str,
    privacy_class: str,
    capabilities: Sequence[str],
    quality_score: float,
    safety_score: float,
    latency_ms: float,
    recommendation: str,
    rejection_reason: Optional[str] = None,
) -> Mapping[str, Any]:
    if not _is_valid_id(request_id) or not _is_valid_id(candidate_id):
        raise ValueError("invalid identifier")
    if privacy_class not in PRIVACY_CLASSES:
        raise ValueError(f"invalid privacy_class: {privacy_class}")
    if not _is_finite_num(quality_score) or not 0.0 <= quality_score <= 1.0 or not _is_finite_num(safety_score) or not 0.0 <= safety_score <= 1.0 or not _is_finite_num(latency_ms) or latency_ms < 0:
        raise ValueError("scores and latency must be finite numbers")
    if not _is_safe_text(recommendation, max_len=128):
        raise ValueError("invalid recommendation")

    if len(capabilities) > MAX_ITEMS_COUNT:
        raise ValueError("capabilities count exceeds maximum")
    clean_caps = []
    for c in capabilities:
        if not _is_safe_text(c, max_len=64):
            raise ValueError("invalid capability string")
        clean_caps.append(c)

    res = {
        "type": "phase6_model_eval_update",
        "request_id": request_id,
        "protocol_version": PROTOCOL_VERSION,
        "candidate_id": candidate_id,
        "privacy_class": privacy_class,
        "capabilities": clean_caps,
        "quality_score": float(quality_score),
        "safety_score": float(safety_score),
        "latency_ms": float(latency_ms),
        "recommendation": recommendation,
    }
    if rejection_reason is not None:
        if not _is_safe_text(rejection_reason, max_len=128):
            raise ValueError("invalid rejection_reason")
        res["rejection_reason"] = rejection_reason
    return res


def build_error_frame(request_id: str, code: Phase6ErrorCode) -> Mapping[str, Any]:
    if not _is_valid_id(request_id):
        raise ValueError("invalid request_id")
    if not isinstance(code, Phase6ErrorCode):
        raise ValueError("invalid error code")

    return {
        "type": "phase6_error",
        "request_id": request_id,
        "protocol_version": PROTOCOL_VERSION,
        "code": code.value,
        "message": SAFE_ERROR_MESSAGES[code],
    }
