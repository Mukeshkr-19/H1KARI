"""Tests for the Phase 3 append-only scheduled-job audit store.

These tests cover only ``core.jobs.audit_store.ScheduledJobAuditStore``. They
use temporary database paths (never the repository default) and assert the
absence of forbidden imports and the presence of privacy/permission guarantees.
"""

from __future__ import annotations

import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.jobs.audit import (
    AuditEvent,
    AuditReasonCode,
    AuditStoreError,
)
from core.jobs.audit_store import ScheduledJobAuditStore
from core.jobs.contracts import JobState

UTC = timezone.utc


def _aware(year, month, day, hour, minute, tz=UTC):
    return datetime(year, month, day, hour, minute, tzinfo=tz)


def _make_event(event_id="evt-1", job_id="job-1", **overrides) -> AuditEvent:
    base = dict(
        event_id=event_id,
        job_id=job_id,
        action="digest",
        previous_state=JobState.SCHEDULED,
        new_state=JobState.RUNNING,
        occurred_at=_aware(2026, 7, 18, 9, 0),
        reason_code=AuditReasonCode.STATE_TRANSITION,
    )
    base.update(overrides)
    return AuditEvent(**base)


def _tmp_db(tmp_path: Path, name="audit.db") -> Path:
    return tmp_path / name


# --------------------------------------------------------------------------
# Append / read
# --------------------------------------------------------------------------


def test_append_then_read_returns_frozen_event(tmp_path: Path):
    store = ScheduledJobAuditStore(_tmp_db(tmp_path))
    event = _make_event()
    store.append(event)
    rows = store.read("job-1")
    assert len(rows) == 1
    got = rows[0]
    assert isinstance(got, AuditEvent)
    assert got.event_id == "evt-1"
    assert got.job_id == "job-1"
    assert got.action == "digest"
    assert got.previous_state is JobState.SCHEDULED
    assert got.new_state is JobState.RUNNING
    assert got.reason_code is AuditReasonCode.STATE_TRANSITION
    assert got.occurred_at == _aware(2026, 7, 18, 9, 0)


def test_append_creation_event_with_none_previous(tmp_path: Path):
    store = ScheduledJobAuditStore(_tmp_db(tmp_path))
    store.append(
        _make_event(
            event_id="evt-c",
            previous_state=None,
            new_state=JobState.SCHEDULED,
            reason_code=AuditReasonCode.CREATED,
        )
    )
    rows = store.read("job-1")
    assert rows[0].previous_state is None
    assert rows[0].new_state is JobState.SCHEDULED


def test_read_scoped_to_exact_job_id(tmp_path: Path):
    store = ScheduledJobAuditStore(_tmp_db(tmp_path))
    store.append(_make_event(job_id="job-1"))
    store.append(_make_event(event_id="evt-2", job_id="job-2"))
    assert len(store.read("job-1")) == 1
    assert len(store.read("job-2")) == 1
    assert store.read("job-3") == []


# --------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------


def test_read_ordered_by_occurred_then_event_id(tmp_path: Path):
    store = ScheduledJobAuditStore(_tmp_db(tmp_path))
    store.append(_make_event(event_id="e-b", occurred_at=_aware(2026, 7, 18, 9, 5)))
    store.append(_make_event(event_id="e-a", occurred_at=_aware(2026, 7, 18, 9, 0)))
    store.append(_make_event(event_id="e-c", occurred_at=_aware(2026, 7, 18, 9, 5)))
    ids = [e.event_id for e in store.read("job-1")]
    # e-a (earliest) first; e-b before e-c (same time, lexicographic id).
    assert ids == ["e-a", "e-b", "e-c"]


# --------------------------------------------------------------------------
# Limit bounds
# --------------------------------------------------------------------------


def test_read_limit_bounds(tmp_path: Path):
    store = ScheduledJobAuditStore(_tmp_db(tmp_path))
    for i in range(10):
        store.append(
            _make_event(event_id=f"e-{i}", occurred_at=_aware(2026, 7, 18, 9, i))
        )
    assert len(store.read("job-1", limit=1)) == 1
    assert len(store.read("job-1", limit=256)) == 10
    with pytest.raises(AuditStoreError):
        store.read("job-1", limit=0)
    with pytest.raises(AuditStoreError):
        store.read("job-1", limit=257)
    with pytest.raises(AuditStoreError):
        store.read("job-1", limit=True)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Duplicate event rejection
# --------------------------------------------------------------------------


def test_append_rejects_duplicate_event_id(tmp_path: Path):
    store = ScheduledJobAuditStore(_tmp_db(tmp_path))
    store.append(_make_event(event_id="dup"))
    with pytest.raises(AuditStoreError):
        store.append(_make_event(event_id="dup"))
    # Original is preserved; no overwrite.
    assert len(store.read("job-1")) == 1


# --------------------------------------------------------------------------
# Invalid transition rejection
# --------------------------------------------------------------------------


def test_append_rejects_impossible_transition(tmp_path: Path):
    store = ScheduledJobAuditStore(_tmp_db(tmp_path))
    # Bypass AuditEvent construction validation to exercise the store's own
    # re-validation path (defensive; construction already rejects this too).
    bad = AuditEvent.__new__(AuditEvent)
    object.__setattr__(bad, "event_id", "bad")
    object.__setattr__(bad, "job_id", "job-1")
    object.__setattr__(bad, "action", "digest")
    object.__setattr__(bad, "previous_state", JobState.SCHEDULED)
    object.__setattr__(bad, "new_state", JobState.COMPLETED)
    object.__setattr__(bad, "occurred_at", _aware(2026, 7, 18, 9, 0))
    object.__setattr__(bad, "reason_code", AuditReasonCode.STATE_TRANSITION)
    with pytest.raises(AuditStoreError):
        store.append(bad)
    assert store.read("job-1") == []


# --------------------------------------------------------------------------
# Malformed database rows
# --------------------------------------------------------------------------


def test_read_rejects_malformed_rows(tmp_path: Path):
    store = ScheduledJobAuditStore(_tmp_db(tmp_path))
    store.append(_make_event(event_id="ok"))
    # Inject a row whose state/reason pass the schema CHECK but whose timestamp
    # is not a valid ISO instant; the store must reject it safely on read.
    import sqlite3

    conn = sqlite3.connect(str(store.db_path))
    conn.execute(
        "INSERT INTO scheduled_job_audit "
        "(event_id, job_id, action, previous_state, new_state, occurred_at, reason_code) "
        "VALUES ('bad', 'job-1', 'digest', 'scheduled', 'running', "
        "'not-a-timestamp', 'state_transition')"
    )
    conn.commit()
    conn.close()
    with pytest.raises(AuditStoreError):
        store.read("job-1")


def test_append_rejects_malformed_reason_via_check(tmp_path: Path):
    # The schema CHECK rejects invalid reason codes at the SQLite layer.
    store = ScheduledJobAuditStore(_tmp_db(tmp_path))
    import sqlite3

    conn = sqlite3.connect(str(store.db_path))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO scheduled_job_audit "
            "(event_id, job_id, action, previous_state, new_state, occurred_at, reason_code) "
            "VALUES ('x', 'job-1', 'digest', 'scheduled', 'running', "
            "'2026-07-18T09:00:00+00:00', 'bogus_reason')"
        )
        conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# Restart persistence
# --------------------------------------------------------------------------


def test_persists_across_restart(tmp_path: Path):
    db = _tmp_db(tmp_path)
    store = ScheduledJobAuditStore(db)
    store.append(_make_event(event_id="e1"))
    store.append(_make_event(event_id="e2"))
    # New store instance reopens the same file.
    reopened = ScheduledJobAuditStore(db)
    rows = reopened.read("job-1")
    assert {e.event_id for e in rows} == {"e1", "e2"}


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------


def test_database_permissions_are_restricted(tmp_path: Path):
    db = _tmp_db(tmp_path)
    store = ScheduledJobAuditStore(db)
    store.append(_make_event())
    mode = stat.S_IMODE(os.stat(db).st_mode)
    assert mode == 0o600
    parent_mode = stat.S_IMODE(os.stat(db.parent).st_mode)
    assert parent_mode == 0o700


def test_sidecar_permissions_are_restricted(tmp_path: Path):
    db = _tmp_db(tmp_path)
    store = ScheduledJobAuditStore(db)
    store.append(_make_event())
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = db.with_name(db.name + suffix)
        if sidecar.exists():
            assert stat.S_IMODE(os.stat(sidecar).st_mode) == 0o600


# --------------------------------------------------------------------------
# Safe errors without paths or SQLite details
# --------------------------------------------------------------------------


def test_error_messages_exclude_paths_and_sql(tmp_path: Path):
    store = ScheduledJobAuditStore(_tmp_db(tmp_path))
    # First append succeeds; second with the same id triggers duplicate error.
    store.append(_make_event(event_id="first"))
    with pytest.raises(AuditStoreError) as exc2:
        store.append(_make_event(event_id="first"))
    msg = str(exc2.value)
    assert "first" not in msg  # no event id leakage
    assert "job-1" not in msg  # no job id leakage
    assert ".db" not in msg  # no path leakage
    assert "SELECT" not in msg and "INSERT" not in msg  # no SQL leakage
    assert "sqlite" not in msg.lower()  # no SQLite text leakage


def test_read_rejects_malformed_job_id(tmp_path: Path):
    store = ScheduledJobAuditStore(_tmp_db(tmp_path))
    with pytest.raises(AuditStoreError):
        store.read("has space")


# --------------------------------------------------------------------------
# Privacy schema inspection
# --------------------------------------------------------------------------


def test_schema_excludes_sensitive_columns(tmp_path: Path):
    store = ScheduledJobAuditStore(_tmp_db(tmp_path))
    import sqlite3

    conn = sqlite3.connect(str(store.db_path))
    cols = [
        r[1]
        for r in conn.execute("PRAGMA table_info(scheduled_job_audit)")
    ]
    conn.close()
    allowed = {
        "event_id",
        "job_id",
        "action",
        "previous_state",
        "new_state",
        "occurred_at",
        "reason_code",
    }
    assert set(cols) == allowed
    for forbidden in (
        "actor",
        "session",
        "proposal",
        "approval",
        "payload",
        "target",
        "provider",
        "secret",
        "exception",
        "notification",
    ):
        assert forbidden not in cols


# --------------------------------------------------------------------------
# No forbidden imports
# --------------------------------------------------------------------------


def test_no_forbidden_imports_in_audit_store():
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "core"
        / "jobs"
        / "audit_store.py"
    )
    # os and sqlite3 are required for the store; everything else is forbidden.
    forbidden = {
        "subprocess",
        "socket",
        "threading",
        "logging",
        "smtplib",
        "requests",
        "asyncio",
        "http",
        "network",
        "notify",
        "webbrowser",
        "smtpd",
        "telnetlib",
        "ftplib",
    }
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden, (
                    f"audit_store.py imports forbidden module {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert node.module.split(".")[0] not in forbidden, (
                    f"audit_store.py imports forbidden module {node.module}"
                )
