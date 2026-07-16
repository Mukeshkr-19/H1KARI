from __future__ import annotations

import os
import sqlite3
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields

import pytest

from core.action_audit import ActionAuditStore
from core.action_policy import (
    ActionRisk,
    Actor,
    ActorContext,
    PolicyOutcome,
    validate_actor_context,
)
from core.grants import GrantStore
from core.policy_service import ActionRequest, PolicyService


def _kernel(tmp_path):
    grants = GrantStore(tmp_path / "grant-state" / "grants.db")
    audit = ActionAuditStore(tmp_path / "audit-state" / "audit.db")
    return grants, audit, PolicyService(grants, audit)


def _owner(session_id: str = "session-1") -> ActorContext:
    return ActorContext("owner-a", Actor.OWNER, session_id)


def test_action_definition_is_server_owned_and_unknown_shell_action_is_denied(tmp_path):
    _grants, audit, policy = _kernel(tmp_path)
    field_names = {field.name for field in fields(ActionRequest)}
    assert "risk" not in field_names
    assert "data_scope" not in field_names
    with pytest.raises(TypeError):
        ActionRequest(  # type: ignore[call-arg]
            "shell.exec", _owner(), risk=ActionRisk.READ_ONLY
        )

    decision = policy.authorize(ActionRequest("shell.exec", _owner(), True))
    assert decision.outcome is PolicyOutcome.DENY
    assert decision.reason == "unknown_action"
    assert audit.list_recent(1)[0].reason == "unknown_action"


def test_selected_resource_requires_and_consumes_exact_one_use_grant(tmp_path):
    grants, audit, policy = _kernel(tmp_path)
    selected = tmp_path / "selected.txt"
    grant = grants.issue(
        actor=_owner(),
        action="document.read",
        resource=str(selected),
        task_id="task-1",
        expires_at=time.time() + 60,
    )
    request = ActionRequest(
        "document.read",
        _owner(),
        resource=str(selected),
        task_id="task-1",
        grant_id=grant.grant_id,
    )

    assert policy.authorize(request).outcome is PolicyOutcome.ALLOW
    replay = policy.authorize(request)
    assert replay.outcome is PolicyOutcome.DENY
    assert replay.reason == "grant_consumed"
    assert [row.outcome for row in audit.list_recent()] == ["deny", "allow"]


def test_one_use_grant_consumption_is_atomic(tmp_path):
    grants, _audit, policy = _kernel(tmp_path)
    grant = grants.issue(
        actor=_owner(), action="app.open", expires_at=time.time() + 60
    )
    request = ActionRequest(
        "app.open", _owner(), user_initiated=True, grant_id=grant.grant_id
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(lambda _unused: policy.authorize(request).outcome, range(2))
        )
    assert outcomes.count(PolicyOutcome.ALLOW) == 1
    assert outcomes.count(PolicyOutcome.DENY) == 1


def test_grant_matches_actor_resource_task_and_session(tmp_path):
    grants, _audit, policy = _kernel(tmp_path)
    grant = grants.issue(
        actor=_owner(),
        action="document.read",
        resource=str(tmp_path / "a.txt"),
        task_id="task-1",
        expires_at=time.time() + 60,
    )
    wrong = ActionRequest(
        "document.read",
        _owner("session-2"),
        resource=str(tmp_path / "a.txt"),
        task_id="task-1",
        grant_id=grant.grant_id,
    )
    decision = policy.authorize(wrong)
    assert decision.outcome is PolicyOutcome.DENY
    assert decision.reason == "grant_actor_mismatch"


def test_revoked_and_expired_grants_fail_closed(tmp_path):
    grants, _audit, policy = _kernel(tmp_path)
    grant = grants.issue(
        actor=_owner(), action="app.open", expires_at=time.time() + 60
    )
    assert grants.revoke(grant.grant_id)
    request = ActionRequest(
        "app.open", _owner(), user_initiated=True, grant_id=grant.grant_id
    )
    assert policy.authorize(request).reason == "grant_revoked"

    expired = grants.issue(
        actor=_owner(), action="app.open", expires_at=time.time() + 60
    )
    with grants._connect() as conn:
        conn.execute(
            "UPDATE approval_grants SET expires_at = ? WHERE grant_id = ?",
            (time.time() - 1, expired.grant_id),
        )
    request = ActionRequest(**{**request.__dict__, "grant_id": expired.grant_id})
    assert policy.authorize(request).reason == "grant_expired"


@pytest.mark.parametrize(
    "actor",
    [
        ActorContext("", Actor.OWNER, "session-1"),
        ActorContext("owner-a", Actor.OWNER, ""),
        ActorContext("owner-a", "owner", "session-1"),
        ActorContext("owner private content", Actor.OWNER, "session-1"),
    ],
)
def test_invalid_actor_context_fails_closed_and_is_generically_audited(tmp_path, actor):
    _grants, audit, policy = _kernel(tmp_path)
    assert validate_actor_context(actor) == (False, "invalid_actor")
    decision = policy.authorize(ActionRequest("help.read", actor))
    assert decision.outcome is PolicyOutcome.DENY
    assert decision.reason == "invalid_actor"
    row = audit.list_recent(1)[0]
    assert row.actor_id == "invalid"
    assert row.reason == "invalid_actor"


def test_guest_private_read_and_autonomous_side_effect_are_denied(tmp_path):
    _grants, audit, policy = _kernel(tmp_path)
    guest = ActorContext("guest-b", Actor.GUEST, "session-1")
    denied = policy.authorize(
        ActionRequest("document.read", guest, resource=str(tmp_path / "private.txt"))
    )
    assert denied.reason == "actor_scope_denied"
    system = ActorContext("runtime", Actor.SYSTEM, "session-1")
    denied = policy.authorize(
        ActionRequest("schedule.create", system, user_initiated=True)
    )
    assert denied.reason == "actor_not_authorized"
    assert len(audit.list_recent()) == 2


def test_guest_private_denial_does_not_resolve_resource(tmp_path, monkeypatch):
    _grants, _audit, policy = _kernel(tmp_path)

    def must_not_resolve(_resource):
        raise AssertionError("guest path must not be resolved")

    monkeypatch.setattr("core.policy_service.canonicalize_resource", must_not_resolve)
    guest = ActorContext("guest-b", Actor.GUEST, "session-1")
    decision = policy.authorize(
        ActionRequest("document.read", guest, resource="/private/owner/document.txt")
    )
    assert decision.reason == "actor_scope_denied"


def test_audit_rejects_content_in_result_fields(tmp_path):
    _grants, audit, policy = _kernel(tmp_path)
    decision = policy.authorize(ActionRequest("help.read", _owner()))
    audit.record_result(decision.audit_id, status="success", code="ok")
    with pytest.raises(ValueError, match="invalid_result_code"):
        audit.record_result(
            decision.audit_id, status="success", code="document contents are here"
        )
    with pytest.raises(ValueError, match="invalid_result_status"):
        audit.record_result(decision.audit_id, status="document contents are here")


def test_malformed_and_symlink_resources_are_audited_generically(tmp_path):
    _grants, audit, policy = _kernel(tmp_path)
    malformed = policy.authorize(
        ActionRequest("document.read", _owner(), resource=object())  # type: ignore[arg-type]
    )
    assert malformed.reason == "invalid_resource"
    assert audit.list_recent(1)[0].resource_ref is None

    target = tmp_path / "target.txt"
    target.write_text("private", encoding="utf-8")
    link = tmp_path / "selected.txt"
    try:
        os.symlink(target.name, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    looped = policy.authorize(
        ActionRequest("document.read", _owner(), resource=str(link))
    )
    assert looped.reason == "invalid_resource"
    row = audit.list_recent(1)[0]
    assert row.reason == "invalid_resource"
    assert row.resource_ref is None


def test_audit_stores_only_opaque_resource_reference(tmp_path):
    grants, audit, policy = _kernel(tmp_path)
    private_path = tmp_path / "private-owner-name.txt"
    grant = grants.issue(
        actor=_owner(),
        action="document.read",
        resource=str(private_path),
        expires_at=time.time() + 60,
    )
    decision = policy.authorize(
        ActionRequest(
            "document.read",
            _owner(),
            resource=str(private_path),
            grant_id=grant.grant_id,
        )
    )
    assert decision.outcome is PolicyOutcome.ALLOW
    row = audit.list_recent(1)[0]
    assert row.resource_ref is not None
    assert row.resource_ref.startswith("sha256.")
    assert str(private_path) not in row.resource_ref


def test_private_state_permissions(tmp_path):
    grants, audit, _policy = _kernel(tmp_path)
    assert stat.S_IMODE(grants.db_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(audit.db_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(grants.db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(audit.db_path.stat().st_mode) == 0o600


@pytest.mark.parametrize("remaining_uses", [0, 2, True, 1.0, "1"])
def test_phase1_grants_reject_non_exact_one_use(tmp_path, remaining_uses):
    grants, _audit, _policy = _kernel(tmp_path)
    with pytest.raises(ValueError, match="invalid_remaining_uses"):
        grants.issue(
            actor=_owner(),
            action="app.open",
            expires_at=time.time() + 60,
            remaining_uses=remaining_uses,
        )


@pytest.mark.parametrize("expires_at", [True, False, float("nan"), "tomorrow"])
def test_phase1_grants_reject_malformed_expiry(tmp_path, expires_at):
    grants, _audit, _policy = _kernel(tmp_path)
    with pytest.raises(ValueError, match="invalid_expiry"):
        grants.issue(actor=_owner(), action="app.open", expires_at=expires_at)


def test_provider_send_document_uses_registry_network_policy(tmp_path):
    grants, _audit, policy = _kernel(tmp_path)
    document = tmp_path / "selected.txt"
    grant = grants.issue(
        actor=_owner(),
        action="provider.send_document",
        resource=str(document),
        destination="provider-a",
        expires_at=time.time() + 60,
    )
    missing_intent = policy.authorize(
        ActionRequest(
            "provider.send_document",
            _owner(),
            resource=str(document),
            destination="provider-a",
            grant_id=grant.grant_id,
        )
    )
    assert missing_intent.reason == "user_intent_required"
    allowed = policy.authorize(
        ActionRequest(
            "provider.send_document",
            _owner(),
            user_initiated=True,
            resource=str(document),
            destination="provider-a",
            grant_id=grant.grant_id,
        )
    )
    assert allowed.outcome is PolicyOutcome.ALLOW


def test_provider_destination_is_required_and_exact(tmp_path):
    grants, audit, policy = _kernel(tmp_path)
    document = tmp_path / "selected.txt"
    missing = policy.authorize(
        ActionRequest(
            "provider.send_document",
            _owner(),
            user_initiated=True,
            resource=str(document),
        )
    )
    assert missing.reason == "destination_required"

    grant = grants.issue(
        actor=_owner(),
        action="provider.send_document",
        resource=str(document),
        destination="provider-a",
        expires_at=time.time() + 60,
    )
    wrong = policy.authorize(
        ActionRequest(
            "provider.send_document",
            _owner(),
            user_initiated=True,
            resource=str(document),
            destination="provider-b",
            grant_id=grant.grant_id,
        )
    )
    assert wrong.reason == "grant_destination_mismatch"
    assert audit.list_recent(1)[0].destination == "provider-b"

    unexpected = policy.authorize(
        ActionRequest("help.read", _owner(), destination="provider-a")
    )
    assert unexpected.reason == "unexpected_destination"


def test_destination_rules_apply_when_grants_are_issued(tmp_path):
    grants, _audit, _policy = _kernel(tmp_path)
    with pytest.raises(ValueError, match="destination_required"):
        grants.issue(
            actor=_owner(),
            action="provider.send_document",
            resource=str(tmp_path / "selected.txt"),
            expires_at=time.time() + 60,
        )
    with pytest.raises(ValueError, match="unexpected_destination"):
        grants.issue(
            actor=_owner(),
            action="app.open",
            destination="provider-a",
            expires_at=time.time() + 60,
        )


def test_policy_databases_migrate_additive_security_columns(tmp_path):
    grants_path = tmp_path / "old-grants" / "grants.db"
    grants_path.parent.mkdir()
    with sqlite3.connect(grants_path) as conn:
        conn.execute(
            """
            CREATE TABLE approval_grants (
                grant_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, actor TEXT NOT NULL,
                session_id TEXT NOT NULL, action TEXT NOT NULL, resource TEXT,
                task_id TEXT, expires_at REAL NOT NULL, remaining_uses INTEGER NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0
            )
            """
        )
    grants = GrantStore(grants_path)
    with grants._connect() as conn:
        assert "destination" in {
            row[1] for row in conn.execute("PRAGMA table_info(approval_grants)")
        }

    audit_path = tmp_path / "old-audit" / "audit.db"
    audit_path.parent.mkdir()
    with sqlite3.connect(audit_path) as conn:
        conn.execute(
            """
            CREATE TABLE action_audit (
                audit_id TEXT PRIMARY KEY, recorded_at REAL NOT NULL,
                actor_id TEXT NOT NULL, actor TEXT NOT NULL, session_id TEXT NOT NULL,
                task_id TEXT, action TEXT NOT NULL, resource TEXT, destination TEXT,
                outcome TEXT NOT NULL, reason TEXT NOT NULL,
                result_status TEXT, result_code TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO action_audit VALUES (
                'Private Audit Identity', 1, 'Private Owner Identity', 'Private Role',
                'Private Session Identity', 'Private Task Identity',
                'Private Action Text', '/private/legacy-owner-document.txt',
                'Private Destination', 'allow', 'Private Reason Text',
                'Private Result Status', 'Private Result Code'
            )
            """
        )
    audit = ActionAuditStore(audit_path)
    with audit._connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(action_audit)")}
        assert {"resource_ref", "destination"} <= columns
        assert conn.execute(
            "SELECT resource FROM action_audit"
        ).fetchone()[0] is None
    row = audit.list_recent(1)[0]
    assert row.audit_id.startswith("legacy.")
    assert row.actor_id == "invalid"
    assert row.actor == "unknown"
    assert row.session_id == "invalid"
    assert row.task_id is None
    assert row.action == "invalid_action"
    assert row.destination is None
    assert row.outcome == "allow"
    assert row.reason == "legacy_record"
    assert row.result_status is None
    assert row.result_code is None
    private_values = (
        "Private Audit Identity",
        "Private Owner Identity",
        "Private Session Identity",
        "/private/legacy-owner-document.txt",
        "Private Destination",
        "Private Result Code",
    )
    raw_database = audit.db_path.read_bytes()
    rendered_row = repr(row)
    for private_value in private_values:
        assert private_value.encode() not in raw_database
        assert private_value not in rendered_row
