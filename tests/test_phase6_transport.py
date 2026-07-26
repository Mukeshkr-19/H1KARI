"""Synthetic test suite for Phase 6 Command-Center transport contracts."""

import math
import pytest

from core.phase6_transport import (
    Phase6CorrelationTracker,
    Phase6ErrorCode,
    build_agent_run_frame,
    build_encrypted_sync_frame,
    build_error_frame,
    build_home_assistant_proposal_frame,
    build_integration_status_frame,
    build_model_eval_frame,
    build_remote_worker_frame,
    build_repo_intel_frame,
    build_skill_evolution_frame,
    build_time_sense_frame,
    parse_phase6_client_frame,
)


def test_parse_valid_client_frame():
    frame = {
        "type": "phase6_integration_list_request",
        "request_id": "req_001",
        "protocol_version": 1,
    }
    valid, header, code, msg = parse_phase6_client_frame(frame)
    assert valid is True
    assert header is not None
    assert header.type == "phase6_integration_list_request"
    assert header.request_id == "req_001"
    assert header.protocol_version == 1


def test_parse_invalid_client_frame():
    # Unsupported version
    frame_bad_ver = {"type": "phase6_test", "request_id": "req_1", "protocol_version": 99}
    v1, h1, c1, _ = parse_phase6_client_frame(frame_bad_ver)
    assert v1 is False

    # Invalid ID
    frame_bad_id = {"type": "phase6_test", "request_id": "INVALID ID!", "protocol_version": 1}
    v2, h2, c2, _ = parse_phase6_client_frame(frame_bad_id)
    assert v2 is False


def test_parse_client_frame_rejects_unknown_fields_and_bad_confirmation():
    frames = (
        {"type": "phase6_unknown", "request_id": "req_1", "protocol_version": 1},
        {"type": "phase6_integration_list_request", "request_id": "req_1", "protocol_version": 1, "role": "owner"},
        {"type": "phase6_home_assistant_confirm_request", "request_id": "req_1", "protocol_version": 1, "proposal_id": "bad id", "nonce": "n"},
    )
    for frame in frames:
        valid, header, code, _ = parse_phase6_client_frame(frame)
        assert valid is False
        assert header is None
        assert code is Phase6ErrorCode.INVALID_REQUEST


def test_correlation_tracker_duplicate_prevention():
    tracker = Phase6CorrelationTracker()

    assert tracker.track_request("req_001") is True
    # Duplicate request_id rejected
    assert tracker.track_request("req_001") is False

    assert tracker.track_nonce("nonce_abc") is True
    # Duplicate nonce rejected
    assert tracker.track_nonce("nonce_abc") is False


def test_build_integration_status_frame():
    frame = build_integration_status_frame(
        request_id="req_int_1",
        integration_id="home_assistant",
        name="Home Assistant Integration",
        status="ready",
        details_summary="All entities connected",
    )
    assert frame["type"] == "phase6_integration_status"
    assert frame["status"] == "ready"

    # Invalid status
    with pytest.raises(ValueError, match="invalid status"):
        build_integration_status_frame("req_1", "ha", "HA", "INVALID_STATUS")


def test_build_agent_run_frame_bounded_summaries():
    frame = build_agent_run_frame(
        request_id="req_run_1",
        run_id="run_001",
        state="running",
        step_count=5,
        action_count=12,
        budget_limit=50,
        safe_summary="Refactoring module imports",
    )
    assert frame["step_count"] == 5
    assert frame["safe_summary"] == "Refactoring module imports"

    # Negative step count
    with pytest.raises(ValueError):
        build_agent_run_frame("req_1", "run_1", "running", -1, 0, 10, "Summary")


def test_build_home_assistant_proposal_wildcard_rejection():
    # Wildcard in entity_id rejected
    with pytest.raises(ValueError, match="wildcards prohibited"):
        build_home_assistant_proposal_frame(
            request_id="req_ha_1",
            proposal_id="prop_1",
            entity_id="light.*",
            domain="light",
            service="turn_on",
            risk="medium",
            effect_summary="Turn on all lights",
            expires_at=1000.0,
            nonce="nonce_123",
        )


def test_build_skill_evolution_no_auto_install():
    frame = build_skill_evolution_frame(
        request_id="req_sk_1",
        package_id="pkg_timer",
        version="1.0.0",
        state="review",
        permissions_summary=["skill:execute:skill:timer"],
        rollback_ready=True,
    )
    assert frame["allows_auto_install"] is False


def test_build_encrypted_sync_no_plaintext():
    frame = build_encrypted_sync_frame(
        request_id="req_sync_1",
        enabled=True,
        configured=True,
        status="synced",
        conflict_count=0,
    )
    assert frame["exposes_plaintext"] is False


def test_build_remote_worker_no_local_authority():
    frame = build_remote_worker_frame(
        request_id="req_rem_1",
        job_id="job_99",
        worker_id="worker_alpha",
        state="quarantined",
        has_evidence=True,
        quarantined=True,
    )
    assert frame["verified_local_authority"] is False


def test_build_model_eval_frame():
    frame = build_model_eval_frame(
        request_id="req_me_1",
        candidate_id="cand_qwen_7b",
        privacy_class="local_only",
        capabilities=["text_gen", "code_gen"],
        quality_score=0.92,
        safety_score=0.98,
        latency_ms=45.0,
        recommendation="Recommended for local inference",
    )
    assert frame["privacy_class"] == "local_only"
    assert frame["quality_score"] == 0.92


def test_build_error_frame():
    frame = build_error_frame("req_err_1", Phase6ErrorCode.UNAUTHORIZED)
    assert frame["type"] == "phase6_error"
    assert frame["code"] == "unauthorized"
    assert "paired owner connection" in frame["message"]
