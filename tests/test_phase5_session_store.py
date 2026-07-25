"""Synthetic test coverage for Phase 5 session store persistence and invariants."""

import os
import sqlite3
import pytest
from pathlib import Path

from core.phase5.contracts import Capability, CapabilityGrant, ScopeConstraint
from core.phase5.session_lifecycle import (
    AccessSession,
    AuthoritySource,
    SessionAuthoritySnapshot,
    SessionDecisionReason,
    SessionState,
    SessionTransition,
    SessionType,
)
from core.phase5.session_store import Phase5SessionStore, StaleRevisionError


def _make_sample_session(
    session_id: str = "sess_001",
    session_type: SessionType = SessionType.OWNER,
    owner_actor_id: str = "owner_1",
    session_actor_id: str = None,
    state: SessionState = SessionState.ACTIVE,
    created_at: float = 1000.0,
    expires_at: float = 2000.0,
    capabilities=(Capability.TEACH_ME,),
    evidence: str = None,
) -> AccessSession:
    if session_actor_id is None:
        session_actor_id = owner_actor_id
    auth = SessionAuthoritySnapshot(
        source=AuthoritySource.OWNER_DIRECT if session_type == SessionType.OWNER else AuthoritySource.CHILD_ACTIVATION,
        owner_actor_id=owner_actor_id,
        activation_evidence=evidence if session_type == SessionType.CHILD else None,
    )
    t = SessionTransition(
        transition_id=f"{session_id}.init",
        session_id=session_id,
        from_state=SessionState.INACTIVE,
        to_state=state,
        reason=SessionDecisionReason.OWNER_ACTIVATION_ALLOWED,
        actor_id=owner_actor_id,
        timestamp=created_at,
    )
    return AccessSession(
        session_id=session_id,
        session_type=session_type,
        owner_actor_id=owner_actor_id,
        session_actor_id=session_actor_id,
        state=state,
        created_at=created_at,
        expires_at=expires_at,
        capabilities=tuple(capabilities),
        authority_snapshot=auth,
        transitions=(t,),
    )


def test_database_permissions_and_schema_initialization(tmp_path: Path):
    db_file = tmp_path / "sessions.db"
    store = Phase5SessionStore(db_file)

    assert db_file.exists()
    file_mode = os.stat(db_file).st_mode & 0o777
    assert file_mode == 0o600


def test_round_trip_persistence(tmp_path: Path):
    db_file = tmp_path / "sessions.db"
    store = Phase5SessionStore(db_file)
    session = _make_sample_session("sess_rt_1")

    saved = store.save_session(session)
    assert saved.session_id == "sess_rt_1"

    loaded = store.get_session("sess_rt_1")
    assert loaded is not None
    assert loaded.session_id == session.session_id
    assert loaded.session_type == session.session_type
    assert loaded.owner_actor_id == session.owner_actor_id
    assert loaded.session_actor_id == session.session_actor_id
    assert loaded.state == session.state
    assert loaded.capabilities == session.capabilities


def test_no_raw_evidence_in_sqlite(tmp_path: Path):
    db_file = tmp_path / "sessions.db"
    store = Phase5SessionStore(db_file)
    raw_evidence = "SUPER_SECRET_RAW_EVIDENCE_12345"
    session = _make_sample_session("sess_ev_1", session_type=SessionType.CHILD, owner_actor_id="owner_1", session_actor_id="child_1", evidence=raw_evidence)

    store.save_session(session)

    # Inspect raw SQLite row content
    conn = sqlite3.connect(db_file)
    row = conn.execute("SELECT evidence_digest, serialized_snapshot FROM phase5_sessions WHERE session_id = ?", ("sess_ev_1",)).fetchone()
    conn.close()

    assert raw_evidence not in str(row[0])
    assert raw_evidence not in str(row[1])


def test_child_evidence_digest_is_stable_across_updates(tmp_path: Path):
    store = Phase5SessionStore(tmp_path / "sessions.db")
    session = _make_sample_session(
        "sess_ev_stable",
        session_type=SessionType.CHILD,
        owner_actor_id="owner_1",
        session_actor_id="child_1",
        evidence="owner-controlled-evidence",
    )
    store.save_session(session)
    loaded = store.get_session("sess_ev_stable")
    first_digest = loaded.authority_snapshot.activation_evidence

    store.save_session(loaded, expected_revision=1)
    reloaded = store.get_session("sess_ev_stable")

    assert reloaded.authority_snapshot.activation_evidence == first_digest
    assert first_digest is not None and len(first_digest) == 64


def test_session_authority_cannot_change_on_update(tmp_path: Path):
    store = Phase5SessionStore(tmp_path / "sessions.db")
    session = _make_sample_session(
        "sess_auth_immutable",
        session_type=SessionType.CHILD,
        owner_actor_id="owner_1",
        session_actor_id="child_1",
        evidence="first-evidence",
    )
    store.save_session(session)
    tampered = _make_sample_session(
        "sess_auth_immutable",
        session_type=SessionType.CHILD,
        owner_actor_id="owner_1",
        session_actor_id="child_1",
        evidence="different-evidence",
    )

    with pytest.raises(ValueError, match="immutable session authority"):
        store.save_session(tampered, expected_revision=1)


def test_stale_revision_rejection(tmp_path: Path):
    db_file = tmp_path / "sessions.db"
    store = Phase5SessionStore(db_file)
    session = _make_sample_session("sess_rev_1")
    store.save_session(session)

    assert store.get_revision("sess_rev_1") == 1

    # Attempt update with expected_revision=5 (stale)
    updated_session = _make_sample_session("sess_rev_1", state=SessionState.LOCKED)
    with pytest.raises(StaleRevisionError):
        store.save_session(updated_session, expected_revision=5)


def test_immutable_identity_and_type_protection(tmp_path: Path):
    db_file = tmp_path / "sessions.db"
    store = Phase5SessionStore(db_file)
    session = _make_sample_session("sess_imm_1", session_type=SessionType.CHILD, owner_actor_id="owner_a", session_actor_id="child_a", evidence="ev_1")
    store.save_session(session)

    # Modify owner_actor_id during update
    tampered = _make_sample_session("sess_imm_1", session_type=SessionType.CHILD, owner_actor_id="owner_b", session_actor_id="child_a", evidence="ev_1")
    with pytest.raises(ValueError, match="immutable"):
        store.save_session(tampered, expected_revision=1)


def test_capability_expansion_rejection(tmp_path: Path):
    db_file = tmp_path / "sessions.db"
    store = Phase5SessionStore(db_file)
    session = _make_sample_session("sess_cap_1", capabilities=(Capability.TEACH_ME,))
    store.save_session(session)

    # Expand capabilities
    expanded = _make_sample_session(
        "sess_cap_1",
        capabilities=(Capability.TEACH_ME, Capability.CARE, Capability.GUIDE_MY_HANDS),
    )
    with pytest.raises(ValueError, match="expansion"):
        store.save_session(expanded, expected_revision=1)


def test_get_active_session_for_actor(tmp_path: Path):
    db_file = tmp_path / "sessions.db"
    store = Phase5SessionStore(db_file)
    s1 = _make_sample_session("sess_act_1", session_type=SessionType.CHILD, owner_actor_id="owner_1", session_actor_id="actor_x", evidence="ev_1", created_at=100.0, expires_at=200.0)
    store.save_session(s1)

    # Active at time 150
    active = store.get_active_session_for_actor("actor_x", now=150.0)
    assert active is not None
    assert active.session_id == "sess_act_1"

    # Expired at time 250
    assert store.get_active_session_for_actor("actor_x", now=250.0) is None


def test_deterministic_ordering_and_bounded_lists(tmp_path: Path):
    db_file = tmp_path / "sessions.db"
    store = Phase5SessionStore(db_file)

    for i in range(10):
        s = _make_sample_session(f"sess_ord_{i:02d}", owner_actor_id="owner_1", created_at=1000.0 + i)
        store.save_session(s)

    list_5 = store.list_owner_sessions("owner_1", limit=5)
    assert len(list_5) == 5
    assert list_5[0].session_id == "sess_ord_09"
    assert list_5[1].session_id == "sess_ord_08"


def test_expire_due_sessions(tmp_path: Path):
    db_file = tmp_path / "sessions.db"
    store = Phase5SessionStore(db_file)

    s_exp = _make_sample_session("sess_to_expire", created_at=100.0, expires_at=200.0)
    s_live = _make_sample_session("sess_to_stay", created_at=100.0, expires_at=500.0)

    store.save_session(s_exp)
    store.save_session(s_live)

    count = store.expire_due_sessions(now=300.0)
    assert count == 1

    loaded_exp = store.get_session("sess_to_expire")
    assert loaded_exp.state == SessionState.EXPIRED

    loaded_live = store.get_session("sess_to_stay")
    assert loaded_live.state == SessionState.ACTIVE


def test_corrupt_row_fails_closed(tmp_path: Path):
    db_file = tmp_path / "sessions.db"
    store = Phase5SessionStore(db_file)
    s = _make_sample_session("sess_corrupt")
    store.save_session(s)

    # Corrupt serialized JSON in DB
    conn = sqlite3.connect(db_file)
    conn.execute("UPDATE phase5_sessions SET serialized_snapshot = 'CORRUPTED_JSON' WHERE session_id = 'sess_corrupt'")
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="corrupt"):
        store.get_session("sess_corrupt")


def test_content_free_repr(tmp_path: Path):
    db_file = tmp_path / "repr.db"
    store = Phase5SessionStore(db_file)
    assert repr(store) == "Phase5SessionStore()"
