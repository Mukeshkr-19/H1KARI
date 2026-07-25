"""Phase 5 transport protocol strictness and safe builders."""

from __future__ import annotations

import copy
import importlib

from core.protocol import validate_client_message, validate_server_message
from core.phase5.contracts import Capability, CapabilityGrant, ScopeConstraint
from core.phase5.session_lifecycle import (
    AccessSession,
    AuthoritySource,
    SessionAuthoritySnapshot,
    SessionState,
    SessionType,
)
from core.phase5.transport import (
    PHASE5_CLIENT_MESSAGE_TYPES,
    PHASE5_ERROR_CODES,
    build_approval_required_message,
    build_helper_grants_message,
    build_phase5_error,
    session_to_update_message,
)


def test_import_has_no_side_effects(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_HOME", str(tmp_path / "home"))
    importlib.reload(importlib.import_module("core.phase5.transport"))
    home = tmp_path / "home"
    assert not home.exists() or not any(home.rglob("*.db"))


def test_unknown_fields_rejected():
    base = {
        "type": "phase5_session_activate",
        "request_id": "req-1",
        "protocol_version": 1,
        "session_type": "owner",
        "capabilities": ["teach_me"],
        "expires_at": 2000.0,
    }
    assert validate_client_message(base) is None
    assert validate_client_message({**base, "actor_id": "owner"}) is not None
    assert validate_client_message({**base, "role": "owner"}) is not None
    assert validate_client_message({**base, "owner_id": "x"}) is not None


def test_bounded_inputs_and_finite_timestamps():
    base = {
        "type": "phase5_capability_prepare",
        "request_id": "req-2",
        "protocol_version": 1,
        "capability": "teach_me",
        "topic": "safe topic",
    }
    assert validate_client_message(base) is None
    assert validate_client_message({**base, "topic": "x" * 1025}) is not None
    assert validate_client_message(
        {
            "type": "phase5_session_activate",
            "request_id": "req-3",
            "protocol_version": 1,
            "session_type": "owner",
            "capabilities": ["teach_me"],
            "expires_at": float("nan"),
        }
    ) is not None


def test_parsing_does_not_mutate_inputs():
    payload = {
        "type": "phase5_helper_grant_list",
        "request_id": "req-4",
        "protocol_version": 1,
    }
    original = copy.deepcopy(payload)
    assert validate_client_message(payload) is None
    assert payload == original


def test_no_generic_execute_tool_message():
    assert "phase5_execute" not in PHASE5_CLIENT_MESSAGE_TYPES
    assert "execute_tool" not in PHASE5_CLIENT_MESSAGE_TYPES


def test_safe_error_frames_only():
    msg = build_phase5_error(request_id="req-5", code="not-a-real-code")
    assert msg["code"] in PHASE5_ERROR_CODES
    assert validate_server_message(msg) is None
    assert "exception" not in msg
    assert "traceback" not in msg


def test_session_update_privacy_safe():
    session = AccessSession(
        session_id="sess-1",
        session_type=SessionType.OWNER,
        owner_actor_id="local-owner",
        session_actor_id="local-owner",
        state=SessionState.ACTIVE,
        created_at=1000.0,
        expires_at=2000.0,
        capabilities=(Capability.TEACH_ME,),
        authority_snapshot=SessionAuthoritySnapshot(
            source=AuthoritySource.OWNER_DIRECT,
            owner_actor_id="local-owner",
            activation_evidence="secret-evidence",
        ),
        transitions=(),
    )
    msg = session_to_update_message(request_id="req-6", session=session)
    assert validate_server_message(msg) is None
    blob = str(msg)
    assert "secret-evidence" not in blob
    assert "activation_evidence" not in blob


def test_helper_grants_wire_shape():
    grant = CapabilityGrant(
        grant_id="grant-1",
        helper_actor_id="helper-1",
        owner_actor_id="local-owner",
        capability=Capability.TEACH_ME,
        scope=ScopeConstraint(capability=Capability.TEACH_ME),
        issued_at=1000.0,
        expires_at=2000.0,
    )
    msg = build_helper_grants_message(request_id="req-7", grants=[grant])
    assert validate_server_message(msg) is None


def test_approval_required_message():
    msg = build_approval_required_message(
        request_id="req-8",
        pending_request_id="req-8",
        capability=Capability.GUIDE_MY_HANDS,
    )
    assert validate_server_message(msg) is None
