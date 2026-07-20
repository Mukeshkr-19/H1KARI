"""Deterministic tests for the private scheduled-action input store."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.jobs.action_store import (
    ScheduledActionStore,
    ScheduledActionStoreError,
    StoredActionEnvelope,
)
from core.productivity.action_inputs import (
    BrowserResearchAdapterInput,
    CalendarDraftAdapterInput,
    CalendarReadAdapterInput,
    EmailDraftAdapterInput,
    ReminderCreateAdapterInput,
)


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def _store(tmp_path: Path) -> ScheduledActionStore:
    return ScheduledActionStore(tmp_path / "private" / "scheduled-actions.db")


def _input_cases():
    return (
        BrowserResearchAdapterInput("release notes", ("example.com",), 5),
        EmailDraftAdapterInput("person@example.com", "subject", "body\ntext"),
        CalendarReadAdapterInput(
            "2026-07-21T09:00:00Z", "2026-07-21T10:00:00Z", "Work"
        ),
        CalendarDraftAdapterInput(
            "Planning",
            "2026-07-21T09:00:00Z",
            "2026-07-21T10:00:00Z",
            "Work",
            "Room 3",
            "Bring notes",
        ),
        ReminderCreateAdapterInput(
            "Pick up parcel", "2026-07-21T09:00:00Z", "Bring ID", "Errands"
        ),
    )


def _envelope(
    adapter_input=None,
    *,
    number: int = 1,
    actor_id: str = "owner-1",
    session_id: str = "session-1",
    created_at: datetime = NOW,
    expires_at: datetime | None = None,
    revision: int = 1,
) -> StoredActionEnvelope:
    return StoredActionEnvelope(
        job_id=f"job-{number}",
        proposal_id=f"proposal-{number}",
        actor_id=actor_id,
        session_id=session_id,
        adapter_input=adapter_input or _input_cases()[0],
        created_at=created_at,
        expires_at=expires_at or created_at + timedelta(days=30),
        revision=revision,
    )


@pytest.mark.parametrize("adapter_input", _input_cases())
def test_round_trip_preserves_exact_validated_adapter_input(
    tmp_path: Path, adapter_input
) -> None:
    store = _store(tmp_path)
    envelope = _envelope(adapter_input)
    store.put(envelope)

    loaded = store.get("job-1", actor_id="owner-1", session_id="session-1")

    assert loaded is not None
    assert loaded.job_id == envelope.job_id
    assert loaded.proposal_id == envelope.proposal_id
    assert loaded.actor_id == envelope.actor_id
    assert loaded.session_id == envelope.session_id
    assert loaded.adapter_input == adapter_input
    assert loaded.action is adapter_input.action
    assert loaded.revision == 1
    loaded.adapter_input.validate()


def test_unicode_and_maximum_email_body_round_trip_exactly(tmp_path: Path) -> None:
    store = _store(tmp_path)
    body = "🌟" + ("x" * 19_998) + "\n"
    adapter_input = EmailDraftAdapterInput("person@example.com", "Résumé", body)
    adapter_input.validate()
    store.put(_envelope(adapter_input))

    loaded = store.get("job-1", actor_id="owner-1", session_id="session-1")
    assert loaded is not None
    assert loaded.adapter_input == adapter_input


def test_exact_input_persists_across_store_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "private" / "scheduled-actions.db"
    original = _envelope(
        CalendarDraftAdapterInput(
            "Planning",
            "2026-07-21T09:00:00.123456Z",
            "2026-07-21T10:00:00.654321Z",
            "Work",
            "Room 3",
            "Bring notes",
        )
    )
    ScheduledActionStore(db_path).put(original)

    loaded = ScheduledActionStore(db_path).get(
        "job-1", actor_id="owner-1", session_id="session-1"
    )

    assert loaded is not None
    assert loaded.adapter_input == original.adapter_input
    assert loaded.proposal_id == original.proposal_id
    assert loaded.revision == original.revision


def test_cross_actor_and_cross_session_reads_hide_existence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.put(_envelope())

    assert store.get("job-1", actor_id="owner-2", session_id="session-1") is None
    assert store.get("job-1", actor_id="owner-1", session_id="session-2") is None


def test_cas_delete_requires_exact_scope_and_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.put(_envelope(revision=7))

    assert (
        store.delete(
            "job-1",
            actor_id="owner-1",
            session_id="session-1",
            expected_revision=6,
        )
        is False
    )
    assert (
        store.delete(
            "job-1",
            actor_id="owner-2",
            session_id="session-1",
            expected_revision=7,
        )
        is False
    )
    assert store.get("job-1", actor_id="owner-1", session_id="session-1")
    assert (
        store.delete(
            "job-1",
            actor_id="owner-1",
            session_id="session-1",
            expected_revision=7,
        )
        is True
    )
    assert store.get("job-1", actor_id="owner-1", session_id="session-1") is None


def test_session_cap_is_exactly_64_and_other_session_is_independent(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    for number in range(1, 65):
        store.put(_envelope(number=number))
    assert store.count(actor_id="owner-1", session_id="session-1") == 64

    with pytest.raises(ScheduledActionStoreError):
        store.put(_envelope(number=65))

    store.put(_envelope(number=65, session_id="session-2"))
    assert store.count(actor_id="owner-1", session_id="session-2") == 1


def test_duplicate_insert_does_not_replace_existing_content(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = _envelope(EmailDraftAdapterInput("a@example.com", "one", "body"))
    store.put(original)
    with pytest.raises(ScheduledActionStoreError):
        store.put(
            _envelope(EmailDraftAdapterInput("b@example.com", "two", "changed"))
        )

    loaded = store.get("job-1", actor_id="owner-1", session_id="session-1")
    assert loaded is not None
    assert loaded.adapter_input == original.adapter_input


def test_purge_expired_is_bounded_and_uses_absolute_time(tmp_path: Path) -> None:
    store = _store(tmp_path)
    eastern = timezone(timedelta(hours=-4))
    store.put(
        _envelope(
            number=1,
            created_at=datetime(2026, 7, 19, 8, 0, tzinfo=eastern),
            expires_at=datetime(2026, 7, 20, 8, 0, tzinfo=eastern),
        )
    )
    store.put(
        _envelope(
            number=2,
            created_at=NOW,
            expires_at=NOW + timedelta(hours=1),
        )
    )

    assert store.purge_expired(NOW, limit=1) == 1
    assert store.get("job-1", actor_id="owner-1", session_id="session-1") is None
    assert store.get("job-2", actor_id="owner-1", session_id="session-1")


def test_retention_is_strictly_bounded(tmp_path: Path) -> None:
    with pytest.raises(ScheduledActionStoreError):
        _envelope(expires_at=NOW + timedelta(days=366, microseconds=1))
    with pytest.raises(ScheduledActionStoreError):
        _envelope(expires_at=NOW)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("job_id", "Job-1"),
        ("proposal_id", "proposal:1"),
        ("actor_id", "owner value"),
        ("session_id", "session\n1"),
        ("revision", True),
    ),
)
def test_envelope_rejects_invalid_identity_and_revision(field: str, value) -> None:
    values = dict(
        job_id="job-1",
        proposal_id="proposal-1",
        actor_id="owner-1",
        session_id="session-1",
        adapter_input=_input_cases()[0],
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
        revision=1,
    )
    values[field] = value
    with pytest.raises(ScheduledActionStoreError) as raised:
        StoredActionEnvelope(**values)
    assert str(raised.value) == "scheduled action store operation failed"


def test_corrupt_payload_fails_with_fixed_content_free_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.put(_envelope(EmailDraftAdapterInput("a@example.com", "subject", "secret")))
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE scheduled_action_inputs SET payload_json = ? WHERE job_id = ?",
            ('{"body":"private-corruption"}', "job-1"),
        )
        connection.commit()

    with pytest.raises(ScheduledActionStoreError) as raised:
        store.get("job-1", actor_id="owner-1", session_id="session-1")
    assert str(raised.value) == "scheduled action store operation failed"
    assert "private" not in str(raised.value)


def test_repr_omits_paths_identifiers_and_content(tmp_path: Path) -> None:
    store = _store(tmp_path)
    envelope = _envelope(EmailDraftAdapterInput("a@example.com", "subject", "secret"))

    store_repr = repr(store)
    envelope_repr = repr(envelope)
    assert str(tmp_path) not in store_repr
    for private in ("job-1", "proposal-1", "owner-1", "session-1", "secret", "@"):
        assert private not in envelope_repr


def test_directory_database_and_existing_sidecars_are_private(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert os.stat(store.db_path.parent).st_mode & 0o777 == 0o700
    assert os.stat(store.db_path).st_mode & 0o777 == 0o600

    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = store.db_path.with_name(store.db_path.name + suffix)
        sidecar.touch(mode=0o644)
        os.chmod(sidecar, 0o644)
    store.count(actor_id="owner-1", session_id="session-1")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = store.db_path.with_name(store.db_path.name + suffix)
        assert os.stat(sidecar).st_mode & 0o777 == 0o600


def test_source_has_no_execution_or_observability_side_effects() -> None:
    source = (
        Path(__file__).parents[1] / "core" / "jobs" / "action_store.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "import subprocess",
        "import requests",
        "import urllib",
        "import socket",
        "Popen(",
        "run(",
        "logging.",
        "print(",
        "execute_authorized",
    )
    for marker in forbidden:
        assert marker not in source
