"""Durable Phase 1 task lifecycle and migration checks."""

from __future__ import annotations

import os
import sqlite3
import stat

import pytest

from core.tasks.context import TaskRecordContext
from core.tasks.schemas import TaskStatus
from core.tasks.service import TaskIntentService
from core.tasks.sqlite_store import SqliteTaskStore
from core.tasks.store import InMemoryTaskStore

OWNER = TaskRecordContext(speaker_label="Owner A", session_id="session-1")
OWNER_RECONNECTED = TaskRecordContext(
    speaker_label="Owner A",
    session_id="session-2",
)
GUEST = TaskRecordContext(
    speaker_label="Guest B",
    session_id="guest-session",
    source="guest",
    is_guest=True,
)


def _service(tmp_path) -> TaskIntentService:
    return TaskIntentService(store=SqliteTaskStore(tmp_path / "tasks.db"))


def test_task_lifecycle_persists_and_compare_and_swap_rejects_stale_writer(tmp_path):
    path = tmp_path / "tasks.db"
    first_store = SqliteTaskStore(path)
    second_store = SqliteTaskStore(path)
    service = TaskIntentService(store=first_store)

    queued = service.queue_task(
        "Explain the approved document",
        kind="document_explain",
        context=OWNER,
    )
    assert queued.status is TaskStatus.QUEUED
    assert queued.actor == "owner"

    running = service.start_task(queued.task_id, context=OWNER, checkpoint="reading")
    assert running is not None
    assert running.attempt_count == 1
    assert service.update_progress(
        queued.task_id,
        40,
        context=OWNER,
        checkpoint="generating",
    )
    verifying = service.begin_verification(queued.task_id, context=OWNER)
    assert verifying is not None

    stale = second_store.transition(
        queued.task_id,
        expected_status=TaskStatus.RUNNING,
        new_status=TaskStatus.FAILED,
        actor="owner",
        speaker_label="Owner A",
    )
    assert stale is None

    completed = service.complete_task(
        queued.task_id,
        context=OWNER,
        result_summary="Done\x00 and verified",
    )
    assert completed is not None
    assert completed.status is TaskStatus.COMPLETED
    assert completed.progress == 100
    assert completed.result_summary == "Done and verified"
    assert completed.verified_at
    assert completed.completed_at

    reopened = SqliteTaskStore(path).get(
        queued.task_id,
        actor="owner",
        speaker_label="Owner A",
    )
    assert reopened is not None
    assert reopened.status is TaskStatus.COMPLETED
    assert reopened.checkpoint == "completed"


def test_startup_recovery_interrupts_unfinished_work(tmp_path):
    path = tmp_path / "tasks.db"
    service = TaskIntentService(store=SqliteTaskStore(path))
    running = service.queue_task(
        "running task", kind="document_explain", context=OWNER
    )
    verifying = service.queue_task(
        "verifying task", kind="document_explain", context=OWNER
    )
    assert service.start_task(running.task_id, context=OWNER)
    assert service.start_task(verifying.task_id, context=OWNER)
    assert service.begin_verification(verifying.task_id, context=OWNER)

    second_store = SqliteTaskStore(path)
    assert second_store.get(
        running.task_id, actor="owner", speaker_label="Owner A"
    ).status is TaskStatus.RUNNING
    assert second_store.get(
        verifying.task_id, actor="owner", speaker_label="Owner A"
    ).status is TaskStatus.VERIFYING
    recovered = TaskIntentService(store=second_store)
    assert recovered.recover_incomplete(context=OWNER_RECONNECTED) == 2
    assert second_store.get(
        running.task_id, actor="owner", speaker_label="Owner A"
    ).status is TaskStatus.INTERRUPTED
    assert second_store.get(
        verifying.task_id, actor="owner", speaker_label="Owner A"
    ).status is TaskStatus.INTERRUPTED


def test_retry_and_cancellation_are_durable_and_idempotent(tmp_path):
    service = _service(tmp_path)
    task = service.queue_task("retry task", kind="document_explain", context=OWNER)
    assert service.start_task(task.task_id, context=OWNER)
    assert service.update_progress(task.task_id, 70, context=OWNER)
    failed = service.fail_task(
        task.task_id,
        "bad\x00" + "x" * 500,
        context=OWNER,
    )
    assert failed is not None
    assert failed.status is TaskStatus.FAILED
    assert "\x00" not in (failed.last_error or "")
    assert len(failed.last_error or "") == 320
    failed.result_summary = "stale result"
    failed.verified_at = "2026-01-01T00:00:00+00:00"
    failed.completed_at = "2026-01-01T00:00:00+00:00"
    service.store.update(failed)

    retried = service.retry_task(task.task_id, context=OWNER_RECONNECTED)
    assert retried is not None
    assert retried.status is TaskStatus.QUEUED
    assert retried.attempt_count == 1
    assert retried.progress == 0
    assert retried.checkpoint == "queued_for_retry"
    assert retried.last_error is None
    assert retried.result_summary is None
    assert retried.verified_at is None
    assert retried.completed_at is None
    restarted = service.start_task(task.task_id, context=OWNER_RECONNECTED)
    assert restarted is not None
    assert restarted.attempt_count == 2

    cancelled = service.cancel_task(task.task_id, context=OWNER_RECONNECTED)
    assert cancelled is not None
    assert service.cancel_task(task.task_id, context=OWNER) == cancelled
    assert service.retry_task(task.task_id, context=OWNER) is None


def test_actor_and_speaker_scope_survives_reconnect_and_blocks_guest(tmp_path):
    service = _service(tmp_path)
    task = service.queue_task("owner task", kind="document_explain", context=OWNER)

    assert service.get_task(task.task_id, context=OWNER_RECONNECTED) is not None
    assert service.get_task(task.task_id, context=GUEST) is None
    assert service.start_task(task.task_id, context=GUEST) is None
    assert service.cancel_task(task.task_id, context=GUEST) is None
    assert service.get_task(task.task_id, context=OWNER).status is TaskStatus.QUEUED
    assert service.start_task(task.task_id, context=OWNER)
    assert service.update_progress(task.task_id, 50, context=GUEST) is None
    assert service.get_task(task.task_id, context=OWNER).progress == 0


def test_terminal_idempotence_cannot_mutate_fields(tmp_path):
    service = _service(tmp_path)
    task = service.queue_task("cancel once", kind="document_explain", context=OWNER)
    cancelled = service.cancel_task(task.task_id, context=OWNER)
    assert cancelled is not None

    repeated = service.store.transition(
        task.task_id,
        expected_status=TaskStatus.CANCELLED,
        new_status=TaskStatus.CANCELLED,
        progress=99,
        checkpoint="mutated",
        result_summary="mutated",
        actor="owner",
        speaker_label="Owner A",
    )
    assert repeated is not None
    assert repeated.progress == 0
    assert repeated.checkpoint == "cancelled"
    assert repeated.result_summary is None
    assert repeated.updated_at == cancelled.updated_at


@pytest.mark.parametrize("store_kind", ["sqlite", "memory"])
def test_raw_update_cannot_reassign_or_resurrect_task(tmp_path, store_kind):
    store = (
        SqliteTaskStore(tmp_path / "tasks.db")
        if store_kind == "sqlite"
        else InMemoryTaskStore()
    )
    service = TaskIntentService(store=store)
    task = service.queue_task("protected task", kind="document_explain", context=OWNER)

    task.actor = "guest"
    with pytest.raises(ValueError, match="scope"):
        store.update(task)

    current = service.get_task(task.task_id, context=OWNER)
    assert current is not None
    cancelled = service.cancel_task(task.task_id, context=OWNER)
    assert cancelled is not None
    cancelled.status = TaskStatus.QUEUED
    with pytest.raises(ValueError, match="terminal"):
        store.update(cancelled)
    assert store.get(
        task.task_id, actor="owner", speaker_label="Owner A"
    ).status is TaskStatus.CANCELLED


def test_progress_cannot_regress_or_win_with_a_stale_revision(tmp_path):
    service = _service(tmp_path)
    task = service.queue_task("ordered progress", kind="document_explain", context=OWNER)
    assert service.start_task(task.task_id, context=OWNER)
    stale = service.get_task(task.task_id, context=OWNER)
    assert stale is not None
    assert service.update_progress(task.task_id, 80, context=OWNER)
    assert service.update_progress(task.task_id, 20, context=OWNER) is None
    assert service.store.transition(
        task.task_id,
        expected_status=TaskStatus.RUNNING,
        new_status=TaskStatus.RUNNING,
        progress=90,
        expected_updated_at=stale.updated_at,
        actor="owner",
        speaker_label="Owner A",
    ) is None
    assert service.get_task(task.task_id, context=OWNER).progress == 80


def test_progress_and_transition_validation_fail_closed(tmp_path):
    service = _service(tmp_path)
    task = service.queue_task(
        "bounded progress", kind="document_explain", context=OWNER
    )
    assert service.start_task(task.task_id, context=OWNER)
    with pytest.raises(ValueError, match="progress"):
        service.update_progress(task.task_id, 101, context=OWNER)
    with pytest.raises(ValueError, match="invalid task transition"):
        service.store.transition(
            task.task_id,
            expected_status=TaskStatus.RUNNING,
            new_status=TaskStatus.COMPLETED,
            actor="owner",
            speaker_label="Owner A",
        )


def test_persistence_boundary_rejects_invalid_records(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(ValueError, match="task kind"):
        service.queue_task("valid text", kind=" ", context=OWNER)
    with pytest.raises(ValueError, match="task text"):
        service.queue_task(" ", kind="document_explain", context=OWNER)
    with pytest.raises(ValueError, match="task text"):
        service.queue_task("x" * 20_001, kind="document_explain", context=OWNER)
    with pytest.raises(ValueError, match="task actor"):
        service.queue_task(
            "valid text",
            kind="document_explain",
            context=TaskRecordContext(speaker_label="Owner A", actor="invalid"),
        )
    valid = service.queue_task("valid record", kind="document_explain", context=OWNER)
    valid.actor = "invalid"
    with pytest.raises(ValueError, match="task actor"):
        service.store.update(valid)
    with pytest.raises(TypeError, match="trusted task context"):
        service.get_task("missing", context=None)  # type: ignore[arg-type]


def test_additive_migration_preserves_legacy_guest_row(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE task_intents (
                task_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                note TEXT,
                source TEXT
            );
            INSERT INTO task_intents VALUES (
                'legacy-guest', 'reminder', 'call Person C', 'not_scheduled',
                '2026-01-01T00:00:00+00:00', 'legacy', 'guest'
            );
            """
        )

    row = SqliteTaskStore(path).get_legacy_unscoped("legacy-guest")
    assert row is not None
    assert row.status is TaskStatus.NOT_SCHEDULED
    assert row.actor == "guest"
    assert row.progress == 0
    assert row.attempt_count == 0


def test_scoped_list_filters_in_sql_before_limit(tmp_path):
    service = _service(tmp_path)
    target = service.queue_task(
        "target",
        context=TaskRecordContext(speaker_label="Owner A", session_id="session-a"),
    )
    for index in range(10):
        service.queue_task(
            f"other {index}",
            context=TaskRecordContext(
                speaker_label="Owner B",
                session_id="session-b",
            ),
        )

    rows = service.store.list_recent(
        limit=1, actor="owner", speaker_label="Owner A"
    )
    assert [row.task_id for row in rows] == [target.task_id]


def test_unscoped_listing_cannot_enumerate_private_follow_up(tmp_path):
    service = _service(tmp_path)
    root = service.queue_document_root(
        str(tmp_path / "private.txt"), context=OWNER
    )
    assert service.start_task(root.task_id, context=OWNER)
    assert service.begin_verification(root.task_id, context=OWNER)
    assert service.complete_task(root.task_id, context=OWNER, result_summary="summary")
    child = service.queue_follow_up(
        root.task_id, "private follow-up question", context=OWNER
    )
    assert child is not None

    assert service.store.list_recent(limit=20) == []
    assert service.store.list_recent(limit=20, speaker_label=OWNER.speaker_label) == []

    scoped = service.store.list_recent(
        limit=20,
        actor="owner",
        speaker_label=OWNER.speaker_label,
    )
    assert child.task_id in {row.task_id for row in scoped}
    assert any(
        row.raw_text == "private follow-up question"
        and row.session_id == OWNER.session_id
        for row in scoped
    )


def test_new_task_ids_are_full_uuid4_hex(tmp_path):
    service = _service(tmp_path)
    task_ids = {
        service.queue_task(f"task {index}", context=OWNER).task_id
        for index in range(50)
    }

    assert len(task_ids) == 50
    assert all(len(task_id) == 32 for task_id in task_ids)
    assert all(int(task_id, 16) >= 0 for task_id in task_ids)
    assert all(task_id[12] == "4" for task_id in task_ids)
    assert all(task_id[16] in "89ab" for task_id in task_ids)


def test_document_root_and_follow_up_survive_restart_without_copying_path(tmp_path):
    path = tmp_path / "tasks.db"
    service = TaskIntentService(store=SqliteTaskStore(path))
    selected_path = str(tmp_path / "original notes.txt")
    root = service.queue_document_root(selected_path, context=OWNER)
    assert root.selected_path == selected_path
    assert root.parent_task_id is None
    assert service.start_task(root.task_id, context=OWNER)
    assert service.begin_verification(root.task_id, context=OWNER)
    completed_root = service.complete_task(
        root.task_id,
        context=OWNER,
        result_summary="Bounded document explanation",
    )
    assert completed_root is not None

    reconnected = TaskIntentService(store=SqliteTaskStore(path))
    child = reconnected.queue_follow_up(
        root.task_id,
        "What is the main conclusion?",
        context=OWNER_RECONNECTED,
    )
    assert child is not None
    assert child.parent_task_id == root.task_id
    assert child.selected_path is None
    assert child.raw_text == "What is the main conclusion?"
    assert selected_path not in child.raw_text
    assert [row.task_id for row in reconnected.list_children(
        root.task_id,
        context=OWNER_RECONNECTED,
    )] == [child.task_id]
    assert reconnected.start_task(child.task_id, context=OWNER_RECONNECTED)
    assert reconnected.begin_verification(child.task_id, context=OWNER_RECONNECTED)
    completed_child = reconnected.complete_task(
        child.task_id,
        context=OWNER_RECONNECTED,
        result_summary="The conclusion is bounded.\x00",
    )
    assert completed_child is not None

    after_child_restart = TaskIntentService(store=SqliteTaskStore(path))
    stored_child = after_child_restart.get_task(child.task_id, context=OWNER)
    assert stored_child is not None
    assert stored_child.result_summary == "The conclusion is bounded."
    assert stored_child.selected_path is None
    assert stored_child.raw_text == "What is the main conclusion?"

    unchanged_root = after_child_restart.get_task(root.task_id, context=OWNER_RECONNECTED)
    assert unchanged_root == completed_root
    assert after_child_restart.cancel_task(root.task_id, context=OWNER) is None


def test_follow_up_parent_scope_blocks_guest_and_non_root_parent(tmp_path):
    service = _service(tmp_path)
    root = service.queue_document_root("/private/owner.txt", context=OWNER)

    assert service.queue_follow_up(root.task_id, "guest question", context=GUEST) is None
    assert service.list_children(root.task_id, context=GUEST) == []
    assert service.queue_follow_up(root.task_id, "too early", context=OWNER) is None
    assert service.start_task(root.task_id, context=OWNER)
    assert service.begin_verification(root.task_id, context=OWNER)
    assert service.complete_task(root.task_id, context=OWNER)
    with pytest.raises(ValueError, match="task text"):
        service.queue_follow_up(root.task_id, "x" * 20_001, context=OWNER)
    child = service.queue_follow_up(root.task_id, "owner question", context=OWNER)
    assert child is not None
    assert service.queue_follow_up(child.task_id, "nested", context=OWNER) is None


def test_parent_scope_is_enforced_by_both_stores(tmp_path):
    for store in (InMemoryTaskStore(), SqliteTaskStore(tmp_path / "tasks.db")):
        service = TaskIntentService(store=store)
        root = service.queue_document_root("/private/owner.txt", context=OWNER)
        forged = service.queue_task("temporary", context=GUEST)
        forged.parent_task_id = root.task_id
        with pytest.raises(ValueError, match="parent"):
            store.add(forged)


def test_context_columns_migrate_additively(tmp_path):
    path = tmp_path / "legacy-context.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE task_intents (
                task_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                note TEXT,
                source TEXT
            );
            INSERT INTO task_intents VALUES (
                'legacy-root', 'document_read', '/tmp/legacy.txt', 'completed',
                '2026-01-01T00:00:00+00:00', NULL, 'text'
            );
            """
        )

    SqliteTaskStore(path)
    with sqlite3.connect(path) as conn:
        columns = {item[1] for item in conn.execute("PRAGMA table_info(task_intents)")}
        context = conn.execute(
            "SELECT parent_task_id, selected_path FROM task_intents "
            "WHERE task_id = 'legacy-root'"
        ).fetchone()
    assert {"parent_task_id", "selected_path"} <= columns
    assert context == (None, None)


@pytest.mark.parametrize("store_kind", ["sqlite", "memory"])
def test_lifecycle_store_reads_and_mutations_require_scope(tmp_path, store_kind):
    store = (
        SqliteTaskStore(tmp_path / "tasks.db")
        if store_kind == "sqlite"
        else InMemoryTaskStore()
    )
    service = TaskIntentService(store=store)
    task = service.queue_task("scoped", context=OWNER)

    with pytest.raises(TypeError):
        store.get(task.task_id)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="actor scope"):
        store.get(task.task_id, actor="", speaker_label="Owner A")
    with pytest.raises(ValueError, match="speaker scope"):
        store.get(task.task_id, actor="owner", speaker_label="")
    with pytest.raises(TypeError):
        store.transition(  # type: ignore[call-arg]
            task.task_id,
            expected_status=TaskStatus.QUEUED,
            new_status=TaskStatus.RUNNING,
        )
    with pytest.raises(ValueError, match="actor scope"):
        store.transition(
            task.task_id,
            expected_status=TaskStatus.QUEUED,
            new_status=TaskStatus.RUNNING,
            actor="",
            speaker_label="Owner A",
        )
    with pytest.raises(ValueError, match="speaker scope"):
        store.transition(
            task.task_id,
            expected_status=TaskStatus.QUEUED,
            new_status=TaskStatus.RUNNING,
            actor="owner",
            speaker_label="",
        )
    assert store.get_legacy_unscoped(task.task_id) is None
    assert service.get_task(task.task_id, context=OWNER) is not None


def test_sqlite_task_store_hardens_private_directory_and_database_modes(tmp_path):
    private_dir = tmp_path / "state"
    private_dir.mkdir(mode=0o755)
    os.chmod(private_dir, 0o755)
    path = private_dir / "tasks.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE task_intents ("
            "task_id TEXT PRIMARY KEY, kind TEXT NOT NULL, raw_text TEXT NOT NULL, "
            "status TEXT NOT NULL, created_at TEXT NOT NULL, note TEXT)"
        )
    os.chmod(path, 0o644)

    SqliteTaskStore(path)

    assert stat.S_IMODE(private_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
