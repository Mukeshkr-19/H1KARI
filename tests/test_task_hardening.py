"""Task system hardening: scoping, migration, scheduler, osascript guard."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from core.brain import HikariBrain
from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.schemas import MemoryCandidateStatus
from core.os_side_effects import ENV_DISABLE_OSASCRIPT, osascript_disabled
from core.path_literals import EPISODES_DB
from core.tasks.context import TaskRecordContext
from core.tasks.db_paths import ENV_HIKARI_TASKS_DB
from core.tasks.scheduler import (
    ENV_ENABLE_TASK_SCHEDULER,
    MacOSReminderScheduler,
    SchedulerResult,
    task_scheduler_enabled,
)
from core.tasks.schemas import TaskStatus
from core.tasks.service import TaskIntentService
from core.tasks.sqlite_store import SqliteTaskStore
from core.tasks.store import InMemoryTaskStore
from tests.test_brain_memory import FakeNeural
from tests.test_brain_v2_write_authority import _minimal_orchestrator


@pytest.fixture
def episode_db(tmp_path):
    from core.brain_v2 import EpisodeStore

    return EpisodeStore(db_path=tmp_path / EPISODES_DB)


@pytest.fixture
def tasks_db_path(tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    monkeypatch.setenv(ENV_HIKARI_TASKS_DB, str(path))
    return path


def test_sqlite_schema_migration_from_legacy_db(tasks_db_path):
    with sqlite3.connect(tasks_db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE task_intents (
                task_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                note TEXT
            );
            INSERT INTO task_intents VALUES (
                'legacy1', 'reminder', 'remind me to call Person C', 'not_scheduled',
                '2026-01-01T00:00:00+00:00', 'legacy note'
            );
            """
        )
        conn.commit()

    store = SqliteTaskStore(tasks_db_path, create_dirs=False)
    rows = store.list_recent(limit=5, include_all_scopes=True)
    assert len(rows) == 1
    assert rows[0].task_id == "legacy1"
    assert rows[0].speaker_label == "owner"


def test_task_records_speaker_session_source(tasks_db_path):
    svc = TaskIntentService()
    record = svc.record_intent(
        "remind me to call Person C tomorrow",
        context=TaskRecordContext(
            speaker_label="Owner A",
            session_id="session-1",
            source="text",
        ),
    )
    assert record.speaker_label == "Owner A"
    assert record.session_id == "session-1"
    assert record.source == "text"
    assert record.due_text == "tomorrow"


def test_guest_task_scoped_not_brain_memory(tasks_db_path, episode_db):
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    orch.speaker.set_primary_user("Owner A")

    orch.process_input("I am Guest B talking to you now")
    orch.process_input("remind me to call Person C tomorrow")

    rows = orch._task_intent_service().store.list_recent(
        limit=5, include_all_scopes=True
    )
    assert rows
    assert rows[0].source == "guest"
    assert rows[0].speaker_label == "Guest B"
    assert not episode_db.get_active_accepted_memories(limit=5)


def test_schedule_confirmation_disabled_does_not_call_osascript(
    tasks_db_path, monkeypatch
):
    monkeypatch.delenv(ENV_ENABLE_TASK_SCHEDULER, raising=False)
    svc = TaskIntentService()
    svc.record_intent(
        "remind me to call Person C tomorrow",
        context=TaskRecordContext(speaker_label="Owner A", session_id="sess-a"),
    )
    with patch("core.tasks.scheduler.subprocess.run") as run_mock:
        _record, reply = svc.schedule_latest_reminder(
            context=TaskRecordContext(speaker_label="Owner A", session_id="sess-a")
        )
    assert "not enabled yet" in reply.lower()
    run_mock.assert_not_called()


def test_legacy_schedule_confirmation_stays_quarantined(tasks_db_path, monkeypatch):
    monkeypatch.setenv(ENV_ENABLE_TASK_SCHEDULER, "1")
    scheduler = MacOSReminderScheduler()
    scheduler.schedule_reminder = MagicMock(
        return_value=SchedulerResult(ok=True)
    )
    svc = TaskIntentService(scheduler=scheduler)
    svc.record_intent(
        "remind me to call Person C tomorrow",
        context=TaskRecordContext(speaker_label="Owner A", session_id="sess-b"),
    )
    record, reply = svc.schedule_latest_reminder(
        context=TaskRecordContext(speaker_label="Owner A", session_id="sess-b")
    )
    assert record is not None
    assert record.status == TaskStatus.NOT_SCHEDULED
    assert "not enabled" in reply.lower()
    scheduler.schedule_reminder.assert_not_called()


def test_quarantined_scheduler_does_not_persist_backend_failures(tasks_db_path, monkeypatch):
    monkeypatch.setenv(ENV_ENABLE_TASK_SCHEDULER, "1")
    scheduler = MacOSReminderScheduler()
    scheduler.schedule_reminder = MagicMock(
        return_value=SchedulerResult(ok=False, error="x" * 300)
    )
    svc = TaskIntentService(scheduler=scheduler)
    svc.record_intent(
        "remind me to call Person C tomorrow",
        context=TaskRecordContext(speaker_label="Owner A", session_id="sess-c"),
    )
    record, reply = svc.schedule_latest_reminder(
        context=TaskRecordContext(speaker_label="Owner A", session_id="sess-c")
    )
    assert record is not None
    assert record.status == TaskStatus.NOT_SCHEDULED
    assert record.scheduler_result is None
    assert "not enabled" in reply.lower()
    scheduler.schedule_reminder.assert_not_called()


def test_quarantined_scheduler_never_exposes_backend_error(tasks_db_path, monkeypatch):
    monkeypatch.setenv(ENV_ENABLE_TASK_SCHEDULER, "1")
    scheduler = MacOSReminderScheduler()
    scheduler.schedule_reminder = MagicMock(
        return_value=SchedulerResult(
            ok=False,
            error="osascript failed while creating remind me to call Person C tomorrow",
        )
    )
    svc = TaskIntentService(scheduler=scheduler)
    svc.record_intent(
        "remind me to call Person C tomorrow",
        context=TaskRecordContext(speaker_label="Owner A", session_id="sess-redact"),
    )

    record, reply = svc.schedule_latest_reminder(
        context=TaskRecordContext(speaker_label="Owner A", session_id="sess-redact")
    )

    assert record is not None
    assert record.status == TaskStatus.NOT_SCHEDULED
    assert record.scheduler_result is None
    assert "call Person C" not in reply
    scheduler.schedule_reminder.assert_not_called()


def test_orchestrator_schedule_that_reminder_honest_when_disabled(
    tasks_db_path, episode_db, monkeypatch
):
    monkeypatch.delenv(ENV_ENABLE_TASK_SCHEDULER, raising=False)
    coord = BrainV2Coordinator(store=episode_db, allow_neural_procedural=False)
    orch = _minimal_orchestrator(coord, HikariBrain(FakeNeural([])))
    orch.speaker.set_primary_user("Owner A")

    orch.process_input("remind me to call Person C tomorrow")
    reply = orch.process_input("schedule that reminder")
    assert "not enabled yet" in reply.lower()


def test_osascript_disabled_by_env(monkeypatch):
    monkeypatch.setenv(ENV_DISABLE_OSASCRIPT, "1")
    assert osascript_disabled() is True


def test_tasks_list_shows_scoped_fields(tasks_db_path):
    svc = TaskIntentService()
    svc.record_intent(
        "write code for Topic A",
        context=TaskRecordContext(
            speaker_label="Owner A",
            session_id="sess-cli",
            source="text",
        ),
    )
    env = os.environ.copy()
    env[ENV_HIKARI_TASKS_DB] = str(tasks_db_path)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run(
        [sys.executable, "hikari.py", "--tasks-list", "--all"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.lower()
    assert "owner a" in out
    assert "sess-cli" in out
    assert "not scheduled" in out


def test_scheduler_enabled_flag(monkeypatch):
    monkeypatch.setenv(ENV_ENABLE_TASK_SCHEDULER, "1")
    assert task_scheduler_enabled() is False
