"""Strict Phase 6 Command-Center transport frame definitions and parsers.

Provides fail-closed parsing and validation for Phase 6 WebSocket frames covering
integration capability status, bounded agent runs, time sense, repository intelligence,
skill evolution, Home Assistant confirmation, encrypted sync, remote workers, and model
evaluation without executing actions or mutating state.
"""

from __future__ import annotations

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
    "approval_required", "active", "cancelling", "failed", "revoked",
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
PHASE6_CLIENT_FIELDS: Mapping[str, frozenset[str]] = {
    "phase6_integration_list_request": frozenset({"type", "request_id", "protocol_version"}),
    "phase6_home_assistant_confirm_request": frozenset({"type", "request_id", "protocol_version", "proposal_id", "nonce"}),
}


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

    if not isinstance(msg_type, str) or msg_type not in PHASE6_CLIENT_FIELDS:
        return False, None, Phase6ErrorCode.INVALID_REQUEST, "invalid frame type"
    if not _is_valid_id(request_id):
        return False, None, Phase6ErrorCode.INVALID_REQUEST, "invalid request_id"
    if proto_ver != PROTOCOL_VERSION:
        return False, None, Phase6ErrorCode.INVALID_REQUEST, "unsupported protocol version"
    if set(data) != PHASE6_CLIENT_FIELDS[msg_type]:
        return False, None, Phase6ErrorCode.INVALID_REQUEST, "unexpected or missing fields"
    if msg_type == "phase6_home_assistant_confirm_request":
        if not _is_valid_id(data.get("proposal_id")) or not _is_safe_text(data.get("nonce"), max_len=80):
            return False, None, Phase6ErrorCode.INVALID_REQUEST, "invalid confirmation fields"

    header = Phase6FrameHeader(type=msg_type, request_id=request_id, protocol_version=PROTOCOL_VERSION)
    return True, header, Phase6ErrorCode.INVALID_REQUEST, "ok"


class Phase6CorrelationTracker:
    """Track request IDs and nonces to prevent duplicate or stale submissions."""

    def __init__(self, max_history: int = 256):
        if not isinstance(max_history, int) or not 1 <= max_history <= 4096:
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
        raise ValueError("results count exceeds maximum")

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

        clean_results.append({
            "path": path,
            "line": line,
            "symbol": symbol,
            "score": float(score),
            "provenance": provenance,
        })

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
    if not all(_is_safe_text(value, max_len=128) for value in (entity_id, domain, service)):
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
